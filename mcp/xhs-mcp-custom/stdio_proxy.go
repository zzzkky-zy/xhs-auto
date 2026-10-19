package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"strings"
	"sync"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

type proxyAuthTransport struct{ token string }

func (t proxyAuthTransport) RoundTrip(request *http.Request) (*http.Response, error) {
	copy := request.Clone(request.Context())
	copy.Header = request.Header.Clone()
	if t.token != "" {
		copy.Header.Set("Authorization", "Bearer "+t.token)
	}
	return http.DefaultTransport.RoundTrip(copy)
}

var proxyConfig struct {
	sync.Mutex
	initialized bool
	fileDigest  string
	fingerprint string
}

func bootstrapProxy(ctx context.Context) (string, error) {
	python, script, config := os.Getenv("XHS_OPS_PYTHON"), os.Getenv("XHS_OPS_SCRIPT"), os.Getenv("XHS_OPS_CONFIG")
	if python == "" || script == "" || config == "" {
		return "", fmt.Errorf("ERR_MCP_BOOTSTRAP: use runtime/codex_mcp.py with private config")
	}
	cmd := exec.CommandContext(ctx, python, script, "--config", config, "service", "ensure")
	data, err := cmd.Output()
	if err != nil {
		if failure, ok := err.(*exec.ExitError); ok {
			return "", fmt.Errorf("ERR_SERVICE_NOT_READY: %s", safeBridgeError(failure.Stderr))
		}
		return "", fmt.Errorf("ERR_SERVICE_NOT_READY: check private supervisor log")
	}
	var value struct {
		Fingerprint string `json:"config_fingerprint"`
	}
	if json.Unmarshal(data, &value) != nil || value.Fingerprint == "" {
		return "", fmt.Errorf("ERR_SERVICE_IDENTITY: missing effective settings fingerprint")
	}
	return value.Fingerprint, nil
}

func ensureProxyBackend(ctx context.Context) error {
	ctx, cancel := context.WithTimeout(ctx, 20*time.Second)
	defer cancel()
	proxyConfig.Lock()
	defer proxyConfig.Unlock()
	if !proxyConfig.initialized {
		proxyConfig.fileDigest, proxyConfig.fingerprint = os.Getenv("XHS_CONFIG_FILE_SHA256"), os.Getenv("XHS_CONFIG_FINGERPRINT")
		proxyConfig.initialized = true
	}
	raw, err := os.ReadFile(os.Getenv("XHS_OPS_CONFIG"))
	if err != nil {
		return fmt.Errorf("ERR_MCP_BOOTSTRAP: private config unavailable")
	}
	sum := sha256.Sum256(raw)
	digest := hex.EncodeToString(sum[:])
	if digest != proxyConfig.fileDigest {
		fp, err := bootstrapProxy(ctx)
		if err != nil {
			return err
		}
		proxyConfig.fileDigest, proxyConfig.fingerprint = digest, fp
	}
	if endpoint := os.Getenv("XHS_MCP_URL"); endpoint != "" {
		probe, cancelProbe := context.WithTimeout(ctx, time.Second)
		req, err := http.NewRequestWithContext(probe, http.MethodGet, strings.TrimSuffix(endpoint, "/mcp")+"/runtime", nil)
		if err != nil {
			cancelProbe()
			return fmt.Errorf("ERR_SERVICE_IDENTITY: invalid configured endpoint")
		}
		if token := os.Getenv("AUTH_TOKEN"); token != "" {
			req.Header.Set("Authorization", "Bearer "+token)
		}
		response, err := http.DefaultClient.Do(req)
		if err == nil {
			var value struct {
				Data struct {
					Account          string `json:"account"`
					StateID          string `json:"state_id"`
					Version          string `json:"version"`
					Fingerprint      string `json:"config_fingerprint"`
					PersistenceError bool   `json:"persistence_error"`
				} `json:"data"`
			}
			decodeErr := json.NewDecoder(response.Body).Decode(&value)
			response.Body.Close()
			cancelProbe()
			if response.StatusCode != 200 || decodeErr != nil || value.Data.Account != os.Getenv("XHS_ACCOUNT_ID") || value.Data.StateID != os.Getenv("XHS_EXPECTED_STATE_ID") || value.Data.Version != version || value.Data.PersistenceError {
				return fmt.Errorf("ERR_SERVICE_IDENTITY: existing runtime does not match private binding")
			}
			if value.Data.Fingerprint == proxyConfig.fingerprint && value.Data.Fingerprint != "" {
				return nil
			}
		} else {
			cancelProbe()
		}
	}
	fp, err := bootstrapProxy(ctx)
	if err == nil {
		proxyConfig.fingerprint, proxyConfig.fileDigest = fp, digest
	}
	return err
}

func connectProxy(ctx context.Context, endpoint string) (*mcp.ClientSession, error) {
	client := mcp.NewClient(&mcp.Implementation{Name: "xhs-local-proxy", Version: version}, nil)
	return client.Connect(ctx, &mcp.StreamableClientTransport{Endpoint: endpoint, HTTPClient: &http.Client{Transport: proxyAuthTransport{os.Getenv("AUTH_TOKEN")}, Timeout: 3 * time.Minute}, MaxRetries: -1, DisableStandaloneSSE: true}, nil)
}

func proxyTool(ctx context.Context, endpoint, name string, params *mcp.CallToolParamsRaw) (*mcp.CallToolResult, error) {
	return forwardProxyTool(ctx, endpoint, name, params, ensureProxyBackend)
}

func forwardProxyTool(ctx context.Context, endpoint, name string, params *mcp.CallToolParamsRaw, ensure func(context.Context) error) (*mcp.CallToolResult, error) {
	ctx, cancel := context.WithTimeout(ctx, 3*time.Minute)
	defer cancel()
	if err := ensure(ctx); err != nil {
		return &mcp.CallToolResult{IsError: true, Content: []mcp.Content{&mcp.TextContent{Text: "ERR_MCP_NOT_DISPATCHED: " + err.Error()}}}, nil
	}
	session, err := connectProxy(ctx, endpoint)
	if err != nil {
		return &mcp.CallToolResult{IsError: true, Content: []mcp.Content{&mcp.TextContent{Text: "ERR_MCP_NOT_DISPATCHED: upstream connection unavailable; no tool dispatched"}}}, nil
	}
	defer session.Close()
	result, err := session.CallTool(ctx, &mcp.CallToolParams{Name: name, Arguments: params.Arguments, Meta: params.Meta})
	if err != nil {
		// 请求一旦发出，丢失结果就保守视为未知；绝不重放 tools/call。
		return &mcp.CallToolResult{IsError: true, Content: []mcp.Content{&mcp.TextContent{Text: "ERR_OPERATION_UNKNOWN: upstream result unavailable; query operation status before retrying"}}}, nil
	}
	return result, nil
}

func runStdioProxy(endpoint string) error {
	ctx := context.Background()
	setup, cancel := context.WithTimeout(ctx, 25*time.Second)
	defer cancel()
	if err := ensureProxyBackend(setup); err != nil {
		return err
	}
	session, err := connectProxy(setup, endpoint)
	if err != nil {
		return err
	}
	tools, err := session.ListTools(setup, nil)
	session.Close()
	if err != nil {
		return err
	}
	server := mcp.NewServer(&mcp.Implementation{Name: "xiaohongshu-operations", Version: version}, nil)
	for _, tool := range tools.Tools {
		name := tool.Name
		server.AddTool(tool, func(ctx context.Context, request *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
			return proxyTool(ctx, endpoint, name, request.Params)
		})
	}
	return server.Run(ctx, &mcp.StdioTransport{})
}
