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
    # 优先本仓库的 ima-skill 目录
    skill_dir = os.path.dirname(os.path.abspath(__file__))
    for candidate in [
        os.path.join(skill_dir, "..", "ima-skill", "ima_api.cjs"),
        os.path.join(skill_dir, "..", "..", "ima-skill", "ima_api.cjs"),
        os.path.expanduser("~/.hermes/skills/ima-skill/ima_api.cjs"),
        os.path.expanduser("~/.workbuddy/skills/ima-skill/ima_api.cjs"),
    ]:
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


def batch_import(api_cjs, kb_id, folder_id, articles, batch_size=10, interval=0.5):
    """批量导入，逐批报告进度"""
    total = len(articles)
    success = 0
    fail = 0
    skipped = 0

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
            log(f"✅ 第 {i + 1}-{batch_end} 篇: +{len(urls)} 条  (累计 {success}/{total})")
        else:
            fail += len(urls)
            log(f"⚠️ 第 {i + 1}-{batch_end} 篇: 导入返回异常 {json.dumps(resp)[:100]}")

        # 批间间隔
        if i + batch_size < total:
            time.sleep(interval)

    return success, fail, skipped


def main():
    p = argparse.ArgumentParser(description="批量导入微信公众号文章到 IMA 知识库")

    # 搜索模式
    p.add_argument("--search", metavar="关键词", help="搜索知识库（查 kb_id）")
    p.add_argument("--list", metavar="知识库ID", help="列出知识库内容（查 folder_id）")

    # 导入模式
    p.add_argument("--import-json", metavar="JSON文件", help="要导入的文章 JSON 文件路径")
    p.add_argument("--kb-id", metavar="知识库ID", help="目标知识库 ID")
    p.add_argument("--folder-id", metavar="文件夹ID", default="", help="目标文件夹 ID（留空=根目录）")
    p.add_argument("--batch-size", type=int, default=10, help="每批导入数量 (默认 10)")
    p.add_argument("--interval", type=float, default=0.5, help="批间间隔秒数 (默认 0.5)")

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
    log(f"📦 每批 {args.batch_size} 篇，间隔 {args.interval}s")

    success, fail, skipped = batch_import(
        api_cjs, args.kb_id, args.folder_id,
        articles, args.batch_size, args.interval,
    )

    log("=" * 40)
    log(f"📊 导入完成: 成功 {success} 篇 / 失败 {fail} 篇 / 跳过 {skipped} 篇")
    log(f"📂 目标: kb_id={args.kb_id}" + (f" folder_id={args.folder_id}" if args.folder_id else ""))


if __name__ == "__main__":
    main()
