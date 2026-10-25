# -*- coding: utf-8 -*-
"""
别人的公众号：多渠道「尽可能多」抓取（合集 + 搜狗）
=================================================
⚠️ 声明一：按 fakeid 抓任意公众号**全量**历史的后台接口已被微信关闭，
   `cgi-bin/appmsg?action=list_ex&fakeid=别人的号` 恒返回 200013（权限墙，不是频控）。
⚠️ 声明二：本脚本只能尽量多拿，**不保证是全部**。输出里带 source / coverage 字段，
   汇报必须写清覆盖率与缺口，禁止说成「已抓全」。

两条免登录渠道：
  1) 合集 album —— 号主建过合集时，getalbum 不需要任何凭据，能拿到「该合集整组」
  2) 搜狗微信搜索 —— 零凭据，但结果按相关性排序，只能覆盖「关键词命中」的一部分

用法:
    # 只用搜狗（最常用）
    python fetch_other_channels.py --name "某公众号" --sogou-pages 4

    # 搜狗 + 合集（album_id 从任意文章页源码里的 album_id 字段取）
    python fetch_other_channels.py --name "某号" --biz "MzUx..." --album-id 3061234567890

    # 顺带抠出 biz / nickname（给文章页链接自动提取）
    python fetch_other_channels.py --from-article "https://mp.weixin.qq.com/s/xxxx"

输出: <name>_多渠道_YYYYMMDD.json，含 title / link / ts / source，并打印覆盖声明。
仅用标准库，无需 pip 安装。
"""
import argparse
import http.cookiejar
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
WX_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 "
         "(KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.44")


def log(msg):
    print(msg, flush=True)


def opener_with_cookies():
    cj = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def get(op, url, headers=None, timeout=25):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": UA})
    with op.open(req, timeout=timeout) as r:
        raw = r.read()
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", "ignore")


# ---------------------------------------------------------------- 渠道 1：合集

def fetch_album(biz, album_id, max_pages=40, sleep=3.0):
    """号主建过合集时可用，免登录。返回该合集内全部文章。"""
    op = opener_with_cookies()
    out, begin = [], 0
    for page in range(max_pages):
        url = (f"https://mp.weixin.qq.com/mp/appmsgalbum?action=getalbum&__biz={urllib.parse.quote(biz)}"
               f"&album_id={album_id}&count=10&begin={begin}&f=json")
        try:
            j = json.loads(get(op, url, {"User-Agent": WX_UA}))
        except Exception as e:
            log(f"  [合集] 第{page+1}页解析失败: {e}")
            break
        if (j.get("base_resp") or {}).get("ret") not in (0, None):
            log(f"  [合集] ret={j['base_resp'].get('ret')} —— album_id 不对或号没建合集，放弃这条路")
            break
        items = j.get("appmsg_list") or []
        for it in items:
            link = it.get("url") or it.get("link") or ""
            if link:
                out.append({"title": it.get("title", ""), "link": link,
                            "ts": it.get("create_time") or 0, "source": "album"})
        total = j.get("article_count")
        begin += 10
        log(f"  [合集] 第{page+1}页 +{len(items)}，累计 {len(out)}/{total or '?'}")
        if not items or (total and len(out) >= int(total)) or not j.get("continue_flag"):
            break
        time.sleep(sleep)
    return out


# ---------------------------------------------------------------- 渠道 2：搜狗

def _parse_sogou(html):
    items = []
    for b in re.split(r'<div class="txt-box">', html)[1:]:
        a = re.search(r'<h3>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', b, re.S)
        if not a:
            continue
        acc = re.search(r'class="all-time-y2">(.*?)<', b)
        ts = re.search(r"timeConvert\('(\d+)'\)", b)
        title = re.sub(r"<[^>]+>", "", a.group(2))
        title = (title.replace("&nbsp;", " ").replace("&amp;", "&")
                      .replace("&quot;", '"').replace("&#39;", "'").strip())
        items.append({
            "title": title,
            "href": a.group(1).replace("&amp;", "&"),
            "account": acc.group(1) if acc else "",
            "ts": int(ts.group(1)) if ts else 0,
        })
    return items


def fetch_sogou(name, pages=4, sleep=6.0):
    op = opener_with_cookies()
    seen, out = set(), []
    for page in range(1, pages + 1):
        url = ("https://weixin.sogou.com/weixin?" +
               urllib.parse.urlencode({"type": 2, "query": name, "page": page}))
        try:
            html = get(op, url, {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
        except Exception as e:
            log(f"  [搜狗] 第{page}页请求失败: {e}")
            break
        if "antispider" in html:
            log(f"  [搜狗] 第{page}页触发 antispider，停（隔 30~60 分钟再来）")
            break
        items = _parse_sogou(html)
        log(f"  [搜狗] 第{page}页 {len(items)} 条")
        for it in items:
            if it["title"] in seen:
                continue
            seen.add(it["title"])
            # 解析成可导入的签名链（必须带 session cookie；失败重试一次，签名偶尔不给）
            real = ""
            for attempt in range(2):
                try:
                    rr = get(op, "https://weixin.sogou.com" + it["href"],
                             {"User-Agent": UA, "Referer": url})
                    real = "".join(re.findall(r"url \+= '([^']*)'", rr)).replace("@", "")
                except Exception:
                    real = ""
                if real:
                    break
                time.sleep(3)
            if not real:
                log(f"    ✗ 解析失败（签名没拿到）: {it['title'][:24]}")
                continue
            out.append({"title": it["title"], "link": real, "ts": it["ts"],
                        "source": "sogou", "account_shown": it["account"]})
            time.sleep(2)          # 解析签名别太快
        if not items:
            break
        time.sleep(sleep)
    return out


def extract_from_article(url):
    """从任意公开文章页抠 biz / nickname（移动 UA 直取，超时多半是被限速，等 60~90s 重试）"""
    op = opener_with_cookies()
    html = get(op, url, {"User-Agent": WX_UA})
    if len(html) < 100000 and "cooldown_tips" in html:
        raise SystemExit("命中「操作频繁」冷却页 —— 等 60~90 秒重试，不是 IP 被封")
    biz = re.search(r'var\s+biz\s*=\s*"([^"]+)"', html)
    nick = re.search(r'var\s+nickname\s*=\s*"([^"]*)"', html)
    albums = sorted(set(re.findall(r'album_id["\']?\s*[:=]\s*["\']?(\d{6,})', html)))
    return (biz.group(1) if biz else ""), (nick.group(1) if nick else ""), albums


# ---------------------------------------------------------------- main

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--name", default="公众号", help="公众号名称（搜狗用它做关键词）")
    p.add_argument("--biz", default="", help="目标号 __biz（合集渠道需要）")
    p.add_argument("--album-id", default="", help="合集 ID（从文章页源码取）")
    p.add_argument("--from-article", default="", help="给一篇文章链接，顺带抠 biz/nickname/album_id")
    p.add_argument("--sogou-pages", type=int, default=4, help="搜狗翻几页（每页 10 条，最多 10）")
    p.add_argument("--out", default="")
    a = p.parse_args()

    biz, nick, albums = a.biz, "", []
    if a.from_article:
        biz, nick, albums = extract_from_article(a.from_article)
        log(f"文章页解析：biz={biz or '-'} nickname={nick or '-'} album_id={albums or '-'}")
        if albums and not a.album_id:
            a.album_id = albums[0]
            log(f"  自动采用 album_id={a.album_id}")
    if nick and a.name == "公众号":
        a.name = nick

    arts = []
    if a.album_id and biz:
        log(f"[渠道1] 合集 album_id={a.album_id}")
        arts += fetch_album(biz, a.album_id)
    else:
        log("[渠道1] 跳过合集（没给 --album-id，或该号没建合集）—— 这不是失败，是这条路不适用")

    log(f"[渠道2] 搜狗关键词搜索：{a.name}")
    arts += fetch_sogou(a.name, a.sogou_pages)

    seen, merged = set(), []
    for x in sorted(arts, key=lambda y: -(y.get("ts") or 0)):
        if x["link"] in seen:
            continue
        seen.add(x["link"])
        merged.append(x)

    out = a.out or f"{a.name}_多渠道_{datetime.now():%Y%m%d}.json"
    json.dump(merged, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    by_src = {}
    for x in merged:
        by_src[x["source"]] = by_src.get(x["source"], 0) + 1
    log(f"\n合计 {len(merged)} 篇 → {out}")
    log(f"  渠道分布：{by_src}")
    if merged:
        first = min(x["ts"] for x in merged if x["ts"]) if any(x["ts"] for x in merged) else 0
        last = max(x["ts"] for x in merged if x["ts"]) if any(x["ts"] for x in merged) else 0
        if first:
            log(f"  时间覆盖：{datetime.fromtimestamp(first):%Y-%m-%d} ~ {datetime.fromtimestamp(last):%Y-%m-%d}")
    log("\n⚠️ 声明：非自己管理的公众号无法获取全量 —— 本结果按渠道尽力收集，"
        "搜狗部分按相关性排序、不保证时间连续，中间可能仍有缺口。汇报请如实写覆盖率。")


if __name__ == "__main__":
    main()
