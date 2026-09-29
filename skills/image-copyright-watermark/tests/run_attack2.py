# -*- coding: utf-8 -*-
"""补充攻击测试：缩放后放回原尺寸、裁剪20%"""
import json
import os
import sys
from PIL import Image

DIR = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.abspath(os.path.join(DIR, "..", "scripts", "watermark_tool.py"))
PY = sys.executable
WM = "©十七° shiqidu@2026"
emb = os.path.join(DIR, "test_ori-watermarked.png")

# 攻击D：缩小50%再放大回原尺寸（压图床再下载的典型路径）
im = Image.open(emb)
s = im.resize((im.width // 2, im.height // 2), Image.LANCZOS)
s = s.resize((im.width, im.height), Image.LANCZOS)
s.save(os.path.join(DIR, "attack_rescale.png"))

# 攻击E：裁掉20%（顶部160px + 右侧240px）
Image.open(emb).crop((0, 160, 960, 800)).save(os.path.join(DIR, "attack_crop20.png"))

for fname, label in [("attack_rescale.png", "缩放后放回原图尺寸"), ("attack_crop20.png", "裁剪20%")]:
    path = os.path.join(DIR, fname)
    r = os.popen(f'"{PY}" "{TOOL}" verify --input "{path}" --password 7 --text "{WM}" --wm-bits 184 2>nul').read()
    idx = r.find('{"ok"')
    if idx < 0:
        print(label, "-> 解析失败:", r[:200])
        continue
    d, _ = json.JSONDecoder().raw_decode(r[idx:])
    print(label, "->", repr(d.get("wm_extracted")), "| match:", d.get("wm_match_ratio"), "| verdict:", d.get("verdict"))
