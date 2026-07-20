# -*- coding: utf-8 -*-
"""
微信公众号文章抓取脚本
===================
从一篇公众号文章 URL 提取 __biz，然后抓取该公众号的全部历史文章，
去重后保存为 JSON（title / link / date）。

用法:
    python fetch_articles.py --url "https://mp.weixin.qq.com/s/xxxx" --name "公众号名"

可选参数:
    --output      输出 JSON 路径 (默认: <name>_全部文章_YYYYMMDD.json)
    --token       (可选) 覆盖 credentials 里的 token
    --cookie      (可选) 覆盖 credentials 里的 cookie
    --max-batch   最大抓取批次数 (默认 80, 即最多 400 篇)
    --count       每批条数 (默认 5)
    --resume      断点续抓模式（从 --state 记录的 offset 继续）
    --state       续抓状态文件路径 (默认: 同目录下 resume_state.json)
    --existing    已存在的文章 JSON，用于去重基准（不重复入库）
    --seed-offset 手动指定起始 offset（覆盖 state）
    --retry       频控时自动重试次数 (默认 0 = 不重试，立即停)
    --retry-wait  频控重试前的等待秒数 (默认 600 = 10 分钟)

依赖: 仅标准库 (urllib / re / json / time)
"""
import os
import re
import sys
import json
import time
import argparse
import datetime
import urllib.request
import urllib.parse

# 让脚本能 import 同目录的 weixin_credentials.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import weixin_credentials as _cred
    DEFAULT_TOKEN = _cred.token
    DEFAULT_COOKIE = _cred.cookie
except Exception:
    DEFAULT_TOKEN = None
    DEFAULT_COOKIE = None

# 兼容三种形态:
#   URL 参数:  ...?__biz=MzU1MjM1Mjc1Nw==&mid=...
#   源码变量:  var biz = "MzU1MjM1Mjc1Nw==";
#   JSON 字段: "biz":"MzU1MjM1Mjc1Nw=="
BIZ_RE = re.compile(r'(?:__)?biz["\']?\s*[=:]\s*["\']?([a-zA-Z0-9+/=]{10,})')
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def log(msg):
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def fetch_url(url, headers, data=None, timeout=20):
    req = urllib.request.Request(url, headers=headers, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "ignore")


def extract_biz(article_url, headers):
    log(f"访问文章页提取 __biz: {article_url[:60]}...")
    html = fetch_url(article_url, headers)
    m = BIZ_RE.search(html)
    if not m:
        # 有时 __biz 在脚本里写成 biz= 形式，再试一次宽松匹配
        m2 = re.search(r'biz[=:]?\s*["\']?([a-zA-Z0-9+/=]{10,})', html)
        if not m2:
            raise RuntimeError("无法从 URL 提取 __biz，请确认链接是公众号文章页")
        return m2.group(1)
    return m.group(1)


def crawl_all(biz, token, cookie, max_batch=80, count=5, sleep_sec=3,
              start_offset=0, seed_seen=None, on_progress=None,
              retry=0, retry_wait=600):
    """抓取公众号全部文章。

    返回: (new_articles, end_offset)
      - new_articles: 本次新抓到（去重后）的文章列表
      - end_offset:   停止时的 offset（用于断点续抓）
    """
    headers = {
        "Cookie": cookie,
        "User-Agent": UA,
        "Referer": f"https://mp.weixin.qq.com/cgi-bin/home?token={token}",
    }
    api = f"https://mp.weixin.qq.com/cgi-bin/appmsg?action=list_ex&token={token}"

    all_articles = []
    seen_links = set(seed_seen or [])
    offset = start_offset
    batch_no = 0
    retries_used = 0

    def do_batch():
        nonlocal offset, batch_no
        post_data = urllib.parse.urlencode({
            "token": token,
            "ajax": 1,
            "fakeid": biz,
            "begin": str(offset),
            "count": str(count),
            "type": "9",
            "query": "",
        }).encode("utf-8")

        raw = fetch_url(api, headers, data=post_data)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return "BADJSON", None

        ret = data.get("base_resp", {}).get("ret")
        if ret != 0:
            return ret, None

        items = data.get("app_msg_list", [])
        if not items:
            return "EMPTY", None

        new_in_batch = 0
        for item in items:
            link = item.get("link", "")
            if link and link not in seen_links:
                seen_links.add(link)
                all_articles.append({
                    "title": item.get("title", ""),
                    "link": link,
                    "date": str(item.get("create_time", "")),
                })
                new_in_batch += 1

        batch_no += 1
        offset += count
        return 0, new_in_batch

    while batch_no < max_batch:
        try:
            ret, info = do_batch()
        except Exception as e:
            log(f"⚠️ 请求异常: {e}")
            ret = "ERR"

        if ret == 0:
            log(f"第 {batch_no} 批: +{info} 篇，累计 {len(all_articles)} 篇")
            if on_progress:
                on_progress(offset, len(all_articles), len(seen_links))
            time.sleep(sleep_sec)
            retries_used = 0
            continue

        if ret == "EMPTY":
            log("✅ 返回空列表，抓取完成（已到末尾）")
            break

        if ret == "BADJSON":
            log("⚠️ 响应不是合法 JSON（可能 Cookie 失效或被拦截），停止")
            break

        # 频控或其它错误
        if ret == 200013:
            if retries_used < retry:
                retries_used += 1
                m, s = divmod(retry_wait, 60)
                log(f"⛔ 触发频控，第 {retries_used}/{retry} 次重试，等待 {m}分{s}秒…")
                if on_progress:
                    on_progress(offset, len(all_articles), len(seen_links))
                time.sleep(retry_wait)
                continue
            log("⛔ 触发频控 (freq control)，已用尽重试。下回可用 --resume 续抓。")
            break
        else:
            log(f"⛔ 接口返回错误 ret={ret}，停止。")
            break

    return all_articles, offset


def load_existing(path):
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return []


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", required=True, help="公众号文章 URL")
    p.add_argument("--name", default="公众号", help="公众号名称（用于输出文件名）")
    p.add_argument("--output", default=None, help="输出 JSON 路径")
    p.add_argument("--token", default=None)
    p.add_argument("--cookie", default=None)
    p.add_argument("--max-batch", type=int, default=80)
    p.add_argument("--count", type=int, default=5)
    p.add_argument("--resume", action="store_true", help="断点续抓模式")
    p.add_argument("--state", default=None, help="续抓状态文件路径")
    p.add_argument("--existing", default=None, help="已存在文章 JSON（去重基准）")
    p.add_argument("--seed-offset", type=int, default=None, help="手动指定起始 offset")
    p.add_argument("--retry", type=int, default=0, help="频控自动重试次数")
    p.add_argument("--retry-wait", type=int, default=600, help="频控重试前等待秒数")
    args = p.parse_args()

    token = args.token or DEFAULT_TOKEN
    cookie = args.cookie or DEFAULT_COOKIE

    if not token or cookie in (None, "", "PASTE_COOKIE_HERE"):
        log("❌ 缺少 token 或 cookie，请先在 weixin_credentials.py 中补充凭证")
        sys.exit(1)

    skill_dir = os.path.dirname(os.path.abspath(__file__))
    state_path = args.state or os.path.join(skill_dir, "resume_state.json")

    headers = {
        "Cookie": cookie,
        "User-Agent": UA,
        "Referer": f"https://mp.weixin.qq.com/cgi-bin/home?token={token}",
    }

    # 续抓状态
    start_offset = 0
    biz = None
    if args.resume and os.path.exists(state_path):
        with open(state_path, encoding="utf-8") as f:
            st = json.load(f)
        start_offset = st.get("offset", 0)
        biz = st.get("biz")
        log(f"📌 续抓模式: 从 offset={start_offset} 继续 (biz={biz})")
    else:
        try:
            biz = extract_biz(args.url, headers)
        except Exception as e:
            log(f"❌ {e}")
            sys.exit(1)
        log(f"✅ 提取到 biz = {biz}")

    if args.seed_offset is not None:
        start_offset = args.seed_offset
        log(f"↪️ 手动指定起始 offset={start_offset}")

    # 去重基准
    existing = load_existing(args.existing) if args.existing else []
    seed_seen = set(a.get("link", "") for a in existing)
    if seed_seen:
        log(f"🧹 载入 {len(seed_seen)} 条已存在链接作为去重基准")

    def save_state(off, _c, _s):
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump({"offset": off, "biz": biz, "updated": datetime.datetime.now().isoformat()}, f, ensure_ascii=False, indent=2)

    articles, end_offset = crawl_all(
        biz, token, cookie, args.max_batch, args.count,
        start_offset=start_offset, seed_seen=seed_seen,
        on_progress=save_state, retry=args.retry, retry_wait=args.retry_wait,
    )

    # 合并已存在
    merged = list(existing)
    for a in articles:
        if a["link"] not in seed_seen:
            merged.append(a)
            seed_seen.add(a["link"])

    # 输出路径
    if args.output:
        out_path = args.output
    else:
        date_str = datetime.datetime.now().strftime("%Y%m%d")
        safe_name = re.sub(r'[\\/:*?"<>|]', "_", args.name)
        out_path = f"{safe_name}_全部文章_{date_str}.json"

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    save_state(end_offset, 0, 0)
    log(f"✅ 本次新增 {len(articles)} 篇，合并后共 {len(merged)} 篇 -> {out_path}")


if __name__ == "__main__":
    main()
