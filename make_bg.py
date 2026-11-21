#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从名画生成海报底图（背景层），复用工作台同一套工艺：
   预饱和(feColorMatrix) → 各向异性拉丝(feGaussianBlur stdDeviation="横向 纵向")
   → 调色(saturate/contrast/brightness) → 暗角 → 颗粒

每张画只调四个东西：取景(zoom/cx/cy)、拉丝角度、拉丝强度、调色。
产出 1756×3138（画布 878×1569 的 2 倍），可直接丢进工作台当底图。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
W, H = 878, 1569          # 画布内部尺寸，与工作台一致

# ——— 四幅画的工艺参数 ———
# cx/cy: 取景焦点（0~1，画面内相对位置）；zoom: 1.0 = 刚好铺满，>1 放大
# blur: (横向, 纵向) 拉丝强度；拉丝方向跟画面主要线条平行
# presat: 进模糊前的饱和度，防止糊成一团脏色
# out: 输出文件名（中文），落在 out/bg/<out>@2x.png
PLANS = [
    dict(key="van-gogh-starry-night", out="星夜",
         name="梵高《星夜》", zoom=1.05, cx=0.66, cy=0.28,
         angle=0, blur=(24, 3), presat=1.15, sat=1.08, con=1.14, bri=0.96),
    dict(key="monet-impression-sunrise", out="日出",
         name="莫奈《日出·印象》", zoom=1.00, cx=0.40, cy=0.50,
         angle=0, blur=(22, 3), presat=1.24, sat=1.22, con=1.36, bri=0.84),
    dict(key="monet-water-lily-pond", out="睡莲",
         name="莫奈《睡莲池》", zoom=1.00, cx=0.50, cy=0.46,
         angle=0, blur=(26, 3), presat=1.10, sat=1.06, con=1.14, bri=0.95),
    dict(key="wang-ximeng-thousand-li", out="千里江山",
         name="王希孟《千里江山图》", zoom=1.25, cx=0.50, cy=0.50,
         angle=0, blur=(20, 2.5), presat=0.96, sat=1.02, con=1.22, bri=0.87),
]


def img_size(path):
    """读 JPEG 宽高（不依赖第三方库）。"""
    import struct
    with open(path, "rb") as f:
        d = f.read()
    i = 2
    while i < len(d):
        if d[i] != 0xFF:
            i += 1
            continue
        m = d[i + 1]
        if m in (0xC0, 0xC1, 0xC2):
            h, w = struct.unpack(">HH", d[i + 5:i + 9])
            return w, h
        if m in (0xD8, 0xD9):
            i += 2
            continue
        ln = struct.unpack(">H", d[i + 2:i + 4])[0]
        i += 2 + ln
    raise ValueError("读不出尺寸: " + path)


def build_html(p, src):
    """按 PLAN 生成一整页背景 HTML（无文字）。"""
    iw, ih = img_size(src)
    margin = max(p["blur"]) * 4          # 留边，避免模糊在画布边缘露出空气
    box_w, box_h = W + margin * 2, H + margin * 2
    s = max(box_w / iw, box_h / ih) * p["zoom"]
    dw, dh = iw * s, ih * s
    # 把取景点 (cx,cy) 对到画布上的相对位置，再居中
    x = (W - dw) * p["cx"]
    y = (H - dh) * p["cy"]
    uri = "file://" + src
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
  *{{margin:0;padding:0;box-sizing:border-box}}
  html,body{{width:{W}px;height:{H}px;background:#0a0d11;overflow:hidden}}
  .stage{{position:relative;width:{W}px;height:{H}px;overflow:hidden;background:#0a0d11}}
  .bg{{position:absolute;inset:0;filter:saturate({p['sat']}) contrast({p['con']}) brightness({p['bri']});}}
  .vignette{{position:absolute;inset:0;pointer-events:none;
    background:radial-gradient(120% 85% at 55% 38%,transparent 40%,rgba(4,24,38,.26) 100%);}}
  .grain{{position:absolute;inset:0;pointer-events:none;opacity:.15;mix-blend-mode:overlay;
    background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='200' height='200'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='3' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='200' height='200' filter='url(%23n)'/%3E%3C/svg%3E");}}
</style></head><body>
<div class="stage">
  <svg class="bg" viewBox="0 0 {W} {H}" preserveAspectRatio="none">
    <defs><filter id="sm" x="-15%" y="-15%" width="130%" height="130%" color-interpolation-filters="sRGB">
      <feColorMatrix type="saturate" values="{p['presat']}"/>
      <feGaussianBlur stdDeviation="{p['blur'][0]} {p['blur'][1]}"/>
    </filter></defs>
    <g transform="rotate({p['angle']} {W/2} {H/2})">
      <image href="{uri}" x="{x:.2f}" y="{y:.2f}" width="{dw:.2f}" height="{dh:.2f}"
             preserveAspectRatio="none" filter="url(#sm)"/>
    </g>
  </svg>
  <div class="vignette"></div>
  <div class="grain"></div>
</div></body></html>"""


def render(html_path, png_path):
    """用 Chrome 无头渲染，设备像素比 2 → 2 倍图。"""
    prof = "/tmp/bgprof-" + os.path.basename(html_path).split(".")[0]
    subprocess.run([
        CHROME, "--headless", "--disable-gpu", "--hide-scrollbars",
        "--allow-file-access-from-files", "--force-device-scale-factor=2",
        f"--window-size={W},{H}", "--virtual-time-budget=9000",
        f"--user-data-dir={prof}", f"--screenshot={png_path}",
        "file://" + html_path,
    ], check=True, capture_output=True)


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    outdir = os.path.join(ROOT, "out", "bg")
    os.makedirs(outdir, exist_ok=True)
    tmp = "/tmp/bgwork"
    os.makedirs(tmp, exist_ok=True)
    for p in PLANS:
        if only and only != p["out"]:
            continue
        src = os.path.join(ROOT, "assets", "art", p["key"] + ".jpg")
        if not os.path.exists(src):
            print("✗ 缺图:", src)
            continue
        html = os.path.join(tmp, p["out"] + ".html")
        png = os.path.join(outdir, p["out"] + "@2x.png")
        open(html, "w", encoding="utf-8").write(build_html(p, src))
        render(html, png)
        print("✓ %-22s → %s  %.1f MB" % (p["name"], os.path.basename(png),
                                         os.path.getsize(png) / 1048576))


if __name__ == "__main__":
    main()
