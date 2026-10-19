#!/usr/bin/env python3
"""仅安装本项目固定 Go 工具链；校验官方归档，不改全局 PATH。"""
import hashlib
import json
from pathlib import Path
import platform
import tarfile
import urllib.request

ROOT=Path(__file__).resolve().parents[1]

def main():
    key=("darwin" if platform.system()=="Darwin" else "linux")+"-"+{"arm64":"arm64","aarch64":"arm64","x86_64":"amd64"}[platform.machine()]
    entry=json.loads((ROOT/"toolchains.lock.json").read_text())["go_archives"][key]
    folder=ROOT/".tools";folder.mkdir(exist_ok=True)
    archive=folder/entry["file"]
    if not archive.exists():
        with urllib.request.urlopen("https://go.dev/dl/"+entry["file"],timeout=30) as response:
            with archive.open("wb") as output:
                while True:
                    block=response.read(1024*1024)
                    if not block:break
                    output.write(block)
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=entry["sha256"]:raise SystemExit("Go checksum mismatch；未安装，保留文件用于检查")
    if (folder/"go").exists():raise SystemExit("工具链目录已存在；不覆盖，先核对版本或明确移走旧版本")
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            if Path(member.name).is_absolute() or ".." in Path(member.name).parts or not (member.name == "go" or member.name.startswith("go/")) or member.issym() or member.islnk():raise SystemExit("unexpected toolchain archive entry")
        tar.extractall(folder)
    print(json.dumps({"go":"1.27.2","path":str(folder/"go/bin/go"),"global_configuration":"unchanged"}))

if __name__=="__main__":main()
