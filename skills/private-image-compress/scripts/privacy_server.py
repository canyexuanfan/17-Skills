#!/usr/bin/env python3
"""image-compressor local server.

点击选择：本地服务弹系统原生文件选择框 -> 直接拿到完整磁盘路径 -> 压缩写回原文件夹。
拖入图片：浏览器拿不到路径 -> 服务端按「文件名+字节大小」全盘智能反查原图 -> 写回其所在文件夹；
         反查不到（截图未另存等）-> 压缩拖入内容，写入最近成功过的文件夹。
Port 8765. 若端口被占用（服务已在跑）则只打开浏览器。
"""
import io
import json
import os
import string
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image

HOST = "127.0.0.1"
PORT = 8765
HERE = os.path.dirname(os.path.abspath(__file__))
IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

JPEG_KW = dict(optimize=True, progressive=True, subsampling="4:2:0")
QUALITY_FLOOR = 60
MAX_DOWNGRADE_ROUNDS = 6

# ---- original locating (drag-in fallback) ----
SKIP_DIRS = {"$recycle.bin", "system volume information", "windows", "appdata",
             "program files", "program files (x86)", "programdata", "msocache",
             "node_modules", ".git", "application data",
             "local settings"}
GLOBAL_DEADLINE = 150.0
DRIVE_BUDGET = 90.0
HINT_BUDGET = 20.0

# 隐私：不做任何落盘记录。会话目录缓存只存在于进程内存，服务退出即消失。
_session_dirs = []

def remember_dir(d):
    if not d or not os.path.isdir(d):
        return
    if d in _session_dirs:
        _session_dirs.remove(d)
    _session_dirs.insert(0, d)
    del _session_dirs[5:]

def session_dirs():
    return [d for d in _session_dirs if os.path.isdir(d)]


# ---------- compression core ----------

def target_size(img, mode, w, h, long_side):
    sw, sh = img.size
    if mode == "exact" and w and h:
        return (int(w), int(h))
    if mode == "long" and long_side:
        s = int(long_side) / max(sw, sh)
        return (round(sw * s), round(sh * s))
    return (sw, sh)


def encode(img, fmt, q=None):
    buf = io.BytesIO()
    if fmt == "jpeg":
        img.convert("RGB").save(buf, "JPEG", quality=q, **JPEG_KW)
    else:
        if img.mode not in ("P", "RGBA", "L"):
            img = img.convert("RGBA")
        colors = min(q if q else 256, 256)
        img.quantize(colors=colors, method=Image.Quantize.FASTOCTREE,
                     dither=Image.Dither.FLOYDSTEINBERG).save(buf, "PNG", optimize=True)
    return buf.getvalue()


def choose_fmt(img, fmt_hint):
    if fmt_hint in ("jpeg", "png"):
        return fmt_hint
    has_alpha = img.mode in ("RGBA", "LA") or (
        img.mode == "P" and "transparency" in img.info)
    return "png" if has_alpha else "jpeg"


def compress_image(src_path, max_bytes, mode, w, h, long_side, fmt_hint):
    img = Image.open(src_path)
    img.load()
    src_w, src_h = img.size
    fmt = choose_fmt(img, fmt_hint)
    locked = mode == "exact"
    tw, th = target_size(img, mode, w, h, long_side)
    cur = img.resize((tw, th), Image.LANCZOS) if (tw, th) != (src_w, src_h) else img.copy()
    q, quality, ok = 92, None, False

    if fmt == "jpeg":
        if max_bytes:
            lo, hi = 10, 95
            for _ in range(10):
                if lo > hi:
                    break
                q = (lo + hi) // 2
                data = encode(cur, fmt, q)
                if len(data) <= max_bytes:
                    quality, ok = q, True
                    lo = q + 1
                else:
                    hi = q - 1
            if not ok:
                raise ValueError("即使最低质量也无法满足大小限制，请放宽限制或减小尺寸")
            rounds = 0
            while quality < QUALITY_FLOOR and not locked and rounds < MAX_DOWNGRADE_ROUNDS:
                cur = img.resize((round(cur.size[0] * 0.92), round(cur.size[1] * 0.92)),
                                 Image.LANCZOS)
                lo, hi, q2, ok2 = quality, 95, None, False
                for _ in range(10):
                    if lo > hi:
                        break
                    q = (lo + hi) // 2
                    data = encode(cur, fmt, q)
                    if len(data) <= max_bytes:
                        q2, ok2 = q, True
                        lo = q + 1
                    else:
                        hi = q - 1
                if not ok2 or q2 <= quality:
                    break
                quality = q2
                rounds += 1
            data = encode(cur, fmt, quality)
        else:
            data = encode(cur, fmt, 92)
    else:  # png
        data = encode(cur, fmt, 256)
        if max_bytes:
            for colors in (256, 128, 64, 32, 16):
                data = encode(cur, fmt, colors)
                quality = colors
                if len(data) <= max_bytes:
                    break
            if len(data) > max_bytes:
                raise ValueError("PNG 调色板压到 16 色仍超限，建议改用 JPEG")

    out = Image.open(io.BytesIO(data))
    return data, fmt, {
        "src_size": os.path.getsize(src_path),
        "bytes": len(data),
        "width": out.size[0],
        "height": out.size[1],
        "quality": quality,
        "src_width": src_w,
        "src_height": src_h,
    }


def unique_dest_for(src_path, fmt):
    d = os.path.dirname(src_path)
    stem, _ = os.path.splitext(os.path.basename(src_path))
    ext = ".png" if fmt == "png" else ".jpg"
    for i in range(1, 100):
        suffix = "-compressed" if i == 1 else f"-compressed-{i}"
        cand = os.path.join(d, stem + suffix + ext)
        if not os.path.exists(cand):
            return cand
    raise ValueError("目标文件名冲突")


# ---------- original locating for drag-in files ----------


def fixed_drives():
    return [f"{L}:/" for L in string.ascii_uppercase if os.path.isdir(f"{L}:/")]


KEYWORD_CHARS = ("图", "照片", "相册", "壁纸", "素材", "截屏", "截图", "pic", "img",
                 "photo", "image", "screenshot", "wallpaper")

def _is_pic_dir(name_l):
    return any(k in name_l for k in KEYWORD_CHARS)

def _walk_match(root, name_l, size, deadline, hit, stop):
    """BFS 逐层扫描 + 图片特征目录优先递归（浅层命中为主）。

    hit[0] = 同名且同大小（最佳，立即返回）。
    hit[1] = 第一个同名但大小不同的文件（名称兜底；命中后全局收窄搜索窗口）。
    """
    from collections import deque
    q = deque([root])
    while q:
        if hit[0] or stop.is_set() or time.time() > deadline:
            return
        d = q.popleft()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        subdirs = []
        for e in entries:
            try:
                if e.is_file():
                    if e.name.lower() == name_l:
                        if e.stat().st_size == size:
                            hit[0] = e.path
                            return
                        if hit[1] is None:
                            hit[1] = e.path
                elif e.is_dir() and e.name.lower() not in SKIP_DIRS:
                    el = e.name.lower()
                    if _is_pic_dir(el):
                        # 名字像图片目录：立即优先深入搜索（限时）
                        _walk_match(e.path, name_l, size,
                                    min(deadline, time.time() + 15), hit, stop)
                        if hit[0]:
                            return
                    else:
                        subdirs.append(e.path)
            except OSError:
                continue
        q.extend(subdirs)


def _pick_best(hit):
    """hit[0] 同名同大小优先；否则同名文件兜底。"""
    if hit[0]:
        return hit[0], "size"
    if hit[1]:
        return hit[1], "name"
    return None, None


def find_original_smart(name, size):
    """定位原图：同名+同字节大小 最佳；否则同名文件兜底。"""
    name_l = name.lower()
    hit = [None, None]
    stop = threading.Event()

    for d in session_dirs():
        _walk_match(d, name_l, size, time.time() + HINT_BUDGET, hit, stop)
        if hit[0]:
            return hit[0], "size", True

    deadline = time.time() + GLOBAL_DEADLINE
    drives = fixed_drives()
    threads = []

    def scan(drive):
        _walk_match(drive, name_l, size,
                    min(deadline, time.time() + DRIVE_BUDGET), hit, stop)

    for drv in drives:
        t = threading.Thread(target=scan, args=(drv,), daemon=True)
        t.start()
        threads.append(t)
    t0 = time.time()
    name_hit_at = None
    while time.time() - t0 < GLOBAL_DEADLINE and not hit[0]:
        if not any(t.is_alive() for t in threads):
            break
        if hit[1] is not None:
            # 名称兜底已命中：给其他盘 10s 找同名同大小的更优匹配，然后收工
            if name_hit_at is None:
                name_hit_at = time.time()
            elif time.time() - name_hit_at > 10:
                stop.set()
                break
        time.sleep(0.3)
    stop.set()
    src, matched = _pick_best(hit)
    return src, matched, False


# ---------- HTTP layer ----------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, "application/json; charset=utf-8",
                   json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "privacy_index.html"), "rb") as f:
                self._send(200, "text/html; charset=utf-8", f.read())
        else:
            self._send(404, "text/plain", b"not found")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        if self.path == "/api/pick-files":
            return self._json(self._pick_files())
        elif self.path == "/api/pick-folder":
            return self._json(self._pick_folder())
        elif self.path == "/api/compress-paths":
            return self._json(self._compress_paths(body))
        elif self.path == "/api/compress-smart":
            return self._json(self._compress_smart(body))
        elif self.path.startswith("/api/upload-fallback"):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            name = qs.get("name", ["image.jpg"])[0]
            return self._json(self._upload_fallback(body, name))
        else:
            self._send(404, "text/plain", b"not found")

    # ---- native dialogs (run tkinter in its own thread) ----

    def _run_dialog(self, fn):
        result = {}

        def ask():
            try:
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk()
                root.withdraw()
                root.attributes("-topmost", True)
                result["value"] = fn(root)
                root.destroy()
            except Exception as e:
                result["error"] = f"无法打开系统选择框: {e}"

        t = threading.Thread(target=ask)
        t.start()
        t.join(timeout=300)
        return result or {"error": "选择超时"}

    def _pick_files(self):
        def fn(root):
            from tkinter import filedialog
            return root.tk.splitlist(filedialog.askopenfilenames(
                title="选择要压缩的图片",
                filetypes=[("图片", "*.png *.jpg *.jpeg *.webp *.bmp"),
                           ("所有文件", "*.*")]))
        r = self._run_dialog(fn)
        if "error" in r:
            return r
        paths = list(r.get("value") or [])
        return {"paths": paths}

    def _pick_folder(self):
        def fn(root):
            from tkinter import filedialog
            return filedialog.askdirectory(title="选择保存位置") or ""
        r = self._run_dialog(fn)
        if "error" in r:
            return r
        d = r.get("value") or ""
        if d:
            remember_dir(d)   # 仅进程内存，服务退出即消失
        return {"dir": d}

    # ---- compression entry points ----

    def _compress_one(self, path, req):
        return compress_image(path, req.get("maxBytes") or None,
                              req.get("mode", "keep"), req.get("width"),
                              req.get("height"), req.get("longSide"),
                              req.get("format", "auto"))

    def _compress_write(self, src, req):
        """压缩 src 并写回其所在文件夹，返回完整报告。"""
        data, fmt, rep = self._compress_one(src, req)
        dst = unique_dest_for(src, fmt)
        with open(dst, "wb") as f:
            f.write(data)
        remember_dir(os.path.dirname(src))
        rep.update({"src_path": src, "output": dst,
                    "out_name": os.path.basename(dst),
                    "folder": os.path.dirname(dst), "ok": True})
        return rep

    def _compress_paths(self, body):
        try:
            req = json.loads(body.decode("utf-8"))
        except Exception:
            return {"error": "bad json"}
        results = []
        for p in req.get("paths", []):
            if not os.path.isfile(p):
                results.append({"name": os.path.basename(p), "ok": False,
                                "error": "文件不存在"})
                continue
            try:
                rep = self._compress_write(p, req)
                rep["name"] = os.path.basename(p)
                results.append(rep)
            except Exception as e:
                results.append({"name": os.path.basename(p), "ok": False,
                                "error": str(e)})
        return {"results": results}

    def _compress_smart(self, body):
        """拖入文件：按名字+大小反查原图（同名兜底取最新）-> 压缩写回其所在文件夹。
        locate_only=1 时只定位不压缩（前端拖入即查）。"""
        try:
            req = json.loads(body.decode("utf-8"))
        except Exception:
            return {"error": "bad json"}
        name, size = req.get("name", ""), req.get("size", 0)
        src, matched, from_hint = find_original_smart(name, size)
        if not src:
            return {"ok": False, "found": False, "name": name,
                    "error": "没有在磁盘上找到同名原图"}
        if req.get("locate_only"):
            return {"ok": True, "located": True, "found": True,
                    "name": name, "src_path": src, "matched": matched,
                    "via_hint": from_hint}
        try:
            rep = self._compress_write(src, req)
            rep.update({"name": name, "found": True, "matched": matched,
                        "via_hint": from_hint})
            return rep
        except Exception as e:
            return {"ok": False, "found": True, "name": name, "error": str(e)}

    def _upload_fallback(self, body, name):
        """拖入且反查不到原图：压缩上传内容，写入本会话最近用过的保存位置。"""
        import tempfile
        folder = next((d for d in session_dirs()), None)
        if not folder:
            return {"ok": False, "need_dir": True, "name": name,
                    "error": "未找到原图，请选择一次保存位置（本次会话内记住，不落盘）"}
        ext = os.path.splitext(name)[1].lower() or ".jpg"
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        try:
            req = json.loads(qs.get("req", ["{}"])[0])
        except Exception:
            req = {}
        tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
        try:
            tmp.write(body)
            tmp.close()
            data, fmt, rep = self._compress_one(tmp.name, req)
            stem = os.path.splitext(os.path.basename(name))[0]
            out_ext = ".png" if fmt == "png" else ".jpg"
            dst = os.path.join(folder, stem + "-compressed" + out_ext)
            i = 2
            while os.path.exists(dst):
                dst = os.path.join(folder, f"{stem}-compressed-{i}{out_ext}")
                i += 1
            with open(dst, "wb") as f:
                f.write(data)
            rep.update({"name": name, "out_name": os.path.basename(dst),
                        "output": dst, "folder": folder, "ok": True,
                        "found": False})
            return rep
        except Exception as e:
            return {"ok": False, "name": name, "error": str(e)}
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass


def main():
    # 隐私清理：删除旧版本可能遗留的文件夹记录文件
    legacy = os.path.join(HERE, "config.json")
    if os.path.exists(legacy):
        try:
            os.remove(legacy)
        except OSError:
            pass
    s = __import__("socket").socket()
    try:
        s.bind((HOST, PORT))
        s.close()
    except OSError:
        print(f"端口 {PORT} 已被占用（可能服务已在运行），直接打开页面。")
        webbrowser.open(f"http://{HOST}:{PORT}")
        return
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"图片压缩器已启动: http://{HOST}:{PORT}  (Ctrl+C 退出)")
    threading.Timer(0.5, lambda: webbrowser.open(f"http://{HOST}:{PORT}")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
