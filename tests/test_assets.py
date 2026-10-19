import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("asset_check",ROOT/"skills/xiaohongshu-operations/scripts/check_assets.py")
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

def png(raw,width=1080,height=1440):
    def chunk(kind,data):return struct.pack(">I",len(data))+kind+data+struct.pack(">I",zlib.crc32(kind+data)&0xffffffff)
    return b"\x89PNG\r\n\x1a\n"+chunk(b"IHDR",struct.pack(">IIBBBBB",width,height,8,2,0,0,0))+chunk(b"IDAT",zlib.compress(raw))+chunk(b"IEND",b"")

class AssetTests(unittest.TestCase):
    def test_complete_png_and_truncated_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            p=Path(temporary)/"fixture.png";p.write_bytes(png((b"\x00"+b"\x00"*(1080*3))*1440))
            self.assertEqual(module.check_png(p),(1080,1440))
            p.write_bytes(png(b"\x00"))
            with self.assertRaises(ValueError):module.check_png(p)

    def test_crc_damage(self):
        with tempfile.TemporaryDirectory() as temporary:
            p=Path(temporary)/"fixture.png";raw=bytearray(png(b"\x00\x00\x00\x00",1,1));raw[-1]^=1;p.write_bytes(raw)
            with self.assertRaises(ValueError):module.check_png(p)

    def test_brand_assets_are_valid_pixels(self):
        for p in (ROOT/"skills/xiaohongshu-operations/assets/brand").glob("*.png"):
            width,height=module.check_png(p);self.assertGreater(width,0);self.assertGreater(height,0)
