# -*- coding: utf-8 -*-
"""
续抓并交付「仅新增」文章文档
==========================
用于频控恢复后，从断点继续抓取公众号历史文章，并把【本次新出现、
且之前没发过的】文章整理成一份 Markdown 文档交付。

设计要点:
- 断点续抓: 读取 resume_state.json 的 offset，从断点继续，不重抓已抓过的
- 去重: 以 master JSON（全量文章列表）为去重基准，避免重复入库
- 增量交付: 与 delivered_links.json 对比，只输出从未发过的文章
- 幂等: 多次运行安全（不会重复发同一篇）

运行:
    python resume_crawl.py
"""
import os
import sys
import json
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_articles import crawl_all, DEFAULT_TOKEN, DEFAULT_COOKIE, log, UA

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(SKILL_DIR, "resume_state.json")
DELIVERED_FILE = os.path.join(SKILL_DIR, "delivered_links.json")
# ↓↓↓ 以下三项请按你的目标公众号修改（勿将真实值提交到公开仓库） ↓↓↓
MASTER = os.path.join(SKILL_DIR, "master_articles.json")  # 全量文章去重基准；首次运行可留空，脚本会自动创建
NAME = "示例公众号"       # 仅用于生成交付文档的标题
BIZ = "YOUR_BIZ_HERE"   # 公众号 __biz（base64）；可留空让脚本从文章 URL 自动提取
# ↑↑↑ 以上三项请按你的目标公众号修改 ↑↑↑


def load_master():
    if os.path.exists(MASTER):
        with open(MASTER, encoding="utf-8") as f:
            return json.load(f)
    return []


def load_delivered():
    if os.path.exists(DELIVERED_FILE):
        with open(DELIVERED_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_master(arts):
    with open(MASTER, "w", encoding="utf-8") as f:
        json.dump(arts, f, ensure_ascii=False, indent=2)


def save_delivered(s):
    with open(DELIVERED_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(s), f, ensure_ascii=False, indent=2)


def save_state(offset):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "offset": offset,
            "biz": BIZ,
            "updated": datetime.datetime.now().isoformat(),
        }, f, ensure_ascii=False, indent=2)


def fmt_date(ts):
    try:
        return datetime.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
    except Exception:
        return str(ts)


def gen_doc(new_arts):
    """生成仅含新增文章的文档，返回路径；无新增返回 None。"""
    if not new_arts:
        return None
    arts_sorted = sorted(new_arts, key=lambda a: int(a.get("date", "0") or 0), reverse=True)
    date_str = datetime.datetime.now().strftime("%Y%m%d")
    out = os.path.join(os.path.dirname(MASTER), f"{NAME}公众号_新增文章链接_{date_str}.md")

    lines = []
    lines.append(f"# {NAME}公众号 · 新增文章链接清单（续抓）")
    lines.append("")
    lines.append(f"> 公众号 biz：`{BIZ}`")
    lines.append(f"> 生成时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"> 本次新增：**{len(arts_sorted)} 篇**（仅含此前未发送过的文章）")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 新增文章列表（按发布时间倒序）")
    lines.append("")
    for i, a in enumerate(arts_sorted, 1):
        title = (a.get("title") or "").strip() or "(无标题)"
        link = a.get("link", "")
        d = fmt_date(a.get("date", ""))
        lines.append(f"{i}. **{title}**  _{d}_\n   {link}")

    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return out


def main():
    token = DEFAULT_TOKEN
    cookie = DEFAULT_COOKIE
    if not token or cookie in (None, "", "PASTE_COOKIE_HERE"):
        log("❌ 缺少 token/cookie，请检查 weixin_credentials.py")
        sys.exit(1)

    # 1) 读取断点
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            st = json.load(f)
        start_offset = st.get("offset", 0)
        log(f"📌 从断点 offset={start_offset} 续抓")
    else:
        start_offset = 0
        log("⚠️ 无断点状态，从 offset=0 开始")

    # 2) 去重基准 = master
    master = load_master()
    seed_seen = set(a.get("link", "") for a in master)
    log(f"🧹 已载入 {len(seed_seen)} 条历史链接作为去重基准")

    # 3) 续抓（频控自动退避重试，最多 4 次 × 15 分钟）
    new_arts, end_offset = crawl_all(
        BIZ, token, cookie, max_batch=80, count=5, sleep_sec=3,
        start_offset=start_offset, seed_seen=seed_seen,
        retry=4, retry_wait=900,
        on_progress=lambda off, c, s: save_state(off),
    )
    save_state(end_offset)

    if new_arts:
        master.extend(new_arts)
        save_master(master)
        log(f"✅ 本次新增 {len(new_arts)} 篇，master 总计 {len(master)} 篇")
    else:
        log("ℹ️ 本次无新增（可能仍未恢复，或已抓到末尾）")

    # 4) 与已交付清单对比，只交付从未发过的
    delivered = load_delivered()
    truly_new = [a for a in new_arts if a["link"] not in delivered]
    out = gen_doc(truly_new)

    if out:
        delivered.update(a["link"] for a in truly_new)
        save_delivered(delivered)
        log(f"📄 新增文档已生成: {out}  (含 {len(truly_new)} 篇)")
        # 特殊标记，便于自动化读取路径
        print("DELIVER_DOC:" + out)
    else:
        log("ℹ️ 没有需要新交付的文章")


if __name__ == "__main__":
    main()
