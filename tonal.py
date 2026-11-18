"""明度跨度实测：给一张图，报 5%~95% 分位的 L 和跨度。
用法: python3 tonal.py path [path...]"""
import sys, colorsys
sys.argv_saved = sys.argv; sys.argv = ['x']
exec(open('sample.py').read().split('if __name__')[0])

def tonal(path):
    w, h, nch, out = read_png(path)
    lum = []
    step = max(1, (w * h // 20000) ** 0.5)
    s = int(step)
    for y in range(0, h, s):
        for x in range(0, w, s):
            r, g, b = px(w, nch, out, x, y)
            lum.append(0.299 * r + 0.587 * g + 0.114 * b)
    lum.sort()
    n = len(lum)
    lo = lum[int(n * 0.05)] / 255 * 100
    hi = lum[int(n * 0.95)] / 255 * 100
    med = lum[n // 2] / 255 * 100
    return lo, med, hi, hi - lo

for p in sys.argv_saved[1:]:
    lo, med, hi, span = tonal(p)
    flag = "  OK" if span >= 45 else ("  偏平" if span >= 38 else "  太扁 ←")
    print("  %-34s L5=%3.0f 中位=%3.0f L95=%3.0f  跨度=%2.0f%s" % (p.split('/')[-1], lo, med, hi, span, flag))
