package fixturebrowser

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func fixtureApp(t *testing.T, name, id string) string {
	t.Helper()
	app := filepath.Join(t.TempDir(), name+".app", "Contents")
	if err := os.MkdirAll(filepath.Join(app, "MacOS"), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(app, "Info.plist"), []byte(`<?xml version="1.0"?><plist version="1.0"><dict><key>CFBundleIdentifier</key><string>`+id+`</string></dict></plist>`), 0600); err != nil {
		t.Fatal(err)
	}
	bin := filepath.Join(app, "MacOS", "Browser")
	if err := os.WriteFile(bin, []byte("#!/bin/sh\nexit 0\n"), 0700); err != nil {
		t.Fatal(err)
	}
	return bin
}

func TestRejectPersonalChromeIncludingRenamedBundleAndSymlink(t *testing.T) {
	if runtime.GOOS != "darwin" {
		t.Skip("macOS application identity")
	}
	bin := fixtureApp(t, "Renamed Personal Browser", "com.google.Chrome")
	link := filepath.Join(t.TempDir(), "browser")
	if err := os.Symlink(bin, link); err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{bin, link} {
		if err := Validate(path); err == nil {
			t.Errorf("personal Chrome must be rejected before launch: %s", path)
		}
	}
}

func TestAllowSeparateTestingApplication(t *testing.T) {
	if err := Validate(fixtureApp(t, "AI Test Browser", "local.ai-test-browser")); err != nil {
		t.Fatal(err)
	}
}

func TestRejectMissingExecutable(t *testing.T) {
	if err := Validate(filepath.Join(t.TempDir(), "missing")); err == nil {
		t.Fatal("missing browser must fail without falling back to personal Chrome")
	}
}
