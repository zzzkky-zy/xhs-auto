#!/usr/bin/env python3
"""Codex stdio 入口：复用私有配置，由官方 Go SDK 转接本机服务。"""
import argparse
import hashlib
import os
from pathlib import Path
import sys

from xhs_ops import Config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(os.getenv("XHS_OPS_CONFIG", str(Path(os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "xhs-operations/xhs.local.json"))))
    args = parser.parse_args()
    config = Config(args.config)
    binary = config.resolve(config.data["mcp_binary"])
    env = os.environ.copy()
    env.update(config.runtime_env())
    env.update({"AUTH_TOKEN": config.token, "XHS_OPS_CONFIG": str(config.path), "XHS_OPS_SCRIPT": str(Path(__file__).resolve().with_name("xhs_ops.py")), "XHS_OPS_PYTHON": sys.executable})
    env.update({"XHS_ACCOUNT_ID": config.account, "XHS_EXPECTED_STATE_ID": config.state_id, "XHS_ALLOW_WRITE": "1" if config.allow_write else "0", "XHS_MCP_URL": config.url})
    env.update({"XHS_CONFIG_FINGERPRINT": config.fingerprint(), "XHS_CONFIG_FILE_SHA256": hashlib.sha256(config.path.read_bytes()).hexdigest()})
    os.execve(str(binary), [str(binary), "-stdio-proxy", "-upstream", config.url], env)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("ERR_MCP_BOOTSTRAP: " + str(error), file=sys.stderr)
        raise SystemExit(1)
