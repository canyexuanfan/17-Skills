"""`python3 -m xweb.xclid` —— 签名模块自检（排障用）。

不带参数：报告平台、原生模块路径、可用平台是否齐全。
加 --selftest：真跑一次签名（有 cookie 时），打印签名长度与形态。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import XClId, available, native_path, platform_tag


def _cookie_from_config():
    try:
        p = os.path.expanduser("~/.config/x-web/config.json")
        cfg = json.load(open(p))
        if cfg.get("auth_token") and cfg.get("ct0"):
            return "auth_token=%s; ct0=%s" % (cfg["auth_token"], cfg["ct0"])
    except Exception:
        pass
    return None


def main():
    ap = argparse.ArgumentParser(prog="python3 -m xweb.xclid")
    ap.add_argument("--selftest", action="store_true", help="真跑一次签名")
    ap.add_argument("--cookie", default=None, help="自定义 cookie 串")
    ap.add_argument("--method", default="GET")
    ap.add_argument("--path", default="/i/api/graphql/abc/SearchTimeline")
    a = ap.parse_args()

    print("平台      :", platform_tag(), "| Python", sys.version.split()[0])
    print("原生模块  :", native_path() or "❌ 缺失")
    print("随包平台  :", ", ".join(available()) or "无")
    ck = a.cookie or _cookie_from_config()
    print("cookie    :", "有" if ck else "无（访客态也能取签名素材，但可能取不全）")
    if not a.selftest:
        return 0
    try:
        x = XClId(cookie=ck, verbose=True).init()
        s = x.generate(a.method, a.path)
    except Exception as e:
        print("✗ 签名失败：%s" % e)
        return 1
    print("签名      :", s)
    print("长度      :", len(s))
    print("自检      :", "✅ 通过" if len(s) > 60 else "❌ 异常")
    return 0


if __name__ == "__main__":
    sys.exit(main())
