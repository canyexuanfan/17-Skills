# -*- coding: utf-8 -*-
"""market-lens 本地索引快照构建（可选，首次使用建议执行一次）

拉取四大数据源到 data/ 目录，供 market_search.py 离线全量检索：
  - official_skills     官方运营市场·技能（约 43k 条，需登录态/cookie，几分钟）
  - official_experts    官方运营市场·专家（约 33k 条，需登录态/cookie，几分钟）
  - official_expert_pack 官方专家包（免登录 COS 直下，秒级）
  - skillhub            SkillHub 社区市场（免登录，约 217k 条，全量约 30-60 分钟）

用法:
  python scripts/pull_snapshot.py                  # 默认官方两源+专家包（推荐先跑这个）
  python scripts/pull_snapshot.py skillhub         # 额外拉 SkillHub 全量（较久）
  python scripts/pull_snapshot.py official_skills  # 只重建指定源
  python scripts/pull_snapshot.py all              # 全部四源

认证（官方源）：零配置自动解密本机客户端登录态；或 config.json 的 auth.cookie_file；
或环境变量 WORKBUDDY_BEARER_TOKEN。SkillHub 与专家包全程免登录。
"""
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import market_search as ms  # 复用认证(_load_bearer)、请求(_curl_json)、编码(_qenc)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
PAGE = 100


def _headers(cfg):
    bearer, uid, _src = ms._load_bearer(cfg)
    auth = cfg.get("auth", {})
    h = {
        "accept": "application/json", "content-type": "application/json",
        "origin": "https://www.workbuddy.cn", "referer": "https://www.workbuddy.cn/app/skills",
        "user-agent": auth.get("ua") or ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"),
        "x-request-id": uuid.uuid4().hex,
        "x-user-id": uid or auth.get("user_id") or "",
    }
    cookie = ""
    cf = auth.get("cookie_file")
    if cf and os.path.exists(cf):
        try:
            cookie = open(cf, encoding="utf-8").read().strip()
        except Exception:
            cookie = ""
    if bearer:
        h["authorization"] = "Bearer " + bearer
    elif cookie:
        h["cookie"] = cookie
    return h, bool(bearer or cookie)


def _iso(ts):
    """epoch 毫秒/秒 → ISO 字符串；已是字符串则原样返回"""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        try:
            ts = ts / 1000.0 if ts > 1e11 else float(ts)
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        except Exception:
            return None
    return str(ts)


def _paginate(out_path, req, parse, label):
    """通用分页拉取：req(page) → 响应；parse(d) → (total, items)。写 ndjson。"""
    total = None
    page = 1
    n = 0
    fails = 0
    with open(out_path, "w", encoding="utf-8") as f:
        while True:
            d = None
            for attempt in range(3):
                try:
                    d = req(page)
                    if d is not None:
                        break
                except Exception:
                    pass
                time.sleep(2 * (attempt + 1))
            if d is None:
                fails += 1
                if fails >= 5:
                    print(f"[{label}] 连续失败 5 次，停止于第 {page} 页（已得 {n} 条）")
                    break
                time.sleep(5)
                continue
            fails = 0
            total, items = parse(d)
            for it in items:
                f.write(json.dumps(it, ensure_ascii=False) + "\n")
            n += len(items)
            if n % 1000 == 0 or len(items) < PAGE:
                print(f"[{label}] {n}/{total} 页 {page}", flush=True)
            if len(items) < PAGE or (total and n >= total):
                break
            page += 1
            time.sleep(0.2)
    print(f"[{label}] 完成：{n} 条 → {out_path}")
    return n


def pull_official(cfg, kind):
    h, ok = _headers(cfg)
    if not ok:
        print("[!] 官方源需要登录态：请先登录 WorkBuddy 客户端后重试；"
              "或在 config.json 配置 auth.cookie_file（浏览器 F12 → Copy as cURL）；"
              "或设置环境变量 WORKBUDDY_BEARER_TOKEN。本次跳过该源。")
        return
    if kind == "official_skills":
        def req(p):
            return ms._curl_json("https://www.workbuddy.cn/portal/enterprises/personal/skill-market/list?"
                                 + ms._qenc({"source": "builtin", "page": p, "page_size": PAGE}),
                                 method="POST", headers=h)
        parse = lambda d: (d["data"]["total"], d["data"]["items"])  # noqa: E731
    else:
        def req(p):
            return ms._curl_json("https://www.workbuddy.cn/portal/operation-platform/market/expert/list",
                                 method="POST",
                                 body={"page": p, "page_size": PAGE, "expert_type": "agent",
                                       "sort_by": "reco_rank", "sort_order": "desc"},
                                 headers=h)
        parse = lambda d: (d["data"]["total_count"], d["data"]["experts"])  # noqa: E731
    _paginate(os.path.join(DATA, f"{kind}.ndjson"), req, parse, kind)


def pull_expert_pack():
    """官方专家包：免登录 COS 直下"""
    url = ("https://acc-1258344699.cos.accelerate.myqcloud.com/"
           "workbuddy/expert-marketplace/expert_center.json")
    out = os.path.join(DATA, "expert_center.json")
    d = ms._curl_json(url, timeout=60)
    if not d or not d.get("experts"):
        print("[official_expert_pack] 拉取失败（COS 暂不可用？），跳过")
        return
    with open(out, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    print(f"[official_expert_pack] 完成：{len(d['experts'])} 条 → {out}")


def _condense_skillhub(s):
    ns = s.get("namespace") or {}
    if isinstance(ns, dict):
        ns = ns.get("owner") or ns.get("slug") or ""
    pub = s.get("publisher") or {}
    return {
        "name": s.get("name") or s.get("slug") or "",
        "slug": s.get("slug") or "",
        "description_zh": s.get("description_zh") or s.get("description") or "",
        "category": s.get("category") or "",
        "version": s.get("version"),
        "updated_at": _iso(s.get("updated_at")),
        "downloads": s.get("downloads") or 0,
        "installs": s.get("installs") or 0,
        "stars": s.get("stars") or 0,
        "verified": bool(s.get("verified") or pub.get("verified")),
        "owner": s.get("ownerName") or ns or "",
        "owner_display": pub.get("name") or "",
    }


def pull_skillhub():
    """SkillHub 全量：免登录，217k 条约 30-60 分钟"""
    out = os.path.join(DATA, "skillhub.ndjson")
    page = 1
    n = 0
    total = None
    with open(out, "w", encoding="utf-8") as f:
        while True:
            d = None
            for attempt in range(3):
                try:
                    d = ms._curl_json(f"https://api.skillhub.cn/api/skills?"
                                      f"page={page}&pageSize={PAGE}&sortBy=downloads&order=desc")
                    if d is not None:
                        break
                except Exception:
                    pass
                time.sleep(2 * (attempt + 1))
            if d is None:
                print(f"[skillhub] 第 {page} 页连续失败，停止（已得 {n} 条）")
                break
            data = d.get("data") or {}
            items = data.get("skills") or []
            total = data.get("total")
            for s in items:
                f.write(json.dumps(_condense_skillhub(s), ensure_ascii=False) + "\n")
            n += len(items)
            if n % 2000 == 0 or len(items) < PAGE:
                print(f"[skillhub] {n}/{total} 页 {page}", flush=True)
            if len(items) < PAGE:
                break
            page += 1
            time.sleep(0.2)
    print(f"[skillhub] 完成：{n} 条 → {out}")


def main():
    cfg = ms.load_config()
    os.makedirs(DATA, exist_ok=True)
    which = sys.argv[1:] or ["official_skills", "official_experts", "official_expert_pack"]
    if which == ["all"]:
        which = ["official_skills", "official_experts", "official_expert_pack", "skillhub"]
    for k in which:
        if k == "official_expert_pack":
            pull_expert_pack()
        elif k == "skillhub":
            pull_skillhub()
        elif k in ("official_skills", "official_experts"):
            pull_official(cfg, k)
        else:
            print(f"[!] 未知源: {k}（可选 official_skills/official_experts/official_expert_pack/skillhub/all）")
    print("\n完成。现在可以运行: python scripts/market_search.py \"你的需求\" --top 8")


if __name__ == "__main__":
    main()
