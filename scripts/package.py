#!/usr/bin/env python3
"""白名单打包；不收集私有运行状态、用户配置、凭据或历史包。"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
TOP = ["AGENTS.md", "VERSION", "README.md", "HANDOFF.md", "plugin.json", "mcp.json", "requirements.txt", "toolchains.lock.json"]
DIRS = ["docs", "runtime", "scripts", "schemas", "skills/xiaohongshu-operations", "skills/humanizer-zh", "tests"]
RUNTIME_DIRS = ["docs", "runtime", "schemas", "skills/xiaohongshu-operations", "skills/humanizer-zh"]
RUNTIME_SCRIPTS = ["setup_codex.py", "smoke_install.py", "cloud_host.py", "cloud_handoff.py", "cloud_checkpoint.py", "private_profile.py"]
FORBIDDEN = {"cookies.json", "xhs.local.json", ".env", "managed-process.json", "runtime-state.json", "tasks.sqlite3", "operation-journal.db"}


def files(target="source"):
    items = [ROOT / p for p in TOP]
    for d in (DIRS if target == "source" else RUNTIME_DIRS):
        items.extend(p for p in (ROOT / d).rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.name not in {"test-results.json", ".DS_Store"})
    if target != "source":
        items.extend(ROOT / "scripts" / p for p in RUNTIME_SCRIPTS)
    items.extend(p for p in (ROOT / "config").glob("*.example.json"))
    # 日常运行仅需二进制和许可；完整构建材料留在独立源码包。
    module = ROOT / "mcp/xhs-mcp-custom"
    if target == "source":
        items.extend(p for p in module.rglob("*.go") if "vendor" not in p.parts)
        items.extend(module / p for p in ["go.mod", "go.sum", "LICENSE", "browser/browser_version.txt"])
        items.extend(p for p in (module / "vendor").rglob("*") if p.is_file())
    else:
        items.append(module / "LICENSE")
        items.extend(p for p in (module / "vendor").rglob("*") if p.is_file() and
                     (p.name.lower().startswith(("license", "copying", "notice")) or p.name == "AUTHORS"))
    return sorted(set(items), key=lambda p: str(p.relative_to(ROOT)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=["source", "linux-amd64", "linux-arm64", "darwin-arm64"], default="source")
    args = parser.parse_args()
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"], cwd=ROOT, text=True)
    if dirty.strip(): raise SystemExit("源码尚未冻结；先检查并提交本轮明确文件，再打包，避免 source_commit 与实际文件不一致")
    version = (ROOT / "VERSION").read_text().strip()
    selected = files(args.target)
    if args.target != "source":
        binary = ROOT / "bin" / ("xhs-mcp-custom" if args.target == "darwin-arm64" else "xhs-mcp-custom-" + args.target)
        if not binary.is_file(): raise SystemExit("请先构建对应目标；未创建空安装包")
        selected.append(binary)
    payload = {}
    for p in selected:
        if not p.is_file() or p.is_symlink(): raise SystemExit("missing or symbolic package file: " + str(p))
        if p.name in FORBIDDEN or p.suffix in {".sqlite3", ".log", ".pem", ".key"}: raise SystemExit("private file excluded: " + str(p))
        name = str(p.relative_to(ROOT))
        if args.target != "source" and p == binary: name = "bin/xhs-mcp-custom"
        payload[name] = p.read_bytes()
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    payload["BUILD-INFO.json"] = (json.dumps({"version": version, "source_commit": commit, "target": args.target,
        "artifact_kind": "source" if args.target == "source" else "runtime", "go": "1.27.2",
        "source_archive": "xiaohongshu-operations-" + version + "-source.tar.gz",
        "local_candidate": True, "cloud_verified": False}, indent=2) + "\n").encode()
    manifest = "".join(hashlib.sha256(data).hexdigest() + "  " + name + "\n" for name, data in sorted(payload.items()))
    payload["MANIFEST.sha256"] = manifest.encode()
    prefix = "xiaohongshu-operations-" + version
    outdir = ROOT / "dist"; outdir.mkdir(exist_ok=True)
    out = outdir / (prefix + "-" + args.target + ".tar.gz")
    with tarfile.open(out, "w:gz") as tar:
        for name, data in sorted(payload.items()):
            info = tarfile.TarInfo(prefix + "/" + name); info.size = len(data); info.mode = 0o755 if name.startswith("bin/") else 0o644
            info.uid = info.gid = 0; info.mtime = 0; tar.addfile(info, io.BytesIO(data))
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    out.with_suffix(out.suffix + ".sha256").write_text(digest + "  " + out.name + "\n")
    # 反读全部文件核对清单和禁入项。
    with tarfile.open(out) as tar:
        for member in tar.getmembers():
            name = member.name[len(prefix)+1:]
            if name not in payload or member.issym() or member.islnk(): raise SystemExit("unexpected archive entry")
            if tar.extractfile(member).read() != payload[name]: raise SystemExit("archive mismatch")
    print(json.dumps({"archive": str(out), "sha256": digest, "files": len(payload), "source_commit": commit, "credentials": "excluded_by_allowlist", "cloud_verified": False}, indent=2))


if __name__ == "__main__": main()
