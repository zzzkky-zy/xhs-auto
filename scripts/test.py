#!/usr/bin/env python3
"""保存实际测试摘要，不输出全部测试日志或将跳过项算作通过。"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from browser_fixture import select_browser

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--fixture-chrome", help="独立测试浏览器；macOS 禁止使用日常 Google Chrome");args=parser.parse_args()
    try: fixture_chrome=select_browser(args.fixture_chrome)
    except (OSError, ValueError) as exc: raise SystemExit(str(exc))
    executable=os.getenv("XHS_GO_BINARY") or str(ROOT/".tools/go/bin/go")
    if not (ROOT/"bin/xhs-mcp-custom").exists(): raise SystemExit("先 scripts/build.py 构建本机 MCP，不能跳过实际 MCP 端到端验收")
    started=dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat();records=[]
    env=os.environ.copy();env.update({"GOPROXY":"off","GOTOOLCHAIN":"local"})
    commands=[("go-race",[executable,"test","-race","-mod=vendor","-count=1","-json","./..."],ROOT/"mcp/xhs-mcp-custom"),
              ("python-e2e",[sys.executable,"-m","unittest","discover","-s","tests","-v"],ROOT)]
    if fixture_chrome:
        env["XHS_TEST_CHROME"]=fixture_chrome
        commands.append(("browser-fixture",[executable,"test","-race","-mod=vendor","-tags","fixture","-run","TestFixture","-count=1","-json",".","./xiaohongshu","./browser"],ROOT/"mcp/xhs-mcp-custom"))
    for name,command,cwd in commands:
        start=time.monotonic();process=subprocess.run(command,cwd=cwd,env=env,text=True,capture_output=True,timeout=180)
        record={"name":name,"exit_code":process.returncode,"seconds":round(time.monotonic()-start,3)}
        if name.startswith("go") or name=="browser-fixture":
            lines=[json.loads(line) for line in process.stdout.splitlines() if line.startswith("{")]
            record["root_tests_passed"]=sum(x.get("Action")=="pass" and "Test" in x and "/" not in x["Test"] for x in lines)
            record["subtests_passed"]=sum(x.get("Action")=="pass" and "Test" in x and "/" in x["Test"] for x in lines)
            record["packages_passed"]=sum(x.get("Action")=="pass" and "Test" not in x for x in lines)
            record["skipped_tests"]=[x["Test"] for x in lines if x.get("Action")=="skip" and "Test" in x]
            record["failed_tests"]=[x["Test"] for x in lines if x.get("Action")=="fail" and "Test" in x]
        else:
            import re
            match=re.search(r"Ran (\d+) tests in",process.stderr);record["tests_run"]=int(match[1]) if match else None
            record["skipped"]=len(re.findall(r"\.\.\. skipped",process.stderr))
        records.append(record);print(json.dumps(record))
        if process.returncode:
            if record.get("failed_tests"):
                failures="".join(x.get("Output", "") for x in lines if x.get("Action")=="output" and x.get("Test") in record["failed_tests"])
                print(failures[-5000:])
            else: print((process.stderr+process.stdout)[-5000:])
            break
    if not fixture_chrome:records.append({"name":"browser-fixture","status":"not_run","reason":"Separate fixture browser not supplied"})
    report={"started_at":started,"ended_at":dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(),"version":(ROOT/"VERSION").read_text().strip(),"fixture_only":True,"records":records,"real_xhs_operations":0,"cloud_verified":False}
    (ROOT/"工作资料/engineering-results.json").write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n")
    return 1 if any(r.get("exit_code",0) for r in records) else 0

if __name__=="__main__":raise SystemExit(main())
