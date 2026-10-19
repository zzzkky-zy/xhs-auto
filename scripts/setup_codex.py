#!/usr/bin/env python3
"""校验完整安装包并用 Codex CLI 安装本地插件；不登录、不创建调度。"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile


def unpack_verified(archive_path, destination, expected_sha256):
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ValueError("need expected archive SHA256")
    if hashlib.sha256(archive_path.read_bytes()).hexdigest() != expected_sha256:
        raise ValueError("archive SHA256 mismatch")
    with tarfile.open(archive_path) as archive:
        members = archive.getmembers()
        names = set()
        for member in members:
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts or not member.isfile() or member.name in names:
                raise ValueError("unsafe or duplicate archive entry")
            names.add(member.name)
        if len({PurePosixPath(name).parts[0] for name in names}) != 1:
            raise ValueError("archive requires one root")
        archive.extractall(destination)
    root = next(destination.iterdir())
    entries = {}
    for line in (root / "MANIFEST.sha256").read_text().splitlines():
        expected, name = line.split("  ", 1)
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or name in entries or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("unsafe manifest entry")
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
            raise ValueError("manifest mismatch: " + name)
        entries[name] = expected
    actual = {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()}
    if actual != set(entries) | {"MANIFEST.sha256"}:
        raise ValueError("manifest does not cover archive")
    return root, entries


def target_name():
    system = {"Darwin": "darwin", "Linux": "linux"}.get(platform.system())
    machine = {"arm64": "arm64", "aarch64": "arm64", "amd64": "amd64", "x86_64": "amd64"}.get(platform.machine().lower())
    return "%s-%s" % (system, machine)


def register(plugin_root, catalog_root, codex):
    catalog_file = catalog_root / ".agents/plugins/marketplace.json"
    catalog_file.parent.mkdir(parents=True, exist_ok=True)
    relative = "./" + str(plugin_root.relative_to(catalog_root))
    entry = {"name": "xiaohongshu-operations", "source": {"source": "local", "path": relative}, "policy": {"installation": "AVAILABLE", "authentication": "ON_USE"}, "category": "Productivity"}
    catalog = {"name": "xhs-operations-local", "interface": {"displayName": "小红书运营（本地）"}, "plugins": []}
    if catalog_file.exists():
        catalog = json.loads(catalog_file.read_text())
        if catalog.get("name") != "xhs-operations-local":
            raise ValueError("catalog belongs to another marketplace; choose a separate root")
    catalog["plugins"] = [item for item in catalog.get("plugins", []) if item.get("name") != entry["name"]] + [entry]
    catalog_file.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n")
    for args in [["plugin", "marketplace", "add", str(catalog_root), "--json"], ["plugin", "add", "xiaohongshu-operations@xhs-operations-local", "--json"]]:
        process = subprocess.run([codex] + args, text=True, capture_output=True, timeout=60)
        if process.returncode:
            raise RuntimeError("Codex registration failed: " + (process.stderr or process.stdout)[-1200:])
        print(process.stdout.strip())


def configure_binary(plugin_root, path):
    """只更新已核对私有配置的程序路径，保留账号、队列与所有开关。"""
    spec = importlib.util.spec_from_file_location("xhs_setup_runtime", plugin_root / "runtime/xhs_ops.py")
    ops = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ops)
    config = ops.Config(path)
    store = ops.Store(config)
    try: store.verify_state()
    finally: store.db.close()
    if ops.managed_status(config)["status"] == "managed":
        raise ValueError("existing backend running; stop it with its existing runtime before upgrading")
    config.data["mcp_binary"] = str(plugin_root / "bin/xhs-mcp-custom")
    ops.atomic_file(config.path, (json.dumps(config.data, ensure_ascii=False, indent=2) + "\n").encode())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--sha256", help="从可信交付报告取得；默认读同名 .sha256 文件")
    parser.add_argument("--catalog-root", type=Path, default=Path(os.getenv("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "xhs-operations/marketplace")
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--config", type=Path, help="升级已有私有配置的程序路径；不改变账号、状态或写入开关")
    args = parser.parse_args()
    digest = args.sha256 or Path(str(args.archive) + ".sha256").read_text().split()[0]
    catalog_root = args.catalog_root.expanduser().resolve()
    with tempfile.TemporaryDirectory(prefix="xhs-codex-unpack-") as temp:
        source, entries = unpack_verified(args.archive.resolve(), Path(temp), digest)
        info = json.loads((source / "BUILD-INFO.json").read_text())
        if info["target"] != target_name() or "bin/xhs-mcp-custom" not in entries:
            raise ValueError("use the native runtime package for this computer")
        plugin_root = catalog_root / "plugins/xiaohongshu-operations" / info["version"]
        if plugin_root.exists():
            old = (plugin_root / "MANIFEST.sha256").read_bytes()
            if old != (source / "MANIFEST.sha256").read_bytes():
                raise ValueError("same version differs; keep old copy and choose a new catalog root")
            existing = list(plugin_root.rglob("*"))
            if any(path.is_symlink() for path in existing) or {str(path.relative_to(plugin_root)) for path in existing if path.is_file()} != set(entries) | {"MANIFEST.sha256"}:
                raise ValueError("installed source contains extra files; keep private state outside the plugin")
            for name, expected in entries.items():
                if hashlib.sha256((plugin_root / name).read_bytes()).hexdigest() != expected:
                    raise ValueError("installed source changed; preserve it before reinstall")
        else:
            plugin_root.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, plugin_root)
    if args.config:
        configure_binary(plugin_root, args.config)
    register(plugin_root, catalog_root, args.codex)
    print(json.dumps({"version": info["version"], "source_commit": info["source_commit"], "plugin_source": str(plugin_root), "marketplace": "xhs-operations-local", "manifest_files_verified": len(entries), "service_started": False, "browser_started": False, "write_setting_changed": False, "next": "按 docs/CODEX-LOCAL.md 创建或复用私有配置，先做 health/status。新会话加载插件；真实账号与写入另行验收。"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
