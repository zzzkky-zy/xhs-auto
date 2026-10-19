import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ops_blackbox", ROOT / "runtime/xhs_ops.py")
ops = importlib.util.module_from_spec(spec); spec.loader.exec_module(ops)


@unittest.skipUnless((ROOT / "bin/xhs-mcp-custom").exists(), "build native MCP binary first")
class MCPBlackboxTests(unittest.TestCase):
    def test_initialize_tools_write_gate_and_process_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
            config_path = root / "config.json"
            config_path.write_text(json.dumps({"schema_version":1,"account_id":"fixture-blackbox","state_dir":"state","output_dir":"out","mcp_binary":str(ROOT/"bin/xhs-mcp-custom"),"mcp_url":"http://127.0.0.1:%d/mcp"%port,"listen":"127.0.0.1:%d"%port,"cdp_url":"http://127.0.0.1:1","allow_write":False,"timeout_seconds":3}))
            ops.prepare_state(config_path); config = ops.Config(config_path)
            first_deadline = None
            for attempt in range(2):
                ops.service_command(config, "start")
                try:
                    deadline = time.monotonic()+8
                    while True:
                        try:
                            with urllib.request.urlopen("http://127.0.0.1:%d/health"%port,timeout=1) as r: health=json.load(r)
                            break
                        except Exception:
                            if time.monotonic()>deadline: raise
                            time.sleep(0.1)
                    self.assertEqual(health["data"]["version"], (ROOT / "VERSION").read_text().strip())
                    transport = ops.MCPTransport(config); transport.connect()
                    names = {t["name"] for t in transport.request("tools/list")["tools"]}
                    self.assertTrue({"get_operation_status","resolve_operation","post_comment_to_feed","publish_content"} <= names)
                    with self.assertRaises(ops.ToolFailure) as unavailable:
                        transport.call("check_login_status", {})
                    self.assertEqual(unavailable.exception.code, "ERR_BROWSER_UNAVAILABLE" if attempt == 0 else "ERR_RATE_LIMIT")
                    paced = json.loads((config.state_dir / "runtime-state.json").read_text())
                    if first_deadline is None:
                        first_deadline = paced["next_action_at"]
                    self.assertEqual(paced["next_action_at"], first_deadline)
                    self.assertEqual(len(paced["read_requests"]), 1)
                    for tool,args in [("post_comment_to_feed",{"feed_id":"fixture","xsec_token":"fixture","content":"fixture","operation_id":"fixture"}), ("publish_content",{"title":"fixture","content":"fixture","images":["/fixture.png"],"operation_id":"fixture-post"})]:
                        with self.assertRaises(ops.ToolFailure) as failure: transport.call(tool,args)
                        self.assertEqual(failure.exception.code,"ERR_WRITE_NOT_ALLOWED")
                    # 浏览器不存在也能证明写闸在任何网页动作之前拒绝。
                    self.assertFalse(ops.health(config)["cdp"] == "reachable")
                finally:
                    self.assertEqual(ops.service_command(config,"stop")["status"],"stopped")
            state = json.loads((config.state_dir / "runtime-state.json").read_text())
            self.assertEqual(state["state_id"], config.state_id); self.assertEqual(state["operations"],{})


if __name__ == "__main__": unittest.main()
