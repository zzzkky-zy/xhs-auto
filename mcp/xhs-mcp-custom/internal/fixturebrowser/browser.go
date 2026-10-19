// Package fixturebrowser selects a browser for local HTML fixtures only.
package fixturebrowser

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
)

// Validate fails closed instead of letting a launcher discover personal Chrome.
func Validate(path string) error {
	resolved, err := filepath.EvalSymlinks(path)
	if err != nil {
		return fmt.Errorf("fixture browser: %w", err)
	}
	info, err := os.Stat(resolved)
	if err != nil {
		return err
	}
	if !info.Mode().IsRegular() || info.Mode()&0111 == 0 {
		return fmt.Errorf("fixture browser is not executable: %s", resolved)
	}
	if runtime.GOOS != "darwin" {
		return nil
	}
	for dir := filepath.Dir(resolved); dir != filepath.Dir(dir); dir = filepath.Dir(dir) {
		if !strings.HasSuffix(strings.ToLower(dir), ".app") {
			continue
		}
		id, err := exec.Command("/usr/libexec/PlistBuddy", "-c", "Print :CFBundleIdentifier", filepath.Join(dir, "Contents", "Info.plist")).Output()
		if err != nil {
			return fmt.Errorf("cannot verify fixture app identity: %w", err)
		}
		if strings.EqualFold(strings.TrimSpace(string(id)), "com.google.Chrome") || strings.EqualFold(filepath.Base(dir), "Google Chrome.app") {
			return fmt.Errorf("personal Google Chrome is forbidden for fixtures; use AI_TEST_BROWSER_EXECUTABLE or /Applications/AI Test Browser.app")
		}
		return nil
	}
	return nil
}
