//go:build fixture

package fixturebrowser

import (
	"os"
	"runtime"
	"testing"
)

// Require selects only an explicitly configured or separately installed browser.
func Require(t testing.TB) string {
	t.Helper()
	bin := os.Getenv("XHS_TEST_CHROME")
	if bin == "" {
		bin = os.Getenv("AI_TEST_BROWSER_EXECUTABLE")
	}
	if bin == "" && runtime.GOOS == "darwin" {
		bin = "/Applications/AI Test Browser.app/Contents/MacOS/AI Test Browser"
	}
	if bin == "" {
		t.Skip("fixture requires a separate XHS_TEST_CHROME; no browser downloaded")
	}
	if err := Validate(bin); err != nil {
		t.Fatal(err)
	}
	return bin
}
