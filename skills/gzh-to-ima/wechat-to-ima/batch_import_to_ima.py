# -*- coding: utf-8 -*-
"""
批量导入文章到 IMA 知识库
========================
读取 fetch_articles.py 输出的 JSON，每批 10 条调用 ima_api.cjs 的 import_urls 导入到 IMA 知识库。

用法:
    # 先查知识库和文件夹 ID
    python batch_import_to_ima.py --search "知识库名称"

    # 指定 knowledge_base_id 导入（导入到根目录）
    python batch_import_to_ima.py --import-json "公众号_全部文章_20260719.json" \\
        --kb-id "知识库ID"

    # 指定 knowledge_base_id + folder_id 导入到指定文件夹
    python batch_import_to_ima.py --import-json "公众号_全部文章_20260719.json" \\
        --kb-id "知识库ID" --folder-id "文件夹ID"

    # 导入到指定文件夹，每批 5 篇（避免频控时也可以设小一点）
    python batch_import_to_ima.py --import-json "公众号_全部文章_20260719.json" \\
        --kb-id "知识库ID" --folder-id "文件夹ID" --batch-size 5

    # 导入到指定文件夹，每批之间间隔 1.5 秒
    python batch_import_to_ima.py --import-json "公众号_全部文章_20260719.json" \\
        --kb-id "知识库ID" --folder-id "文件夹ID" --interval 1.5

依赖:
    - Node.js 18+ (运行 ima_api.cjs)
    - IMA 凭证（环境变量 IMA_CLIENT_ID + IMA_API_KEY，或 ~/.config/ima/client_id + api_key）
"""
import os
import sys
import json
import time
import argparse
import datetime
import subprocess


def log(msg):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def find_ima_api_cjs():
    """自动查找 ima_api.cjs 路径"""
    # 优先本仓库的 ima-skill 目录；其余候选可用环境变量 IMA_API_CJS 显式指定
    skill_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(skill_dir, "..", "ima-skill", "ima_api.cjs"),
        os.path.join(skill_dir, "..", "..", "ima-skill", "ima_api.cjs"),
    ]
    custom = os.environ.get("IMA_API_CJS")
    if custom:
        candidates.insert(0, os.path.expanduser(custom))
    candidates.append(os.path.expanduser("~/.agents/skills/ima-skill/ima_api.cjs"))
    for candidate in candidates:
        candidate = os.path.abspath(candidate)
        if os.path.exists(candidate):
            return candidate
    return None


def call_ima_api(api_cjs, api_path, body_dict):
    """调用 ima_api.cjs，返回解析后的 JSON"""
    body_str = json.dumps(body_dict, ensure_ascii=False)
    cmd = ["node", api_cjs, api_path, body_str]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        error_info = result.stderr.strip()
        log(f"⚠️ IMA API 调用失败 (exit={result.returncode}): {error_info[:200]}")
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        log(f"⚠️ IMA API 返回非 JSON: {result.stdout[:200]}")
        return None


def search_knowledge_base(api_cjs, query):
    """搜索知识库"""
    resp = call_ima_api(api_cjs, "openapi/wiki/v1/search_knowledge_base", {
        "query": query, "cursor": "", "limit": 10,
    })
    if resp:
        print(json.dumps(resp, ensure_ascii=False, indent=2))
    return resp


def get_knowledge_list(api_cjs, kb_id):
    """获取知识库内容列表（含文件夹结构）"""
    resp = call_ima_api(api_cjs, "openapi/wiki/v1/get_knowledge_list", {
        "knowledge_base_id": kb_id, "cursor": "", "limit": 50,
    })
    if resp:
        print(json.dumps(resp, ensure_ascii=False, indent=2))
    return resp


def import_urls(api_cjs, kb_id, urls, folder_id=""):
    """导入一批 URL 到 IMA 知识库"""
    body = {
        "knowledge_base_id": kb_id,
        "urls": urls,
    }
    if folder_id:
        body["folder_id"] = folder_id
    return call_ima_api(api_cjs, "openapi/wiki/v1/import_urls", body)


def batch_import(api_cjs, kb_id, folder_id, articles, batch_size=1, interval=3.0):
    """逐条导入，逐条报告进度。

    ⚠️ 2026-10-26 实测：import_urls **必须一条一条来**。
       一批 5 条只成 1 条（其余留裸链接），单条 + 3s 间隔 6/6 全成。
       默认 batch_size=1，保留参数只是为了兼容旧调用，>1 会打印警告。
    """
    total = len(articles)
    success = 0
    fail = 0
    skipped = 0

    if batch_size > 1:
        log("⚠️ batch_size>1 已知会大批失败（实测 5 条只成 1 条），建议用默认值 1")

    for i in range(0, total, batch_size):
        batch = articles[i:i + batch_size]
        urls = [a["link"] for a in batch if a.get("link")]
        if not urls:
            skipped += len(batch)
            continue

        resp = import_urls(api_cjs, kb_id, urls, folder_id)
        batch_end = min(i + batch_size, total)

        if resp is None:
            fail += len(urls)
            log(f"❌ 第 {i + 1}-{batch_end} 篇 (共 {len(urls)} 条): 导入失败")
        elif resp.get("code") == 0 or resp.get("ret") == 0:
            success += len(urls)
            title = (batch[0].get("title") or "")[:28]
            log(f"✅ [{i + 1}/{total}] {title}  (累计 {success}/{total})")
        elif resp.get("code") == 200005:
            log("⛔ 200005：IMA 每日导入额度用尽，明天再跑（已导的不会重复）")
            break
        else:
            fail += len(urls)
            log(f"⚠️ 第 {i + 1}-{batch_end} 篇: 导入返回异常 {json.dumps(resp, ensure_ascii=False)[:120]}")

        # 条间间隔
        if i + batch_size < total:
            time.sleep(interval)

    return success, fail, skipped


def check_acceptance(api_cjs, kb_id, folder_id):
    """验收四条：总条数 / 裸链接率 / 重复组 / （篇数对齐由抓取脚本负责）"""
    import re as _re

    def norm(t):
        return _re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", t or "")

    cur, titles = "", []
    while True:
        resp = call_ima_api(api_cjs, "openapi/wiki/v1/get_knowledge_list",
                            {"knowledge_base_id": kb_id, "folder_id": folder_id,
                             "cursor": cur, "limit": 50})
        data = (resp or {}).get("data") or {}
        titles += [x.get("title", "") for x in (data.get("knowledge_list") or [])]
        if data.get("is_end") or not data.get("next_cursor"):
            break
        cur = data["next_cursor"]

    bare = [t for t in titles if t.startswith("http")]
    cnt = {}
    for t in titles:
        k = norm(t)
        if k:
            cnt[k] = cnt.get(k, 0) + 1
    dups = {k: v for k, v in cnt.items() if v > 1}

    log("\n=== 验收 ===")
    log(f"总条目 {len(titles)} | 裸链接 {len(bare)}（{len(bare) * 100 // max(len(titles), 1)}%）| 重复组 {len(dups)}")
    for k, v in list(dups.items())[:5]:
        log(f"  x{v} {k[:40]}")
    if bare:
        log("  ⚠️ 裸链接 = 解析失败：重取签名后单条重导")
    if dups:
        log("  ⚠️ 重复行 IMA 没有删除接口，只能到客户端手删")
    ok = not bare and not dups
    log(f"结果：{'✅ 通过' if ok else '⚠️ 需处理'}")
    return ok


def main():
    p = argparse.ArgumentParser(description="批量导入微信公众号文章到 IMA 知识库")

    # 搜索模式
    p.add_argument("--search", metavar="关键词", help="搜索知识库（查 kb_id）")
    p.add_argument("--list", metavar="知识库ID", help="列出知识库内容（查 folder_id）")

    # 导入模式
    p.add_argument("--import-json", metavar="JSON文件", help="要导入的文章 JSON 文件路径")
    p.add_argument("--kb-id", metavar="知识库ID", help="目标知识库 ID")
    p.add_argument("--folder-id", metavar="文件夹ID", default="", help="目标文件夹 ID（留空=根目录）")
    p.add_argument("--batch-size", type=int, default=1,
                   help="每次 import_urls 提交几条 (默认 1，强烈建议保持 1)")
    p.add_argument("--interval", type=float, default=3.0, help="条间间隔秒数 (默认 3.0，别低于 3)")
    p.add_argument("--check", action="store_true", help="只做验收（裸链接率/重复组），不导入")

    # 定位 ima_api.cjs
    p.add_argument("--ima-api", metavar="路径", help="ima_api.cjs 的路径（自动查找时可不填）")

    args = p.parse_args()

    # 找 ima_api.cjs
    api_cjs = args.ima_api or find_ima_api_cjs()
    if not api_cjs:
        log("❌ 找不到 ima_api.cjs。请用 --ima-api 指定路径，或确认 ima-skill 目录在仓库同级")
        sys.exit(1)
    log(f"📁 使用 ima_api.cjs: {api_cjs}")

    # 搜索模式
    if args.search:
        search_knowledge_base(api_cjs, args.search)
        return

    # 列表模式
    if args.list:
        get_knowledge_list(api_cjs, args.list)
        return

    # 验收模式（只查不导）
    if args.check:
        if not args.kb_id:
            log("❌ --check 需要 --kb-id（配 --folder-id）")
            sys.exit(1)
        sys.exit(0 if check_acceptance(api_cjs, args.kb_id, args.folder_id) else 1)

    # 导入模式
    if not args.import_json:
        log("❌ 请指定 --import-json 或使用 --search/--list")
        sys.exit(1)

    if not args.kb_id:
        log("❌ 请指定 --kb-id（目标知识库 ID），或先用 --search 查找")
        sys.exit(1)

    # 读取 JSON
    if not os.path.exists(args.import_json):
        log(f"❌ 文件不存在: {args.import_json}")
        sys.exit(1)

    with open(args.import_json, encoding="utf-8") as f:
        articles = json.load(f)

    if not isinstance(articles, list):
        log("❌ JSON 内容不是数组格式")
        sys.exit(1)

    folder_info = f"（文件夹: {args.folder_id}）" if args.folder_id else "（根目录）"
    log(f"📥 准备导入 {len(articles)} 篇文章到 kb_id={args.kb_id} {folder_info}")
    log(f"📦 每次提交 {args.batch_size} 条，条间间隔 {args.interval}s（建议 1 条 / ≥3s）")

    success, fail, skipped = batch_import(
        api_cjs, args.kb_id, args.folder_id,
        articles, args.batch_size, args.interval,
    )

    log("=" * 40)
    log(f"📊 导入完成: 成功 {success} 篇 / 失败 {fail} 篇 / 跳过 {skipped} 篇")
    log(f"📂 目标: kb_id={args.kb_id}" + (f" folder_id={args.folder_id}" if args.folder_id else ""))


if __name__ == "__main__":
    main()
