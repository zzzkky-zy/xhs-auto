#!/usr/bin/env python3
"""PNG 完整性/尺寸与品牌来源检查；不证明文案/视觉质量。"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import zlib


def check_png(path):
    if path.stat().st_size > 32*1024*1024: raise ValueError("PNG too large")
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(str(path.name) + ": not PNG")
    pos, size, end, depth, color, palette = 8, None, False, None, None, False
    compressed = bytearray()
    while pos < len(data):
        if pos + 12 > len(data): raise ValueError("truncated PNG chunk")
        length = struct.unpack(">I", data[pos:pos+4])[0]
        kind = data[pos+4:pos+8]; content = data[pos+8:pos+8+length]
        if len(content) != length or pos+12+length > len(data): raise ValueError("truncated PNG")
        crc = struct.unpack(">I", data[pos+8+length:pos+12+length])[0]
        if zlib.crc32(kind+content) & 0xffffffff != crc: raise ValueError("PNG CRC mismatch")
        if kind == b"IHDR":
            if len(content) != 13: raise ValueError("invalid IHDR")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", content)
            size = (width, height)
            if not width or not height or compression or filtering or interlace: raise ValueError("invalid/unsupported PNG geometry or interlace")
            if color not in {0,2,3,4,6} or depth not in ({1,2,4,8,16} if color==0 else {1,2,4,8} if color==3 else {8,16}): raise ValueError("invalid PNG pixel format")
        if kind == b"PLTE":
            if len(content) % 3 or not 0 < len(content) <= 768: raise ValueError("invalid PNG palette")
            palette = True
        if kind == b"IDAT": compressed.extend(content)
        pos += 12+length
        if kind == b"IEND": end = True; break
    if not end or pos != len(data) or size is None: raise ValueError("PNG incomplete")
    decoder = zlib.decompressobj(); raw = decoder.decompress(compressed, 64*1024*1024)
    if not decoder.eof or decoder.unconsumed_tail: raise ValueError("PNG image data invalid or too large")
    if not raw: raise ValueError("PNG empty image data")
    if color == 3 and not palette: raise ValueError("PNG palette missing")
    row = (size[0]*{0:1,2:3,3:1,4:2,6:4}[color]*depth+7)//8+1
    if len(raw) != row*size[1] or any(raw[y*row] > 4 for y in range(size[1])): raise ValueError("PNG pixel rows incomplete")
    return size


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("directory"); args = parser.parse_args()
    root = Path(args.directory); files = sorted(root.rglob("*.png"))
    if not files: raise SystemExit("没有 PNG；图片未完成，不可发布")
    for p in files:
        size = check_png(p)
        if size != (1080, 1440): raise SystemExit(p.name + ": expected 1080x1440, got " + str(size))
    brand = Path(__file__).resolve().parents[1] / "assets/brand"
    manifest = json.loads((brand / "manifest.json").read_text())
    for item in manifest["assets"]:
        if hashlib.sha256((brand / item["file"]).read_bytes()).hexdigest() != item["sha256"]: raise SystemExit("品牌母版校验失败")
    print(json.dumps({"png_files": len(files), "format": "passed", "brand_source": "passed", "visual_quality": "requires_actual_review"}))


if __name__ == "__main__": main()
