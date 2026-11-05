# -*- coding: utf-8 -*-
"""
market-lens 检索评估引擎
在 WorkBuddy 官方运营市场(技能/专家/专家包)与 SkillHub 社区市场中
按需求检索候选并做多维度打分。纯标准库，无第三方依赖。

用法:
  python market_search.py "需求描述" [--top 10] [--sources all|official_skills,official_experts,...] [--json] [--live]
  python market_search.py --check-name "候选名称" [--live]   # 发布前名称占用自检（精确+近似，命中占用时退出码 2）
"""
import argparse, json, math, os, re, sys, time, heapq, subprocess, urllib.request, urllib.parse, uuid
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("MARKET_SCOUT_CONFIG") or os.path.join(BASE, "..", "config.json")

# ---------------- 文本匹配 ----------------
EN_TOKEN = re.compile(r"[a-zA-Z][a-zA-Z0-9_.-]{1,}")
CN_RE = re.compile(r"[\u4e00-\u9fff]")

def bigrams(text):
    """中文取相邻双字，英文取小写词"""
    out = set()
    if not text:
        return out
    for m in EN_TOKEN.finditer(text):
        w = m.group(0).lower()
        out.add(w)
    cn = "".join(CN_RE.findall(text))
    for i in range(len(cn) - 1):
        out.add(cn[i:i + 2])
    for ch in cn:  # 单字也要给部分 credit（短查询）
        out.add(ch)
    return out

def relevance(qgrams, text, weight=1.0):
    t = bigrams(text)
    if not qgrams or not t:
        return 0.0
    hit = sum(1 for g in qgrams if g in t)
    # 命中查询 gram 的比例；对长文本召回有偏，用查全率口径
    return weight * hit / len(qgrams)

# ---------------- 检索优化：BM25 + 同义词查询扩展 ----------------
# Okapi BM25 方法论参考 rank_bm25/bm25s（均开源），实现为独立编写（binary tf 变体）
SYNONYMS = {
    "文档": "word doc 办公", "表格": "excel sheet 电子表格", "图片": "图像 image 画图",
    "截图": "screenshot 屏幕捕捉", "视频": "video 剪辑", "字幕": "subtitle caption",
    "音频": "声音 audio voice", "语音": "audio 转写 asr", "翻译": "translate",
    "爬虫": "抓取 crawl scrape spider", "浏览器": "browser", "数据库": "database sql",
    "代码": "code 编程", "笔记": "note", "邮件": "email mail", "日历": "calendar",
    "幻灯片": "ppt slides 演示", "密钥": "api key token secret",
    "压缩": "zip 解压 archive", "网页": "web html 页面", "清洗": "clean 数据处理",
    "透视": "pivot 汇总", "图表": "chart 可视化", "可视化": "chart 图表",
    "自动化": "automation rpa", "工作流": "workflow", "提示词": "prompt",
    "知识库": "knowledge rag", "搜索": "search 检索", "文件": "file 管理",
    "备份": "backup", "监控": "monitor", "安全": "security", "转写": "transcribe 语音",
    "简历": "resume", "合同": "contract", "发票": "invoice", "海报": "poster 设计",
    "小红书": "xiaohongshu 文案", "文案": "copywriting marketing", "营销": "marketing 增长",
}

def expand_query(query):
    """同义词查询扩展：命中中文关键词则附加英文/中文同义表达（提升跨语言召回）"""
    extra = [v for k, v in SYNONYMS.items() if k in query]
    return (query + " " + " ".join(extra)).strip() if extra else query

def _bm25_score(qterms, gset, df, N, avgdl, k1=1.5, b=0.75):
    """binary-tf BM25：qterms {gram: weight}，df {gram: 含该词文档数}"""
    if not gset:
        return 0.0
    dl = max(len(gset), 1)
    s = 0.0
    for gr, w in qterms.items():
        if gr in gset:
            dfv = df.get(gr, 0)
            idf = math.log(1 + (N - dfv + 0.5) / (dfv + 0.5))
            s += w * idf * (k1 + 1) / (1 + k1 * (1 - b + b * dl / avgdl))
    return s

def parse_dt(v):
    """兼容 ISO 字符串与毫秒时间戳"""
    if not v:
        return None
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v / 1000, tz=timezone.utc)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:
        return None

def freshness(dt, halflife_days=120):
    if not dt:
        return 0.35  # 无日期给中性偏低分
    days = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400)
    return math.exp(-days * math.log(2) / halflife_days)

def pop_norm(v, vmax):
    if v <= 0 or vmax <= 0:
        return 0.0
    return math.log10(1 + v) / math.log10(1 + vmax)

def _adoption(r):
    """效果验证（使用转化率，0-1）：装了的人是否真的在用。
    仅官方技能源有 use/install 双口径可算；其他源给中性 0.5（不偏袒不惩罚）。
    ⚠ 参考信号：低频专业功能使用天然少，需结合功能质量综合判断，不可单看"""
    use, inst = r.get("pop_raw") or 0, r.get("pop2_raw") or 0
    if r.get("source") != "官方市场·技能" or inst <= 0 or use <= 0:
        return 0.5
    return min(use / inst, 3.0) / 3.0

def _meta_quality(r):
    """元数据"交付认真度"分（0-1）：全量候选零成本可算的质量代理信号。
    ⚠ 这是代理，不是质量本身——功能是否真实实现只能靠源码深检裁决
    （决赛圈 inspect 通道）。刻意不使用安装/使用转化率：低频专业功能
    使用天然少，热度不该绑架质量评价。
      - 描述充实度（0.55）：是否讲清功能边界与用法，200-800 字最佳
      - 命名规范（0.15）/ 分类明确（0.15）/ 版本维护意识（0.15）"""
    parts = []
    dl = len((r.get("desc") or "").strip())
    parts.append((0.55, 0.7 if dl >= 800 else 1.0 if dl >= 200
                  else 0.75 if dl >= 80 else 0.4 if dl >= 30 else 0.0))
    t = (r.get("title") or "").strip()
    if t:
        parts.append((0.15, 1.0 if 4 <= len(t) <= 40 and not t.isupper()
                      else 0.6 if len(t) >= 2 else 0.0))
    parts.append((0.15, 1.0 if (r.get("category") or "").strip() else 0.0))
    parts.append((0.15, 1.0 if r.get("version") else 0.0))
    tot = sum(w for w, _ in parts) or 0.1
    return sum(w * v for w, v in parts) / tot

# ---------------- 数据源 ----------------
def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)

def iter_ndjson(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except Exception:
                    continue

def candidates_official_skills(path):
    vmax = 1
    rows = []
    for s in iter_ndjson(path):
        st = s.get("use_stat") or {}
        use = st.get("use_count", 0) or 0
        inst = st.get("install_count", 0) or 0
        vmax = max(vmax, use)
        rows.append({
            "title": s.get("display_name") or s.get("name") or "",
            "name": s.get("name") or "",
            "desc": s.get("description_zh") or s.get("description") or "",
            "category": (s.get("category_name") or "") + " " + " ".join(s.get("categories") or []),
            "version": s.get("version"),
            "updated": parse_dt(s.get("updated_at") or s.get("created_at")),
            "pop_raw": use,
            "pop2_raw": inst,
            "authority": 1.0 if s.get("namespace") in ("official",) else (0.9 if s.get("featured") else 0.8),
            "source": "官方市场·技能",
            "id": s.get("skill_id"),
            "extra": {"install_count": inst, "namespace": s.get("namespace"), "preinstalled": s.get("preinstalled")},
        })
    return rows, vmax

def candidates_official_experts(path):
    rows = []
    for e in iter_ndjson(path):
        rows.append({
            "title": e.get("display_name_zh") or e.get("display_name_en") or e.get("agent_name") or "",
            "name": e.get("agent_name") or "",
            "desc": e.get("description_zh") or e.get("description_en") or "",
            "category": e.get("profession_zh") or "",
            "version": e.get("version"),
            "updated": parse_dt(e.get("updated_at")),
            "pop_raw": 0,
            "pop2_raw": 0,
            "authority": {"official_sync": 1.0, "official": 1.0}.get(e.get("source"), 0.7),
            "source": "官方市场·专家",
            "id": e.get("expert_id"),
            "extra": {"origin_source": e.get("source"), "expert_type": e.get("expert_type")},
        })
    return rows, 1

def candidates_expert_pack(path):
    d = json.load(open(path, encoding="utf-8"))
    rows = []
    for e in d.get("experts", []):
        dn = e.get("displayName") or {}
        pf = e.get("profession") or {}
        ds = e.get("description") or {}
        rows.append({
            "title": dn.get("zh") or dn.get("en") or e.get("id"),
            "name": e.get("id") or "",
            "desc": ds.get("zh") or ds.get("en") or "",
            "category": pf.get("zh") or "",
            "version": None,
            "updated": parse_dt(e.get("updatedAt")),
            "pop_raw": 0, "pop2_raw": 0,
            "authority": 1.0,
            "source": "官方专家包(内置)",
            "id": e.get("id"),
            "extra": {"categoryId": e.get("categoryId")},
        })
    return rows, 1

def candidates_skillhub(path):
    vmax = 1
    rows = []
    for s in iter_ndjson(path):
        dl = s.get("downloads", 0) or 0
        vmax = max(vmax, dl)
        rows.append({
            "title": s.get("name") or s.get("slug") or "",
            "name": s.get("slug") or "",
            "desc": s.get("description_zh") or s.get("description") or (s.get("category") or "").replace("-", " "),
            "category": s.get("category") or "",
            "version": s.get("version"),
            "updated": parse_dt(s.get("updated_at")),
            "pop_raw": dl,
            "pop2_raw": s.get("stars", 0) or 0,
            "authority": 1.0 if s.get("verified") else 0.6,
            "source": "SkillHub",
            "id": (s.get("owner") or "") + "/" + (s.get("slug") or ""),
            "extra": {"stars": s.get("stars", 0), "verified": s.get("verified"), "owner": s.get("owner_display")},
        })
    return rows, vmax

LOADERS = {
    "official_skills": (candidates_official_skills, True),
    "official_experts": (candidates_official_experts, True),
    "official_expert_pack": (candidates_expert_pack, True),
    "skillhub": (candidates_skillhub, True),
}

# ---------------- 检索主流程 ----------------
FIELDS = ("title", "name", "desc")

def search(query, cfg, sources, top):
    """BM25 检索：同义词扩展查询 → 单遍扫描建 df/avgdl 统计并收集匹配文档
    → 分字段 BM25 + 字段权重（title .5 / name .2 / desc .3）归一化合成 relevance"""
    expanded = expand_query(query)
    q_main = {g: 1.0 for g in bigrams(query)}
    q_syn = {g: 0.45 for g in bigrams(expanded) if g not in q_main}
    qterms = {**q_main, **q_syn}
    if not qterms:  # 空查询兜底
        qterms, q_main = {g: 1.0 for g in bigrams(expanded)}, {}
    results = {}
    for src in sources:
        meta = cfg["sources"].get(src)
        if not meta:
            continue
        path = meta["path"]
        if path and not os.path.isabs(path):
            path = os.path.normpath(os.path.join(BASE, "..", path))  # 相对路径基于技能根目录解析
        if not os.path.exists(path):
            results[src] = {"error": f"数据文件不存在: {path}（可运行 scripts/pull_snapshot.py 构建本地索引，或加 --live 实时搜索）", "label": meta["label"]}
            continue
        loader, _ = LOADERS[src]
        rows, vmax = loader(path)
        w = cfg.get("weights", {})
        # pass 1: 语料统计 + 匹配收集（gram 集只对匹配文档驻留内存）
        N = 0
        df = {f: {} for f in FIELDS}
        sumdl = {f: 0 for f in FIELDS}
        matched = []  # (row, {field: gramset})
        qkeys = qterms.keys()
        for r in rows:
            gsets = {"title": bigrams(r["title"]), "name": bigrams(r["name"]),
                     "desc": bigrams(r["desc"])}
            N += 1
            hit = False
            for f in FIELDS:
                gs = gsets[f]
                sumdl[f] += len(gs)
                if gs & qkeys:
                    hit = True
                    for g in gs & qkeys:
                        df[f][g] = df[f].get(g, 0) + 1
            if hit:
                matched.append((r, gsets))
        # pass 2: 分字段 BM25 → 各字段除以自身最大值归一化 → 加权合成
        avgdl = {f: max(sumdl[f] / max(N, 1), 1.0) for f in FIELDS}
        fscore = {f: [_bm25_score(qterms, g[f], df[f], N, avgdl[f])
                      for (_, g) in matched] for f in FIELDS}
        fmax = {f: max(fscore[f]) if fscore[f] else 0.0 for f in FIELDS}
        scored = []
        for i, (r, _) in enumerate(matched):
            rel = sum((0.5 if f == "title" else 0.2 if f == "name" else 0.3) *
                      (fscore[f][i] / fmax[f]) for f in FIELDS if fmax[f] > 0)
            if rel <= 0:
                continue
            pop = pop_norm(r["pop_raw"], vmax)
            fr = freshness(r["updated"])
            au = r["authority"]
            qu = _meta_quality(r)
            ad = _adoption(r)
            total = (w.get("relevance", .35) * rel + w.get("quality", .20) * qu
                     + w.get("adoption", .10) * ad
                     + w.get("popularity", .15) * pop
                     + w.get("freshness", .10) * fr + w.get("authority", .10) * au)
            scored.append((total, rel, qu, ad, pop, fr, au, r))
        scored.sort(key=lambda x: -x[0])
        results[src] = {
            "label": meta["label"],
            "scanned": N,
            "matched": len(scored),
            "top": [
                {
                    "title": r["title"], "id": r["id"], "source": r["source"],
                    "version": r["version"],
                    "scores": {"total": round(t, 4), "relevance": round(rel, 4),
                               "quality": round(qu, 4), "adoption": round(ad, 4),
                               "popularity": round(pop, 4), "freshness": round(fr, 4),
                               "authority": round(au, 3)},
                    "pop_raw": r["pop_raw"], "pop2_raw": r["pop2_raw"],
                    "updated": r["updated"].date().isoformat() if r["updated"] else None,
                    "category": r["category"][:60],
                    "desc": (r["desc"] or "")[:220],
                    "extra": r["extra"],
                }
                for (t, rel, qu, ad, pop, fr, au, r) in scored[:top]
            ],
        }
    return results

# ---------------- 六维玫瑰风向图（纯 SVG，零依赖） ----------------
RADAR_AXES = [("契合", "relevance"), ("质量", "quality"), ("效果", "adoption"),
              ("热度", "popularity"), ("活跃", "freshness"), ("权威", "authority")]
RADAR_COLORS = ["#4f86f7", "#e5533d", "#3ba55d", "#f5a623", "#9b59b6", "#20b2aa"]

def render_radar_svg(cands, out_path, title="market-lens 六维评分"):
    """把候选的六维分数画成玫瑰风向图（雷达图），最多叠加 6 个候选。
    cands: [{"title":..., "scores": {...}}, ...]；写入 out_path 返回路径。"""
    W, H = 880, 620
    cx, cy, R = 320, H / 2 + 14, 208
    n = len(RADAR_AXES)

    def pt(i, r):
        ang = math.pi / 2 * 5 + 2 * math.pi * i / n  # 从顶部开始顺时针
        return cx + r * math.cos(ang), cy - r * math.sin(ang)

    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
           f'viewBox="0 0 {W} {H}" font-family="Microsoft YaHei, sans-serif">',
           f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
           f'<text x="{cx}" y="34" text-anchor="middle" font-size="20" font-weight="bold" fill="#333">{title}</text>']
    # 网格（5 层同心多边形 + 轴线 + 刻度）
    for ring in (0.2, 0.4, 0.6, 0.8, 1.0):
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in (pt(i, R * ring) for i in range(n)))
        svg.append(f'<polygon points="{pts}" fill="none" stroke="#e0e0e0" stroke-width="1"/>')
    for i, (label, _) in enumerate(RADAR_AXES):
        x, y = pt(i, R)
        svg.append(f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" stroke="#e0e0e0" stroke-width="1"/>')
        lx, ly = pt(i, R + 30)
        svg.append(f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="middle" font-size="15" fill="#555">{label}</text>')
    # 候选多边形（半透明叠加）
    for ci, c in enumerate(cands[:6]):
        s = c.get("scores") or {}
        pts = []
        for i, (_, key) in enumerate(RADAR_AXES):
            v = max(0.0, min(float(s.get(key, 0) or 0), 1.0))
            x, y = pt(i, R * (0.03 + 0.97 * v))
            pts.append(f"{x:.1f},{y:.1f}")
        color = RADAR_COLORS[ci % len(RADAR_COLORS)]
        svg.append(f'<polygon points="{" ".join(pts)}" fill="{color}" fill-opacity="0.16" '
                   f'stroke="{color}" stroke-width="2"/>')
        for p in pts:
            svg.append(f'<circle cx="{p.split(",")[0]}" cy="{p.split(",")[1]}" r="3" fill="{color}"/>')
    # 图例：右侧独立栏（与玫瑰图完全分离，不压图）
    items = cands[:6]
    if items:
        lx, ly0, lw = 636, 92, 232
        lh = 44 * len(items) + 24
        svg.append(f'<rect x="{lx - 12}" y="{ly0 - 34}" width="{lw + 24}" height="{lh}" rx="8" '
                   f'fill="#fafafa" stroke="#e0e0e0"/>')
        svg.append(f'<text x="{lx}" y="{ly0 - 12}" font-size="13" font-weight="bold" fill="#666">候选图例（总分降序）</text>')
        for ci, c in enumerate(items):
            color = RADAR_COLORS[ci % len(RADAR_COLORS)]
            t = (c.get("title") or "?")[:14]
            tot = (c.get("scores") or {}).get("total")
            tot_s = f"总分 {tot:.2f}" if isinstance(tot, (int, float)) else "总分 -"
            y1 = ly0 + 44 * ci + 12
            svg.append(f'<rect x="{lx}" y="{y1 - 11}" width="12" height="12" fill="{color}" rx="2"/>')
            svg.append(f'<text x="{lx + 18}" y="{y1}" font-size="13" fill="#333">{t}</text>')
            svg.append(f'<text x="{lx + 18}" y="{y1 + 17}" font-size="11" fill="#777">{tot_s}</text>')
    svg.append("</svg>")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(svg))
    return out_path

# ---------------- 实时补搜 ----------------
def _curl_json(url, method="GET", body=None, headers=None, timeout=30, retries=3, with_status=False):
    """用 curl 子进程请求（本机 urllib 走 Clash TUN 有 SSL EOF 问题，curl 已验证稳定）"""
    import subprocess, tempfile
    hdrs = {"user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36"}
    hdrs.update(headers or {})
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        if body is not None:
            f.write(body if isinstance(body, str) else json.dumps(body))
        body_fp = f.name
    try:
        for attempt in range(retries):
            cmd = ["curl", "-s", "-X", method, url, "-H", "accept: application/json",
                   "-H", "content-type: application/json", "--max-time", str(timeout),
                   "-w", "\n__WB_STATUS__%{http_code}"]
            if body is not None:
                cmd += ["--data-binary", "@" + body_fp]
            for k, v in hdrs.items():
                cmd += ["-H", f"{k}: {v}"]
            p = subprocess.run(cmd, capture_output=True, timeout=timeout + 10)
            raw = p.stdout.decode("utf-8", errors="replace")
            status = 0
            if "__WB_STATUS__" in raw:
                raw, _, sc = raw.rpartition("__WB_STATUS__")
                try:
                    status = int(sc.strip())
                except ValueError:
                    pass
            try:
                d = json.loads(raw)
                return (d, status) if with_status else d
            except json.JSONDecodeError:
                if attempt == retries - 1:
                    if with_status:
                        return (None, status)
                    raise
                time.sleep(1 + attempt)
    finally:
        try:
            os.unlink(body_fp)
        except OSError:
            pass

def _qenc(params):
    """空格必须编成 %20（服务端不认 + 号）"""
    return urllib.parse.urlencode(params, quote_via=urllib.parse.quote)

def live_skillhub(query, top=10):
    url = ("https://api.skillhub.cn/api/skills?" + _qenc(
        {"page": 1, "pageSize": min(50, top * 2), "keyword": query, "sortBy": "downloads", "order": "desc"}))
    try:
        d = _curl_json(url)
        items = d.get("data", {}).get("skills") or d.get("data", {}).get("items") or d.get("skills") or []
        out = []
        for s in items[:top]:
            out.append({"title": s.get("name"), "id": f"{s.get('owner') or ''}/{s.get('slug') or ''}",
                        "downloads": s.get("downloads"), "stars": s.get("stars"),
                        "version": s.get("version"), "desc": (s.get("description") or "")[:200]})
        return out
    except Exception as e:
        return [{"error": str(e)}]

def _load_bearer(cfg):
    """零配置认证：从用户本机 WorkBuddy 客户端登录态解密 Bearer accessToken。

    凭证永远来自用户自己的机器 / 用户自己设置的环境变量，本脚本不含任何硬编码凭证，
    只读内存解密，不写任何文件、不打印完整凭证（wb_auth.mask_token 掩码除外）。

    优先级：
      1) 环境变量 WORKBUDDY_BEARER_TOKEN（显式指定）
      2) 本机登录态（wb_auth.load_token_best 自包含解密，兼容 5.6.2+ AES-256-GCM
         加密信封）：Windows %LOCALAPPDATA%/APPDATA、macOS ~/Library/Application
         Support、Linux ~/.config 下的 CodeBuddyExtension/Data/Public/auth/
         workbuddy-desktop.info，以及 ~/.workbuddy/auth 兜底路径。uid 取登录态
         account.uid。
    返回 (token|None, uid|None, 来源说明)。
    """
    tok = os.environ.get("WORKBUDDY_BEARER_TOKEN", "").strip()
    if tok:
        return tok, (os.environ.get("WORKBUDDY_USER_ID")
                     or (cfg.get("auth", {}).get("user_id") or "")), "环境变量"
    try:
        import wb_auth  # 与本脚本同目录的自包含凭证模块
        token, _dom, path = wb_auth.load_token_best()
        uid = ""
        try:
            with open(path, "r", encoding="utf-8") as f:
                uid = (json.load(f).get("account", {}) or {}).get("uid") or ""
        except Exception:
            uid = (cfg.get("auth", {}).get("user_id") or "")
        return token, uid, "本机客户端登录态（自动解密）"
    except Exception:
        pass
    return None, None, "本机登录态不可用（可设置 WORKBUDDY_BEARER_TOKEN 或 auth.cookie_file）"

def live_official(query, cfg, kind="skills", top=10):
    auth = cfg.get("auth", {})
    bearer, uid, bearer_src = _load_bearer(cfg)
    cookie = open(auth["cookie_file"], encoding="utf-8").read().strip() if os.path.exists(auth.get("cookie_file", "")) else ""
    if not bearer and not cookie:
        return [{"error": "无可用凭据。优先路径（零配置）：确保 WorkBuddy 客户端已在本机登录；"
                          "兜底路径：浏览器打开 www.workbuddy.cn/app → F12 → Network → 刷新 → "
                          "找任意 /portal/ 请求 → Copy as cURL → 把其中 cookie 整行存入 "
                          "config.json 的 auth.cookie_file 指向的文件"}]
    headers = {
        "accept": "application/json", "content-type": "application/json",
        "origin": "https://www.workbuddy.cn",
        "referer": "https://www.workbuddy.cn/app/skills",
        "user-agent": auth["ua"],
        "x-request-id": uuid.uuid4().hex,
        "x-user-id": uid or auth["user_id"],
    }
    if bearer:
        headers["authorization"] = "Bearer " + bearer   # 已实测门户接口接受 Bearer
    else:
        headers["cookie"] = cookie
    req_body = None
    if kind == "skills":
        url = ("https://www.workbuddy.cn/portal/enterprises/personal/skill-market/list?"
               + _qenc({"source": "builtin", "page": 1, "page_size": top, "keyword": query}))
        item_key, total_key = "items", "total"
    else:
        url = "https://www.workbuddy.cn/portal/operation-platform/market/expert/list"
        req_body = {"page": 1, "page_size": top, "keyword": query,
                    "sort_by": "reco_rank", "sort_order": "desc"}
        item_key, total_key = "experts", "total_count"
    try:
        d, status = _curl_json(url, method="POST", body=req_body, headers=headers, with_status=True)
        if status == 401:
            if bearer:
                return [{"error": f"Bearer 令牌失效（401，来源：{bearer_src}）。"
                                  "请重新登录 WorkBuddy 客户端后重试；或改用 cookie 兜底"
                                  "（F12 → Copy as cURL → 存入 auth.cookie_file 指向的文件）"}]
            return [{"error": "登录 Cookie 已过期（401）。更新方法：浏览器打开 www.workbuddy.cn/app → F12 → Network → 刷新 → 找任意 /portal/ 请求 → Copy as cURL → 把其中 cookie 整行存入 config.json 的 auth.cookie_file 指向的文件"}]
        if d is None:
            return [{"error": f"接口返回非 JSON（HTTP {status}），可能为网络抖动，重跑一次"}]
        data = d.get("data") or {}
        out = []
        for it in (data.get(item_key) or [])[:top]:
            out.append({
                "title": it.get("display_name") or it.get("display_name_zh") or it.get("agent_name"),
                "id": it.get("skill_id") or it.get("expert_id"),
                "desc": (it.get("description_zh") or it.get("description") or "")[:200],
                "use_stat": it.get("use_stat"),
            })
        return out
    except Exception as e:
        return [{"error": str(e)}]

# ---------------- 源码级深检 ----------------
import zipfile, shutil, tempfile, string

# Pattern definitions (prompt injection / obfuscation / secret / dangerous script)
# are derived from getsentry/skills skill-scanner (scripts/scan_skill.py),
# Copyright (c) Sentry, licensed under the Apache License, Version 2.0.
# Modifications and extensions for market-lens by 十七° (2026). See THIRDPARTY-NOTICES.md.

PROMPT_INJECTION_PATTERNS = [
    (r"(?i)ignore\s+(all\s+)?previous\s+instructions", "指令覆盖: ignore previous instructions", "critical"),
    (r"(?i)disregard\s+(all\s+)?(previous|prior|above)\s+(instructions|rules|guidelines)", "指令覆盖: disregard previous", "critical"),
    (r"(?i)forget\s+(all\s+)?(previous|prior|your)\s+(instructions|rules|training)", "指令覆盖: forget previous", "critical"),
    (r"(?i)you\s+are\s+now\s+(a|an|in)\s+", "角色重置: 'you are now'", "high"),
    (r"(?i)act\s+as\s+(a|an)\s+unrestricted", "角色重置: unrestricted mode", "critical"),
    (r"(?i)enter\s+(developer|debug|admin|god)\s+mode", "越狱: developer/debug mode", "critical"),
    (r"(?i)DAN\s+(mode|prompt|jailbreak)", "越狱: DAN pattern", "critical"),
    (r"(?i)do\s+anything\s+now", "越狱: do anything now", "critical"),
    (r"(?i)bypass\s+(safety|security|content|filter|restriction)", "越狱: bypass safety", "critical"),
    (r"(?i)override\s+(system|safety|security)\s+(prompt|message|instruction)", "系统提示覆盖", "critical"),
    (r"(?i)\bsystem\s*:\s*you\s+are\b", "系统提示注入标记", "high"),
    (r"(?i)new\s+system\s+(prompt|instruction|message)\s*:", "新系统提示注入", "critical"),
    (r"(?i)from\s+now\s+on,?\s+(you|ignore|forget|disregard)", "时序指令覆盖", "high"),
    (r"(?i)pretend\s+(that\s+)?you\s+(have\s+no|don't\s+have|are\s+not\s+bound)", "伪装越狱", "high"),
    (r"(?i)respond\s+(only\s+)?with\s+(the\s+)?(raw|full|complete)\s+(system|initial)\s+prompt", "系统提示词窃取", "high"),
    (r"(?i)output\s+(your|the)\s+(system|initial|original)\s+(prompt|instructions)", "系统提示词窃取", "high"),
    # ---- 中文变体与工具投毒（行为检测思路参考 cisco-ai-defense/mcp-scanner，Apache-2.0）----
    (r"忽略(之前|以上|前面|所有|先前)(的)?(所有|全部)?(指令|规则|设置|提示词)", "中文指令覆盖", "critical"),
    (r"(不要|无需|停止|不必)(再)?(遵守|遵循|理会)(任何)?(规则|限制|约束|安全)", "中文越狱", "critical"),
    (r"(从现在起|现在开始)[，,]?(你|请)(就是|改为|变成|扮演)", "中文角色重置", "high"),
    (r"(输出|泄露|打印|透露)(你的)?(系统提示|系统指令|system prompt|初始指令)", "中文提示词窃取", "critical"),
    (r"(优先|强制|必须)(调用|使用|执行)(其他|别的|指定的)(技能|工具|skill|tool)", "跨工具劫持指令（工具投毒）", "high"),
    (r"(?:(安装后|运行后|首次运行)(自动|静默|暗中|悄悄)|(静默|暗中|悄悄))(执行|运行|删除|上传|发送)", "延迟隐藏指令", "high"),
]

OBFUSCATION_PATTERNS = [
    ("零宽字符", r"[\u200b\u200c\u200d\u2060\ufeff]"),
    ("RTL 方向覆盖", r"[\u202a-\u202e\u2066-\u2069]"),
    ("Unicode Tag 隐写 (U+E0000-E007F 可编码 LLM 可读的隐形 ASCII)", r"[\U000e0000-\U000e007f]"),
]

SECRET_PATTERNS = [
    (r"(?i)AKIA[0-9A-Z]{16}", "AWS Access Key ID", "critical"),
    (r"(?i)aws.{0,20}secret.{0,20}['\"][0-9a-zA-Z/+]{40}['\"]", "AWS Secret Access Key", "critical"),
    (r"ghp_[0-9a-zA-Z]{36}", "GitHub Personal Access Token", "critical"),
    (r"ghs_[0-9a-zA-Z]{36}", "GitHub Server Token", "critical"),
    (r"gho_[0-9a-zA-Z]{36}", "GitHub OAuth Token", "critical"),
    (r"github_pat_[0-9a-zA-Z_]{82}", "GitHub Fine-Grained PAT", "critical"),
    (r"sk-[0-9a-zA-Z]{20,}T3BlbkFJ[0-9a-zA-Z]{20,}", "OpenAI API Key", "critical"),
    (r"sk-ant-api03-[0-9a-zA-Z\-_]{90,}", "Anthropic API Key", "critical"),
    (r"xox[bpors]-[0-9a-zA-Z\-]{10,}", "Slack Token", "critical"),
    (r"hooks\.slack\.com/(?:services|workflows|triggers)/[A-Za-z0-9+/]{43,56}", "Slack Webhook URL", "critical"),
    (r"(?i)xapp-\d-[A-Z0-9]+-\d+-[a-z0-9]+", "Slack App Token", "critical"),
    # ---- 以下规则移植自 gitleaks v8（MIT License, Copyright (c) 2019 Zachary Rice）----
    (r"\bLTAI(?i:[a-z0-9]{20})\b", "阿里云 AccessKey ID (LTAI)", "critical"),
    (r"\bey[a-zA-Z0-9]{17,}\.ey[a-zA-Z0-9/\\_-]{17,}\.[a-zA-Z0-9/\\_-]{10,}={0,2}\b", "JWT 令牌", "high"),
    (r"glpat-[\w-]{20}", "GitLab Personal Access Token", "critical"),
    (r"\b(sk|rk)_(?:test|live|prod)_[a-zA-Z0-9]{10,99}\b", "Stripe API Key", "critical"),
    (r"\b[0-9]{5,16}:A[a-z0-9_\-]{34}\b", "Telegram Bot Token", "critical"),
    (r"\bSG\.(?i:[a-z0-9=_\-.]{66})\b", "SendGrid API Key", "critical"),
    (r"\bhf_[a-zA-Z0-9]{34}\b", "HuggingFace Access Token", "high"),
    (r"(?i)-----BEGIN[ A-Z0-9_-]{0,100}PRIVATE KEY(?: BLOCK)?-----", "私钥块（RSA/EC/OPENSSH/PGP 全覆盖）", "critical"),
    (r"(?i)(password|passwd|pwd)\s*[:=]\s*['\"][^'\"]{8,}['\"]", "硬编码密码", "high"),
    (r"(?i)(api[_-]?key|apikey)\s*[:=]\s*['\"][0-9a-zA-Z]{16,}['\"]", "硬编码 API key", "high"),
    (r"(?i)(secret|token)\s*[:=]\s*['\"][0-9a-zA-Z]{16,}['\"]", "硬编码 secret/token", "high"),
]

DANGEROUS_SCRIPT_PATTERNS = [
    # 数据外传
    (r"(?i)(requests\.(get|post|put)|urllib\.request|http\.client|aiohttp)\s*\(", "HTTP 请求（潜在外传）", "medium"),
    (r"(?i)(curl|wget)\s+", "Shell HTTP 请求", "medium"),
    (r"(?i)socket\.(connect|create_connection)", "裸 socket 连接", "high"),
    (r"(?i)subprocess.*\b(nc|ncat|netcat)\b", "netcat（潜在反弹 shell）", "critical"),
    # 凭证访问
    (r"(?i)(~|HOME|USERPROFILE).*\.(ssh|aws|gnupg|config)", "敏感目录访问", "high"),
    (r"(?i)open\s*\(.*(\.env|credentials|\.netrc|\.pgpass|\.my\.cnf)", "敏感文件访问", "high"),
    (r"(?i)os\.environ\s*\[.*(?:KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL)", "环境变量密钥访问", "medium"),
    # 危险执行
    (r"\beval\s*\(", "eval() 使用", "high"),
    (r"\bexec\s*\(", "exec() 使用", "high"),
    (r"(?i)subprocess.*shell\s*=\s*True", "shell=True 执行", "high"),
    (r"(?i)os\.(system|popen|exec[lv]p?e?)\s*\(", "OS 命令执行", "high"),
    (r"(?i)__import__\s*\(", "动态 import", "medium"),
    # Agent/shell 配置篡改
    (r"(?i)(open|write|Path).*\.(claude|bashrc|zshrc|profile|bash_profile)", "Agent/shell 配置修改", "critical"),
    (r"(?i)(open|write|Path).*(settings\.json|CLAUDE\.md|MEMORY\.md|\.mcp\.json)", "Agent 设置文件修改", "critical"),
    (r"(?i)(open|write|Path).*(\.git/hooks|\.husky)", "Git hooks 修改", "critical"),
    # 编码混淆
    (r"(?i)base64\.(b64decode|decodebytes)\s*\(", "Base64 解码（潜在混淆）", "medium"),
    (r"(?i)codecs\.(decode|encode)\s*\(.*rot", "ROT 编码混淆", "high"),
    (r"(?i)compile\s*\(.*exec", "动态代码编译", "high"),
    # ---- 以下检测思路移植自 PyCQA/bandit（Apache-2.0, Copyright (c) 2014 Hewlett-Packard Development Company）----
    (r"(?i)(pickle|marshal|dill|shelve)\.loads?\s*\(", "反序列化不可信数据（pickle.loads）", "high"),
    (r"(?i)yaml\.load\s*\((?![^)]*Loader)", "yaml.load 未指定 SafeLoader", "high"),
    (r"(?i)(verify\s*=\s*False|_create_unverified_context|CERT_NONE)", "TLS 证书校验禁用", "high"),
    (r"(?i)tempfile\.mktemp\s*\(", "tempfile.mktemp 竞态漏洞", "medium"),
]

# 常见可信域名（文档/包管理源），URL 检查时过滤
TRUSTED_DOMAINS = {
    "github.com", "api.github.com", "raw.githubusercontent.com",
    "pypi.org", "npmjs.com", "crates.io",
    "docs.python.org", "developer.mozilla.org", "stackoverflow.com",
    "agentskills.io", "wikipedia.org",
}

def _scan_text(content, path, sg):
    """对单个文本文件跑全部安全/质量规则，累加进信号 dict sg。
    官方 zip 与 SkillHub 源码直读两通道共用。"""
    low = path.lower()
    ext = os.path.splitext(low)[1]
    # 提示注入
    for pattern, desc, sev in PROMPT_INJECTION_PATTERNS:
        m = re.search(pattern, content)
        if m:
            sg.setdefault("injections", []).append(
                f"{path}: {desc} → '{m.group(0)[:50]}'")
            if sev == "critical":
                sg["critical_hits"] = sg.get("critical_hits", 0) + 1
            break  # 每文件记一条即可
    # Unicode 混淆隐写
    for desc, pattern in OBFUSCATION_PATTERNS:
        found = re.findall(pattern, content)
        if found:
            sg.setdefault("obfuscation", []).append(f"{path}: {desc} x{len(found)}")
            break
    # 密钥
    for pattern, desc, sev in SECRET_PATTERNS:
        for m in SECRET_PAT_CACHE.setdefault(desc, re.compile(pattern)).finditer(content):
            sg["secrets_found"].append(f"{path}: {desc} → {m.group(0)[:40]}...")
            if sev == "critical":
                sg["critical_hits"] = sg.get("critical_hits", 0) + 1
    # 危险脚本行为（仅脚本文件）
    if ext in (".py", ".js", ".ts", ".sh", ".ps1"):
        for pattern, desc, sev in DANGEROUS_SCRIPT_PATTERNS:
            m = re.search(pattern, content)
            if m:
                sg.setdefault("dangerous_behavior", []).append(
                    f"{path}: {desc} → '{m.group(0)[:50]}'")
                if sev == "critical":
                    sg["critical_hits"] = sg.get("critical_hits", 0) + 1
                break
    # TODO/烂尾
    sg["todo_fixme"] += len(re.findall(r"\b(TODO|FIXME|XXX)\b", content))
    # 外部 URL（过滤可信域名）
    for u in re.findall(r"https?://[a-zA-Z0-9._/-]{8,80}", content):
        host = u.split("//", 1)[-1].split("/")[0].lower()
        if any(host == d or host.endswith("." + d) for d in TRUSTED_DOMAINS):
            continue
        if u not in sg["hardcoded_urls"]:
            sg["hardcoded_urls"].append(u)

SECRET_PAT_CACHE = {}

def _frontmatter_desc(content):
    """从 SKILL.md frontmatter 提取所有 description* 字段（多语言），返回 {key: text}"""
    out = {}
    if not content.lstrip().startswith("---"):
        return out
    lines = content.lstrip().splitlines()[1:]
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("---"):
            break
        m = re.match(r"(description[a-z_]*):\s*(\||>)?\s*(.*)$", ln)
        if m:
            key, body = m.group(1), m.group(3).strip()
            buf = [body] if body else []
            i += 1
            while i < len(lines):
                sub = lines[i]
                if sub.startswith((" ", "\t")) or sub.strip() == "":
                    buf.append(sub.strip())
                    i += 1
                else:
                    break
            out[key] = " ".join(b for b in buf if b)
            continue
        i += 1
    return out

def _desc_consistency(fm_descs, market_desc):
    """frontmatter 各语言描述与市场描述的最高 bigram 重叠度，低 = 货不对板风险"""
    if not fm_descs or not market_desc:
        return None
    b = bigrams(market_desc[:400])
    if not b:
        return None
    best = 0.0
    for text in fm_descs.values():
        a = bigrams(text[:400])
        if a:
            best = max(best, sum(1 for g in a if g in b) / len(a))
    return round(best, 2)

def _fm_keys(content):
    """提取 frontmatter 顶层字段名列表（元数据完备性检查用，awesome-lint 思路）"""
    if not content.lstrip().startswith("---"):
        return []
    keys = []
    for ln in content.lstrip().splitlines()[1:]:
        if ln.startswith("---"):
            break
        if ln[:1] in (" ", "\t", "-") or ":" not in ln:
            continue
        m = re.match(r"([A-Za-z_][\w-]*):", ln)
        if m:
            keys.append(m.group(1).lower())
    return keys

def _inspect_zip(zip_path, market_desc=""):
    """解包并分析技能包内容，返回质量信号"""
    signals = {"files": [], "skmd_len": 0, "skmd_has_frontmatter": False,
               "skmd_desc_mismatch": None, "script_lines": 0, "script_files": 0,
               "languages": {}, "has_scripts_dir": False, "has_references": False,
               "has_assets": False, "secrets_found": [], "todo_fixme": 0,
               "total_files": 0, "total_bytes": 0, "hardcoded_urls": [],
               "injections": [], "obfuscation": [], "dangerous_behavior": [],
               "fm_keys": [],
               "errors": []}
    try:
        with zipfile.ZipFile(zip_path) as z:
            names = z.namelist()
            signals["total_files"] = len(names)
            for info in z.infolist():
                if info.is_dir():
                    continue
                signals["total_bytes"] += info.file_size
                low = info.filename.lower()
                if "/scripts/" in low or low.startswith("scripts/"):
                    signals["has_scripts_dir"] = True
                if "/references/" in low or low.startswith("references/"):
                    signals["has_references"] = True
                if "/assets/" in low or low.startswith("assets/"):
                    signals["has_assets"] = True
                ext = os.path.splitext(low)[1]
                if ext in (".py", ".js", ".ts", ".sh", ".ps1", ".rb", ".go"):
                    signals["script_files"] += 1
                    signals["languages"][ext] = signals["languages"].get(ext, 0) + 1
                signals["files"].append(info.filename)
                # 读小文本文件分析
                if info.file_size < 512 * 1024 and ext in (".md", ".py", ".js", ".ts", ".sh", ".json", ".yaml", ".yml", ".txt", ".ps1"):
                    try:
                        content = z.read(info.filename).decode("utf-8", errors="replace")
                    except Exception:
                        continue
                    if ext in (".py", ".js", ".ts", ".sh", ".ps1"):
                        signals["script_lines"] += content.count("\n") + 1
                    if low.endswith("skill.md") or low.endswith("skill.yaml") or (info.filename.count("/") == 0 and low.endswith(".md")):
                        signals["skmd_len"] = max(signals["skmd_len"], len(content))
                        if content.lstrip().startswith("---"):
                            signals["skmd_has_frontmatter"] = True
                        fm = _frontmatter_desc(content)
                        if fm:
                            signals["skmd_desc_mismatch"] = _desc_consistency(fm, market_desc)
                        signals["fm_keys"] = sorted(set(signals["fm_keys"]) | set(_fm_keys(content)))
                    _scan_text(content, info.filename, signals)
    except zipfile.BadZipFile:
        signals["errors"].append("zip 文件损坏")
    return signals

def _quality_verdict(sg, market_desc=""):
    """把源码信号转成结构化质量评价（供 AI 二次评估）"""
    checks = []
    def add(name, ok, detail):
        checks.append({"check": name, "pass": ok, "detail": detail})
    add("SKILL.md 完整性", sg["skmd_len"] >= 500,
        f"{sg['skmd_len']} 字符" + ("" if sg["skmd_has_frontmatter"] else "（⚠ 无 YAML frontmatter）"))
    add("脚本工程化", sg["script_files"] > 0 or sg["has_scripts_dir"],
        f"{sg['script_files']} 个脚本 / {sg['script_lines']} 行 {''.join(sorted(sg['languages'])) or ''}".strip())
    add("文档参考齐备", sg["has_references"] or sg["skmd_len"] >= 2000,
        "有 references/" if sg["has_references"] else "主文档较长可替代")
    add("无硬编码密钥", not sg["secrets_found"],
        sg["secrets_found"][0] if sg["secrets_found"] else "未扫出疑似密钥")
    inj = sg.get("injections") or []
    add("无提示注入", not inj,
        inj[0] if inj else "未检出指令覆盖/越狱/提示词窃取模式")
    obf = sg.get("obfuscation") or []
    add("无字符混淆隐写", not obf,
        obf[0] if obf else "无零宽字符/RTL覆盖/Unicode Tag 隐写")
    dang = sg.get("dangerous_behavior") or []
    add("无危险脚本行为", not dang,
        (f"共 {len(dang)} 处，首个: {dang[0]}" if dang else "脚本无危险模式"))
    add("内容完成度", sg["todo_fixme"] < 5, f"TODO/FIXME {sg['todo_fixme']} 处")
    dm = sg.get("skmd_desc_mismatch")
    if dm is None:
        add("描述一致性", True, "无法提取 frontmatter 描述，跳过")
    else:
        add("描述一致性（货不对板检查）", dm >= 0.3,
            f"SKILL.md 内描述与市场描述重叠度 {dm:.0%}" + ("——⚠ 实际能力与宣传可能不符" if dm < 0.3 else ""))
    # 元数据完备性（awesome-lint / anthropics skill-creator 结构规范思路）
    keys = sg.get("fm_keys") or []
    core_ok = "name" in keys and "description" in keys
    bonus = [k for k in ("keywords", "license", "version", "author", "metadata",
                         "argument-hint", "allowed-tools", "homepage") if k in keys]
    add("元数据完备性", core_ok,
        (f"frontmatter {len(keys)} 字段（{('、'.join(keys[:6])) or '无'}）"
         + (f"；加成字段 {len(bonus)} 个" if bonus else "；核心字段缺失" if not core_ok else "")) if keys else "未检出 frontmatter 字段")
    n_pass = sum(1 for c in checks if c["pass"])
    score = round(n_pass / len(checks), 2)
    # critical 熔断（方法论参考 OSSF Scorecard：critical 项命中即压制总分，
    # 防止"9 项过 6 项=0.67"把高危包稀释成及格分）
    if sg.get("critical_hits"):
        score = min(score, 0.3)
    return checks, score

CACHE_DIR = os.path.join(BASE, "..", "cache")

def _download_skill_zip(du, zp):
    """下载技能包到缓存路径，返回是否成功"""
    cmd = ["curl", "-s", "-L", "-o", zp, du, "--max-time", "120",
           "-H", "user-agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/156.0.0.0"]
    subprocess.run(cmd, capture_output=True, timeout=150)
    return os.path.exists(zp) and os.path.getsize(zp) > 100

def _inspect_one_official(s):
    """下载（带本地缓存+坏缓存自愈）并分析单个官方技能包"""
    du = s.get("download_url")
    out = {"title": s.get("display_name"), "id": s.get("skill_id"),
           "version": s.get("version"), "desc": s.get("description_zh") or s.get("description")}
    if not du:
        return {**out, "error": "该技能无 download_url（可能为服务化技能）"}
    os.makedirs(CACHE_DIR, exist_ok=True)
    zp = os.path.join(CACHE_DIR, (s.get("skill_id") or "unknown") + ".zip")
    out["from_cache"] = os.path.exists(zp) and os.path.getsize(zp) > 100
    if not out["from_cache"]:
        if not _download_skill_zip(du, zp):
            return {**out, "error": "下载失败（网络抖动可重跑）"}
    sg = _inspect_zip(zp, out["desc"] or "")
    if sg["errors"]:  # 缓存文件损坏 → 清掉重下一次
        try:
            os.unlink(zp)
        except OSError:
            pass
        if not _download_skill_zip(du, zp):
            return {**out, "error": "下载失败（重下后仍异常）"}
        out["from_cache"] = False
        sg = _inspect_zip(zp, out["desc"] or "")
        if sg["errors"]:
            return {**out, "error": "; ".join(sg["errors"])}
    out["zip_bytes"] = os.path.getsize(zp)
    checks, score = _quality_verdict(sg, out["desc"] or "")
    out["signals"] = {k: v for k, v in sg.items() if k != "files"}
    out["files_preview"] = sg["files"][:40]
    out["checks"] = checks
    out["quality_score"] = score
    return out

def inspect_official_skills(skill_ids, cfg):
    """批量深检：一次扫描索引定位全部 download_url（避免每个 id 重扫 43k 条）"""
    src = cfg["sources"]["official_skills"]
    wanted = set(skill_ids)
    found = {}
    for s in iter_ndjson(src["path"]):
        sid = s.get("skill_id")
        if sid in wanted:
            found[sid] = s
            if len(found) == len(wanted):
                break
    return [({**{"id": sid}, "error": "skill_id 不在本地快照中"} if sid not in found
             else _inspect_one_official(found[sid])) for sid in skill_ids]

SH_FILE_BASE = "https://api.skillhub.cn/api/v1/skills"

def _skillhub_fetch_file(slug, ns, path, retries=3):
    """拉 SkillHub 单文件源码：/file 端点 302 到 COS 直链，curl -L 跟随。
    Clash TUN 下 SSL 握手间歇性失败，空结果自动重试。"""
    url = f"{SH_FILE_BASE}/{urllib.parse.quote(slug)}/file?" + _qenc(
        {"namespace": ns, "path": path})
    for i in range(retries):
        cmd = ["curl", "-s", "-L", "--max-time", "30",
               "-H", "user-agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/156.0.0.0", url]
        p = subprocess.run(cmd, capture_output=True, timeout=40)
        content = p.stdout.decode("utf-8", errors="replace")
        if content.strip():
            return content
        time.sleep(1 + i)
    return content

def inspect_skillhub_skill(owner_slug, cfg):
    """SkillHub 技能源码级深检（全免登录）：
      /files 端点 → 文件清单（含 sha256/size）
      /file  端点 → 逐文件拉源码（302 → COS 直链）
      /versions 端点 → keen/sanbu 安全报告状态
    复用与官方包同一套 6 项质量检查。owner_slug 形如 'user_xxx/slug'。
    """
    owner, slug = owner_slug.split("/", 1)
    # 1) 详情（市场描述/发布者/下载量）
    d = _curl_json("https://api.skillhub.cn/api/skills?" + _qenc(
        {"page": 1, "pageSize": 5, "keyword": slug, "sortBy": "downloads", "order": "desc"}))
    items = d.get("data", {}).get("skills") or []
    hit = next((s for s in items if s.get("slug") == slug), None)
    if not hit:
        return {"error": f"SkillHub 未找到 {owner_slug}"}
    desc = hit.get("description_zh") or hit.get("description") or ""
    ns = (hit.get("namespace") or {}).get("handle") or owner
    pub = hit.get("publisher") or {}
    out = {
        "title": hit.get("name"), "id": owner_slug, "version": hit.get("version"),
        "downloads": hit.get("downloads"), "installs": hit.get("installs"),
        "stars": hit.get("stars"),
        "publisher": {"name": pub.get("name"), "verified": pub.get("verified"),
                      "certified": pub.get("certifiedName")},
        "homepage": hit.get("homepage"),
        "desc_full_len": len(desc), "desc": desc[:600],
        "labels": hit.get("labels") or {},
        "is_serviceized": hit.get("isServiceized"),
    }
    # 2) 安全报告（keen=科恩 tix.qq.com / sanbu=云鼎，平台侧已审）
    try:
        dv = _curl_json(f"{SH_FILE_BASE}/{urllib.parse.quote(slug)}/versions?" + _qenc({"namespace": ns}))
        vers = (dv.get("data") or dv).get("versions") or []
        if vers:
            rep = vers[0].get("securityReports") or {}
            out["security"] = {
                k: {"status": v.get("status"), "text": v.get("statusText"),
                    "report": v.get("reportUrl")}
                for k, v in rep.items() if isinstance(v, dict)}
    except Exception:
        pass
    # 3) 文件清单
    lf = _curl_json(f"{SH_FILE_BASE}/{urllib.parse.quote(slug)}/files?" + _qenc({"namespace": ns}))
    flist = (lf.get("data") or lf).get("files") or []
    if not flist:
        return {**out, "note": "文件清单不可达，仅元数据深检"}
    out["total_files"] = len(flist)
    out["total_bytes"] = sum(f.get("size") or 0 for f in flist)
    out["files_preview"] = [f.get("path") for f in flist][:40]
    # 4) 逐文件拉源码做安全/质量检查（与官方 zip 同一套 _scan_text 口径）
    sg = {"skmd_len": 0, "skmd_has_frontmatter": False, "skmd_desc_mismatch": None,
          "fm_keys": [],
          "script_lines": 0, "script_files": 0, "languages": {},
          "has_scripts_dir": False, "has_references": False, "has_assets": False,
          "secrets_found": [], "todo_fixme": 0, "hardcoded_urls": [],
          "injections": [], "obfuscation": [], "dangerous_behavior": []}
    sha_bad = []
    import hashlib
    for f in flist[:60]:  # 上限防失控
        path = f.get("path") or ""
        low = path.lower()
        ext = os.path.splitext(low)[1]
        if "/scripts/" in low or low.startswith("scripts/"):
            sg["has_scripts_dir"] = True
        if "/references/" in low or low.startswith("references/"):
            sg["has_references"] = True
        if "/assets/" in low or low.startswith("assets/"):
            sg["has_assets"] = True
        if ext in (".py", ".js", ".ts", ".sh", ".ps1", ".rb", ".go"):
            sg["script_files"] += 1
            sg["languages"][ext] = sg["languages"].get(ext, 0) + 1
        if (f.get("size") or 0) >= 512 * 1024 or ext not in (
                ".md", ".py", ".js", ".ts", ".sh", ".json", ".yaml", ".yml", ".txt", ".ps1"):
            continue
        content = _skillhub_fetch_file(slug, ns, path)
        if hashlib.sha256(content.encode("utf-8")).hexdigest() != (f.get("sha256") or ""):
            content = _skillhub_fetch_file(slug, ns, path)  # 可能截断/错误页，重拉一次
        if hashlib.sha256(content.encode("utf-8")).hexdigest() != (f.get("sha256") or ""):
            # COS 返回可能与清单 sha 串行化差异，仅记录不判废
            sha_bad.append(path)
        if ext in (".py", ".js", ".ts", ".sh", ".ps1"):
            sg["script_lines"] += content.count("\n") + 1
        if low.endswith("skill.md") or (path.count("/") == 0 and low.endswith(".md")):
            sg["skmd_len"] = max(sg["skmd_len"], len(content))
            if content.lstrip().startswith("---"):
                sg["skmd_has_frontmatter"] = True
            fm = _frontmatter_desc(content)
            if fm:
                sg["skmd_desc_mismatch"] = _desc_consistency(fm, desc)
            sg["fm_keys"] = sorted(set(sg["fm_keys"]) | set(_fm_keys(content)))
        _scan_text(content, path, sg)
    checks, score = _quality_verdict(sg, desc)
    out["signals"] = sg
    out["checks"] = checks
    out["quality_score"] = score
    out["sha_mismatch_files"] = sha_bad[:5]
    return out

# ---------------- 输出 ----------------
def render_md(query, results):
    lines = [f"# market-lens 检索结果：{query}", ""]
    for src, res in results.items():
        lines.append(f"## {res.get('label', src)}")
        if "error" in res:
            lines.append(f"- ⚠ {res['error']}")
            lines.append("")
            continue
        if "scanned" in res:
            lines.append(f"- 扫描 {res['scanned']} 条，命中 {res.get('matched', len(res['top']))} 条，返回 Top {len(res['top'])}")
        elif res["top"] and isinstance(res["top"][0], dict) and "error" in res["top"][0]:
            lines.append(f"- ⚠ 实时接口异常: {res['top'][0]['error']}")
        else:
            lines.append(f"- 实时 API 返回 Top {len(res['top'])}")
        if res["top"]:
            lines.append("")
            lines.append("| 综合分 | 契合 | **质量** | 效果 | 热度 | 活跃 | 权威 | 名称 | 来源ID | 更新 | 热度原始值 |")
            lines.append("|---:|---:|---:|---:|---:|---:|---:|---|---|---|---:|")
            for c in res["top"]:
                if "scores" not in c:  # 实时 API 条目，无本地打分
                    extra = c.get("downloads", c.get("use_stat", ""))
                    lines.append(f"| - | - | - | - | - | - | - | {c.get('title','')} | {c.get('id','')} | - | {extra} |")
                    continue
                s = c["scores"]
                lines.append(f"| {s['total']:.3f} | {s['relevance']:.2f} | {s.get('quality', 0):.2f} | "
                             f"{s.get('adoption', 0):.2f} | {s['popularity']:.2f} | "
                             f"{s['freshness']:.2f} | {s['authority']:.2f} | {c['title']} | {c['id']} | "
                             f"{c['updated'] or '-'} | {c['pop_raw']} |")
        lines.append("")
    # 跨源速览：质量/契合/权威跨源可比；热度跨源不可比
    pool = [c for src, res in results.items() for c in res.get("top", [])[:3] if "scores" in c]
    if len(results) > 1 and pool:
        pool.sort(key=lambda c: -(c["scores"].get("quality", 0) * 0.5 + c["scores"]["relevance"] * 0.5))
        lines.append("## ⚡ 跨源速览（按质量×契合排序；热度分跨源不可直接横比）")
        lines.append("")
        lines.append("| 质量 | 契合 | 名称 | 来源 | 综合分 |")
        lines.append("|---:|---:|---|---|---:|")
        for c in pool[:8]:
            lines.append(f"| {c['scores'].get('quality', 0):.2f} | {c['scores']['relevance']:.2f} | {c['title']} | {c['source']} | {c['scores']['total']:.3f} |")
        lines.append("")
    return "\n".join(lines)

def render_inspect(r):
    if "error" in r:
        return f"# 深检失败：{r.get('title') or r.get('id')}\n- ⚠ {r['error']}"
    lines = [f"# 源码级深检：{r.get('title')}（{r.get('id')}）v{r.get('version')}", ""]
    if "checks" in r:
        size_note = (f"{r.get('zip_bytes', 0)} 字节 zip" if r.get("zip_bytes")
                     else f"{r.get('total_files', '?')} 文件 / {r.get('total_bytes', 0)} 字节（SkillHub API 直读源码）")
        lines.append(f"**包体** {size_note} ｜ **源码质量分** {r.get('quality_score')}")
        if r.get("security"):
            sec = " ｜ ".join(f"{k}:{v.get('status')}" for k, v in r["security"].items())
            lines.append(f"**平台安全报告** {sec}")
        lines.append("")
        FUNC_CHECKS = ("SKILL.md 完整性", "脚本工程化", "文档参考齐备",
                       "内容完成度", "描述一致性", "元数据完备性")
        func = [c for c in r["checks"] if c["check"] in FUNC_CHECKS]
        sec_checks = [c for c in r["checks"] if c["check"] not in FUNC_CHECKS]
        lines.append("### 功能质量（能不能达到效果）")
        lines.append("")
        lines.append("| 检查项 | 结果 | 说明 |")
        lines.append("|---|---|---|")
        for c in func:
            lines.append(f"| {'✅' if c['pass'] else '⚠️'} {c['check']} | {'通过' if c['pass'] else '存疑'} | {c['detail']} |")
        lines.append("")
        lines.append("### 安全质量（有没有恶意/风险）")
        lines.append("")
        lines.append("| 检查项 | 结果 | 说明 |")
        lines.append("|---|---|---|")
        for c in sec_checks:
            lines.append(f"| {'✅' if c['pass'] else '⚠️'} {c['check']} | {'通过' if c['pass'] else '存疑'} | {c['detail']} |")
        sg = r.get("signals") or {}
        lines.append("")
        lines.append(f"- 文件总数 {r.get('total_files', sg.get('total_files'))}，脚本 {sg.get('script_files')} 个，"
                     f"目录含 scripts={sg.get('has_scripts_dir')} references={sg.get('has_references')} assets={sg.get('has_assets')}")
        if sg.get("hardcoded_urls"):
            lines.append(f"- 引用外部 URL {len(sg['hardcoded_urls'])} 个，前 5: {sg['hardcoded_urls'][:5]}")
        if r.get("sha_mismatch_files"):
            lines.append(f"- ⚠ sha256 与清单不一致: {r['sha_mismatch_files']}")
        lines.append("")
        lines.append("**文件清单（前 40）**：")
        for f in r.get("files_preview", []):
            lines.append(f"- {f}")
    else:
        # SkillHub 元数据深检
        for k in ("downloads", "installs", "homepage", "is_serviceized"):
            if r.get(k) is not None:
                lines.append(f"- {k}: {r[k]}")
        pub = r.get("publisher") or {}
        lines.append(f"- 发布者: {pub.get('name')}（认证: {pub.get('verified')}，{pub.get('certified') or '-'}）")
        lines.append(f"- 完整描述 {r.get('desc_full_len')} 字符：")
        lines.append("> " + (r.get("desc") or "").replace(chr(10), " "))
        if r.get("note"):
            lines.append(f"- ℹ {r['note']}")
    return "\n".join(lines)

def show_stats(cfg):
    """数据源新鲜度自检"""
    lines = ["# market-lens 数据源状态", ""]
    for name, meta in cfg["sources"].items():
        p = meta["path"]
        if not os.path.exists(p):
            lines.append(f"- ⚠ **{meta['label']}**: 文件缺失 {p}（需重建）")
            continue
        mt = os.path.getmtime(p)
        age_d = (time.time() - mt) / 86400
        mt_s = time.strftime("%Y-%m-%d %H:%M", time.localtime(mt))
        if p.endswith(".ndjson"):
            n = sum(1 for _ in iter_ndjson(p))
        else:
            try:
                n = len(json.load(open(p, encoding="utf-8")).get("experts", []))
            except Exception:
                n = "-"
        warn = " ⚠ **数据较旧，建议重建**" if age_d > 7 else ""
        lines.append(f"- **{meta['label']}**: {n} 条 ｜ 快照 {mt_s}（{age_d:.1f} 天前）{warn}")
    cache = sorted(os.listdir(CACHE_DIR)) if os.path.isdir(CACHE_DIR) else []
    lines.append("")
    lines.append(f"- 深检缓存: {len(cache)} 个技能包（{CACHE_DIR}）")
    auth = cfg.get("auth", {})
    cf = auth.get("cookie_file", "")
    if os.path.exists(cf):
        age = (time.time() - os.path.getmtime(cf)) / 86400
        lines.append(f"- 登录 Cookie: {age:.1f} 天前更新（7 天过期，过期后 live 会 401）")
    else:
        lines.append("- ⚠ 登录 Cookie 文件缺失，live/官方实时不可用")
    return "\n".join(lines)

# ---------------- 发布前名称占用自检 ----------------
def _norm_name(s):
    """名称归一化：小写并抹掉连字符/下划线/空格/点，'market-lens' 与 'Market_Lens' 视为同名"""
    return re.sub(r"[-_\s.]+", "", (s or "").lower())

def check_name(name, cfg, sources, top=5):
    """发布前名称占用自检：对候选技能名在指定数据源做精确与近似匹配。
    精确 = 归一化后与某技能的 slug/name/display_name 完全一致；
    近似 = 互为子串且长度差 <= 10（提示易混淆，建议避开）。
    返回 (report_lines, taken:bool)。"""
    raw = (name or "").strip()
    tgt = _norm_name(raw)
    if not tgt:
        return ["错误：名称为空"], False
    lines = [f"名称占用自检：{raw!r}（归一化 → {tgt!r}）", ""]
    taken = False
    for src in sources:
        meta = cfg["sources"].get(src)
        if not meta:
            continue
        path = meta["path"]
        if path and not os.path.isabs(path):
            path = os.path.normpath(os.path.join(BASE, "..", path))
        if not os.path.exists(path):
            lines.append(f"[{meta['label']}] ⚠ 数据缺失: {path}")
            lines.append("    → 该源未被本次自检覆盖！可运行 scripts/pull_snapshot.py 建索引后重跑，或加 --live 实时校验")
            continue
        loader, _ = LOADERS[src]
        rows, _ = loader(path)
        exact, near = [], []
        for r in rows:
            variants = {_norm_name(r.get("name")), _norm_name(r.get("title"))}
            variants.discard("")
            if tgt in variants:
                exact.append(r)
                continue
            if len(near) < top:
                for c in variants:
                    if (tgt in c or c in tgt) and min(len(c), len(tgt)) >= 5 and abs(len(c) - len(tgt)) <= 10:
                        near.append(r)
                        break
        if exact:
            taken = True
            lines.append(f"[{meta['label']}] ❌ 已被占用（{len(exact)} 条）：")
            for r in exact[:top]:
                lines.append(f"    - slug/name: {r['name']}  显示名: {r['title']}  id: {r['id']}  ver: {r.get('version')}  更新: {r.get('updated')}")
        else:
            lines.append(f"[{meta['label']}] ✅ 未占用")
        if near:
            lines.append("    ⚠ 相近名称（发布易混淆，建议避开）：")
            for r in near[:top]:
                lines.append(f"    - {r['name']} / {r['title']}  id: {r['id']}")
    return lines, taken

def check_name_live(name, cfg, top=10):
    """名称占用自检的实时补充：查在线 API 关键词命中里是否有精确同名（覆盖快照之后新上架的）。失败不影响离线结论。"""
    tgt = _norm_name(name)
    out = []
    hits = live_skillhub(name, top)
    for it in hits:
        if it.get("error"):
            out.append(f"[SkillHub·实时] 跳过: {it['error'][:80]}")
            continue
        if tgt in {_norm_name(it.get("title")), _norm_name(it.get("id", "").split("/")[-1])}:
            out.append(f"[SkillHub·实时] ❌ 已占用: {it.get('id')}  ver={it.get('version')}")
    lo = live_official(name, cfg, "skills", top)
    for it in lo:
        if it.get("error"):
            out.append(f"[官方技能·实时] 跳过: {it['error'][:80]}")
            continue
        if tgt in {_norm_name(it.get("title"))}:
            out.append(f"[官方技能·实时] ❌ 已占用: {it.get('title')}  id={it.get('id')}")
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="?", help="需求描述（检索模式）")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--sources", default="all")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--live", action="store_true", help="同时实时调用在线 API 补搜")
    ap.add_argument("--inspect", metavar="ID[,ID...]",
                    help="源码级深检（可逗号分隔批量）：官方技能传 skill_id，SkillHub 传 owner/slug")
    ap.add_argument("--stats", action="store_true", help="查看数据源新鲜度与缓存状态")
    ap.add_argument("--radar", metavar="OUT.svg", help="生成 top 候选六维玫瑰风向图 SVG")
    ap.add_argument("--check-name", metavar="NAME",
                    help="发布前名称占用自检：对候选技能名做四源精确+近似匹配，命中占用时退出码 2（可配合 --live 实时复核）")
    args = ap.parse_args()

    cfg = load_config()

    if args.check_name:
        srcs = list(cfg["sources"].keys()) if args.sources == "all" else args.sources.split(",")
        lines, taken = check_name(args.check_name, cfg, srcs)
        if args.live:
            live_lines = check_name_live(args.check_name, cfg)
            if live_lines:
                lines.append("")
                lines.extend(["-- 实时复核（覆盖快照后新上架） --"])
                lines.extend(live_lines)
                for l in live_lines:
                    if "❌" in l:
                        taken = True
        print("\n".join(lines))
        print("\n结论：" + ("❌ 名称已被占用，必须换名后重新自检" if taken else "✅ 全部数据源未发现占用，可以使用"))
        sys.exit(2 if taken else 0)

    if args.stats:
        print(show_stats(cfg))
        return

    if args.inspect:
        ids = [x.strip() for x in args.inspect.split(",") if x.strip()]
        results = []
        official = [i for i in ids if i.startswith("skill_")]
        community = [i for i in ids if not i.startswith("skill_")]
        if official:
            results.extend(inspect_official_skills(official, cfg))
        for cid in community:
            results.append(inspect_skillhub_skill(cid, cfg))
        if args.json:
            print(json.dumps(results if len(results) > 1 else results[0], ensure_ascii=False, indent=1))
        else:
            print("\n\n".join("\n".join(["---", render_inspect(r)]) for r in results))
        return

    if not args.query:
        ap.error("需要提供查询词或 --inspect")

    cfg = load_config()
    srcs = list(cfg["sources"].keys()) if args.sources == "all" else args.sources.split(",")
    results = search(args.query, cfg, srcs, args.top)
    if args.radar:
        pool = [c for src, res in results.items() for c in res.get("top", [])[:3] if "scores" in c]
        if pool:
            render_radar_svg(pool, args.radar, f"market-lens：{args.query[:20]}")
            print(f"玫瑰图已生成: {args.radar}")

    if args.live:
        results["_live_skillhub"] = {"label": "SkillHub(实时API)", "top": live_skillhub(args.query, args.top)}
        results["_live_official_skills"] = {"label": "官方技能(实时API)", "top": live_official(args.query, cfg, "skills", args.top)}
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=1))
    else:
        print(render_md(args.query, results))

if __name__ == "__main__":
    main()
