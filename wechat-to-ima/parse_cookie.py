# -*- coding: utf-8 -*-
# 把浏览器 F12 → Cookies → Copy All 的制表符分隔文本，转成 Cookie 请求头字符串
# 用法：python parse_cookie.py < cookies_dump.txt
import sys

raw = sys.stdin.read()
lines = [ln for ln in raw.splitlines() if ln.strip()]

pairs = []
for ln in lines:
    # 以制表符分割；兼容多个空格/制表混合
    parts = ln.split("\t")
    # 过滤掉空字段（Copy All 可能用多个制表符）
    parts = [p for p in parts if p != ""]
    if len(parts) < 2:
        continue
    name, value = parts[0], parts[1]
    # 跳过表头（如果有）
    if name.lower() in ("name",) and value.lower() in ("value",):
        continue
    pairs.append(f"{name}={value}")

cookie_header = "; ".join(pairs)
print(cookie_header)
