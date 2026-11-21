#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从成品海报反推「干净底图」。

为什么不用抠图：每张海报的底图不是一张图片，而是 HTML 里的 <svg> 滤镜链
（预饱和 → 定向高斯模糊 → 调色 → 暗角）在浏览器里实时算出来的。所以把文字层
（.stage）隐藏、把画布还原成 1:1，再渲染一次，拿到的就是这条链路的原生输出——
比"把成品图上的字 P 掉"干净得多，也没有任何插值损失。

用法：
    python3 make_bg_from_poster.py            # 生成全部
    python3 make_bg_from_poster.py 草原青山    # 只生成指定几张

产物落在 assets/<中文名>.png，尺寸 878×1569（= 工作台画布原生尺寸）。
"""

import os
import subprocess
import sys

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
ROOT = os.path.dirname(os.path.abspath(__file__))
W, H = 878, 1569

# (海报 HTML, 输出中文名)
PLAN = [
    ("p1b-grass-photo.html",    "草原青山"),
    ("p2b-autumn-photo.html",   "暖金秋叶"),
    ("p3b-lavender-photo.html", "薰衣草花海"),
    ("p4-tea.html",             "雨雾茶园"),
    ("p5-sakura.html",          "樱湖春景"),
    ("p6-snow.html",            "黄昏雪原"),
    ("p7-blackgold.html",       "黑金天际线"),
    ("p8-wheat.html",           "麦黄田野"),
    ("p9-sea.html",             "暖阳海面"),
    ("p10-shanqiu.html",        "山丘"),
]

# 注入到 <head> 末尾的覆盖样式：
#   .stage 藏掉所有文字   .poster/.canvas 从 404×722 放大回 878×1569 原尺寸
#   .bg 强制到画幅大小 —— p2b 那张的 SVG viewBox 是 404×722，不强制会缩在左上角
OVERRIDE = (
    "<style>"
    ".stage{display:none!important}"
    ".poster{width:%dpx!important;height:%dpx!important}"
    ".canvas{width:%dpx!important;height:%dpx!important;transform:none!important}"
    ".bg{width:%dpx!important;height:%dpx!important}"
    "html,body{margin:0;background:#000}"
    "</style>"
) % (W, H, W, H, W, H)


def build_probe(src_html, out_html):
    """把海报 HTML 改写成「只剩底图」的探针页。"""
    s = open(src_html, encoding="utf-8").read()
    if "</head>" in s:
        s = s.replace("</head>", OVERRIDE + "</head>", 1)
    else:
        s = OVERRIDE + s
    # 底图是相对路径 ../assets/原图/，探针落在 /tmp 会断，换成绝对 file://
    s = s.replace('href="../assets/', 'href="file://%s/assets/' % ROOT)
    open(out_html, "w", encoding="utf-8").write(s)


def render(probe_html, out_png, profile_dir):
    """Chrome headless 截图：窗口就是画布尺寸，1 倍缩放，出图即原生像素。"""
    subprocess.run([
        CHROME, "--headless", "--disable-gpu", "--hide-scrollbars",
        "--allow-file-access-from-files",
        "--user-data-dir=" + profile_dir,
        "--virtual-time-budget=8000",
        "--force-device-scale-factor=1",
        "--window-size=%d,%d" % (W, H),
        "--screenshot=" + out_png,
        "file://" + probe_html,
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    only = set(sys.argv[1:])
    tmp = "/tmp/_make_bg_probe"
    os.makedirs(tmp, exist_ok=True)
    done = 0
    for html, name in PLAN:
        if only and name not in only:
            continue
        src = os.path.join(ROOT, "out", html)
        if not os.path.isfile(src):
            print("  跳过（找不到海报）：%s" % html)
            continue
        probe = os.path.join(tmp, name + ".html")
        out = os.path.join(ROOT, "assets", name + ".png")
        build_probe(src, probe)
        render(probe, out, os.path.join(tmp, "_prof_" + name))
        print("  %-12s ← %s" % (name + ".png", html))
        done += 1
    print("完成 %d 张，输出在 assets/" % done)


if __name__ == "__main__":
    main()
