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
