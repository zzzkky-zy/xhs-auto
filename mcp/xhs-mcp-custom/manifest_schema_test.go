package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/google/jsonschema-go/jsonschema"
	"github.com/stretchr/testify/require"
)

// 使用已固定的标准 Schema 和现有 SDK 依赖离线验证，不能据此宣称 dot 已导入。
func TestPortableManifestSchemas(t *testing.T) {
	root := filepath.Join("..", "..")
	var lock struct {
		Schemas map[string]struct {
			SHA256 string `json:"sha256"`
		} `json:"manifest_schemas"`
	}
	data, err := os.ReadFile(filepath.Join(root, "toolchains.lock.json"))
	require.NoError(t, err)
	require.NoError(t, json.Unmarshal(data, &lock))
	for _, c := range []struct{ name, file string }{{"plugin", "plugin.json"}, {"mcp", "mcp.json"}, {"mcp", "config/mcp.remote.example.json"}} {
		t.Run(c.file, func(t *testing.T) {
			raw, err := os.ReadFile(filepath.Join(root, "schemas", c.name+".schema.json"))
			require.NoError(t, err)
			hash := sha256.Sum256(raw)
			require.Equal(t, lock.Schemas[c.name].SHA256, hex.EncodeToString(hash[:]))
			var schema jsonschema.Schema
			require.NoError(t, json.Unmarshal(raw, &schema))
			// 标准负向前瞻等价为“不含连续--/..”和普通RE2模式。
			// SDK采用RE2，不支持前瞻；精确检查原规则后显式分解，不放宽规则。
			if c.name == "plugin" {
				require.Equal(t, `^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$`, schema.Properties["name"].Pattern)
				schema.Properties["name"].Pattern = `^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$`
			}
			resolved, err := schema.Resolve(nil)
			require.NoError(t, err)
			validate := func(value any) error {
				if c.name == "plugin" {
					object, _ := value.(map[string]any)
					name, _ := object["name"].(string)
					if strings.Contains(name, "--") || strings.Contains(name, "..") {
						return fmt.Errorf("invalid plugin name")
					}
				}
				return resolved.Validate(value)
			}
			contents, err := os.ReadFile(filepath.Join(root, c.file))
			require.NoError(t, err)
			var value any
			require.NoError(t, json.Unmarshal(contents, &value))
			require.NoError(t, validate(value))
			object := value.(map[string]any)
			if c.name == "plugin" {
				original := object["name"]
				for _, invalid := range []string{"two--dashes", "two..dots", "-leading", "trailing-", "Upper", "slash/name", ""} {
					object["name"] = invalid
					require.Error(t, validate(value), invalid)
				}
				object["name"] = original
			}
			object["unknown_field"] = true
			require.Error(t, validate(object))
		})
	}
}
