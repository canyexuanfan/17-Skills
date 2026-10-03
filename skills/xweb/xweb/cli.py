#!/usr/bin/env python3
"""xweb — X（x.com）命令行客户端：读取公开数据；填自己的 cookie 后可读全站并执行互动操作。"""
from __future__ import annotations

import argparse
import html as H
import json
import os
import re
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone

try:
    from curl_cffi import requests as cr
except ImportError:
    print("[xweb] 缺少依赖：python3 -m pip install curl_cffi", file=sys.stderr)
    raise SystemExit(2)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
CFG_DIR = os.path.expanduser("~/.config/x-web")
STATE = os.path.join(CFG_DIR, "state.json")
CONFIG = os.path.join(CFG_DIR, "config.json")

try:
    from .queryids_data import FEATURE_SETS          
except ImportError:                                  
    try:
        from queryids_data import FEATURE_SETS
    except ImportError:
        FEATURE_SETS = {"FEATURES": {}}

BEARER = ("AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D"
          "1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA")
IMPERSONATE = os.environ.get("X_WEB_IMPERSONATE", "safari18_0")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/18.0 Safari/605.1.15")
PV = {"__relay_internal__pv__appviewerisloggedinprovider": False}

def _TWEET_RESULT_VARS(tid: str) -> dict:
    

    return {"tweetId": tid, "restId": tid, "includePromotedContent": False,
            "withVoice": False, "withCommunity": False, **PV}

DRY_RUN = False
DRY_JSON = False
SECRET_HEADERS = ("authorization", "x-csrf-token", "cookie", "x-guest-token")

DATA_DIR = os.path.join(HERE, "data")

def _qids():
    
    for p in (os.path.join(DATA_DIR, "queryids.json"), os.path.join(HERE, "queryids.json")):
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return json.load(f)
    raise SystemExit("[xweb] 缺少 data/queryids.json —— 请确认仓库文件完整")

def _qids_raw():
    with open(os.path.join(DATA_DIR, "queryids.json"), encoding="utf-8") as f:
        return json.load(f)

def _num(v):
    
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    try:
        return int(str(v).replace(",", ""))
    except Exception:
        return None

def _money(v):
    

    if not isinstance(v, dict):
        return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    u, n = v.get("units"), v.get("nanos")
    if u is None and n is None:
        return None
    try:
        return float(u or 0) + (float(n or 0) / 1e9)
    except Exception:
        return None

def fmt_n(n):
    n = _num(n)
    if n is None:
        return "—"
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)

def _dt(v):
    
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    s = str(v)
    try:
        return datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y").astimezone(
            timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(
            timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return s

def _age_days(ts: str | None):
    if not ts:
        return None
    try:
        d = datetime.strptime(ts, "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
    except Exception:
        return None
    return (datetime.now(timezone.utc) - d).days

def _handle(h: str) -> str:
    h = (h or "").strip()
    m = re.search(r"(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})", h)
    return m.group(1) if m else h.lstrip("@").strip()

@contextmanager
def human_only(a):
    

    if getattr(a, "json", False):
        old = sys.stdout
        sys.stdout = sys.stderr
        try:
            yield
        finally:
            sys.stdout = old
    else:
        yield

def _tid(s: str) -> str:
    s = (s or "").strip()
    m = re.search(r"status(?:es)?/(\d+)", s)
    return m.group(1) if m else s

def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def parse_user(u: dict) -> dict:
    if not isinstance(u, dict):
        return {}
    core, lg = u.get("core") or {}, u.get("legacy") or {}
    rc, tc, ver = u.get("relationship_counts") or {}, u.get("tweet_counts") or {}, u.get("verification") or {}
    av = ((u.get("avatar") or {}).get("image_url") or lg.get("profile_image_url_https") or "")
    av = re.sub(r"_(normal|bigger|mini)\.", ".", av)
    created = core.get("created_at_ms")
    if not created and lg.get("created_at"):
        try:
            created = int(datetime.strptime(lg["created_at"], "%a %b %d %H:%M:%S %z %Y").timestamp() * 1000)
        except Exception:
            created = None
    screen = core.get("screen_name") or lg.get("screen_name")
    return {
        "rest_id": u.get("rest_id") or lg.get("id_str"),
        "screen_name": screen,
        "name": core.get("name") or lg.get("name"),
        "bio": (u.get("profile_bio") or {}).get("description") or lg.get("description") or "",
        "location": (u.get("location") or {}).get("location") or lg.get("location") or "",
        "followers": _num(rc.get("followers") if rc else lg.get("followers_count")),
        "following": _num(rc.get("following") if rc else lg.get("friends_count")),
        "tweets": _num(tc.get("tweets") if tc else lg.get("statuses_count")),
        "media_count": _num(tc.get("media") if tc else lg.get("media_count")),
        "verified": bool(ver.get("is_blue_verified") or ver.get("verified")
                         or u.get("is_blue_verified") or lg.get("verified")),
        "avatar": av,
        "created_at": (datetime.fromtimestamp(created / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                       if created else None),
        "url": f"https://x.com/{screen}" if screen else None,
    }

def user_node(j: dict) -> dict:
    
    d = j.get("data") or {}
    for key, wrap in (("user_result_by_screen_name", "result"), ("user", "result"), ("user", None)):
        node = d.get(key)
        if isinstance(node, dict):
            if wrap and isinstance(node.get(wrap), dict):
                return node[wrap]
            if any(k in node for k in ("rest_id", "core", "legacy")):
                return node
    return {}

def _author(t: dict) -> dict:
    try:
        u = t["core"]["user_results"]["result"]
    except Exception:
        return {}
    lg, core = u.get("legacy") or {}, u.get("core") or {}
    return {"screen_name": core.get("screen_name") or lg.get("screen_name"),
            "name": core.get("name") or lg.get("name"),
            "rest_id": u.get("rest_id") or lg.get("id_str"),
            "verified": bool(u.get("is_blue_verified") or (u.get("verification") or {}).get("is_blue_verified")
                            or lg.get("verified"))}

def _media(t: dict) -> list:
    out = []
    for m in (t.get("media_entities2") or []):
        if isinstance(m, dict):
            out.append({"type": m.get("type"), "url": m.get("url") or m.get("media_url_https"),
                        "alt": m.get("alt_text")})
    lg = t.get("legacy") or {}
    for m in ((lg.get("extended_entities") or {}).get("media") or (lg.get("entities") or {}).get("media") or []):
        url = m.get("media_url_https")
        if m.get("type") in ("video", "animated_gif"):
            vs = [x for x in ((m.get("video_info") or {}).get("variants") or [])
                  if x.get("content_type") == "video/mp4"]
            if vs:
                url = max(vs, key=lambda x: x.get("bitrate") or 0).get("url")
        out.append({"type": m.get("type"), "url": url, "alt": m.get("ext_alt_text") or m.get("alt_text")})
    return out

def _is_truncated(channel: str, note_text, full_text) -> bool:
    

    if note_text:
        return False
    if channel == "graphql":
        return False
    return len(full_text or "") >= 270

def parse_tweet(t: dict, channel: str = "graphql") -> dict:
    if not isinstance(t, dict):
        return {}
    tn = t.get("__typename")
    if tn in ("TweetTombstone", "TweetUnavailable") or ("details" not in t and "legacy" not in t):
        return {"unavailable": True, "typename": tn}
    d, c, lg = t.get("details") or {}, t.get("counts") or {}, t.get("legacy") or {}
    a = _author(t)
    tid = t.get("rest_id")
    ts = d.get("created_at_ms")
    
    
    
    def _nt_of(obj):
        r = (((obj or {}).get("note_tweet") or {}).get("note_tweet_results") or {}).get("result")
        return r.get("text") if isinstance(r, dict) else None

    _full = _nt_of(t) or _nt_of(d) or _nt_of(lg)
    return {
        "id": tid,
        "text": _full or d.get("full_text") or lg.get("full_text") or "",
        
        
        
        "text_truncated": _is_truncated(channel, _full,
                                        d.get("full_text") or lg.get("full_text")),
        "lang": lg.get("lang") or d.get("lang") or "",
        "created_at": _dt(ts) if ts else _dt(lg.get("created_at")),
        "likes": _num(c.get("favorite_count") if c else lg.get("favorite_count")),
        "retweets": _num(c.get("retweet_count") if c else lg.get("retweet_count")),
        "replies": _num(c.get("reply_count") if c else lg.get("reply_count")),
        "quotes": _num(c.get("quote_count") if c else lg.get("quote_count")),
        "bookmarks": _num(c.get("bookmark_count") if c else lg.get("bookmark_count")),
        "views": _num((t.get("views") or {}).get("count")),
        "author": a, "media": _media(t),
        "hashtags": [h.get("text") for h in (d.get("hashtag_entities")
                     or (lg.get("entities") or {}).get("hashtags") or []) if h.get("text")],
        "url": f"https://x.com/{a.get('screen_name')}/status/{tid}" if tid and a.get("screen_name")
               else (f"https://x.com/i/status/{tid}" if tid else None),
        "quoted": bool((t.get("quoted_tweet_results") or {}).get("result") or lg.get("is_quote_status")),
        "is_retweet": bool(lg.get("retweeted_status_result")),
        "in_reply_to": lg.get("in_reply_to_status_id_str"),
    }

def walk_tweets(node, out=None, depth=0):
    
    if out is None:
        out = []
    if depth > 12:
        return out
    if isinstance(node, dict):
        if "rest_id" in node and ("legacy" in node or "details" in node):
            out.append(node)
            return out
        for v in node.values():
            walk_tweets(v, out, depth + 1)
    elif isinstance(node, list):
        for v in node:
            walk_tweets(v, out, depth + 1)
    return out

def timeline_tweets(data: dict) -> tuple[list, str | None]:
    
    seen, tweets, cursor = set(), [], None

    def scan(node, depth=0):
        nonlocal cursor
        if depth > 14:
            return
        if isinstance(node, dict):
            if node.get("cursorType") in ("Bottom", "ShowMore", "ShowMoreThreads") and node.get("value"):
                cursor = cursor or node["value"]
            ic = node.get("itemContent") or {}
            res = (ic.get("tweet_results") or {}).get("result") if isinstance(ic, dict) else None
            if res:
                if res.get("__typename") == "TweetWithVisibilityResults":
                    res = res.get("tweet") or res
                pt = parse_tweet(res)
                if pt and not pt.get("unavailable") and pt.get("id") and pt["id"] not in seen:
                    seen.add(pt["id"])
                    tweets.append(pt)
            for v in node.values():
                scan(v, depth + 1)
        elif isinstance(node, list):
            for v in node:
                scan(v, depth + 1)

    scan(data)
    if not tweets:
        
        
        
        
        res = (((data or {}).get("data") or {}).get("tweetResult") or {}).get("result")
        if isinstance(res, dict):
            if res.get("__typename") == "TweetWithVisibilityResults":
                res = res.get("tweet") or res
            pt = parse_tweet(res)
            if pt and not pt.get("unavailable") and pt.get("id"):
                tweets.append(pt)
    tweets.sort(key=lambda t: t.get("created_at") or "", reverse=True)
    return tweets, cursor

_MAX_SCAN_DEPTH = 24
_MAX_SCAN_HITS = 1000

def parse_users_from(data: dict) -> list:
    
    out = []
    truncated = {"depth": 0, "hits": False}

    def scan(node, depth=0):
        if depth > _MAX_SCAN_DEPTH:
            truncated["depth"] += 1
            return
        if len(out) > _MAX_SCAN_HITS:
            truncated["hits"] = True
            return
        if isinstance(node, dict):
            if (("rest_id" in node and node.get("rest_id")) or "id_str" in node) \
                    and ("core" in node or "legacy" in node) \
                    and ("screen_name" in (node.get("core") or {})
                         or "screen_name" in (node.get("legacy") or {})):
                out.append(node)
                return
            for v in node.values():
                scan(v, depth + 1)
        elif isinstance(node, list):
            for v in node:
                scan(v, depth + 1)

    scan(data)
    ded, seen = [], set()
    for u in out:
        pu = parse_user(u)
        if pu.get("rest_id") and pu["rest_id"] not in seen:
            seen.add(pu["rest_id"])
            ded.append(pu)
    
    if truncated["depth"] and not ded:
        print(f"[xweb] ⚠️ 用户扫描触及深度上限 {_MAX_SCAN_DEPTH}（{truncated['depth']} 个分支被截），"
              f"结果可能不完整甚至为空 —— 这是 CLI 的 bug，不是\"对方没有数据\"。",
              file=sys.stderr)
    return ded

class X:
    def __init__(self, verbose=False):
        self.verbose = verbose
        self.qids = _qids()
        self.s = cr.Session(impersonate=IMPERSONATE, timeout=35)
        self.s.headers.update({"User-Agent": UA, "Accept": "*/*",
                               "Accept-Language": "en-US,en;q=0.9"})
        self.logged_in = False
        self.guest_token = None
        self.screen_name = None
        self._xclid_gen = None      
        self._boot()

    
    def _boot(self):
        cfg = _load_json(CONFIG, {})
        at = cfg.get("auth_token") or os.environ.get("X_AUTH_TOKEN")
        ct0 = cfg.get("ct0") or os.environ.get("X_CT0")
        try:
            r = self.s.get("https://x.com/")
        except Exception as e:
            raise SystemExit(f"[xweb] 无法访问 x.com: {e}")
        if r.status_code == 403:
            raise SystemExit("[xweb] x.com 403：指纹/IP 被拦。试 X_WEB_IMPERSONATE=firefox135")
        if at and ct0:
            self.logged_in = True
            self.screen_name = cfg.get("screen_name")
            self.s.cookies.set("auth_token", at, domain=".x.com")
            self.s.cookies.set("ct0", ct0, domain=".x.com")
            self.s.cookies.set("auth_token", at, domain="x.com")
            self._log("[auth] 登录态（config.json / 环境变量）")
        else:
            self._activate()

    def _activate(self):
        r = self.s.post("https://api.x.com/1.1/guest/activate.json",
                        headers={"authorization": f"Bearer {BEARER}",
                                 "content-type": "application/x-www-form-urlencoded"})
        if r.status_code != 200:
            raise SystemExit(f"[xweb] guest 激活失败 HTTP {r.status_code}: {r.text[:200]}")
        self.guest_token = r.json()["guest_token"]
        st = _load_json(STATE, {})
        st.update({"guest_token": self.guest_token, "guest_token_ts": time.time()})
        os.makedirs(CFG_DIR, exist_ok=True)
        with open(STATE, "w") as f:
            json.dump(st, f, indent=1)
        os.chmod(STATE, 0o600)
        self._log(f"[auth] 访客态 guest_token={self.guest_token}")

    def _xclid(self, method, path):
        

        if not self.logged_in or os.environ.get("X_WEB_NO_XCLID"):
            return None
        if self._xclid_gen is None:
            try:
                try:
                    from . import xclid as _x        
                except ImportError:
                    import xclid as _x               
                at = self.s.cookies.get("auth_token")
                ct0 = self.s.cookies.get("ct0")
                self._xclid_gen = _x.XClId(cookie=f"auth_token={at}; ct0={ct0}",
                                           verbose=os.environ.get("X_WEB_VERBOSE_XCLID") == "1").init()
                self._log("[xclid] 签名素材就绪")
            except Exception as e:      
                self._log(f"[xclid] 素材失败（不致命）：{e}")
                self._xclid_gen = False
        if not self._xclid_gen:
            return None
        try:
            return self._xclid_gen.generate(method, path)
        except Exception as e:
            self._log(f"[xclid] 生成失败：{e}")
            return None

    def headers(self, referer="https://x.com/", xclid_for=None):
        h = {"authorization": f"Bearer {BEARER}", "x-twitter-active-user": "yes",
             "x-twitter-client-language": "en", "content-type": "application/json",
             "referer": referer}
        ct0 = self.s.cookies.get("ct0")
        if ct0:
            h["x-csrf-token"] = ct0
        if self.logged_in:
            h["x-twitter-auth-type"] = "OAuth2Session"
        elif self.guest_token:
            h["x-guest-token"] = self.guest_token
        
        if xclid_for:
            sig = self._xclid(xclid_for[0], xclid_for[1])
            if sig:
                h["x-client-transaction-id"] = sig
        return h

    def _log(self, m):
        if self.verbose:
            print(m, file=sys.stderr)

    def _dump_request(self, method, url, headers, params, extra=None):
        
        safe = {}
        for k, v in (headers or {}).items():
            if k.lower() in SECRET_HEADERS:
                s = str(v)
                safe[k] = (s[:8] + "…[REDACTED]" + s[-4:]) if len(s) > 16 else "[REDACTED]"
            else:
                safe[k] = v
        out = {"method": method, "url": url, "headers": safe,
               "params": params or {}, **(extra or {})}
        if DRY_JSON:
            print(json.dumps(out, ensure_ascii=False, indent=1))
            return
        print("── DRY RUN（未发送任何请求）" + "─" * 34)
        for k, v in (extra or {}).items():
            print(f"  {k:9s}: {v}")
        print(f"  {method:9s}: {url}")
        for k, v in safe.items():
            print(f"  {k:9s}: {v}")
        for k, v in (params or {}).items():
            s = str(v)
            print(f"  {k:9s}: {s if len(s) <= 1200 else s[:1200] + '…[' + str(len(s)) + 'B]'}")
        print("─" * 58)

    
    def op_entry(self, op):
        e = self.qids.get(op)
        if isinstance(e, str):
            e = {"id": e}
        if not e or not e.get("id"):
            raise SystemExit(f"[xweb] 未知操作 {op}（跑 refresh_query_ids.py 刷新 op 表）")
        return e

    def gql(self, op, variables, features=None, retries=4, referer="https://x.com/",
            field_toggles=None):
        

        e = self.op_entry(op)
        if e.get("status") in ("guest_404", "login-required") and not self.logged_in and not DRY_RUN:
            raise SystemExit(
                f"[xweb] `{op}` 对访客不可用（该操作对访客不可用，不是 id 失效）。\n"
                f"        要解锁：xweb login --cookie \"auth_token=...; ct0=...\"")
        qid = e["id"]
        alts = [x for x in (e.get("alt") or []) if x]
        cands = [qid] + alts
        
        
        
        
        
        
        
        fsrc = features if features is not None else e.get("features")
        if isinstance(fsrc, dict):
            feats = fsrc
        else:
            feats = FEATURE_SETS.get(fsrc or "FEATURES", FEATURE_SETS.get("FEATURES", {}))
        if DRY_RUN:
            self._dump_request("POST" if e.get("opType") == "mutation" else "GET",
                               f"https://api.x.com/graphql/{qid}/{op}",
                               self.headers(referer),
                               {"variables": json.dumps(variables, ensure_ascii=False),
                                "features": json.dumps(feats, ensure_ascii=False)},
                               extra={"op": op, "queryId": qid,
                                      "alt_ids": alts,
                                      "status": e.get("status") or "unverified",
                                      "mode": "登录态" if self.logged_in else "访客态"})
            return {"data": {}}
        last = None
        
        
        
        
        is_write = (e.get("opType") == "mutation")
        switched = 0            
        for i in range(retries):
            use = cands[i % len(cands)] if len(cands) > 1 else qid
            try:
                if is_write:
                    body = {"variables": variables, "features": feats, "queryId": use}
                    if field_toggles:
                        body["fieldToggles"] = field_toggles
                    r = self.s.post(
                        f"https://api.x.com/graphql/{use}/{op}",
                        headers={**self.headers(referer, ("POST", f"/i/api/graphql/{use}/{op}")),
                                 "Content-Type": "application/json"},
                        json=body)
                else:
                    gparams = {"variables": json.dumps(variables),
                               "features": json.dumps(feats)}
                    if field_toggles:
                        gparams["fieldToggles"] = json.dumps(field_toggles)
                    r = self.s.get(f"https://api.x.com/graphql/{use}/{op}",
                                   headers=self.headers(referer, ("GET", f"/i/api/graphql/{use}/{op}")),
                                   params=gparams)
            except Exception as ex:
                last = f"网络异常 {ex}"
                time.sleep(1.5 * (2 ** i))
                continue
            self._log(f"[gql] {op} HTTP {r.status_code} ({len(r.content)}B)")
            if r.status_code == 200:
                j = r.json()
                if j.get("errors") and not j.get("data"):
                    raise SystemExit(f"[xweb] {op} 业务错误：{json.dumps(j['errors'], ensure_ascii=False)[:300]}")
                return j
            if r.status_code == 422:
                try:
                    _e0 = r.json()["errors"][0]
                    miss = (_e0.get("path") or ["", "?"])[1]
                    _msg = _e0.get("message") or ""
                except Exception:
                    miss, _msg = "?", ""
                
                
                
                alt = _alt_var_name(miss)
                if alt and alt in variables and switched < 2:
                    variables[miss] = variables[alt]
                    switched += 1
                    self._log(f"[gql] 422 缺 `{miss}` → 用 `{alt}` 的值补上重试")
                    continue
                
                
                
                
                
                
                if (not _msg) or ("not provided" in _msg) or ("was not provided" in _msg):
                    raise SystemExit(f"[xweb] {op} 缺必填变量 `{miss}`（422）")
                raise SystemExit(
                    f"[xweb] {op} 422 入参校验失败（**不是缺变量**，path=`{miss}`）\n"
                    f"        服务端原话：{_msg[:300]}")
            if r.status_code == 406:
                
                last = f"406 方法不对（{r.text[:60]}）"
                break
            if r.status_code == 404:
                last = "404（该 op 在此 token 下未注册）"
                time.sleep(0.5)
                continue
            if r.status_code in (401, 403):
                if not self.logged_in:
                    self._log("[gql] 401/403 → 重取 guest token")
                    self._activate()
                    continue
                last = f"HTTP {r.status_code}（登录态失效？用 xweb login 重新填 cookie）"
                break
            if r.status_code == 429:
                last = "429 限流"
                time.sleep(5 * (2 ** i))
                continue
            last = f"HTTP {r.status_code}: {r.text[:150]}"
            time.sleep(1.5 * (2 ** i))
        raise SystemExit(f"[xweb] {op} 重试 {retries} 次仍失败：{last}")

    def get(self, url, params=None, headers=None, retries=4):
        h = headers or self.rest_headers("GET", url)
        if DRY_RUN:
            self._dump_request("GET", url, h, params,
                               extra={"mode": "登录态" if self.logged_in else "访客态"})
            return type("R", (), {"status_code": 200, "json": lambda s=None: {},
                                  "text": "", "content": b""})()
        for i in range(retries):
            r = self.s.get(url, headers=h, params=params)
            self._log(f"[get] {url.split('?')[0]} HTTP {r.status_code}")
            if r.status_code == 200:
                return r
            if r.status_code in (401, 403) and not self.logged_in:
                self._activate()
                h = self.rest_headers("GET", url)
                continue
            time.sleep(1.5 * (2 ** i))
        raise SystemExit(f"[xweb] GET {url.split('?')[0]} 失败 HTTP {r.status_code}")

    def rest_headers(self, method, url, referer="https://x.com/", extra=None):
        

        h = dict(self.headers(referer))
        if extra:
            h.update(extra)
        if self.logged_in and not os.environ.get("X_WEB_NO_XCLID"):
            path = "/" + url.split("://", 1)[-1].split("/", 1)[-1]
            path = path.split("?")[0]
            sig = self._xclid(method, path)
            if not sig:
                
                sig = self._xclid(method, "/i/api" + path)
            if sig:
                h["x-client-transaction-id"] = sig
        return h

    
    def request(self, method, url, params=None, data=None, headers=None,
                referer="https://x.com/", retries=2):
        

        h = self.rest_headers(method, url, referer=referer, extra=headers)
        last, r = None, None
        for i in range(retries):
            try:
                r = self.s.request(method, url, headers=h, params=params, data=data, timeout=30)
            except Exception as e:
                last = f"网络异常 {e}"
                time.sleep(1.2 * (2 ** i))
                continue
            self._log(f"[rest] {method} {url.split('?')[0]} HTTP {r.status_code} ({len(r.content)}B)")
            if r.status_code not in (429, 500, 502, 503, 504):
                return r
            last = f"HTTP {r.status_code}"
            time.sleep(2.0 * (2 ** i))
        if last:
            self._log(f"[rest] {method} {url.split('?')[0]} 重试后仍 {last}")
        return r

    def user_id(self, handle):
        h = _handle(handle)
        j = self.gql("UserByScreenName", {"screenName": h, **PV}, features="USER_FEATURES",
                     referer=f"https://x.com/{h}")
        u = parse_user(user_node(j))
        if not u.get("rest_id"):
            raise SystemExit(f"[xweb] 找不到 @{h}")
        return u

    def widget_tweets(self, handle):
        
        try:
            r = self.s.get(f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{handle}",
                           headers={"User-Agent": UA, "Accept": "text/html"}, timeout=30)
            if r.status_code == 429:
                return [], "429 限流（该端点限速紧，等几分钟）"
            if r.status_code != 200:
                return [], f"HTTP {r.status_code}"
            m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
            if not m:
                return [], "无 __NEXT_DATA__"
            data = json.loads(m.group(1))
        except Exception as e:
            return [], f"异常 {str(e)[:50]}"
        items, out, seen = [], [], set()
        stack = [data]
        while stack:
            o = stack.pop()
            if isinstance(o, dict):
                if "full_text" in o and (o.get("id_str") or o.get("id")):
                    items.append(o)
                stack.extend(o.values())
            elif isinstance(o, list):
                stack.extend(o)
        for it in items:
            tid = it.get("id_str") or it.get("id")
            if not tid or str(tid) in seen:
                continue
            seen.add(str(tid))
            u = it.get("user") or {}
            out.append({"id": str(tid), "text": it.get("full_text") or it.get("text") or "",
                        "lang": it.get("lang") or "", "created_at": _dt(it.get("created_at")),
                        "likes": _num(it.get("favorite_count")), "retweets": _num(it.get("retweet_count")),
                        "replies": _num(it.get("reply_count")), "quotes": _num(it.get("quote_count")),
                        "bookmarks": None, "views": None,
                        "author": {"screen_name": u.get("screen_name"), "name": u.get("name"),
                                   "rest_id": u.get("id_str"), "verified": bool(u.get("verified"))},
                        "media": [{"type": mm.get("type"),
                                   "url": mm.get("media_url_https"),
                                   "alt": mm.get("ext_alt_text")}
                                  for mm in ((it.get("extended_entities") or it.get("entities") or {})
                                             .get("media") or [])],
                        "hashtags": [], "url": f"https://x.com/{u.get('screen_name')}/status/{tid}",
                        "quoted": bool(it.get("is_quote_status")),
                        "is_retweet": bool(it.get("retweeted_status_id_str")
                                           or (it.get("full_text") or "").startswith("RT @")),
                        "source": "widget"})
        return out, (None if out else "解析到 0 条")

def p_user(u, as_json=False):
    if as_json:
        print(json.dumps(u, ensure_ascii=False, indent=1))
        return
    print(f"{u.get('name')}  (@{u.get('screen_name')}){'  ✔' if u.get('verified') else ''}")
    if u.get("bio"):
        print(f"  {u['bio'][:300]}")
    if u.get("location"):
        print(f"  位置: {u['location']}")
    line = (f"  关注 {fmt_n(u.get('following'))} · 粉丝 {fmt_n(u.get('followers'))} · "
            f"推文 {fmt_n(u.get('tweets'))}")
    if u.get("created_at"):
        line += f" · 加入 {u['created_at']}"
    print(line)
    print(f"  ID {u.get('rest_id')}   {u.get('url')}")

def p_tweet(t, indent="· "):
    mark = "  [转推]" if t.get("is_retweet") else ""
    print(f"{indent}{t.get('created_at')}  ♥{fmt_n(t.get('likes'))} ↻{fmt_n(t.get('retweets'))} "
          f"💬{fmt_n(t.get('replies'))} 👁{fmt_n(t.get('views'))}{mark}")
    for line in (t.get("text") or "").splitlines():
        print(f"{indent}  {line}")
    for m in (t.get("media") or [])[:3]:
        print(f"{indent}  [{m.get('type')}] {m.get('url')}")
    print(f"{indent}  {t.get('url')}")
    print()

def p_tweets(tweets, as_json=False, label="", extra=None, warn="", stale_warn=True):
    if as_json:
        d = {"count": len(tweets), "tweets": tweets}
        if extra:
            d.update(extra)
        if warn:
            
            
            d["warn"] = warn
        print(json.dumps(d, ensure_ascii=False, indent=1))
        return
    if label:
        print(label)
    if warn:
        print(warn)
    if stale_warn and tweets:
        newest = max((t.get("created_at") for t in tweets if t.get("created_at")), default=None)
        d = _age_days(newest)
        if d is not None and d > 30:
            print(f"⚠️ 最新一条距今 {d} 天（{newest[:10]}）。访客时间线可能只有较早的采样，"
                  f"**不能据此判断该账号停更**；要确认最近动态需登录态。")
    print()
    for t in tweets:
        p_tweet(t)

TABLE_COOKIES = ("auth_token", "ct0")

def _extract_cookie_blob(blob: str) -> dict:
    

    text = blob or ""
    vals: dict = {}
    
    
    
    
    masked: list = []
    pos = 0
    for line in text.splitlines():
        parts = [p.strip().strip('"') for p in
                 re.split(r"\t+|\s{3,}", line.strip()) if p.strip()]
        if len(parts) >= 2 and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", parts[0]):
            if parts[0] in TABLE_COOKIES:
                vals.setdefault(parts[0], parts[1])
            off = text.find(parts[1], pos)
            if off >= 0:
                masked.append((off, off + len(parts[1])))
                pos = off + len(parts[1])
    for m in re.finditer(r"([A-Za-z_][A-Za-z0-9_]*)=([^;\s'\"]+)", text):
        if any(s <= m.start() < e for s, e in masked):
            continue
        vals.setdefault(m.group(1), m.group(2).strip())
    return vals

def cmd_login(a, c=None):
    os.makedirs(CFG_DIR, exist_ok=True)
    if a.logout:
        for p in (CONFIG, STATE):
            if os.path.exists(p):
                os.remove(p)
        print("✓ 已清除本地凭据")
        return
    blob = a.cookie or ""
    if getattr(a, "cookie_file", None):
        try:
            with open(a.cookie_file, encoding="utf-8") as fh:
                blob += "\n" + fh.read()
        except OSError as e:
            sys.exit(f"✗ 读不了 --cookie-file：{e}")
    if not blob.strip() and not (a.auth_token and a.ct0):
        print("用法（推荐，从浏览器 DevTools 复制整条 Cookie）：")
        print('  xweb login --cookie "auth_token=xxx; ct0=yyy"')
        print("  或  xweb login --cookie-file cookie.txt        # 整段粘进文件也行")
        print("  或  xweb login --auth-token xxx --ct0 yyy")
        print("\n拿 cookie 的步骤见 README「登录态」一节（约 30 秒）。")
        sys.exit(1)
    kv = _extract_cookie_blob(blob)
    at = (a.auth_token or kv.get("auth_token") or "").strip()
    ct0 = (a.ct0 or kv.get("ct0") or "").strip()
    cfg = {"auth_token": at, "ct0": ct0}
    
    if len(at) < 30 or len(ct0) < 30:
        print(f"⚠️ token 偏短（auth_token {len(at)} 字符 / ct0 {len(ct0)} 字符）"
              f"—— 常见于复制不全。若下一步验证失败，先怀疑这里。")
    with open(CONFIG, "w") as f:
        json.dump(cfg, f, indent=1)
    os.chmod(CONFIG, 0o600)
    print("✓ 已保存凭据（600）")
    
    cc = X(verbose=a.verbose)
    if not cc.logged_in:
        sys.exit("✗ cookie 未被识别（auth_token/ct0 可能不完整）")
    try:
        j = cc.gql("Viewer", {}, features="FEATURES")
        u = parse_users_from(j)
        if u:
            cc.screen_name = u[0].get("screen_name")
            cfg["screen_name"] = cc.screen_name
            with open(CONFIG, "w") as f:
                json.dump(cfg, f, indent=1)
            os.chmod(CONFIG, 0o600)
            print(f"✓ 登录成功：@{cc.screen_name}（{u[0].get('name')}）")
    except SystemExit:
        pass
    if not cc.screen_name:
        try:
            u = cc.user_id("twitter")
            print(f"✓ cookie 可用（Viewer 未解析到身份，但请求已通过认证）")
        except SystemExit as e:
            print(f"⚠️ cookie 可能无效：{e}")
    print("\n当前可用：" + ("全部操作" if cc.logged_in else "访客可用部分"))
    print("跑 `xweb caps` 看逐项状态。")

def cmd_user(a, c):
    p_user(c.user_id(a.handle), a.json)

def cmd_tweets(a, c):
    u = c.user_id(a.handle)
    op = {"tweets": "UserTweets", "replies": "UserTweetsAndReplies", "media": "UserMedia",
          "originals": "UserOriginalsTimeline", "photos": "UserPhotoTimeline",
          "videos": "UserVideoTimeline", "reposts": "UserRepostsTimeline",
          "articles": "UserArticlesTweets", "likes": "Likes"}[a.tab]
    var = {"userId": str(u["rest_id"]), "count": max(a.n, 20), "includePromotedContent": False,
           "withQuickPromoteEligibilityTweetFields": True, "withVoice": True}
    if op == "UserTweetsAndReplies":
        var["withCommunity"] = True
    if op == "Likes":
        var.update({"withClientEventToken": True, "withBirdwatchNotes": False})
    ref = f"https://x.com/{u['screen_name']}"

    
    
    
    
    j = c.gql(op, var, referer=ref)
    tweets, cur = timeline_tweets(j)
    pages_used, paginated = 1, False
    seen = {t["id"] for t in tweets}
    while a.pages > pages_used and cur:
        v2 = dict(var)
        v2["cursor"] = cur
        try:
            j2 = c.gql(op, v2, referer=ref)
        except SystemExit:
            break
        t2, cur = timeline_tweets(j2)
        fresh = [t for t in t2 if t["id"] not in seen]
        if not fresh:
            break
        for t in fresh:
            seen.add(t["id"])
            tweets.append(t)
        pages_used += 1
        paginated = True
        time.sleep(1.2)

    srcs, warn = ["graphql"], ""
    if a.pages > 1 and not paginated and not cur:
        warn = ("⚠️ 该账号的访客时间线是**封顶快照**（无游标，翻不动）—— "
                "不是「没有更多」。要全量历史需登录态。")
    if a.widget:
        w, err = c.widget_tweets(u["screen_name"])
        if err:
            warn = f"⚠️ 嵌入视图未取到：{err}（已用主通道结果继续，**不是「没有推文」**）"
        else:
            have = {t["id"] for t in tweets}
            for t in w:
                if t["id"] not in have:
                    tweets.append(t)
                    have.add(t["id"])
            srcs.append(f"widget({len(w)}条)")
    tweets.sort(key=lambda t: t.get("created_at") or "", reverse=True)
    if paginated:
        srcs.append(f"{pages_used}页")
    if not tweets:
        sys.exit(f"[xweb] @{u['screen_name']} 在 tab={a.tab} 下没取到推文"
                 f"（该操作对访客不可用，不代表账号没发）")
    p_tweets(tweets[:a.n], a.json,
             label=f"@{u['screen_name']} 最近 {min(len(tweets), a.n)} 条（tab={a.tab}，"
                   f"来源 {'+'.join(srcs)}；访客态为精选集合，非严格时间序，已按时间倒排）：",
             extra={"user": u["screen_name"], "tab": a.tab, "cursor": cur,
                    "pages": pages_used, "total_fetched": len(tweets)}, warn=warn)

def cmd_tweet(a, c):
    tid = _tid(a.target)
    if not tid.isdigit():
        sys.exit(f"[xweb] 无法从 `{a.target}` 解析推文 ID")
    
    
    if c.logged_in:
        try:
            j = c.gql("TweetResultByRestId", _TWEET_RESULT_VARS(tid),
                      features="TWEET_RESULT_BY_REST_ID_FEATURES")
            tweets, _ = timeline_tweets(j)
            if tweets:
                if a.json:
                    print(json.dumps(tweets[0], ensure_ascii=False, indent=1))
                else:
                    t = tweets[0]
                    print(f"{t['author'].get('name')} (@{t['author'].get('screen_name')}) · {t['created_at']}")
                    print(f"\n{t['text']}\n")
                    print(f"♥{fmt_n(t['likes'])} ↻{fmt_n(t['retweets'])} "
                          f"💬{fmt_n(t['replies'])} 👁{fmt_n(t['views'])}")
                    print(t["url"])
                return
        except SystemExit:
            pass
    
    try:
        r = c.s.get("https://cdn.syndication.twimg.com/tweet-result", params={"id": tid, "token": "a"},
                    headers={"User-Agent": UA, "Accept": "application/json"})
        if r.status_code == 200 and r.text.strip().startswith("{"):
            s = r.json()
            if s.get("text") is not None:
                out = {"id": s.get("id_str"), "text": s.get("text"), "lang": s.get("lang"),
                       "created_at": _dt(s.get("created_at")), "likes": _num(s.get("favorite_count")),
                       "author": {"screen_name": (s.get("user") or {}).get("screen_name"),
                                  "name": (s.get("user") or {}).get("name"),
                                  "verified": (s.get("user") or {}).get("is_blue_verified")},
                       "media": [{"type": m.get("type"), "url": m.get("media_url_https")}
                                 for m in (s.get("mediaDetails") or [])],
                       "url": f"https://x.com/i/status/{tid}", "source": "syndication"}
                if a.json:
                    print(json.dumps(out, ensure_ascii=False, indent=1))
                else:
                    au = out["author"]
                    print(f"{au.get('name')} (@{au.get('screen_name')}) · {out['created_at']} "
                          f"· ♥{fmt_n(out['likes'])}")
                    print(f"\n{out['text']}\n")
                    for m in out["media"]:
                        print(f"[{m['type']}] {m['url']}")
                    print(out["url"])
                return
    except Exception:
        pass
    
    j = c.gql("TweetResultByRestId", _TWEET_RESULT_VARS(tid),
              features="TWEET_RESULT_BY_REST_ID_FEATURES")
    tweets, _ = timeline_tweets(j)
    if not tweets:
        sys.exit(f"[xweb] 推文 {tid} 不可用（删除/受限/需登录）")
    if a.json:
        print(json.dumps(tweets[0], ensure_ascii=False, indent=1))
    else:
        t = tweets[0]
        print(f"{t['author'].get('name')} (@{t['author'].get('screen_name')}) · {t['created_at']}")
        print(f"\n{t['text']}\n")
        print(f"♥{fmt_n(t['likes'])} ↻{fmt_n(t['retweets'])} 💬{fmt_n(t['replies'])} 👁{fmt_n(t['views'])}")
        print(t["url"])

def _detail_ids(a) -> list[str]:
    
    raw = []
    f = getattr(a, "ids_file", None)
    if f:
        try:
            with open(f, encoding="utf-8") as fh:
                raw += [ln.strip() for ln in fh if ln.strip() and not ln.lstrip().startswith("#")]
        except OSError as e:
            sys.exit(f"[xweb] 读不了 --ids-file：{e}")
    if getattr(a, "target", None):
        raw += [x for x in re.split(r"[,\s]+", a.target) if x]
    seen, out = set(), []
    for s in raw:
        t = _tid(s)
        if t.isdigit() and t not in seen:
            seen.add(t)
            out.append(t)
    return out

def _detail_one(c, tid, verbose=False):
    

    got = None
    if c.logged_in:
        got = c.gql("TweetDetail", {"focalTweetId": tid, "with_rux_injections": False,
                                    "includePromotedContent": True, "withCommunity": True,
                                    "withQuickPromoteEligibilityTweetFields": True,
                                    "withBirdwatchNotes": True, "withVoice": True},
                    features="FEATURES")
    focal, cur, replies = None, None, []
    if got is not None:
        tws, cur = timeline_tweets(got)
        if tws:
            focal = next((t for t in tws if t["id"] == tid), tws[0])
            replies = [t for t in tws if t["id"] != tid]
    if focal is None:
        ssr, err = fetch_page_ssr(c, f"https://x.com/i/status/{tid}", None)
        focal = fetch_tweet_public(c, tid) or fetch_og_tweet(c, tid)
        replies = [t for t in ssr if t["id"] != tid]
        if verbose:
            print("[来源] 焦点=公开 CDN ／ 回复=详情页", file=sys.stderr)
        if focal is None and not replies:
            return None, [], None, (err or "syndication 也失败")
    return focal, replies, cur, None

def cmd_detail(a, c):
    ids = _detail_ids(a)
    if not ids:
        sys.exit("[xweb] 没给推文 ID（位置参数或 --ids-file）")

    if len(ids) == 1:
        tid = ids[0]
        focal, replies, cur, err = _detail_one(c, tid, a.verbose)
        if err:
            sys.exit(f"[xweb] 详情取不到：{err}\n"
                     f"        （登录态可解锁 GraphQL TweetDetail，字段最全）")
        if a.json:
            print(json.dumps({"focal": focal, "replies": replies[:a.n], "cursor": cur},
                             ensure_ascii=False, indent=1))
            return
        print("【主推】")
        p_tweet(focal)
        if replies:
            print(f"【回复/引用 {len(replies[:a.n])} 条】\n")
            for t in replies[:a.n]:
                p_tweet(t, indent="└ ")
        return

    
    from concurrent.futures import ThreadPoolExecutor, as_completed
    res = {}
    with ThreadPoolExecutor(max_workers=max(1, min(a.concurrency, 8))) as ex:
        futs = {ex.submit(_detail_one, c, t, False): t for t in ids}
        for fu in as_completed(futs):
            t = futs[fu]
            try:
                f, r, cu, e = fu.result()
            except Exception as exc:                      
                f, r, e = None, [], f"{type(exc).__name__}: {exc}"
            res[t] = {"focal": f, "replies": r[:a.n], "error": e}
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return
    ok = sum(1 for v in res.values() if v["focal"] or v["replies"])
    print(f"批量详情 {len(ids)} 条：有内容 {ok} · 取不到 {len(ids) - ok}\n")
    for tid in ids:
        v = res.get(tid) or {}
        print("─" * 58)
        print(f"ID {tid}   https://x.com/i/status/{tid}")
        if not v.get("focal") and not v.get("replies"):
            print(f"  ❌ {v.get('error') or '无内容'}")
            continue
        if v.get("focal"):
            p_tweet(v["focal"])
        else:
            print("  （焦点推文未渲染，仅拿到回复串）")
        if v.get("replies"):
            print(f"  └ 回复/引用 {len(v['replies'])} 条：")
            for t in v["replies"][:3]:
                p_tweet(t, indent="    └ ")

def _tl_cmd(a, c, op, var, label, feats="FEATURES"):
    j = c.gql(op, var, features=feats)
    tweets, cur = timeline_tweets(j)
    p_tweets(tweets[:a.n], a.json, label=f"{label}（{len(tweets[:a.n])} 条）：",
             extra={"cursor": cur}, stale_warn=False)

def cmd_search(a, c):
    prod = a.product
    _tl_cmd(a, c, "SearchTimeline",
            {"rawQuery": a.query, "count": max(a.n, 20), "querySource": "typed_query",
             "product": prod},
            f"搜索「{a.query}」product={prod}")

def cmd_home(a, c):
    op = "HomeLatestTimeline" if a.latest else "HomeTimeline"
    _tl_cmd(a, c, op, {"count": max(a.n, 20), "includePromotedContent": False,
                       "latestControlAvailable": True, "withCommunity": True},
            f"首页时间线({'最新' if a.latest else '推荐'})")

def cmd_bookmarks(a, c):
    _tl_cmd(a, c, "Bookmarks", {"count": max(a.n, 20), "includePromotedContent": False}, "我的收藏")

def cmd_notifications(a, c):
    
    
    
    _tl_cmd(a, c, "NotificationsTimeline", {"timeline_type": "All", "count": max(a.n, 20),
                                            "includePromotedContent": False,
                                            "withCommunity": True, "withVoice": True}, "通知")

def cmd_relations(a, c):
    

    u = c.user_id(a.handle)
    op = "Followers" if a.followers else "Following"
    label = "粉丝" if a.followers else "关注"
    
    
    
    if not c.logged_in and not DRY_RUN:
        sys.exit("[xweb] 关注/粉丝列表需要登录态（X 对访客回 404 空 body，**不是**对方没数据/没关注人）：\n"
                 "      xweb login --cookie \"auth_token=...; ct0=...\"   # 步骤见 README「登录态」")
    j = c.gql(op, {"userId": str(u["rest_id"]), "count": max(a.n, 100),
                   "includePromotedContent": False}, features="FEATURES")
    users = parse_users_from(j)
    shown = users[:a.n]
    declared = u.get("followers" if a.followers else "following")
    if len(users) > len(shown):
        extra = f"，资料页标 {label} {fmt_n(declared)}" if declared is not None else ""
        print(f"⚠️ 只渲染前 {len(shown)} 条，实际取到 {len(users)} 条{extra}"
              f" —— 这不是「只有这么多」，是渲染上限（-n）。要看全部：-n {len(users)}",
              file=sys.stderr)
    if a.json:
        print(json.dumps(shown, ensure_ascii=False, indent=1))
        return
    head = f"（共 {len(users)} 个" + (f"，显示前 {len(shown)}" if len(users) > len(shown) else "") + "）"
    print(f"@{u['screen_name']} 的{label}{head}：\n")
    for x in shown:
        print(f"  {x.get('name')}  (@{x.get('screen_name')})  粉丝 {fmt_n(x.get('followers'))}"
              f"{'  ✔' if x.get('verified') else ''}")

SNOWFLAKE_EPOCH = 1288834974657

def _alt_var_name(n: str):
    

    if not n or n == "?":
        return None
    if "_" in n:
        head, *rest = n.split("_")
        return head + "".join(p.title() for p in rest)
    return re.sub(r"(?<!^)(?=[A-Z])", "_", n).lower()

def snf_time(tid) -> str:
    
    try:
        ms = (int(tid) >> 22) + SNOWFLAKE_EPOCH
        return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""

def snf_ts(tid) -> float:
    try:
        return ((int(tid) >> 22) + SNOWFLAKE_EPOCH) / 1000
    except Exception:
        return 0.0

ENGINES = [
    ("duckduckgo", "https://html.duckduckgo.com/html/", "q"),
    ("bing", "https://www.bing.com/search", "q"),
    ("brave", "https://search.brave.com/search", "q"),
    ("startpage", "https://www.startpage.com/sp/search", "query"),
    ("mojeek", "https://www.mojeek.com/search", "q"),
    ("yandex", "https://yandex.com/search/", "text"),
]

BLOCK_CODES = (202, 403, 429, 503)
INDEX_FILE = os.path.join(CFG_DIR, "tweet_index.json")

def _load_index():
    return _load_json(INDEX_FILE, {})

def _save_index(idx):
    try:
        os.makedirs(CFG_DIR, exist_ok=True)
        with open(INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False, indent=1)
    except Exception:
        pass

def search_engine_ids(handle, c, pages=2, log=None, pace=2.0, max_engines=4):
    

    ids, notes = set(), []
    for name, url, qk in ENGINES[:max_engines]:
        got = 0
        blocked = None
        for p in range(pages):
            try:
                params = {qk: f"site:x.com/{handle}/status"}
                if name == "bing":
                    params["first"] = str(p * 10 + 1)
                elif name in ("duckduckgo", "mojeek"):
                    params["s"] = str(p * 10)
                r = c.s.get(url, params=params, headers={"User-Agent": UA},
                            timeout=25, allow_redirects=True)
            except Exception as e:
                blocked = f"异常 {str(e)[:38]}"
                break
            body = r.text
            if r.status_code in BLOCK_CODES:
                blocked = f"HTTP {r.status_code}（限流/挑战，已放弃该引擎）"
                break
            if r.status_code != 200:
                blocked = f"HTTP {r.status_code}"
                break
            low = body.lower()
            if "captcha" in low or "unusual traffic" in low or "are you a robot" in low \
                    or "verifying your browser" in low or "security check" in low \
                    or "enable javascript and cookies" in low:
                blocked = "CAPTCHA/JS 挑战页"
                break
            found = set(re.findall(rf'x\.com/{re.escape(handle)}/status/(\d{{15,25}})', body))
            found |= set(re.findall(rf'%2F{re.escape(handle)}%2Fstatus%2F(\d{{15,25}})', body))
            fresh = found - ids
            if not fresh:
                break
            ids |= found
            got += len(fresh)
            time.sleep(pace)
        notes.append(f"{name}: " + (f"+{got} 条" if got else f"0 条（{blocked or '无结果'}）"))
        if log:
            log(notes[-1])
        time.sleep(pace)
    return sorted(ids, key=snf_ts, reverse=True), notes

def fetch_tweet_public(c, tid):
    

    if c.logged_in:
        try:
            j = c.gql("TweetResultByRestId", _TWEET_RESULT_VARS(tid),
                      features="TWEET_RESULT_BY_REST_ID_FEATURES")
            tws, _ = timeline_tweets(j)
            if tws:
                return tws[0]
        except SystemExit:
            pass
    try:
        r = c.s.get("https://cdn.syndication.twimg.com/tweet-result",
                    params={"id": tid, "token": "x"},
                    headers={"User-Agent": UA, "Accept": "application/json"}, timeout=25)
        if r.status_code == 200 and r.text.strip().startswith("{"):
            s = r.json()
            if s.get("text") is not None:
                return {"id": str(s.get("id_str") or tid), "text": s.get("text"),
                        
                        
                        "text_truncated": len(s.get("text") or "") >= 270,
                        "created_at": _dt(s.get("created_at")) or snf_time(tid),
                        "likes": _num(s.get("favorite_count")),
                        "retweets": _num(s.get("retweet_count")),
                        "replies": _num(s.get("conversation_count")),
                        "lang": s.get("lang"),
                        "author": {"screen_name": (s.get("user") or {}).get("screen_name"),
                                   "name": (s.get("user") or {}).get("name"),
                                   "verified": (s.get("user") or {}).get("is_blue_verified")},
                        "media": [{"type": m.get("type"), "url": m.get("media_url_https")}
                                  for m in (s.get("mediaDetails") or [])],
                        "url": f"https://x.com/i/status/{tid}", "source": "syndication"}
    except Exception:
        pass
    try:
        r = c.s.get(f"https://api.fxtwitter.com/i/status/{tid}",
                    headers={"User-Agent": UA}, timeout=20)
        if r.status_code == 200:
            j = r.json()
            t = j.get("tweet") or {}
            if t.get("text"):
                return {"id": str(t.get("id") or tid), "text": t.get("text"),
                        "text_truncated": len(t.get("text") or "") >= 270,
                        "created_at": _dt(t.get("created_at")) or snf_time(tid),
                        "likes": _num((t.get("likes") or 0)), "retweets": _num((t.get("retweets") or 0)),
                        "replies": _num((t.get("replies") or 0)),
                        "lang": t.get("lang"),
                        "author": {"screen_name": (t.get("author") or {}).get("screen_name"),
                                   "name": (t.get("author") or {}).get("name"),
                                   "verified": (t.get("author") or {}).get("is_blue_verified")},
                        "media": [], "url": f"https://x.com/i/status/{tid}", "source": "fxtwitter"}
    except Exception:
        pass
    return None

def _js_unescape(s: str) -> str:
    
    def rep(m):
        c = m.group(1)
        return {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/"}.get(
            c, c if c != "u" else "u")
    s = re.sub(r"\\(u[0-9a-fA-F]{4}|.)", lambda m: rep(m), s)
    s = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), s)
    return s

def parse_profile_ssr(html: str, handle: str | None):
    

    who = re.escape(handle) if handle else r'[^/"]+'
    marks = [(m.start(), m.end(), m.group(1), m.group(2)) for m in
             re.finditer(rf'data-href="/({who})/status/(\d{{15,25}})"', html, re.I)]
    
    out, seen = [], set()
    for i, (start, hend, author, tid) in enumerate(marks):
        if tid in seen:
            continue
        seen.add(tid)
        end = marks[i + 1][0] if i + 1 < len(marks) else min(len(html), start + 16000)
        seg = html[hend:end]                 

        
        eng: dict[str, int | None] = {"replies": None, "retweets": None,
                                      "likes": None, "views": None}
        key = {"Reply": "replies", "Repost": "retweets", "Like": "likes", "View count": "views"}
        labels = list(re.finditer(r'aria-label="(Reply|Repost|Like|View count)"', seg))
        cut = len(seg)
        for j, m in enumerate(labels):
            k = key[m.group(1)]
            part = seg[m.start():(labels[j + 1].start() if j + 1 < len(labels) else len(seg))]
            v = re.search(r'data-animated-count-visual="true"[^>]*>([^<]+)<', part)
            if v:
                eng[k] = _parse_count(v.group(1))
        if labels:
            
            
            lt = seg.rfind("<", 0, labels[0].start())
            cut = lt if lt > 0 else labels[0].start()

        
        head = seg[:cut]
        plain = re.sub(r"<script.*?</script>", " ", head, flags=re.S)
        plain = H.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", plain))).strip()
        
        plain = re.sub(r"^[^<>]{0,300}>", "", plain).strip()
        
        datepat = (r"(?:\d{1,2}[smhd]|now|yesterday|"
                   r"[A-Z][a-z]{2} \d{1,2}(?:, \d{4})?|\d{4}-\d{2}-\d{2})")
        
        
        hits = list(re.finditer(rf'@{re.escape(author)}\s+({datepat})\s*', plain, re.I))
        body = plain[hits[0].end():].strip() if hits else plain

        
        
        text = body
        for L in (40, 30, 20, 12, 6, 3):
            if len(body) < L:
                continue
            anchor = re.sub(r"\s+", " ", body)[:L]
            best = None
            for fm in re.finditer(r'full_text:"((?:[^"\\]|\\.)*)"', html):
                cand = _js_unescape(fm.group(1))
                if not cand.startswith(anchor):
                    continue
                if L <= 6 and len(cand) > 200:      
                    continue
                if best is None or len(cand) > len(best):
                    best = cand
            if best:
                text = best
                break

        media = sorted(set(re.findall(
            r'https://pbs\.twimg\.com/(?:media|ext_tw_video_thumb|amplify_video_thumb)/[A-Za-z0-9_\-]+\.(?:jpg|png|gif)', seg)))

        out.append({
            "id": tid,
            "created_at": snf_time(tid),                 
            "created_at_ms": int(datetime.strptime(snf_time(tid) + " UTC", "%Y-%m-%d %H:%M UTC")
                                 .replace(tzinfo=timezone.utc).timestamp() * 1000) if snf_time(tid) else None,
            "text": text,
            "likes": eng["likes"], "retweets": eng["retweets"],
            "replies": eng["replies"], "views": eng["views"],
            "media": [{"type": "photo", "url": u} for u in media],
            "author": {"screen_name": author, "rest_id": None},
            "url": f"https://x.com/{author}/status/{tid}",
            "source": "ssr-page",
        })
    out.sort(key=lambda t: t["id"], reverse=True)        
    return out

def _parse_count(s: str):
    
    s = s.strip().replace(",", "")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([KM])?", s)
    if not m:
        return None
    n = float(m.group(1))
    return int(n * {"K": 1e3, "M": 1e6}.get(m.group(2) or "", 1))

def fetch_profile_ssr(c, handle):
    
    return fetch_page_ssr(c, f"https://x.com/{handle}", handle)

def fetch_page_ssr(c, url, handle=None):
    

    try:
        r = c.s.get(url, headers={"User-Agent": UA, "Accept": "text/html"}, timeout=30)
    except Exception as e:
        return [], f"请求异常 {str(e)[:60]}"
    if r.status_code == 403:
        return [], "HTTP 403（指纹被拦，试 X_WEB_IMPERSONATE=firefox135）"
    if r.status_code != 200:
        return [], f"HTTP {r.status_code}"
    tws = parse_profile_ssr(r.text, handle)
    if not tws:
        hint = "（该路由是登录墙空壳，或该内容对访客不开放）" if len(r.text) > 250000 else ""
        return [], f"页面 {len(r.text)}B 但没解析到卡片{hint}"
    return tws, None

def fetch_og_tweet(c, tid):
    

    try:
        r = c.s.get(f"https://x.com/i/status/{tid}",
                    headers={"User-Agent": UA, "Accept": "text/html"}, timeout=25)
        h = r.text

        def meta(k):
            m = re.search(rf'<meta[^>]+(?:property|name)="{k}"[^>]+content="([^"]*)"', h)
            return H.unescape(m.group(1)) if m else None
        desc = meta("og:description")
        title = meta("og:title")
        if not desc:
            return None
        au = None
        if title:
            m = re.search(r"@([A-Za-z0-9_]{1,20})", title)
            au = m.group(1) if m else None
        return {"id": str(tid), "text": desc, "created_at": snf_time(tid),
                "likes": None, "retweets": None, "replies": None, "views": None,
                "lang": None, "media": [],
                "author": {"screen_name": au, "name": (title or "").split(" (@")[0] or None},
                "url": f"https://x.com/{au or 'i'}/status/{tid}",
                "source": "og-meta", "note": "正文可能被 og 截断"}
    except Exception:
        return None

def cmd_latest(a, c):
    

    h = _handle(a.handle)
    u = c.user_id(h)
    idx = _load_index()
    bag = idx.setdefault(h, {"ids": {}, "texts": {}, "updated": None})

    
    ssr, ssr_err = ([], "已跳过(--no-ssr)") if a.no_ssr else fetch_profile_ssr(c, h)

    ext = []
    if a.ids:
        ext = re.findall(r"\d{15,25}", a.ids)
    if a.ids_file:
        try:
            with open(a.ids_file, encoding="utf-8") as f:
                ext += re.findall(r"\d{15,25}", f.read())
        except Exception as e:
            sys.exit(f"[xweb] 读 --ids-file 失败：{e}")

    
    if not a.json:
        print(f"@{u['screen_name']}  {u.get('name')}  粉丝 {fmt_n(u.get('followers'))} · "
              f"推文 {fmt_n(u.get('tweets'))} · 建立 {u.get('created_at')}")
        print("（资料来自 GraphQL，**实时**；推文走 SSR 页面 → 备选：缓存/外部ID/搜索引擎）\n")

    def _eng_log(m, _v=a.verbose):
        if _v:
            print(f"  [引擎] {m}", file=sys.stderr)

    
    gql_tws, gql_top = [], None
    try:
        j = c.gql("UserTweets", {"userId": str(u["rest_id"]), "count": 20,
                                 "includePromotedContent": False,
                                 "withQuickPromoteEligibilityTweetFields": True,
                                 "withVoice": True})
        gql_tws, _ = timeline_tweets(j)
        gql_top = gql_tws[0]["created_at"] if gql_tws else None
    except SystemExit:
        pass

    
    if ssr:
        for t in ssr:
            bag["ids"].setdefault(str(t["id"]), None)
            bag["texts"][str(t["id"])] = t
        bag["updated"] = datetime.now(timezone.utc).isoformat()[:19]
        _save_index(idx)
        picked = ssr[:a.n]
        if a.json:
            print(json.dumps(picked, ensure_ascii=False, indent=1))
            return
        newest = picked[0]["created_at"] if picked else "?"
        lag = None
        if gql_top and newest:
            try:
                lag = (datetime.strptime(newest[:10], "%Y-%m-%d")
                       - datetime.strptime(gql_top[:10], "%Y-%m-%d")).days
            except Exception:
                pass
        print("来源：x.com 公开页面")
        print(f"拿到 {len(ssr)} 条；**最新 {newest}（UTC）**")
        print()
        for t in picked:
            print(f"· {t['created_at']}  ♥{fmt_n(t.get('likes'))} "
                  f"↻{fmt_n(t.get('retweets'))} 💬{fmt_n(t.get('replies'))} "
                  f"👁{fmt_n(t.get('views'))}")
            print(f"  {(t.get('text') or '').strip()}")
            for m in t.get("media") or []:
                print(f"  [{m.get('type')}] {m.get('url')}")
            print(f"  {t['url']}")
            print()
        print(f"共 {len(picked)} 条。**最新到 {newest}（UTC）**。")
        print(f"（已缓存到 {INDEX_FILE}）")
        return

    
    if a.verbose or True:
        
        print(f"⚠️ 页面通道未取到：{ssr_err}",
              file=sys.stderr if a.json else sys.stdout)
        print("   改用备用通道 …\n",
              file=sys.stderr if a.json else sys.stdout)

    
    found, notes = [], []
    if not a.no_search and not a.cache_only:
        found, notes = search_engine_ids(h, c, pages=a.pages, log=_eng_log)

    for k in found + [str(x) for x in ext if str(x) in bag.get("ids", {})] + list(bag["ids"]):
        bag["ids"].setdefault(str(k), None)
    if found:
        bag["updated"] = datetime.now(timezone.utc).isoformat()[:19]
    for x in ext:
        bag["ids"].setdefault(str(x), None)
    _save_index(idx)

    allids = sorted(set(bag["ids"]) | set(found) | set(ext), key=snf_ts, reverse=True)
    if not allids:
        if a.json:
            
            print(json.dumps({"tweets": [], "ids": [],
                              "reason": notes or ["已关闭搜索引擎且缓存为空"],
                              "hint": "这不能说明他没发新推；可用 --ids 直接喂 ID"}, 
                             ensure_ascii=False, indent=1))
            return
        print("⚠️ 没有拿到任何推文 ID。具体原因：")
        for n in notes:
            print(f"   · {n}")
        if not notes:
            print("   · 已关闭搜索引擎（--no-search / --cache-only）且缓存为空")
        if gql_top:
            print(f"   已有时间线快照最新：{gql_top}（不代表账号停更）")
        print("\n   **这不能说明他没发新推。** 可以：")
        print("   · 把任意来源的推文链接/ID 直接喂进来：xweb latest %s --ids <id1>,<id2>" % h)
        print("   · 或稍后再跑")
        return

    with human_only(a):          
        if a.verbose:
            for n in notes:
                print(f"  [引擎] {n}", file=sys.stderr)
        print(f"ID 池 {len(allids)} 条（缓存 {len(bag['ids'])} + 本轮新抓 {len(found)} "
              f"+ 外部喂入 {len(ext)}）；最新 {snf_time(allids[0])}，最旧 {snf_time(allids[-1])}")
        if gql_top and snf_time(allids[0]) > gql_top:
            try:
                lag = (datetime.strptime(snf_time(allids[0])[:10], "%Y-%m-%d")
                       - datetime.strptime(gql_top[:10], "%Y-%m-%d")).days
                print(f"（另有时间线快照最新 {gql_top}）")
            except Exception:
                pass
        print()

    picked = (sorted(set(ext), key=snf_ts, reverse=True) if ext else allids)[:a.n]
    if a.json:
        out = []
        for tid in picked:
            t = fetch_tweet_public(c, tid)
            if t:
                bag["texts"][str(t["id"])] = t
            out.append(t or {"id": tid, "created_at": snf_time(tid), "text": None,
                             "note": "取正文失败（已删/受限/CDN 限流）"})
        _save_index(idx)
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return

    print(f"取最近 {len(picked)} 条的正文（零凭据）：\n")
    shown = 0
    for tid in picked:
        t = fetch_tweet_public(c, tid) or bag["texts"].get(str(tid))
        if not t:
            print(f"· {snf_time(tid)}  [{tid}]  ⚠️ 正文取不到（已删/受限/CDN 限流）")
            continue
        bag["texts"][str(tid)] = t
        au = t.get("author") or {}
        print(f"· {t.get('created_at')}  ♥{fmt_n(t.get('likes'))} "
              f"↻{fmt_n(t.get('retweets'))} 💬{fmt_n(t.get('replies'))}"
              + (f"  [来源 {t.get('source')}]" if a.verbose else ""))
        print(f"  {t.get('text')}")
        for m in t.get("media") or []:
            print(f"  [{m.get('type')}] {m.get('url')}")
        print(f"  https://x.com/{au.get('screen_name') or h}/status/{t.get('id')}")
        print()
        shown += 1
    _save_index(idx)
    if shown:
        print(f"共 {shown} 条。**最新到 {snf_time(picked[0])}（UTC）**。")
        print(f"（ID 已缓存到 {INDEX_FILE}，下次引擎被封也能查）")
    else:
        print("⚠️ 一条正文都没取到 —— 不代表没有推文，可能 id 已失效或 CDN 限流。")

def cmd_community(a, c):
    
    t = a.target.strip()
    if "/communities/" in t:
        cid = t.split("/communities/")[1].split("/")[0].split("?")[0]
    else:
        m = re.search(r"(\d{15,25})", t)
        cid = m.group(1) if m else t
    if not cid.isdigit():
        sys.exit(f"[xweb] 无法解析社区 ID：{a.target}"
                 f"（形如 1471580197908586507 或 x.com/i/communities/<id>）")
    if a.what == "about":
        j = c.gql("CommunityAboutTimeline", {"communityId": cid})
        _community_dump(j, cid, a, kind="简介")
    elif a.what == "posts":
        
        tws, err = fetch_page_ssr(c, f"https://x.com/i/communities/{cid}", None)
        if not tws:
            sys.exit(f"[xweb] 社区帖子取不到：{err}")
        if a.json:
            print(json.dumps(tws[:a.n], ensure_ascii=False, indent=1))
            return
        print(f"社区 {cid} 帖子 {len(tws)} 条（来源：社区页 SSR）\n")
        for t in tws[:a.n]:
            print(f"· {t['created_at']}  @{t['author']['screen_name']}  "
                  f"♥{fmt_n(t.get('likes'))} 💬{fmt_n(t.get('replies'))} 👁{fmt_n(t.get('views'))}")
            print(f"  {(t.get('text') or '').strip()}")
            print(f"  {t['url']}\n")
    elif a.what == "media":
        j = c.gql("CommunityMediaLoggedOutTimeline",
                  {"communityId": cid, "count": max(a.n, 20), "withCommunity": True})
        _community_dump(j, cid, a, kind="媒体")
    else:
        j = c.gql("CommunityQuery", {"communityId": cid})
        _community_dump(j, cid, a, kind="")

def _community_dump(j, cid, a, kind=""):
    data = j.get("data") or {}
    if a.json:
        print(json.dumps(j, ensure_ascii=False, indent=1))
        return
    cr = (data.get("communityResults") or {}).get("result") or {}
    tws, _ = timeline_tweets(data)
    if kind == "媒体" and tws:
        p_tweets(tws[:a.n], label=f"社区 {cid} 媒体（{len(tws)} 条）")
        return
    s = json.dumps(data, ensure_ascii=False)
    m = re.search(r'"name":\s*"([^"]{1,60})"', s)
    m2 = re.search(r'"member_count":\s*(\d+)', s)
    m3 = re.search(r'"description":\s*"([^"]{0,200})"', s)
    head = f"社区 {cid}" + (f"  {m.group(1)}" if m else "")
    print(head + (f"  [{kind}]" if kind else ""))
    if m2:
        print(f"  成员 {m2.group(1)}")
    if m3 and m3.group(1):
        print(f"  简介 {m3.group(1)[:200]}")
    if tws:
        print(f"  解析到 {len(tws)} 条帖子（--json 看全量）")
    if not m and not tws:
        print(f"  ⚠️ 未解析出社区名 —— 该 id 可能不存在（返回 {len(s)}B 骨架）")
    if cr.get("id"):
        print(f"  {cr['id']}")

def cmd_users(a, c):
    ids = [x.strip() for x in a.ids.split(",") if x.strip()]
    names = {}
    
    
    primary = "UsersByRestIds" if c.logged_in else "getUsersByIdsQuery"
    try:
        j = c.gql(primary, {"restIds": ids, **PV}, features="USER_FEATURES")
        for x in parse_users_from(j):
            names[str(x["rest_id"])] = x
    except SystemExit:
        pass
    out = []
    for uid in ids:
        u = names.get(uid)
        if not u or u.get("followers") is None:
            
            if u and u.get("screen_name"):
                try:
                    j2 = c.gql("UserByScreenName", {"screenName": u["screen_name"], **PV},
                               features="USER_FEATURES", referer=f"https://x.com/{u['screen_name']}")
                    f = parse_user(user_node(j2))
                    if f.get("screen_name"):
                        u = f
                except SystemExit:
                    pass
            if not u or u.get("followers") is None:
                try:
                    j3 = c.gql("UserByRestId", {"userId": uid, **PV}, features="USER_FEATURES")
                    f = parse_user(user_node(j3))
                    if f.get("screen_name"):
                        u = f
                except SystemExit:
                    pass
        out.append(u or {"rest_id": uid, "partial": True})
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    for u in out:
        if not u.get("screen_name"):
            print(f"  (未解析) id={u.get('rest_id')}  ⚠️ 本次未取到（重跑或改用 user <handle>）")
            continue
        print(f"{u.get('name')}  (@{u.get('screen_name')})  粉丝 {fmt_n(u.get('followers'))} · "
              f"推文 {fmt_n(u.get('tweets'))}  {u.get('url')}")

def cmd_trends(a, c):
    if a.list:
        for loc in c.get("https://api.x.com/1.1/trends/available.json").json()[:80]:
            print(f"{loc.get('woeid'):>10}  {loc.get('name')}  ({loc.get('country')})")
        return
    arr = c.get("https://api.x.com/1.1/trends/place.json", params={"id": a.woeid}).json()
    if not isinstance(arr, list) or not arr:
        sys.exit("[xweb] 趋势返回异常")
    tr = arr[0].get("trends", [])[:a.n]
    if a.json:
        print(json.dumps(tr, ensure_ascii=False, indent=1))
        return
    print(f"趋势 TOP {len(tr)}（woeid={a.woeid}）：\n")
    for i, t in enumerate(tr, 1):
        vol = t.get("tweet_volume")
        print(f"{i:>3}. {t.get('name')}" + (f"   [{fmt_n(vol)} 推文]" if vol else ""))

def cmd_settings(a, c):
    

    q = ("include_ext_sharing_audiospaces_listening_data_with_followers=true&include_mention_filter=true"
         "&include_nsfw_user_flag=true&include_nsfw_admin_flag=true&include_ranked_timeline=true"
         "&include_alt_text_compose=true&include_ext_dm_av_call_settings=true&ext=ssoConnections"
         "&include_country_code=true&include_ext_dm_nsfw_media_filter=true")
    r = c.request("GET", f"https://api.x.com/1.1/account/settings.json?{q}")
    if r.status_code != 200:
        sys.exit(f"[xweb] settings HTTP {r.status_code}: {r.text[:160]}")
    j = r.json()
    if a.json:
        print(json.dumps(j, ensure_ascii=False, indent=1))
        return
    nice = [("screen_name", "用户名"), ("protected", "私密账号"),
            ("always_use_https", "强制 HTTPS"), ("use_cookie_personalization", "Cookie 个性化"),
            ("sleep_time", "休眠时段"), ("time_zone", "时区"),
            ("allow_contributor_request", "允许被加为贡献者"),
            ("country_code", "国家"), ("language", "语言")]
    print(f"账号设置（{len(j)} 个字段）：\n")
    for k, zh in nice:
        if k in j:
            print(f"  {zh:14s} {j[k]}")
    extra = [k for k in j if k not in dict(nice) and not isinstance(j[k], (dict, list))]
    print(f"\n  其余标量字段 {len(extra)} 个：{', '.join(sorted(extra)[:12])}…")
    print("  （完整：xweb settings --json）")

def cmd_stream(a, c):
    

    mk = a.media_key
    r = c.request("GET", f"https://x.com/i/api/1.1/live_video_stream/status/{mk}")
    if r.status_code != 200:
        sys.exit(f"[xweb] stream HTTP {r.status_code}: {r.text[:160]}")
    j = r.json()
    if a.json:
        print(json.dumps(j, ensure_ascii=False, indent=1))
        return
    src = (j.get("source") or {})
    print(f"流状态 media_key={mk}\n")
    print(f"  状态      {j.get('state') or j.get('status') or '（未给）'}")
    print(f"  总观看    {j.get('totalWatching')}")
    print(f"  会话      {j.get('sessionId')}")
    print(f"  分享页    {j.get('shareUrl')}")
    print(f"  聊天权限  {j.get('chatPermissionType')}")
    print(f"  HLS       {src.get('location')}")
    print(f"  聊天令牌  {'有' if j.get('chatToken') else '无'}（--json 里看全）")

def cmd_hashflags(a, c):
    

    r = c.request("GET", "https://x.com/i/api/1.1/hashflags.json")
    if r.status_code != 200:
        sys.exit(f"[xweb] hashflags HTTP {r.status_code}: {r.text[:160]}")
    j = r.json()
    if a.json:
        print(json.dumps(j, ensure_ascii=False, indent=1))
        return
    keys = list(j) if isinstance(j, dict) else []
    if not keys and isinstance(j, list):
        
        
        print(f"hashflags {len(j)} 项\n")
        for it in j[:a.n]:
            if isinstance(it, dict):
                kk = it.get("keyword") or it.get("hashtag") or ""
                print(f"  {kk:26s} → {it.get('hashtag') or ''}  {it.get('starting_time_ms') or ''}")
            else:
                print(f"  {str(it)[:70]}")
        if len(j) > a.n:
            print(f"\n  …还有 {len(j) - a.n} 项（--json 看全）")
        return
    print(f"hashflags {len(keys)} 项\n")
    for k in keys[:a.n]:
        v = j[k]
        first = v[0] if isinstance(v, list) and v else (v if isinstance(v, dict) else None)
        if isinstance(first, dict):
            print(f"  {k:26s} → {first.get('hashtag') or first.get('keyword') or ''}"
                  f"  {first.get('starting_time_ms') or ''}")
        else:
            print(f"  {k:26s} → {str(first)[:70]}")
    if len(keys) > a.n:
        print(f"\n  …还有 {len(keys) - a.n} 项（--json 看全）")

def cmd_rest(a, c):
    

    method = (a.method or "GET").upper()
    url = a.path if a.path.startswith("http") else "https://x.com" + a.path
    data = a.data
    if data and method in ("POST", "PUT", "PATCH"):
        
        hdr = {"content-type": "application/x-www-form-urlencoded"}
    else:
        hdr = None
    r = c.request(method, url, data=data, headers=hdr)
    body = r.text
    if a.json:
        try:
            print(json.dumps(r.json(), ensure_ascii=False, indent=1))
        except Exception:
            print(body)
        return
    print(f"{method} {url.split('?')[0]}  →  HTTP {r.status_code} · {len(body)}B")
    print(body[:a.max_chars] + ("…" if len(body) > a.max_chars else ""))

def cmd_quote(a, c):
    

    sym = a.symbol.lstrip("$").upper()
    j = c.gql("smartTagAttachmentCardQuery", {"assetSymbol": sym, "timeframe": a.timeframe})
    d = j.get("data") or {}
    asset = ((d.get("finance_asset_data") or {}).get("asset") or {})
    pts = ((d.get("finance_chart_data") or {}).get("data_points") or [])
    prices = [_money(p.get("price")) for p in pts]
    prices = [x for x in prices if x is not None]

    if a.json:
        print(json.dumps({
            "symbol": sym, "timeframe": a.timeframe,
            "asset": asset or None,
            "chart_points": len(prices),
            "chart": [{"t": p.get("timestamp"), "price": _money(p.get("price"))} for p in pts],
            "reason": ([] if asset else ["该符号无 finance_asset_data（可能不是股票代码，"
                                         "或该资产未开通行情卡）"]),
        }, ensure_ascii=False, indent=1))
        return

    if not asset:
        sys.exit(f"[xweb] ${sym} 没有行情数据（服务端 200 但未下发 finance_asset_data）。\n"
                 f"        常见原因：不是股票代码（加密/外汇暂不支持），或符号拼错。\n"
                 f"        这不是错误，别当成 0 值。")

    q = asset.get("quote") or {}
    st = asset.get("stock") or {}
    price = _money(q.get("price"))
    chg = q.get("change_24h_percent")
    mcap = _money(q.get("market_cap"))
    cur = (q.get("price") or {}).get("currency_code") or st.get("currency") or ""
    arrow = "▲" if (chg or 0) > 0 else ("▼" if (chg or 0) < 0 else "—")

    print(f"{asset.get('name') or '—'}  (${asset.get('ticker') or sym})")
    print(f"  价格 {('%.2f' % price) if price is not None else '—'} {cur}"
          f"  ·  24h {arrow} {('%.2f%%' % chg) if chg is not None else '—'}"
          f"  ·  市值 {(_big(mcap) + ' ' + cur) if mcap else '—'}"
          f"  ·  {st.get('exchange_short_name') or '—'}")
    if prices:
        print(f"  走势 {_spark(prices)}  ({len(prices)} 点 · {a.timeframe})")
    print(f"  {asset.get('logo_url') or ''}".rstrip())

def _big(n):
    

    if n is None:
        return "—"
    for unit, div in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if abs(n) >= div:
            return f"{n / div:.2f}{unit}"
    return f"{n:,.0f}"

def _spark(vals, width=42):
    

    blocks = "▁▂▃▄▅▆▇█"
    if len(vals) > width:
        step = len(vals) / width
        vals = [vals[min(len(vals) - 1, int(i * step + step - 1))] for i in range(width)]
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return blocks[3] * len(vals)
    return "".join(blocks[min(len(blocks) - 1, int((v - lo) / (hi - lo) * len(blocks)))]
                   for v in vals)

def cmd_caps(a, c):
    q = c.qids
    ops = [k for k in q if not k.startswith("_")]
    from collections import Counter
    cnt = Counter((q[k] or {}).get("status") or "unverified" for k in ops)
    if a.json:
        by_status: dict[str, list] = {}
        for k in sorted(ops):
            e = q[k] or {}
            st = e.get("status") or "unverified"
            by_status.setdefault(st, []).append(
                {"op": k, "id": e.get("id"), "hits": e.get("hits"), "note": e.get("note")})
        print(json.dumps({
            "mode": "登录态" if c.logged_in else "访客态",
            "screen_name": c.screen_name if c.logged_in else None,
            "op_total": len(ops),
            "counts": dict(cnt),
            "by_status": by_status,
        }, ensure_ascii=False, indent=1))
        return
    print(f"模式：{'登录态 ' + ('@' + c.screen_name if c.screen_name else '') if c.logged_in else '访客态'}")
    print(f"op 表：{len(ops)} 个  " + "  ".join(f"{k}={v}" for k, v in cnt.items()))
    if not c.logged_in:
        print("ℹ️ 状态列是快照标记，服务端调整后会过期。")
        
        
        print("⚠️ 注意：下表 status 以登录态为基准。"
              "访客态可用清单见 README「访客态」一节。")
    print()
    
    
    
    
    
    
    for st, label in (("verified", "✅ 可用"),
                      ("write", "✍️ 写操作"),
                      ("login-required", "🔒 需登录"),
                      ("unverified", "❔ 未标记")):
            keys = sorted(k for k in ops if (q[k] or {}).get("status") == st)
            if not keys:
                continue
            print(f"{label}（{len(keys)}）：")
            for k in keys:
                e = q[k]
                hits = f" [{e.get('hits')}]" if e.get("hits") else ""
                note = f"  ← {e['note']}" if e.get("note") else ""
                print(f"   {k}{hits}{note}")
            print()
    
    
    
    
    
    
    
    
    need = sorted(k for k in ops
                  if (q[k] or {}).get("status") == "guest_404"
                  and (q[k] or {}).get("opType") == "query")
    stale = sorted(k for k in ops
                   if (q[k] or {}).get("status") == "guest_404"
                   and (q[k] or {}).get("opType") != "query")
    if c.logged_in:
        
        print(f"🚫 当前模式不可用（共 {len(need)} 个）：")
        print("   " + ", ".join(need[:40])
              + (f"\n   …共 {len(need)} 个" if len(need) > 40 else "") + "\n")
    else:
        
        
        
        
        
        
        print("🚫 需登录（访客态）：搜索、详情、粉丝、收藏、通知等只对登录用户开放。\n")
    other = sorted(k for k in ops
                   if (q[k] or {}).get("status") in ("unverified", "reachable"))
    if other:
        print(f"❔ 未标记（共 {len(other)} 个）：xweb ops --status unverified")
    print("\n📖 status 词表：verified 可用 ｜ write 写操作 ｜ "
          "login-required 需登录 ｜ unverified 未标记")
    if not c.logged_in:
        print("\n🔑 登录后：xweb login --cookie \"auth_token=...; ct0=...\"  →  解锁更多 op")
    print("🔎 查任意 op：xweb ops [--filter 正则] [--guest]   ／   xweb op <名> [--auto-vars]")

def _coerce(v: str):
    
    s = v.strip()
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    if s.lower() in ("null", "none"):
        return None
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    if re.fullmatch(r"-?\d*\.\d+", s):
        return float(s)
    if s[:1] in "[{":
        try:
            return json.loads(s)
        except Exception:
            return v
    return v

def _op_seed_vars(e):
    
    seed = {}
    for src in (e.get("vars"), e.get("variables"), e.get("seed_vars")):
        if isinstance(src, dict):
            seed.update(src)
    return seed

def _auto_vars(c, op, variables=None, max_iter=16):
    

    var = dict(variables or {})
    names, tries = [], {}
    err = ""
    for _ in range(max_iter):
        try:
            c.gql(op, var)
            return names, None, var
        except SystemExit as e:
            m = re.search(r"缺必填变量 `([^`]+)`", str(e))
            if not m:
                
                
                
                
                
                
                m = re.search(r"path=`([^`]+)`", str(e))
            if not m:
                return names, str(e), var
            n = m.group(1)
            if n not in var:
                names.append(n)
                var[n] = ""
                continue
            
            i = tries.get(n, 0)
            cands = [False, 0, "", [], {}, "0", True]
            if i >= len(cands):
                return names, f"{n} 类型不符（已试 bool/int/str/array/object）：{str(e)[:140]}", var
            var[n] = cands[i]
            tries[n] = i + 1
    return names, "未收敛", var

def cmd_op(a, c):
    

    op = a.operation
    e = c.op_entry(op)
    if a.auto_vars:
        names, err, found = _auto_vars(c, op)
        print(f"{op}  必填变量：")
        for n in names:
            print(f"  · {n}  = {found.get(n)!r}")
        if err:
            print(f"\n停止原因：{err[:200]}")
        else:
            print("\n（全部必填变量已给，服务端未再报缺）")
        if a.json:
            print(json.dumps({"op": op, "queryId": e.get("id"), "required": names,
                              "probed": {k: repr(v) for k, v in found.items()},
                              "stopped": err}, ensure_ascii=False, indent=1))
        return
    variables = _op_seed_vars(e)
    for kv in (a.var or []):
        if "=" not in kv:
            sys.exit(f"[xweb] --var 需要 k=v 形式，收到：{kv}")
        k, v = kv.split("=", 1)
        variables[k] = _coerce(v)
    if a.vars:
        try:
            extra = json.loads(a.vars)
        except Exception as ex:
            sys.exit(f"[xweb] --vars 不是合法 JSON：{ex}")
        if not isinstance(extra, dict):
            sys.exit("[xweb] --vars 必须是一个 JSON 对象")
        variables.update(extra)
    feats = a.features or e.get("features") or "FEATURES"
    j = c.gql(op, variables, features=feats, referer=a.referer or "https://x.com/")
    data = j.get("data") if isinstance(j, dict) else j
    if a.json:
        print(json.dumps(j, ensure_ascii=False, indent=1))
        return
    if not data:
        
        
        
        print("(响应无 data：服务端返回空 data 或错误；请求已发送，用 --json 看原文)")
        return
    
    
    
    
    tws, _cur = timeline_tweets(data)
    tws = [t for t in tws if t.get("id")]
    if tws:
        p_tweets(tws[:a.n], as_json=False, label=f"{op} → {len(tws)} 条推文")
        return
    us = parse_users_from(data)
    if us:
        for u in us[:a.n]:
            print()
            p_user(u)
        return
    s = json.dumps(data, ensure_ascii=False, indent=1)
    print(s if len(s) <= 6000 else s[:6000] + f"\n…（共 {len(s)}B，用 --json 存全量）")

def cmd_ops(a, c):
    
    q = c.qids
    rows = []
    for k in sorted(q):
        if k.startswith("_"):
            continue
        e = q[k] if isinstance(q[k], dict) else {"id": q[k]}
        st = e.get("status") or "unverified"
        if a.status and st != a.status:
            continue
        if a.guest and st not in ("verified", "empty", "flaky", "partial"):
            continue
        if a.src and (e.get("src") or "") != a.src:
            continue
        if a.filter and not re.search(a.filter, k, re.I):
            continue
        rows.append((k, st, e.get("id") or "", e.get("src") or "",
                     "、".join(e.get("alt") or []), e.get("note") or ""))
    if a.json:
        print(json.dumps([{"op": r[0], "status": r[1], "id": r[2], "src": r[3],
                           "alt": r[4], "note": r[5]} for r in rows], ensure_ascii=False, indent=1))
        return
    from collections import Counter
    if not rows:
        print("没有匹配的 op。换个 --filter，或先跑 refresh_query_ids.py 刷新表。")
        return
    print(f"{'操作名':46s} {'状态':11s} {'id':26s} 来源")
    print("─" * 100)
    for k, st, i, src, alt, note in rows:
        print(f"{k:46s} {st:11s} {i:26s} {src}{('  alt=' + alt) if alt else ''}")
        if note:
            
            
            print(f"{'':46s} └ {note[:150]}")
    cnt = Counter(r[1] for r in rows)
    print(f"\n共 {len(rows)} 个  " + "  ".join(f"{k}={v}" for k, v in cnt.items()))
    print("提示：`xweb op <操作名> --var k=v` 可直接调用任意一个。")

WRITE_GUARD = ("[xweb] 这是**写操作**，会真实改动你的账号。确认后加 --yes 重跑：\n"
               "        {cmd}")

def _mutate(a, c, op, var, desc, feats="FEATURES", semantic=None, alts=()):
    

    semantic = semantic or {}
    if not c.logged_in and not DRY_RUN:
        sys.exit("[xweb] 写操作需要登录态：xweb login --cookie \"auth_token=...; ct0=...\"")
    if not a.yes and not DRY_RUN:
        sys.exit(WRITE_GUARD.format(cmd=f"{desc}\n        （变量将按 x.com 当前要求自动探测）"))
    if DRY_RUN:
        
        print(f"[dry-run] 写操作预览：{desc}")
        c.gql(op, var, features=feats)
        return
    var = dict(var)
    last = None
    for _ in range(4):
        try:
            j = c.gql(op, var, features=feats)
            print(f"✓ {desc}")
            if a.json:
                print(json.dumps(j.get("data") or j, ensure_ascii=False, indent=1)[:2000])
            return
        except SystemExit as e:
            m = re.search(r"缺必填变量 `([^`]+)`", str(e))
            if not m:
                sys.exit(str(e))
            name = m.group(1)
            low = name.lower()
            val = next((v for k, v in semantic.items() if k in low), None)
            if val is None:
                sys.exit(f"[xweb] {op} 需要变量 `{name}`，但我不知道它该填什么。\n"
                         f"        请把这条错误发我，我补进语义表。")
            var[name] = val
            last = name
    sys.exit(f"[xweb] {op} 变量填充未收敛（最后补的是 `{last}`），请把输出发我。")

def cmd_post(a, c):
    var = {"tweet_text": a.text, "dark_request": False,
           "media": {"media_entities": [], "possibly_sensitive": False},
           "semantic_annotation_ids": []}
    if a.reply_to:
        var["reply"] = {"in_reply_to_tweet_id": _tid(a.reply_to), "exclude_reply_user_ids": []}
    if a.quote:
        var["attachment_url"] = f"https://x.com/i/status/{_tid(a.quote)}"
    _mutate(a, c, "CreateTweet", var, f"发推：{a.text[:60]}")

def cmd_simple_mut(a, c, op, var, desc):
    _mutate(a, c, op, var, desc)

def build_parser():
    ap = argparse.ArgumentParser(prog="xweb", description="x.com CLI（访客 / 登录态）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--verbose", action="store_true", help="打印请求日志到 stderr")
    ap.add_argument("--dry-run", action="store_true",
                    help="只构造并打印请求（凭据脱敏），不真的发送")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("login", help="保存/清除 cookie，解锁登录态")
    p.add_argument("--cookie", help='浏览器 Cookie 整串："auth_token=...; ct0=..."')
    p.add_argument("--cookie-file", help="从文件读 cookie（整串/整个 cookie header 都行）")
    p.add_argument("--auth-token")
    p.add_argument("--ct0")
    p.add_argument("--logout", action="store_true")

    sub.add_parser("doctor", help="环境自检")
    sub.add_parser("caps", help="当前模式下的能力清单")

    p = sub.add_parser("ops", help="列出/筛选 op 表（470 个接口清单）")
    p.add_argument("--filter", help="按操作名正则筛选，如 --filter 'List'")
    p.add_argument("--status",
                   choices=["verified", "empty", "reachable", "flaky", "partial",
                            "needs-sample", "guest_404", "untested-write",
                            "unknown-type", "unverified"],
                   help="只看某状态")
    p.add_argument("--guest", action="store_true",
                   help="只看访客能通的（verified/empty/flaky/partial）")
    p.add_argument("--src", help="只看某来源线：classic / app")

    p = sub.add_parser("op", help="调用任意一个 op（兜底逃生口）")
    p.add_argument("operation", help="操作名，如 TweetDetail / SearchTimeline")
    p.add_argument("--vars", help="变量 JSON 对象")
    p.add_argument("--var", action="append", help="变量 k=v，可重复；值是 JSON 就按 JSON 解析")
    p.add_argument("--features", help="feature flag 组名（默认按 op 表自动选）")
    p.add_argument("--auto-vars", action="store_true",
                   help="只用 422 问出必填变量清单并打印（不发真实业务请求）")
    p.add_argument("--referer")
    p.add_argument("-n", type=int, default=20, help="最多渲染多少条")

    p = sub.add_parser("user", help="用户资料")
    p.add_argument("handle")
    p = sub.add_parser("tweets", help="用户时间线/各 tab")
    p.add_argument("handle")
    p.add_argument("-n", type=int, default=10)
    p.add_argument("--tab", default="tweets",
                   choices=["tweets", "replies", "media", "originals", "photos", "videos",
                            "reposts", "articles", "likes"],
                   help="tweets=主时间线(replies/media/…需登录态)")
    p.add_argument("--widget", action="store_true", help="并合并公开嵌入 widget 通道")
    p.add_argument("--pages", type=int, default=1,
                   help="翻页数：跟着游标往回翻（部分账号可翻；无游标时自动停止）")

    p = sub.add_parser("tweet", help="单条推文（ID/URL）")
    p.add_argument("target")
    p = sub.add_parser("detail", help="推文详情 + 回复串；支持批量")
    p.add_argument("target", nargs="?", help="推文 ID/URL；可逗号分隔多个，或改用 --ids-file")
    p.add_argument("-n", type=int, default=20)
    p.add_argument("--ids-file", help="文件批量：每行一个 ID/URL（# 开头为注释）")
    p.add_argument("-j", "--concurrency", type=int, default=4, help="批量并发数（默认 4）")
    p = sub.add_parser("users", help="按 ID 批量查用户")
    p.add_argument("ids")

    p = sub.add_parser("search", help="搜索推文（需登录）")
    p.add_argument("query")
    p.add_argument("-n", type=int, default=20)
    p.add_argument("--product", default="Top", choices=["Top", "Latest", "Media", "People"])

    p = sub.add_parser("home", help="首页时间线（需登录）")
    p.add_argument("-n", type=int, default=20)
    p.add_argument("--latest", action="store_true")
    p = sub.add_parser("bookmarks", help="我的收藏（需登录）")
    p.add_argument("-n", type=int, default=20)
    p = sub.add_parser("notifications", help="通知（需登录）")
    p.add_argument("-n", type=int, default=20)

    p = sub.add_parser("relations", help="粉丝/关注（需登录）")
    p.add_argument("handle")
    p.add_argument("-n", type=int, default=20)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--followers", action="store_true")
    g.add_argument("--following", action="store_true")

    p = sub.add_parser("latest",
                       help="拿某人真正的最新推文（访客时间线拿不到的最新推文，访客可用）")
    p.add_argument("handle")
    p.add_argument("-n", type=int, default=8, help="取最近几条正文（默认 8）")
    p.add_argument("--pages", type=int, default=2, help="每个搜索引擎翻几页")
    p.add_argument("--ids", help="外部直接喂推文 ID/链接（逗号分隔），最稳，不碰搜索引擎")
    p.add_argument("--ids-file", help="从文件读 ID/链接（正则抓 15-25 位数字）")
    p.add_argument("--cache-only", action="store_true", help="只用本地缓存，不发搜索请求")
    p.add_argument("--no-search", action="store_true", help="关掉搜索引擎，用缓存 + 外部 ID")
    p.add_argument("--no-ssr", action="store_true",
                   help="跳过页面通道（默认优先，因为更实时）")
    
    

    p = sub.add_parser("community", help="看社区（访客可用）")
    p.add_argument("target", help="社区 ID 或 x.com/i/communities/<id> URL")
    p.add_argument("--what", default="info", choices=["info", "about", "media", "posts"],
                   help="info=基本信息 / about=简介 / media=媒体 / posts=帖子(SSR)")
    p.add_argument("-n", type=int, default=20)

    p = sub.add_parser("trends", help="趋势榜")
    p.add_argument("-n", type=int, default=20)
    p.add_argument("--woeid", default="1")
    p.add_argument("--list", action="store_true")

    p = sub.add_parser("quote", help="股票行情卡（访客可用，如 xweb quote AAPL）")
    p.add_argument("symbol", help="股票代码，可带 $，如 AAPL / $TSLA")
    p.add_argument("--timeframe", default="1D", help="1D/1M 等（默认 1D）")

    
    p = sub.add_parser("settings", help="账号设置（api.x.com/1.1/account/settings.json，需登录）")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("stream", help="直播/回放流状态 + HLS 地址（需登录）")
    p.add_argument("media_key", help="形如 28_2099719087031259138，从推文数据里拿")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("hashflags", help="活动话题表情表（需登录）")
    p.add_argument("-n", type=int, default=10)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("rest", help="REST 逃生口：直打 /1.1/* 或 /i/api/1.1/*（自动补签名）")
    p.add_argument("path", help="如 /i/api/1.1/hashflags.json 或完整 https URL")
    p.add_argument("--method", default="GET")
    p.add_argument("--data", help="POST/PUT 的 form 体（如 'a=1&b=2'）")
    p.add_argument("--max-chars", type=int, default=2000)
    p.add_argument("--json", action="store_true")

    
    p = sub.add_parser("post", help="发推（需登录 + --yes）")
    p.add_argument("text")
    p.add_argument("--reply-to")
    p.add_argument("--quote")
    p.add_argument("--yes", action="store_true")
    for name, helptext in [("like", "点赞"), ("unlike", "取消赞"), ("repost", "转推"),
                           ("unrepost", "取消转推"), ("bookmark", "加收藏"), ("unbookmark", "取消收藏"),
                           ("del", "删除推文")]:
        pp = sub.add_parser(name, help=f"{helptext}（需登录 + --yes）")
        pp.add_argument("target")
        pp.add_argument("--yes", action="store_true")
    for name, helptext in [("follow", "关注"), ("unfollow", "取关"), ("block", "拉黑"),
                           ("unblock", "取消拉黑"), ("mute", "静音"), ("unmute", "取消静音")]:
        pp = sub.add_parser(name, help=f"{helptext}（需登录 + --yes）")
        pp.add_argument("handle")
        pp.add_argument("--yes", action="store_true")
    return ap

MUT = {
    "like": (["FavoriteTweet"], "tweet_id", "已点赞"),
    "unlike": (["UnfavoriteTweet"], "tweet_id", "已取消赞"),
    "repost": (["CreateRetweet"], "tweet_id", "已转推"),
    "unrepost": (["DeleteRetweet"], "source_tweet_id", "已取消转推"),
    "bookmark": (["CreateBookmark"], "tweet_id", "已收藏"),
    "unbookmark": (["DeleteBookmark"], "tweet_id", "已取消收藏"),
    "del": (["DeleteTweet"], "tweet_id", "已删除"),
}

FOLLOW_OPS = {
    "follow":   (["followUserMutation"], "userId", "已关注"),
    "unfollow": (["unfollowUserMutation"], "userId", "已取关"),
    "block":    (["blockUserMutation"], "userId", "已拉黑"),
    "unblock":  (["unblockUserMutation"], "userId", "已取消拉黑"),
    "mute":     (["muteUserMutation"], "userId", "已静音"),
    "unmute":   (["unmuteUserMutation"], "userId", "已取消静音"),
}

def cmd_doctor(a, c):
    print("=== xweb 自检 ===")
    print(f"  指纹        : {IMPERSONATE}")
    print(f"  模式        : {'登录态' + (' (@' + c.screen_name + ')' if c.screen_name else '') if c.logged_in else '访客态（无账号）'}")
    print(f"  guest_token : {c.guest_token or '(登录态不需要)'}")
    print(f"  ct0         : {'有' if c.s.cookies.get('ct0') else '无'}")
    print(f"  配置目录    : {CFG_DIR}")
    print(f"  op 表       : {len([k for k in c.qids if not k.startswith('_')])} 个操作")
    tests = [("用户资料  ", lambda: c.gql("UserByScreenName", {"screenName": "x", **PV},
                                        features="USER_FEATURES")),
             ("用户时间线", lambda: c.gql("UserTweets", {"userId": "1", "count": 20,
                                                       "includePromotedContent": False,
                                                       "withQuickPromoteEligibilityTweetFields": True,
                                                       "withVoice": True})),
             ("趋势      ", lambda: c.get("https://api.x.com/1.1/trends/place.json", params={"id": 1})),
             ("单条推文  ", lambda: c.s.get("https://cdn.syndication.twimg.com/tweet-result",
                                            params={"id": "20", "token": "a"}))]
    if c.logged_in:
        tests.append(("登录态搜索", lambda: c.gql("SearchTimeline", {"rawQuery": "x", "count": 20,
                                                                   "querySource": "typed_query",
                                                                   "product": "Top"})))
    ok = 0
    for label, fn in tests:
        try:
            fn()
            print(f"  {label}      : ✅")
            ok += 1
        
        
        
        
        
        except BaseException as e:
            print(f"  {label}      : ❌ {str(e)[:110]}")
    print(f"\n  {ok}/{len(tests)} 通过")

def main():
    global DRY_RUN, DRY_JSON
    
    
    
    
    if "--list-subs" in sys.argv[1:]:
        _ap = build_parser()
        for _act in _ap._subparsers._group_actions:            
            print(" ".join(sorted(_act.choices)))
        return 0
    ap = build_parser()
    a = ap.parse_args()
    DRY_RUN = bool(getattr(a, "dry_run", False))
    DRY_JSON = bool(getattr(a, "json", False))
    if not a.cmd:
        ap.print_help()
        return 1
    if a.cmd == "login":
        cmd_login(a)
        return 0
    c = X(verbose=getattr(a, "verbose", False))
    if a.cmd == "doctor":
        cmd_doctor(a, c)
    elif a.cmd == "caps":
        cmd_caps(a, c)
    elif a.cmd == "ops":
        cmd_ops(a, c)
    elif a.cmd == "op":
        cmd_op(a, c)
    elif a.cmd == "user":
        cmd_user(a, c)
    elif a.cmd == "tweets":
        cmd_tweets(a, c)
    elif a.cmd == "tweet":
        cmd_tweet(a, c)
    elif a.cmd == "detail":
        cmd_detail(a, c)
    elif a.cmd == "users":
        cmd_users(a, c)
    elif a.cmd == "search":
        cmd_search(a, c)
    elif a.cmd == "home":
        cmd_home(a, c)
    elif a.cmd == "bookmarks":
        cmd_bookmarks(a, c)
    elif a.cmd == "notifications":
        cmd_notifications(a, c)
    elif a.cmd == "relations":
        cmd_relations(a, c)
    elif a.cmd == "latest":
        cmd_latest(a, c)
    elif a.cmd == "community":
        cmd_community(a, c)
    elif a.cmd == "trends":
        cmd_trends(a, c)
    elif a.cmd == "quote":
        cmd_quote(a, c)
    elif a.cmd == "settings":
        cmd_settings(a, c)
    elif a.cmd == "stream":
        cmd_stream(a, c)
    elif a.cmd == "hashflags":
        cmd_hashflags(a, c)
    elif a.cmd == "rest":
        cmd_rest(a, c)
    elif a.cmd == "post":
        cmd_post(a, c)
    elif a.cmd in MUT:
        ops, col, desc = MUT[a.cmd]
        tid = _tid(a.target)
        _mutate(a, c, ops[0], {col: tid}, f"{desc} {a.target}",
                semantic={"tweet": tid, "rest": tid, "source": tid})
    elif a.cmd in FOLLOW_OPS:
        ops, col, desc = FOLLOW_OPS[a.cmd]
        u = c.user_id(a.handle)
        uid = str(u["rest_id"])
        _mutate(a, c, ops[0], {col: uid}, f"{desc} @{u['screen_name']}（id={uid}）",
                semantic={"user": uid, "rest": uid})
    return 0

if __name__ == "__main__":
    sys.exit(main())
