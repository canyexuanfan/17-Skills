# -*- coding: utf-8 -*-
# 微信公众号 API 凭证（示例模板）
# ============================================================
# 复制本文件为 weixin_credentials.py 并填入你自己的凭证：
#   cp weixin_credentials.example.py weixin_credentials.py
#
# token: 来自浏览器地址栏
#   https://mp.weixin.qq.com/cgi-bin/home?t=home/index&lang=zh_CN&token=XXXXXX
#   取 token= 后面的数字
# cookie: 来自浏览器 F12 → Application → Cookies → mp.weixin.qq.com → 右键 Copy All
#   复制后可用本目录的 parse_cookie.py 转成单行 Cookie 请求头字符串：
#     python parse_cookie.py < cookies_dump.txt
# ============================================================

token = ""   # ← 填你的微信 token
cookie = ""  # ← 填你的微信 Cookie（单行字符串；留空则脚本会提示先用 parse_cookie.py 处理）
