package main

import (
	"fmt"
	"os"
	"path/filepath"
	"syscall"

	"github.com/sirupsen/logrus"
)

const defaultLockFileName = "xhs-mcp-custom.lock"

// acquireInstanceLock 获取全局文件锁，确保同一时刻只有一个 MCP 实例。
// 锁文件默认写在可执行文件同目录（或 COOKIES_PATH 所在目录旁），也可用 XHS_INSTANCE_LOCK 覆盖。
// 第二实例启动时直接失败并提示已有实例。
// 返回的 closer 应在进程退出时调用（关闭后释放锁）。
func acquireInstanceLock() (*os.File, error) {
	path := os.Getenv("XHS_INSTANCE_LOCK")
	if path == "" {
		path = filepath.Join(runtimeDir(), defaultLockFileName)
	}

	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil && filepath.Dir(path) != "." {
		return nil, fmt.Errorf("创建锁目录失败: %w", err)
	}

	f, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil, fmt.Errorf("打开实例锁失败 (%s): %w", path, err)
	}

	if err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		_ = f.Close()
		return nil, fmt.Errorf(
			"已有 xhs-mcp-custom 实例在运行（锁文件 %s）。请先结束旧进程，或换 XHS_INSTANCE_LOCK 路径。底层: %v",
			path, err,
		)
	}

	if err := f.Truncate(0); err == nil {
		_, _ = f.Seek(0, 0)
		_, _ = fmt.Fprintf(f, "%d\n", os.Getpid())
		_ = f.Sync()
	}

	logrus.Infof("单实例锁已获取: %s (pid=%d)", path, os.Getpid())
	return f, nil
}
