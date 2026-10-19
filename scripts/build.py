#!/usr/bin/env python3
"""固定工具链、离线模块构建。不会安装服务或启动浏览器。"""
import argparse
import json
import os
from pathlib import Path
import platform
import subprocess

ROOT = Path(__file__).resolve().parents[1]
GO_VERSION = "go1.27.2"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=["darwin-arm64", "darwin-amd64", "linux-amd64", "linux-arm64"])
    args = parser.parse_args()
    local = ROOT / ".tools/go/bin/go"
    executable = os.getenv("XHS_GO_BINARY") or str(local)
    if not Path(executable).is_file(): raise SystemExit("需要项目锁定的 Go 1.27.2；见 toolchains.lock.json，未安装任何全局软件")
    version = subprocess.check_output([executable, "version"], text=True).split()[2]
    if version != GO_VERSION: raise SystemExit("Go version mismatch: expected " + GO_VERSION + ", got " + version)
    if not (ROOT / "mcp/xhs-mcp-custom/vendor/modules.txt").exists(): raise SystemExit("缺少锁定的 vendor；用完整交付包，或 go mod vendor 并核验 go.sum")
    target = args.target or ("darwin" if platform.system() == "Darwin" else "linux") + "-" + {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64"}[platform.machine()]
    env = os.environ.copy(); env.update(dict(zip(("GOOS", "GOARCH"), target.split("-"))))
    env.update({"CGO_ENABLED": "0", "GOTOOLCHAIN": "local", "GOPROXY": "off"})
    env.setdefault("GOMAXPROCS", "2")
    out = ROOT / "bin" / ("xhs-mcp-custom" if args.target is None else "xhs-mcp-custom-" + target)
    out.parent.mkdir(exist_ok=True)
    subprocess.run([executable, "build", "-p=2", "-mod=vendor", "-trimpath", "-buildvcs=false", "-ldflags", "-X main.version=" + (ROOT / "VERSION").read_text().strip(), "-o", str(out), "."], cwd=ROOT / "mcp/xhs-mcp-custom", env=env, check=True)
    print(json.dumps({"target": target, "binary": str(out), "go": GO_VERSION, "status": "built", "runtime_acceptance": "not_implied"}))


if __name__ == "__main__": main()
