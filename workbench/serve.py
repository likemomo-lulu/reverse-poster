#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""海报工作台 —— 本地服务

用途：
    在浏览器里改文案 / 换版式 / 换底图 / 调参数，实时预览，一键导出 @2x PNG。

用法：
    python3 workbench/serve.py            # 默认 8765 端口，自动打开浏览器
    python3 workbench/serve.py 9000       # 指定端口

实现要点：
    - 预览：前端把整份海报 HTML 塞进 iframe 的 srcdoc，路径按根目录解析。
    - 导出：前端把同一份 HTML POST 到 /render，服务端落成临时文件，调 Chrome
      headless 截图，回传 PNG。预览与导出共用同一份 HTML，不会出现"预览是一回事、
      导出来是另一回事"。
    - 临时文件放在仓库根目录，这样 HTML 里的 assets/、fonts/ 相对路径天然可用；
      Chrome 需要 --allow-file-access-from-files 才能读到本地图片。
"""

import http.server
import json
import mimetypes
import os
import subprocess
import sys
import threading
import time
import webbrowser
from urllib.parse import urlparse, parse_qs, quote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# 截图时临时落地的文件，统一放仓库根目录，保证相对路径可用
TMP_HTML = os.path.join(ROOT, "_wb_render.html")
TMP_PNG = os.path.join(ROOT, "_wb_render.png")


def render_png(html: str, scale: int = 2) -> bytes:
    """把一份 HTML 字符串渲染成 PNG 字节。

    关键点：
    - 每次先删旧文件：Chrome 截图失败时会静默跳过，旧的 PNG 会被误当成新结果。
    - 用 --virtual-time-budget 等字体和图片加载完，否则首帧可能是空白。
    """
    for p in (TMP_PNG,):
        if os.path.exists(p):
            os.remove(p)

    with open(TMP_HTML, "w", encoding="utf-8") as f:
        f.write(html)

    cmd = [
        CHROME,
        "--headless",
        "--disable-gpu",
        "--hide-scrollbars",
        "--allow-file-access-from-files",
        "--virtual-time-budget=5000",
        "--force-device-scale-factor=%d" % scale,
        "--window-size=404,722",
        "--screenshot=%s" % TMP_PNG,
        "file://" + TMP_HTML,
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=90)

    if not os.path.exists(TMP_PNG):
        msg = (proc.stderr or b"").decode("utf-8", "ignore")[-800:]
        raise RuntimeError("Chrome 没有产出截图：%s" % msg)

    with open(TMP_PNG, "rb") as f:
        data = f.read()
    # 临时文件都清掉，别在仓库里留垃圾
    for p in (TMP_PNG, TMP_HTML):
        if os.path.exists(p):
            os.remove(p)
    return data


IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".avif", ".bmp"}

# ——— 本机字体 ————————————————————————————————————————
# 浏览器出于指纹防护，不允许网页枚举系统字体；工作台是本地服务，可以由 Python
# 读字体文件的 name 表把家族名取出来，再交给页面做下拉选择。
FONT_EXT = {".ttf", ".otf", ".ttc", ".otc"}
FONT_DIRS = [
    os.path.expanduser("~/Library/Fonts"),
    "/Library/Fonts",
    "/System/Library/Fonts",
    "/System/Library/Fonts/Supplemental",
]
FONT_CACHE = {"at": 0.0, "data": None}
FONT_TTL = 120.0


def _ok_zh(t: str) -> bool:
    """判断一个字符串像不像正经中文名：必须有汉字，且不能掺别的文字系统。

    有些字体的 name 表编码是坏的（把 Mac-Roman 当 UTF-16 解），会解出
    "䄀瀀瀀氀攀" 这种看着是汉字、实际是乱码的东西，靠这条挡掉。
    """
    if not t or not any("\u4e00" <= c <= "\u9fff" for c in t):
        return False
    for c in t:
        if "\u4e00" <= c <= "\u9fff":      # 常用汉字
            continue
        if "\u3000" <= c <= "\u303f":      # 中文标点
            continue
        if c.isascii() and (c.isalnum() or c in " -_.()（）·"):
            continue
        if c in "·・（）——":
            continue
        return False
    return True


def _ok_en(t: str) -> bool:
    """英文名：只要可打印的拉丁字符，不含汉字。"""
    if not t or any("\u4e00" <= c <= "\u9fff" for c in t):
        return False
    return all(c.isascii() and c.isprintable() or "\u00a0" <= c <= "\u024f" for c in t)


def _font_names(path: str):
    """读字体 name 表，返回 (中文名, 英文名)，读不出返回 (None, None)。

    只 seek 读表头，不整文件加载；支持 ttf / otf / ttc（字体集合取第 0 个）。
    """
    try:
        with open(path, "rb") as f:
            head = f.read(12)
            if len(head) < 12:
                return None, None
            base = 0
            if head[:4] == b"ttcf":                      # 字体集合
                f.seek(8)
                n = int.from_bytes(f.read(4), "big")
                if n < 1:
                    return None, None
                f.seek(12)
                base = int.from_bytes(f.read(4), "big")
                f.seek(base)
                head = f.read(12)
                if len(head) < 12:
                    return None, None
            num = int.from_bytes(head[4:6], "big")
            if num <= 0 or num > 512:
                return None, None
            f.seek(base + 12)
            recs = f.read(16 * num)
            name_off = None
            for i in range(num):
                r = recs[i * 16:(i + 1) * 16]
                if len(r) < 16:
                    break
                if r[:4] == b"name":
                    name_off = int.from_bytes(r[8:12], "big") + base
                    break
            if name_off is None:
                return None, None
            f.seek(name_off)
            hdr = f.read(6)
            count = int.from_bytes(hdr[2:4], "big")
            str_off = int.from_bytes(hdr[4:6], "big")
            if count <= 0 or count > 4096:
                return None, None
            f.seek(name_off + 6)
            body = f.read(12 * count)
            got = {}
            for i in range(count):
                e = body[i * 12:(i + 1) * 12]
                if len(e) < 12:
                    break
                pid = int.from_bytes(e[0:2], "big")
                lid = int.from_bytes(e[4:6], "big")
                nid = int.from_bytes(e[6:8], "big")
                ln = int.from_bytes(e[8:10], "big")
                off = int.from_bytes(e[10:12], "big")
                if nid not in (1, 16) or ln <= 0 or ln > 512:
                    continue
                try:
                    f.seek(name_off + str_off + off)
                    raw = f.read(ln)
                    txt = raw.decode("utf-16-be") if pid in (0, 3) else raw.decode("latin-1")
                except Exception:
                    continue
                txt = txt.strip().replace("\x00", "")
                if not txt:
                    continue
                if pid == 3 and lid == 0x0804:            # Windows 简体中文
                    got.setdefault("zh%d" % nid, txt)
                elif pid == 3 and lid == 0x0409:          # Windows 英文
                    got.setdefault("en%d" % nid, txt)
                elif pid == 1 and nid == 1:               # Mac 罗马名
                    got.setdefault("mac", txt)
            zh = next((got[k] for k in ("zh16", "zh1") if _ok_zh(got.get(k, ""))), None)
            en = next((got[k] for k in ("en16", "en1", "mac") if _ok_en(got.get(k, ""))), None)
            return zh, en
    except Exception:
        return None, None


def list_fonts():
    """扫字体目录，返回 [{name, alt, file}]。

    name 是优先展示/使用的家族名（中文名优先，中文用户好找）；
    alt 是另一个语言的名字，写 font-family 时一起塞进备选链，避免某个名字
    浏览器认不出来时静默回退。
    """
    now = time.time()
    if FONT_CACHE["data"] is not None and now - FONT_CACHE["at"] < FONT_TTL:
        return FONT_CACHE["data"]
    out, seen = [], set()
    for d in FONT_DIRS:
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn.startswith("."):
                continue
            if os.path.splitext(fn)[1].lower() not in FONT_EXT:
                continue
            full = os.path.join(d, fn)
            if not os.path.isfile(full):
                continue
            zh, en = _font_names(full)
            name = (zh or en or "").strip()
            # 跳过系统私有字体（.LastResort / .Keyboard 之类）和取不出名字的
            if not name or name.startswith("."):
                continue
            # 名字里带控制字符说明这张表的编码是坏的，列出来也没法用
            if any(ord(c) < 32 for c in name):
                continue
            alt = en if name == zh else zh
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append({"name": name, "alt": alt or "", "file": fn})
    out.sort(key=lambda x: x["name"])
    FONT_CACHE["data"] = out
    FONT_CACHE["at"] = now
    return out


MAX_FILES = 500          # 文件夹里图片太多时截断，避免下拉框卡死


def resolve_dir(raw: str) -> str:
    """把用户输入的文件夹解析成绝对路径。

    相对路径按仓库根目录解析（所以默认填 assets 就能用）。
    """
    d = (raw or "assets").strip().strip('"').strip("'")
    d = os.path.expanduser(d)
    if not os.path.isabs(d):
        d = os.path.join(ROOT, d)
    return os.path.realpath(d)


def list_images(raw: str):
    """列出文件夹下的图片，按文件名排序。只扫一层，不递归。"""
    d = resolve_dir(raw)
    if not os.path.isdir(d):
        raise FileNotFoundError("文件夹不存在：%s" % d)
    names = []
    for name in sorted(os.listdir(d), key=str.lower):
        if name.startswith("."):
            continue
        full = os.path.join(d, name)
        if os.path.isfile(full) and os.path.splitext(name)[1].lower() in IMAGE_EXT:
            names.append(name)
    return d, names[:MAX_FILES], len(names)


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def log_message(self, fmt, *args):
        # 只保留错误，正常请求不刷屏
        if args and str(args[1]).startswith(("4", "5")):
            sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), fmt % args))

    def do_GET(self):
        # SimpleHTTPRequestHandler 不会剥查询串，/?layout=vary 会被当成目录
        url = urlparse(self.path)
        if url.path == "/api/images":
            return self._list_images(url.query)
        if url.path == "/api/fonts":
            return self._list_fonts()
        if url.path == "/bg":
            return self._serve_bg(url.query)
        if url.path in ("/", "/index.html"):
            self.path = "/workbench/index.html"
        super().do_GET()

    def _list_images(self, query):
        raw = (parse_qs(query).get("dir") or ["assets"])[0]
        try:
            d, names, total = list_images(raw)
        except Exception as e:
            self._json({"ok": False, "error": str(e)}, code=404)
            return
        self._json({"ok": True, "dir": d, "files": names, "total": total})

    def _list_fonts(self):
        try:
            fonts = list_fonts()
        except Exception as e:
            self._json({"ok": False, "error": str(e)}, code=500)
            return
        self._json({"ok": True, "fonts": fonts, "total": len(fonts)})

    def _serve_bg(self, query):
        """按绝对路径回传图片，供预览用。

        导出走的是 file://（服务端落临时文件后 Chrome 直读），所以这里只服务预览。
        仍然限制必须是图片后缀，避免被当成任意文件读取器。
        """
        raw = (parse_qs(query).get("path") or [""])[0]
        full = os.path.realpath(os.path.expanduser(raw))
        if not os.path.isfile(full):
            self.send_error(404, "file not found")
            return
        if os.path.splitext(full)[1].lower() not in IMAGE_EXT:
            self.send_error(403, "not an image")
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        try:
            with open(full, "rb") as f:
                data = f.read()
        except OSError as e:
            self.send_error(500, str(e))
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path == "/render":
            self._handle_render()
        elif self.path == "/quit":
            self._json({"ok": True})
            threading.Timer(0.3, lambda: os._exit(0)).start()
        else:
            self.send_error(404)

    def _json(self, payload, code=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_render(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(n).decode("utf-8"))
            html = payload["html"]
            scale = int(payload.get("scale", 2))
            png = render_png(html, scale)
        except Exception as e:  # 任何失败都回传给前端显示，不吞
            self._json({"ok": False, "error": str(e)}, code=500)
            return

        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(png)))
        self.end_headers()
        self.wfile.write(png)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    if not os.path.exists(CHROME):
        sys.exit("找不到 Chrome：%s" % CHROME)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = "http://127.0.0.1:%d/" % port
    print("工作台已启动：%s" % url)
    print("按 Ctrl+C 停止")
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
