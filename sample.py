import zlib, struct, sys, colorsys

def read_png(path):
    data = open(path,'rb').read()
    assert data[:8] == b'\x89PNG\r\n\x1a\n'
    pos = 8; idat=b''; w=h=bd=ct=None
    while pos < len(data):
        ln = struct.unpack('>I', data[pos:pos+4])[0]
        typ = data[pos+4:pos+8]
        chunk = data[pos+8:pos+8+ln]
        if typ==b'IHDR':
            w,h,bd,ct,comp,filt,inter = struct.unpack('>IIBBBBB', chunk)
            assert bd==8 and inter==0, (bd,inter)
        elif typ==b'IDAT': idat += chunk
        elif typ==b'IEND': break
        pos += 12+ln
    raw = zlib.decompress(idat)
    nch = {0:1,2:3,3:1,4:2,6:4}[ct]
    stride = w*nch
    out = bytearray(h*stride); prev = bytearray(stride)
    p = 0
    for y in range(h):
        f = raw[p]; p+=1
        line = bytearray(raw[p:p+stride]); p+=stride
        if f==1:
            for i in range(nch, stride): line[i] = (line[i]+line[i-nch])&255
        elif f==2:
            for i in range(stride): line[i] = (line[i]+prev[i])&255
        elif f==3:
            for i in range(stride):
                a = line[i-nch] if i>=nch else 0
                line[i] = (line[i]+((a+prev[i])>>1))&255
        elif f==4:
            for i in range(stride):
                a = line[i-nch] if i>=nch else 0
                b = prev[i]; c = prev[i-nch] if i>=nch else 0
                pp = a+b-c
                pa,pb,pc = abs(pp-a),abs(pp-b),abs(pp-c)
                pr = a if (pa<=pb and pa<=pc) else (b if pb<=pc else c)
                line[i] = (line[i]+pr)&255
        out[y*stride:(y+1)*stride] = line
        prev = line
    return w,h,nch,out

def px(w,nch,out,x,y):
    i=(y*w+x)*nch
    return out[i],out[i+1],out[i+2]

for path in sys.argv[1:]:
    w,h,nch,out = read_png(path)
    print("="*70); print(path, w,h,"ch",nch)
    # grid sample
    rows = 8; cols = 6
    for ry in range(rows):
        y = int(h*(ry+0.5)/rows)
        line=[]
        for cx in range(cols):
            x = int(w*(cx+0.5)/cols)
            r,g,b = px(w,nch,out,x,y)
            line.append("#%02x%02x%02x"%(r,g,b))
        print(f"  y={ry:>2} ", " ".join(line))
    # dominant hues via coarse quantize
    from collections import Counter
    c=Counter()
    for y in range(0,h,7):
        for x in range(0,w,7):
            r,g,b = px(w,nch,out,x,y)
            c[(r//24*24, g//24*24, b//24*24)] += 1
    print("  top colors:")
    for (r,g,b),n in c.most_common(8):
        hh,l,s = colorsys.rgb_to_hls(r/255,g/255,b/255)
        print("    #%02x%02x%02x"% (r,g,b), f"n={n:<5d} hue={hh*360:5.0f} L={l*100:4.0f} S={s*100:3.0f}")

def scan(path, box, pred, label):
    w,h,nch,out = read_png(path)
    x0,y0,x1,y1 = [int(v) for v in box]
    acc=[0,0,0]; n=0
    for y in range(y0,min(y1,h)):
        for x in range(x0,min(x1,w)):
            r,g,b = px(w,nch,out,x,y)
            if pred(r,g,b):
                acc[0]+=r; acc[1]+=g; acc[2]+=b; n+=1
    if n: print(f"  {label}: #%02x%02x%02x  (n={n})"%(acc[0]//n,acc[1]//n,acc[2]//n))
    else: print(f"  {label}: none")

if __name__ == "__main__" and len(sys.argv)>1:
    pass
