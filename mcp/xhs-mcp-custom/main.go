package main

import (
	"flag"
	"os"

	"github.com/sirupsen/logrus"
	"github.com/xpzouying/xiaohongshu-mcp/browser"
	"github.com/xpzouying/xiaohongshu-mcp/configs"
	"github.com/xpzouying/xiaohongshu-mcp/cookies"
	"github.com/xpzouying/xiaohongshu-mcp/execution"
)

// version 构建版本号，发布时通过 -ldflags "-X main.version=vX.Y.Z" 注入。
var version = "dev"

func main() {
	var (
		headless     bool
		port         string
		token        string
		stdioProxy   bool
		upstream     string
		exportLegacy bool
	)
	flag.BoolVar(&headless, "headless", true, "是否无头模式（仅 XHS_LAUNCH_BROWSER=1 自启时有意义；attach CDP 时忽略）")
	flag.StringVar(&port, "port", "127.0.0.1:18061", "监听地址，默认仅本机")
	flag.StringVar(&token, "token", "", "鉴权 Token，留空则读取 AUTH_TOKEN")
	flag.BoolVar(&stdioProxy, "stdio-proxy", false, "通过标准stdio接入本机MCP；不重放工具调用")
	flag.StringVar(&upstream, "upstream", "", "stdio代理的配置服务地址")
	flag.BoolVar(&exportLegacy, "export-legacy-state", false, "离线导出当前完整提交记录供0.3.6回退；必须先停止服务")
	flag.Parse()
	if exportLegacy {
		lock, err := acquireInstanceLock()
		if err != nil {
			logrus.Fatal(err)
		}
		defer lock.Close()
		s := NewXiaohongshuService()
		if os.Getenv("XHS_EXPECTED_STATE_ID") == "" {
			logrus.Fatal("ERR_STATE_UNCONFIRMED: bind the exact private state before export")
		}
		if err := s.InitRuntime(); err != nil {
			logrus.Fatal(err)
		}
		if err := s.state.exportLegacy(); err != nil {
			logrus.Fatal(err)
		}
		return
	}
	if stdioProxy {
		if err := runStdioProxy(upstream); err != nil {
			logrus.Error(err)
			os.Exit(1)
		}
		return
	}
	if token == "" {
		token = os.Getenv("AUTH_TOKEN")
	}

	if err := validateListenAddress(port, token); err != nil {
		logrus.Fatal(err)
	}

	logrus.Infof("xhs-mcp-custom (fork of xiaohongshu-mcp) version: %s", version)

	// 单实例锁：第二实例启动失败并明确报错
	lockFile, err := acquireInstanceLock()
	if err != nil {
		logrus.Fatalf("%v", err)
	}
	defer lockFile.Close()

	cdpURL := configs.CDPURLFromEnv()
	switch {
	case cdpURL != "":
		logrus.Infof("browser mode: attach CDP %s（禁止自启；会话以 box-chrome 为准）", cdpURL)
	case configs.LaunchBrowserAllowed():
		binPath, err := browser.EnsureBrowser()
		if err != nil {
			logrus.Fatalf("%v", err)
		}
		logrus.Warnf("browser mode: XHS_LAUNCH_BROWSER=1 自启内置 Chromium: %s", binPath)
	default:
		logrus.Fatalf("未设置 XHS_CDP_URL/XHS_CDP_PORT，且未开 XHS_LAUNCH_BROWSER=1：拒绝启动。请先开 box-chrome（本会话通常 CDP :9224），例如：\n  export XHS_CDP_URL=http://127.0.0.1:9224\n  ./bin/xhs-mcp-custom -headless=false -port :18061")
	}

	configs.InitHeadless(headless)
	// 入口层解析出 seed 和代理，经 configs 透传给浏览器工厂。
	// seed 取值：环境变量 > 会话文件 > 新生成并写回，保证同一账号每次启动一致。
	// attach 模式下 fingerprint/proxy 不作用于已有 Chrome，仍保留配置便于兜底自启。
	if cdpURL == "" {
		configs.SetFingerprintSeed(configs.ResolveFingerprintSeed(cookies.NewLoadCookie(cookies.GetCookiesFilePath())))
	}
	configs.SetProxy(configs.ProxyFromEnv())
	if configs.Proxy() == "" {
		logrus.Info("XHS_PROXY 未设置，直连（推荐；attach 时以 box-chrome 代理为准）")
	} else {
		logrus.Infof("XHS_PROXY 已设置（仅自启路径生效）；attach 时以 box-chrome 启动参数为准")
	}

	execution.Configure(os.Getenv("XHS_STATE_DIR"))

	// 初始化服务
	xiaohongshuService := NewXiaohongshuService()
	if err := xiaohongshuService.InitRuntime(); err != nil {
		logrus.Fatalf("初始化运行记录失败: %v", err)
	}

	// 创建并启动应用服务器
	appServer := NewAppServer(xiaohongshuService, token)
	if err := appServer.Start(port); err != nil {
		logrus.Fatalf("failed to run server: %v", err)
	}
}
