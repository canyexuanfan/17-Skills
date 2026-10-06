#!/usr/bin/env python3
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

__version__ = "1.2.0"

try:
    from . import rcore as _rc
except Exception:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import rcore as _rc

HOME = os.path.expanduser("~")
CFG_DIR = os.environ.get("RWEB_HOME") or os.path.join(HOME, ".config", "rweb")
JAR = os.path.join(CFG_DIR, "cookies.jar")
CFG = os.path.join(CFG_DIR, "config.json")
def _find_data():
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "data", "ops.json"),
                 os.path.join(here, "..", "data", "ops.json"),
                 os.path.join(here, "..", "..", "data", "ops.json")):
        if os.path.exists(cand):
            return os.path.normpath(cand)
    return os.path.normpath(os.path.join(here, "..", "data", "ops.json"))

DATA = _find_data()
TOR_SOCKS = os.environ.get("RWEB_TOR_SOCKS", "127.0.0.1:9050")
TOR_CTRL = ("127.0.0.1", int(os.environ.get("RWEB_TOR_CTRL_PORT", "9051")))
TOR_COOKIE = os.environ.get("RWEB_TOR_COOKIE", "")
BASE = "https://www.reddit.com"
GQL = BASE + "/svc/shreddit/graphql"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

def load_cfg():
    try:
        return json.load(open(CFG))
    except Exception:
        return {}

def save_cfg(c):
    os.makedirs(CFG_DIR, exist_ok=True)
    json.dump(c, open(CFG, "w"), indent=1)
    os.chmod(CFG, 0o600)

def use_tor(a):
    if getattr(a, "tor", False):
        return True
    if getattr(a, "direct", False):
        return False
    env = os.environ.get("RWEB_TOR")
    if env is not None:
        return env == "1"
    return bool(load_cfg().get("tor", False))

def jar_path(a=None):
    return (getattr(a, "jar", None) if a else None) or JAR

def _curl(url, method="GET", data=None, hdrs=None, jar=None, tor=True, timeout=60):
    tmp = tempfile.mkdtemp(prefix="rweb-")
    hp, bp = os.path.join(tmp, "h"), os.path.join(tmp, "b")
    try:
        cmd = ["curl", "-sS", "-m", str(timeout), "-A", UA, "-D", hp, "-o", bp, "-w", "%{http_code}"]
        if jar:
            os.makedirs(os.path.dirname(os.path.abspath(jar)), exist_ok=True)
            cmd += ["-b", jar, "-c", jar]
        if tor:
            cmd += ["--socks5-hostname", TOR_SOCKS]
        if method != "GET":
            cmd += ["-X", method]
        if data is not None:
            cmd += ["--data", data]
        for h in (hdrs or []):
            cmd += ["-H", h]
        cmd.append(url)
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=timeout + 20)
            code = int(r.stdout.decode(errors="ignore").strip() or 0)
        except Exception as e:
            return None, b"", {"error": str(e)}
        body = open(bp, "rb").read() if os.path.exists(bp) else b""
        hd = {}
        try:
            for ln in open(hp, errors="ignore"):
                if ":" in ln:
                    k, v = ln.split(":", 1)
                    hd[k.strip().lower()] = v.strip()
        except Exception:
            pass
        return code, body, hd
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def jar_csrf(jar=None):
    try:
        for ln in open(jar or JAR):
            if "csrf_token" in ln:
                return ln.split("\t")[-1].strip()
    except Exception:
        pass
    return ""

def newnym():
    try:
        ck = open(TOR_COOKIE, "rb").read().hex()
        s = __import__("socket").create_connection(TOR_CTRL, timeout=20)
        s.sendall(f"AUTHENTICATE {ck}\r\nSIGNAL NEWNYM\r\nQUIT\r\n".encode())
        time.sleep(0.6)
        resp = s.recv(4096).decode("utf-8", "ignore")
        s.close()
    except Exception as e:
        return False, str(e)
    time.sleep(11)
    return "250" in resp, resp.strip()[:60]

_VSTATE = {0: "ok", 1: "no-session", 2: "throttled"}

def _session_state(jar, tor):
    c, b, h = _curl(BASE + "/r/programming/hot.json?limit=1", jar=jar, tor=tor)
    v, _ = _rc.p(c, h.get("content-type") or "", b or b"")
    return _VSTATE.get(v, "throttled"), c

def _json_ok(jar, tor):
    return _session_state(jar, tor)[0] == "ok"

def solve_session(a=None, jar=None):
    jar = jar or jar_path(a)
    tor = use_tor(a) if a is not None else bool(os.environ.get("RWEB_TOR") == "1")
    st, _c = _session_state(jar, tor)
    if st == "ok":
        return True, "session valid"
    if st == "throttled":
        return True, f"session usable (upstream throttled, HTTP {_c})"
    last = None
    for _attempt in range(4):
        c, body, _ = _curl(BASE + "/", jar=jar, tor=tor)
        v, follow = _rc.p(c, "", body or b"")
        if not follow:
            last = (f"session page seen but unrecognised (HTTP {c})" if v == 1
                    else f"no session page served (HTTP {c}, {len(body or b'')}B)")
            st2, _ = _session_state(jar, tor)
            if st2 != "no-session":
                return True, ("session valid" if st2 == "ok"
                              else "session usable (upstream throttled)")
            time.sleep(2)
            continue
        c2, _, _ = _curl(BASE + follow, jar=jar, tor=tor)
        st3, _ = _session_state(jar, tor)
        if st3 == "ok":
            return True, f"session established (HTTP {c2})"
        if st3 == "throttled":
            return True, f"session established (HTTP {c2}) · upstream throttled"
        last = f"session established (HTTP {c2}) but still not usable"
        time.sleep(2)
    return False, last or "session not established"

def _solved_get(url, a, jar, hdrs=None):
    c, b, h = _curl(url, jar=jar, tor=use_tor(a), hdrs=hdrs)
    retry = c is None or c == 0
    if not retry and c in (403, 429):
        retry = _rc.p(c, h.get("content-type") or "", b or b"")[0] == 1
    if retry:
        ok, _ = solve_session(a, jar=jar)
        if ok:
            c, b, h = _curl(url, jar=jar, tor=use_tor(a), hdrs=hdrs)
    return c, b, h

def api(path, a, jar=None, **kw):
    if not path.startswith("http"):
        path = BASE + (path if path.startswith("/") else "/" + path)
    return _curl(path, jar=jar or jar_path(a), tor=use_tor(a), **kw)

def op_table():
    try:
        return json.load(open(os.path.normpath(DATA)))
    except Exception:
        return {}

def is_write(name, meta=None):
    if meta and meta.get("kind"):
        return meta["kind"] == "write"
    return bool(re.match(r"^(Update|Delete|Create|Submit|Mod|Remove|Add|Set|Save|Vote|"
                         r"Report|Block|Mute|Ban|Approve|Lock|Unlock|Sticky|Distinguish|"
                         r"Assign|Reorder|Sync|Send|Edit|Restore|Upsert|Disapprove|Store)", name))

def call_op(name, variables, a, jar=None):
    body = json.dumps({"operation": name, "variables": variables,
                       "csrf_token": jar_csrf(jar or jar_path(a))})
    if a.dry_run:
        shown = json.dumps({"operation": name, "variables": variables,
                            "csrf_token": "<redacted>"}, ensure_ascii=False)
        print(f"[dry-run] POST {GQL}\n  {shown}")
        return 0
    c, b, h = _curl(GQL, method="POST", data=body, jar=jar or jar_path(a),
                    tor=use_tor(a),
                    hdrs=["Content-Type: application/json", "Accept: application/json",
                          "Origin: " + BASE, "Referer: " + BASE + "/"])
    txt = (b or b"").decode("utf-8", "ignore")
    try:
        j = json.loads(txt)
    except Exception:
        j = None
    if a.json:
        print(json.dumps({"operation": name, "status": c,
                          "data": (j or {}).get("data") if isinstance(j, dict) else None,
                          "errors": (j or {}).get("errors") if isinstance(j, dict) else txt[:300]},
                         ensure_ascii=False))
        return 0 if (c == 200 and isinstance(j, dict) and not j.get("errors")) else 1
    if isinstance(j, dict) and not j.get("errors") and c == 200:
        print(f"HTTP {c}  OK")
        print("  " + json.dumps(j.get("data"), ensure_ascii=False)[:800])
        return 0
    print(f"HTTP {c}")
    print("  " + (json.dumps(j.get("errors"), ensure_ascii=False)[:400] if isinstance(j, dict)
                  else txt[:300]))
    return 1

def cmd_session(a):
    ok, msg = solve_session(a)
    print(("OK   " if ok else "FAIL ") + msg)
    if ok:
        print(f"  session file: {jar_path(a)}")
    return 0 if ok else 1

def cmd_login(a):
    ck = a.cookie or ""
    if not ck and a.cookie_file:
        ck = open(a.cookie_file).read().strip()
    if not ck:
        sys.exit("[rweb] need --cookie \"token_v2=...; reddit_session=...\" or --cookie-file")
    ck = re.sub(r"^\s*Cookie:\s*", "", ck, flags=re.I)
    jar = jar_path(a)
    os.makedirs(os.path.dirname(jar), exist_ok=True)
    lines = ["# Netscape HTTP Cookie File"]
    for part in ck.split(";"):
        if "=" not in part:
            continue
        k, v = part.strip().split("=", 1)
        if not k:
            continue
        domain = ".reddit.com" if k in ("token_v2", "reddit_session", "csrf_token",
                                        "rdt", "loid", "csv", "edgebucket",
                                        "session_tracker") else "www.reddit.com"
        lines.append(f"{domain}\tTRUE\t/\tFALSE\t1900000000\t{k}\t{v}")
    open(jar, "w").write("\n".join(lines) + "\n")
    os.chmod(jar, 0o600)
    c, b, h = api("/api/me.json", a, jar=jar)
    name = None
    try:
        name = json.loads(b).get("data", {}).get("name")
    except Exception:
        pass
    if not name:
        print("FAIL  cookie stored but not logged in — check you copied the right cookies")
        return 1
    print(f"OK    logged in as u/{name}  (session: {jar})")
    return 0

def cmd_me(a):
    if a.dry_run:
        print(f"[dry-run] GET {BASE}/api/me.json")
        return 0
    c, b, h = api("/api/me.json", a)
    try:
        d = json.loads(b).get("data", {})
    except Exception:
        print(f"HTTP {c} not logged in"); return 1
    if not d.get("name"):
        print("not logged in (visitor session)"); return 1
    if a.json:
        print(json.dumps({k: d.get(k) for k in
                          ("name", "id", "total_karma", "link_karma", "comment_karma",
                           "created_utc", "is_mod", "is_gold", "over_18")},
                         ensure_ascii=False))
    else:
        print(f"u/{d.get('name')}  id={d.get('id')}  karma={d.get('total_karma')} "
              f"(link {d.get('link_karma')} / comment {d.get('comment_karma')})")
    return 0

def cmd_doctor(a):
    checks = [
        ("session check", lambda: _curl(BASE + "/", jar=os.path.join(CFG_DIR, ".probe.jar"),
                                          tor=use_tor(a))),
        ("json api", lambda: api("/r/programming/hot.json?limit=1", a)),
        ("graphql", lambda: _curl(GQL, method="POST", jar=jar_path(a), tor=use_tor(a),
                                  data=json.dumps({"operation": "ExposeVariantBatch",
                                                   "variables": {"inputs": []},
                                                   "csrf_token": jar_csrf(jar_path(a))}),
                                  hdrs=["Content-Type: application/json"])),
        ("rss", lambda: api("/r/programming/.rss", a)),
    ]
    okn = 0
    np = _rc.native_path()
    print(f"rweb {__version__} · mode={'logged-in' if load_cfg().get('logged_in') else 'visitor'}"
          f" · tor={'on' if use_tor(a) else 'off'}")
    print(f"  {'OK  ' if np else 'FAIL'} {'core module':<10} "
          f"{np or ('missing for ' + _rc.platform_tag())}")
    for name, fn in checks:
        c, b, h = fn()
        ct = h.get("content-type", "")
        good = c == 200 and ("json" in ct or "xml" in ct)
        if name == "session check":
            good = c in (200, 403)
        okn += 1 if good else 0
        print(f"  {'OK  ' if good else 'FAIL'} {name:<10} HTTP{c} {ct[:34]}")
    print(f"  {okn}/4  + core module {'ok' if np else 'MISSING'}")
    return 0 if (okn >= 3 and np) else 1

def cmd_selftest(a):
    import platform as _pf
    print(f"rweb {__version__} · selftest")
    print(f"  python      : {sys.version.split()[0]}  {_pf.machine()}")
    print(f"  platform tag: {_rc.platform_tag()}")
    print(f"  bundled     : {', '.join(_rc.available()) or '(none)'}")
    np = _rc.native_path()
    print(f"  core module : {np or '(missing)'}")
    if not np:
        print("  ✗ 当前平台没有核心模块 —— 会话功能不可用")
        return 1
    ex = sorted(n for n in dir(_rc._impl) if not n.startswith("_"))
    print(f"  public api  : {', '.join(ex) or '(none)'}")
    j = _rc.p(200, "application/json; charset=utf-8", b'{"data":{}}')
    h = _rc.p(404, "text/html", b"<html><body>down</body></html>")
    print(f"  classify    : json->{j[0]}  html->{h[0]}   (期望 0 / 2)")
    ok = (j[0] == 0 and h[0] == 2)
    jar = jar_path(a)
    c, body, _ = _curl(BASE + "/", jar=jar, tor=use_tor(a))
    v, follow = _rc.p(c, "", body or b"")
    print(f"  live probe  : HTTP {c}  {len(body or b'')}B  verdict={v}")
    ok2, msg = solve_session(a, jar=jar)
    print(f"  session     : {'OK  ' if ok2 else 'FAIL '}{msg}")
    print(f"  session file: {jar}")
    print(f"  selftest    : {'✅ 通过' if (ok and ok2) else '❌ 未通过'}")
    return 0 if (ok and ok2) else 1

def cmd_caps(a):
    t = op_table()
    r = sum(1 for v in t.values() if v["kind"] == "read")
    w = sum(1 for v in t.values() if v["kind"] == "write")
    logged = bool(load_cfg().get("logged_in"))
    rows = [
        ("session", True), ("json listings/comments/search", True),
        ("rss feeds", True), ("graphql read ops", True),
        ("graphql write ops", logged), ("account data (me/saved/hidden/subs)", logged),
        ("legacy api writes (subscribe/vote)", logged),
    ]
    print(f"mode: {'logged-in' if logged else 'visitor'}   ops: {r} read / {w} write")
    for n, ok in rows:
        print(f"  {'YES' if ok else '-- '}  {n}")
    return 0

def _emit(rows, a, kind="post"):
    if a.json:
        for r in rows:
            print(json.dumps(r, ensure_ascii=False))
    else:
        print(f"{len(rows)} item(s)")
        for r in rows[:getattr(a, "show", 15)]:
            if kind == "post":
                print(f"  {str(r.get('score')):>6}↑ {str(r.get('num_comments')):>5}💬 "
                      f"u/{r.get('author')} · {str(r.get('title'))[:64]}")
            elif kind == "comment":
                print(f"  {str(r.get('score')):>6}↑ u/{r.get('author')} · "
                      f"{str(r.get('body'))[:70]}")
    return 0

def cmd_json(a):
    jar = jar_path(a)
    if not a.no_session and not os.path.exists(jar):
        ok, msg = solve_session(a, jar=jar)
        if not ok:
            print(f"[rweb] session failed: {msg}", file=sys.stderr)
            return 1
    path = a.path
    base = path if path.startswith("http") else BASE + (path if path.startswith("/") else "/" + path)
    if ".json" not in base:
        if "?" in base:
            p, _, q = base.partition("?")
            base = p.rstrip("/") + ".json?" + q
        else:
            base = base.rstrip("/") + ".json"
    url = base + ("&" if "?" in base else "?") + f"limit={a.limit}"
    if a.dry_run:
        print(f"[dry-run] GET {url}")
        return 0
    rows, seen, page, anchor = [], set(), 0, None
    while page < max(1, int(a.pages)):
        u = url + (f"&after={anchor}" if anchor else "")
        c, b, h = _solved_get(u, a, jar, hdrs=["Accept: application/json"])
        try:
            j = json.loads(b)
        except Exception:
            print(f"[rweb] page {page+1}: HTTP{c} not JSON", file=sys.stderr)
            break
        docs = j if isinstance(j, list) else [j]
        page += 1
        kids = []
        for doc in docs:
            d = doc.get("data", doc) if isinstance(doc, dict) else {}
            for k in (d.get("children") or []):
                kids.append(k)
                kd = k.get("data", {})
                i = kd.get("name") or kd.get("id")
                if i and i not in seen:
                    seen.add(i); rows.append(kd)
        anchor = docs[0].get("data", {}).get("after") if isinstance(docs[0], dict) else None
        if not anchor or not isinstance(j, dict):
            break
    if a.out:
        with open(a.out, "w") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"wrote {len(rows)} rows -> {a.out}", file=sys.stderr)
    return _emit(rows, a, "comment" if "/comments/" in path else "post")

def cmd_rss(a):
    path = a.path
    if ".rss" not in path:
        path = path.rstrip("/") + "/.rss"
    if not path.startswith("http"):
        path = BASE + (path if path.startswith("/") else "/" + path)
    if a.dry_run:
        print(f"[dry-run] GET {path}"); return 0
    c, b, h = _curl(path, jar=jar_path(a), tor=use_tor(a))
    txt = (b or b"").decode("utf-8", "ignore")
    ent = re.findall(r"<entry>(.*?)</entry>", txt, re.S)
    items = []
    for e in ent:
        g = lambda t: (re.search(rf"<{t}[^>]*>(.*?)</{t}>", e, re.S) or [None, None])[1]
        items.append({"id": g("id"), "title": (g("title") or "").strip(),
                      "author": (g("name") or "").lstrip("/u/").strip(),
                      "published": g("published") or g("updated"),
                      "link": (re.search(r'<link[^>]*href="([^"]+)"', e) or [None, None])[1]})
    if a.json:
        for it in items:
            print(json.dumps(it, ensure_ascii=False))
    else:
        print(f"HTTP {c} · {len(items)} entries")
        for it in items[:a.show]:
            print(f"  {str(it['published'])[:16]}  u/{it['author']} · {str(it['title'])[:60]}")
    return 0

def cmd_ops(a):
    t = op_table()
    for n in sorted(t):
        m = t[n]
        if a.filter and not re.search(a.filter, n, re.I):
            continue
        if a.write and m["kind"] != "write":
            continue
        if a.read and m["kind"] != "read":
            continue
        if a.json:
            print(json.dumps({"name": n, **m}, ensure_ascii=False))
        else:
            print(f"  {m['kind']:<5} {n}")
    return 0

def cmd_op(a):
    t = op_table()
    name = a.operation
    meta = t.get(name)
    if meta is None:
        near = [k for k in t if name.lower() in k.lower()][:6]
        sys.exit(f"[rweb] unknown operation `{name}`" + (f" — similar: {near}" if near else ""))
    if a.vars:
        try:
            variables = json.loads(a.vars)
        except json.JSONDecodeError as e:
            sys.exit(f"[rweb] --vars invalid JSON: {e}")
    else:
        variables = json.loads(json.dumps(meta.get("vars") or {}))
        for kv in (a.var or []):
            if "=" not in kv:
                sys.exit(f"[rweb] --var expects k=v, got {kv!r}")
            k, v = kv.split("=", 1)
            try:
                v = json.loads(v)
            except Exception:
                pass
            cur = variables
            parts = k.split(".")
            for p in parts[:-1]:
                cur = cur.setdefault(p, {})
            cur[parts[-1]] = v
    if is_write(name, meta) and not a.yes and not a.dry_run:
        sys.exit(f"[rweb] `{name}` is a WRITE operation — re-run with --yes to confirm.\n"
                 f"        variables would be: {json.dumps(variables, ensure_ascii=False)[:200]}")
    return call_op(name, variables, a)

def cmd_api(a):
    if a.dry_run:
        print(f"[dry-run] GET {BASE}{a.path}"); return 0
    c, b, h = api(a.path, a)
    txt = (b or b"").decode("utf-8", "ignore")
    if a.json:
        print(txt[:4000]); return 0
    print(f"HTTP {c}  {h.get('content-type','')[:40]}")
    print("  " + txt[:800])
    return 0

def cmd_write(a):
    if not a.yes and not a.dry_run:
        sys.exit(f"[rweb] `{a.cmd}` changes your account state — re-run with --yes.")
    if a.cmd == "vote":
        d = {"1": "1", "-1": "-1", "0": "0"}[str(a.direction)]
        body = f"id={a.id}&dir={d}&uh={a.uh or ''}"
        path = "/api/vote"
    elif a.cmd == "subscribe":
        body = f"action={'sub' if a.on else 'unsub'}&sr={a.name}&uh={a.uh or ''}&api_type=json"
        path = "/api/subscribe"
    else:
        sys.exit("[rweb] unsupported")
    if a.dry_run:
        print(f"[dry-run] POST {BASE}{path}\n  {body}"); return 0
    c, b, h = api(path, a, method="POST", data=body)
    print(f"HTTP {c}  {(b or b'').decode('utf-8','ignore')[:200]}")
    return 0 if c == 200 else 1

def build():
    ap = argparse.ArgumentParser(prog="rweb", description="reddit.com CLI (visitor + login)")
    ap.add_argument("--version", action="version", version=f"rweb {__version__}")
    ap.add_argument("--json", action="store_true", help="JSON output")
    ap.add_argument("--dry-run", action="store_true", help="build the request, do not send")
    ap.add_argument("--tor", action="store_true", help="route requests through Tor")
    ap.add_argument("--direct", action="store_true", help="force a direct connection")
    ap.add_argument("--jar", help="cookie jar path")
    ap.add_argument("--selftest", action="store_true",
                    help="self-check the bundled core module and run a live session setup")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("selftest", help="self-check the bundled core module")
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    p = sub.add_parser("session", help="get a visitor session"); p.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    p = sub.add_parser("login", help="store your account cookie")
    p.add_argument("--cookie", help='e.g. --cookie "token_v2=...; reddit_session=..."')
    p.add_argument("--cookie-file")
    p = sub.add_parser("doctor"); p.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    p = sub.add_parser("caps")
    p = sub.add_parser("me"); p.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    p = sub.add_parser("json", help="read any .json endpoint (listings/comments/search)")
    p.add_argument("path"); p.add_argument("--limit", default="100")
    p.add_argument("--pages", default="1"); p.add_argument("--out")
    p.add_argument("--show", type=int, default=15); p.add_argument("--no-session", action="store_true")
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    p = sub.add_parser("rss", help="read any .rss feed")
    p.add_argument("path"); p.add_argument("--show", type=int, default=15)
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    p = sub.add_parser("ops", help="operation table")
    p.add_argument("--filter"); p.add_argument("--read", action="store_true")
    p.add_argument("--write", action="store_true"); p.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    p = sub.add_parser("op", help="call any operation")
    p.add_argument("operation"); p.add_argument("--var", action="append")
    p.add_argument("--vars"); p.add_argument("--yes", action="store_true")
    p.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    p = sub.add_parser("api", help="raw GET on a legacy/JSON path")
    p.add_argument("path"); p.add_argument("--json", action="store_true", default=argparse.SUPPRESS)

    p = sub.add_parser("vote", help="vote a post/comment (--yes)")
    p.add_argument("id"); p.add_argument("direction", choices=["1", "-1", "0"])
    p.add_argument("--uh", help="modhash"); p.add_argument("--yes", action="store_true")
    p = sub.add_parser("subscribe", help="subscribe/unsubscribe a subreddit (--yes)")
    p.add_argument("name"); p.add_argument("--on", action="store_true")
    p.add_argument("--uh"); p.add_argument("--yes", action="store_true")
    return ap

def main():
    argv = sys.argv[1:]
    if "--selftest" in argv:
        argv = [x for x in argv if x != "--selftest"]
        a = build().parse_args(argv or [])
        return cmd_selftest(a) or 0
    a = build().parse_args()
    if not a.cmd:
        build().print_help(); return 0
    fn = {"session": cmd_session, "login": cmd_login, "doctor": cmd_doctor, "caps": cmd_caps,
          "me": cmd_me, "json": cmd_json, "rss": cmd_rss, "ops": cmd_ops, "op": cmd_op,
          "api": cmd_api, "vote": cmd_write, "subscribe": cmd_write,
          "selftest": cmd_selftest}.get(a.cmd)
    if fn is None:
        sys.exit(f"[rweb] unknown command {a.cmd}")
    rc = fn(a)
    if a.cmd == "login" and rc == 0:
        c = load_cfg(); c["logged_in"] = True; save_cfg(c)
    return rc or 0

if __name__ == "__main__":
    sys.exit(main())
