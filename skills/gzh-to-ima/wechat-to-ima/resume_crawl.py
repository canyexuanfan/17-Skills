# -*- coding: utf-8 -*-
"""
续抓并交付「仅新增」文章文档
==========================
用于频控恢复后，从断点继续抓取公众号历史文章，并把【本次新出现、
且之前没发过的】文章整理成一份 Markdown 文档交付。

用法:
    # 从断点续抓（需要先手动设过 resume_state.json 或跑过一次 full fetch）
    python resume_crawl.py --biz "公众号__biz" --name "公众号名"

    # 从指定 offset 开始
    python resume_crawl.py --biz "公众号__biz" --name "公众号名" --seed-offset 50

    # 指定 master JSON（去重基准）和已交付记录路径
    python resume_crawl.py --biz "公众号__biz" --name "公众号名" \\
        --master "./master.json" --delivered "./delivered.json"

    # 续抓后自动导入 IMA
    python resume_crawl.py --biz "公众号__biz" --name "公众号名" \\
        --import-ima --kb-id "知识库ID"

    # 续抓 + 导入 IMA + 导入到指定文件夹
    python resume_crawl.py --biz "公众号__biz" --name "公众号名" \\
        --import-ima --kb-id "知识库ID" --folder-id "文件夹ID"

设计要点:
- 断点续抓: 读取 resume_state.json 的 offset，从断点继续
- 去重: 以 master JSON 为去重基准，避免重复入库
- 增量交付: 与 delivered_links.json 对比，只输出从未发过的文章
- 幂等: 多次运行安全（不会重复发同一篇）
- 自动导入: 支持 --import-ima 续抓完成后自动导入 IMA 知识库
"""
import os
import sys
import re
import json
import argparse
import datetime
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_articles import crawl_all, DEFAULT_TOKEN, DEFAULT_COOKIE, log, UA

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(SKILL_DIR, "resume_state.json")
DELIVERED_FILE = os.path.join(SKILL_DIR, "delivered_links.json")
MASTER_DEFAULT = os.path.join(SKILL_DIR, "master_articles.json")


def load_master(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return []


def load_delivered(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_master(arts, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(arts, f, ensure_ascii=False, indent=2)


def save_delivered(s, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sorted(s), f, ensure_ascii=False, indent=2)


def save_state(offset, biz):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "offset": offset,
            "biz": biz,
            "updated": datetime.datetime.now().isoformat(),
        }, f, ensure_ascii=False, indent=2)


def fmt_date(ts):
    try:
        return datetime.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
    except Exception:
        return str(ts)


def gen_doc(new_arts, name, biz):
    """生成仅含新增文章的文档，返回路径；无新增返回 None。"""
    if not new_arts:
        return None
    arts_sorted = sorted(new_arts, key=lambda a: int(a.get("date", "0") or 0), reverse=True)
    date_str = datetime.datetime.now().strftime("%Y%m%d")
    safe_name = re.sub(r'[\\/:*?"<>|]', "_", name) if name else "公众号"
    out = os.path.join(SKILL_DIR, f"{safe_name}公众号_新增文章链接_{date_str}.md")

    lines = []
    lines.append(f"# {name}公众号 · 新增文章链接清单（续抓）")
    lines.append("")
    lines.append(f"> 公众号 biz：`{biz}`")
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


def find_ima_api_cjs():
    """自动查找 ima_api.cjs 路径"""
    for candidate in [
        os.path.join(SKILL_DIR, "..", "ima-skill", "ima_api.cjs"),
        os.path.join(SKILL_DIR, "..", "..", "ima-skill", "ima_api.cjs"),
        os.path.expanduser("~/.hermes/skills/ima-skill/ima_api.cjs"),
        os.path.expanduser("~/.workbuddy/skills/ima-skill/ima_api.cjs"),
    ]:
        candidate = os.path.abspath(candidate)
        if os.path.exists(candidate):
            return candidate
    return None


def call_ima_api(api_cjs, api_path, body_dict):
    """调用 ima_api.cjs，返回解析后的 JSON"""
    import subprocess as _sp
    body_str = json.dumps(body_dict, ensure_ascii=False)
    cmd = ["node", api_cjs, api_path, body_str]
    result = _sp.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        log(f"⚠️ IMA 调用失败: {result.stderr.strip()[:200]}")
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def import_to_ima(biz, name, articles, kb_id, folder_id=""):
    """把新增文章导入 IMA 知识库"""
    api_cjs = find_ima_api_cjs()
    if not api_cjs:
        log("❌ 找不到 ima_api.cjs，请确认 ima-skill 已安装")
        return 0, 0

    if not articles:
        return 0, 0

    total = len(articles)
    success = 0
    fail = 0

    for i in range(0, total, 10):
        batch = articles[i:i + 10]
        urls = [a["link"] for a in batch if a.get("link")]
        if not urls:
            continue

        body = {"knowledge_base_id": kb_id, "urls": urls}
        if folder_id:
            body["folder_id"] = folder_id

        resp = call_ima_api(api_cjs, "openapi/wiki/v1/import_urls", body)
        batch_end = min(i + 10, total)

        if resp is None:
            fail += len(urls)
            log(f"❌ 第 {i + 1}-{batch_end} 篇: 导入失败")
        elif resp.get("code") == 0 or resp.get("ret") == 0:
            success += len(urls)
            log(f"✅ 第 {i + 1}-{batch_end} 篇: +{len(urls)} 条（累计 {success}/{total}）")
        else:
            fail += len(urls)
            log(f"⚠️ 第 {i + 1}-{batch_end} 篇: 返回异常 {json.dumps(resp)[:80]}")

        if i + 10 < total:
            import time as _t
            _t.sleep(0.5)

    return success, fail


def main():
    p = argparse.ArgumentParser(description="断点续抓微信公众号文章 + 增量交付")
    p.add_argument("--biz", default=None, help="公众号 __biz（base64）")
    p.add_argument("--name", default="公众号", help="公众号名称（用于输出文件命名）")
    p.add_argument("--master", default=None, help="全量文章去重基准 JSON 路径")
    p.add_argument("--delivered", default=None, help="已交付链接记录 JSON 路径")
    p.add_argument("--seed-offset", type=int, default=None, help="手动指定起始 offset")
    p.add_argument("--max-batch", type=int, default=80, help="最大抓取批次数")
    p.add_argument("--retry", type=int, default=4, help="频控自动重试次数 (默认 4)")
    p.add_argument("--retry-wait", type=int, default=900, help="频控重试前等待秒数 (默认 900)")
    # IMA 自动导入
    p.add_argument("--import-ima", action="store_true", help="续抓完成后自动导入 IMA 知识库")
    p.add_argument("--kb-id", default=None, help="IMA 知识库 ID（配合 --import-ima）")
    p.add_argument("--folder-id", default="", help="IMA 文件夹 ID（配合 --import-ima）")
    args = p.parse_args()

    token = DEFAULT_TOKEN
    cookie = DEFAULT_COOKIE
    if not token or cookie in (None, "", "PASTE_COOKIE_HERE"):
        log("❌ 缺少 token/cookie，请检查 weixin_credentials.py")
        sys.exit(1)

    master_path = args.master or MASTER_DEFAULT
    delivered_path = args.delivered or DELIVERED_FILE

    # 1) 读取断点
    start_offset = 0
    biz = args.biz
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            st = json.load(f)
        start_offset = st.get("offset", 0)
        biz = biz or st.get("biz")
        log(f"📌 从断点 offset={start_offset} 续抓 (biz={biz})")
    else:
        if not biz:
            log("⚠️ 无断点状态文件，请用 --biz 指定公众号 __biz")
            sys.exit(1)
        log("⚠️ 无断点状态，从 offset=0 开始")

    if args.seed_offset is not None:
        start_offset = args.seed_offset
        log(f"↪️ 手动指定起始 offset={start_offset}")

    # 2) 去重基准 = master
    master = load_master(master_path)
    seed_seen = set(a.get("link", "") for a in master)
    log(f"🧹 已载入 {len(seed_seen)} 条历史链接作为去重基准")

    # 3) 续抓
    new_arts, end_offset = crawl_all(
        biz, token, cookie, max_batch=args.max_batch, count=5, sleep_sec=3,
        start_offset=start_offset, seed_seen=seed_seen,
        retry=args.retry, retry_wait=args.retry_wait,
        on_progress=lambda off, c, s: save_state(off, biz),
    )
    save_state(end_offset, biz)

    if new_arts:
        master.extend(new_arts)
        save_master(master, master_path)
        log(f"✅ 本次新增 {len(new_arts)} 篇，master 总计 {len(master)} 篇")
    else:
        log("ℹ️ 本次无新增（可能仍未恢复，或已抓到末尾）")

    # 4) 与已交付清单对比，只交付从未发过的
    delivered = load_delivered(delivered_path)
    truly_new = [a for a in new_arts if a["link"] not in delivered]
    out = gen_doc(truly_new, args.name, biz)

    if out:
        delivered.update(a["link"] for a in truly_new)
        save_delivered(delivered, delivered_path)
        log(f"📄 新增文档已生成: {out}  (含 {len(truly_new)} 篇)")
        print("DELIVER_DOC:" + out)
    else:
        log("ℹ️ 没有需要新交付的文章")

    # 5) 自动导入 IMA
    if args.import_ima:
        if not args.kb_id:
            log("❌ --import-ima 需要同时指定 --kb-id")
        elif not truly_new:
            log("ℹ️ 无新增文章，跳过 IMA 导入")
        else:
            log(f"📥 开始导入 {len(truly_new)} 篇新增文章到 IMA 知识库...")
            ok, fail = import_to_ima(biz, args.name, truly_new, args.kb_id, args.folder_id)
            log(f"📊 IMA 导入完成: 成功 {ok} 篇 / 失败 {fail} 篇")


if __name__ == "__main__":
    main()
