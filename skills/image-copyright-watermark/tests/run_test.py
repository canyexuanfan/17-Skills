# -*- coding: utf-8 -*-
"""端到端实测：嵌入 → 三种攻击 → 提取验证 + EXIF 验证"""
import json
import logging
import os
import sys


def parse_json_output(text):
    """从工具输出中解析第一个完整 JSON 对象（用 raw_decode 忽略尾部杂讯）。"""
    idx = text.find('{"ok"')
    obj, _ = json.JSONDecoder().raw_decode(text[idx:])
    return obj

for n in ("matplotlib", "blind_watermark"):
    logging.getLogger(n).setLevel(logging.ERROR)
logging.disable(logging.WARNING)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "image-copyright-watermark", "scripts"))
# 直接以子命令方式调用 watermark_tool 更贴近真实使用；本脚本只负责造测试图和攻击模拟
from PIL import Image, ImageDraw, ImageFilter

DIR = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.abspath(os.path.join(DIR, "..", "..", "image-copyright-watermark", "scripts", "watermark_tool.py"))

# 1) 造一张有纹理的测试图（纯色图频域特征弱，不能代表真实照片）
img = Image.new("RGB", (1200, 800))
d = ImageDraw.Draw(img)
for x in range(0, 1200, 8):          # 彩条纹理
    d.line([(x, 0), (x - 400, 800)], fill=(x % 256, (x * 2) % 256, 120), width=3)
d.ellipse([450, 250, 750, 550], fill=(255, 200, 60))
d.text((520, 380), "TEST IMAGE 2026", fill=(40, 40, 40))
img = img.filter(ImageFilter.GaussianBlur(0.5))
ori = os.path.join(DIR, "test_ori.png")
img.save(ori)
print("test image:", ori)

WM = "©十七° shiqidu@2026"

# 2) embed
r = os.popen(f'"{sys.executable}" "{TOOL}" embed --input "{ori}" --text "{WM}" --password 7').read()
print("EMBED:", r.strip()[-300:])
emb = parse_json_output(r)
emb_path = emb["output"]
wm_bits = emb["wm_bits"]

# 3) 攻击模拟
from blind_watermark import WaterMark

# 攻击A：JPEG q85 重压缩（模拟平台二压）
jpg85 = os.path.join(DIR, "attack_q85.jpg")
Image.open(emb_path).convert("RGB").save(jpg85, quality=85)

# 攻击B：缩放 50%（模拟裁剪压缩场景）
half = os.path.join(DIR, "attack_half.png")
im = Image.open(emb_path)
im.resize((im.width // 2, im.height // 2), Image.LANCZOS).save(half)

# 攻击C：EXIF 读取验证（图片被 Pillow 重存后属性是否保留）
from PIL import Image as I2
meta_jpg = {}
_e = I2.open(jpg85).getexif()
meta_jpg = {hex(k): str(v) for k, v in _e.items()}

# 4) verify 全部（攻击图属性被剥离 → 按真实使用场景，持有人提供凭证 --wm-bits）
def run_verify(path, label, bits=None):
    bits_arg = f" --wm-bits {bits}" if bits else ""
    r = os.popen(f'"{sys.executable}" "{TOOL}" verify --input "{path}" --password 7 --text "{WM}"{bits_arg} 2>nul').read()
    d = parse_json_output(r)
    print(f"VERIFY[{label}]:", json.dumps({k: d.get(k) for k in ("wm_bits_used", "wm_extracted", "wm_match_ratio", "verdict")}, ensure_ascii=False))
    return d

run_verify(emb_path, "无损PNG原产物")
run_verify(jpg85, "JPEG q85 重压缩(带凭证)", wm_bits)
run_verify(half, "缩放50%(带凭证)", wm_bits)
run_verify(jpg85, "属性被剥离且无凭证(应跳过提取并提示)")

print("EXIF after q85 resave:", json.dumps(meta_jpg, ensure_ascii=False))
print("wm_bits =", wm_bits)
