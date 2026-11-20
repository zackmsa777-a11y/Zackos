#!/usr/bin/env python3
"""Render the ZackOS orbital wallpaper and the matching GRUB menu artwork."""
import math
from pathlib import Path
import random
import subprocess
import tempfile
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
SCALE = 2


def wordmark(folder):
    tree = ET.parse(HERE / "zackos.svg")
    tree.getroot().remove(tree.find("{http://www.w3.org/2000/svg}rect"))
    svg = folder / "wordmark.svg"
    tree.write(svg)
    png = folder / "wordmark.png"
    subprocess.run(["inkscape", str(svg), "-w", "1000", "-o", str(png)], check=True)
    mark = Image.open(png).convert("RGBA")
    return mark.crop(mark.getbbox())


def planet(radius):
    # Orthographic sphere. Vary the latitude of each band with the normal
    # rather than its screen position so the strata wrap around the limb.
    side = 2 * radius + 1
    v, u = np.mgrid[-radius:radius+1, -radius:radius+1].astype(np.float32) / radius
    z = np.sqrt(np.maximum(0, 1-u*u-v*v))
    lat = -0.30*u - 0.88*v + 0.36*z
    wind = lat + 0.026*np.sin(7*u+4*z) + 0.012*np.sin(20*u+8*lat)
    light = np.clip(0.52-0.39*u-0.41*v+0.14*z, 0, 1)
    bands = 0.075*np.sin(53*wind) + 0.030*np.sin(127*wind+1.5*z)
    score = np.clip(light + bands, 0, 1)
    colors = np.array([[14,34,41],[25,61,64],[53,100,92],[108,162,132],
                       [180,211,141],[229,242,171]], dtype=np.float32)
    stops = [0,.21,.43,.62,.83,1]
    rgb = np.stack([np.interp(score, stops, colors[:,k]) for k in range(3)], axis=-1)
    # Fine engraved lines and a narrow highlight echo a screen-printed atlas.
    engrave = np.clip((np.cos(240*wind)-.74)*1.5, 0, .27)
    rgb *= 1-engrave[:,:,None]
    edge = np.clip((.11-z)/.11,0,1) * np.clip(light,0,1)
    rgb += edge[:,:,None]*np.array([26,30,16])
    out = np.empty((side,side,4), dtype=np.uint8)
    out[:,:,:3] = np.clip(rgb,0,255)
    out[:,:,3] = np.where(u*u+v*v<=1,255,0)
    return Image.fromarray(out)


def render(size, menu, logo, destination):
    w, h = size
    W, H = w*SCALE, h*SCALE
    cx, cy = (int(w*(.83 if menu else .69)), int(h*(.46 if menu else .445)))
    radius = int(min(w*.206, h*.29))
    y, x = np.mgrid[:h,:w]
    glow = np.exp(-((x-cx)/(radius*1.65))**2-((y-cy)/(radius*1.6))**2)
    glow += .14*np.exp(-((x-w*.08)/(w*.4))**2-((y-h*.75)/(h*.7))**2)
    base = np.array([13,23,29]) + glow[:,:,None]*np.array([14,25,22])
    canvas = Image.fromarray(np.uint8(base)).resize((W,H),Image.Resampling.BILINEAR)
    draw = ImageDraw.Draw(canvas)
    rng = random.Random(116)
    def pt(a,b):
        return (round(a*SCALE),round(b*SCALE))
    def line(coords,color,width=1):
        draw.line([pt(a,b) for a,b in coords],fill=color,width=max(1,round(width*SCALE)))
    for _ in range(w*h//1450):
        a,b = rng.uniform(0,w),rng.uniform(0,h)
        c = rng.choice(["#35474b","#53645f","#263b40","#6d8175"])
        r = rng.choice([.45,.7,.85])
        draw.ellipse([*pt(a-r,b-r),*pt(a+r,b+r)],fill=c)
    tilt = math.radians(-21)
    def orbit(angle, r):
        ax, by = math.cos(angle)*radius*r, math.sin(angle)*radius*r*.26
        return (cx+ax*math.cos(tilt)-by*math.sin(tilt),cy+ax*math.sin(tilt)+by*math.cos(tilt))
    def rings(start, end):
        for r,c,weight in [(1.18,"#354d4b",1),(1.23,"#597369",1),
                           (1.26,"#8bad91",1.5),(1.30,"#587669",1),
                           (1.33,"#a9b99b",2.5),(1.36,"#789582",1),
                           (1.41,"#4e655e",1),(1.47,"#adc5a5",1),
                           (1.51,"#435852",1)]:
            line([orbit(a,r) for a in np.linspace(start,end,220)],c,weight)
        for _ in range(610):
            r=rng.uniform(1.22,1.46); a=rng.uniform(start,end)
            line([orbit(a,r),orbit(a+.004,r)],rng.choice(["#c1cba1","#698879","#384f4d"]),.55)
    rings(math.pi, 2*math.pi)
    ball=planet(radius*SCALE)
    canvas.paste(ball,pt(cx-radius,cy-radius),ball)
    rings(0, math.pi)
    # The white satellite and its three pellets follow the outer ring.
    a=-.92
    sx,sy=orbit(a,1.64)
    q=max(4,round(radius*.029))
    draw.pieslice([*pt(sx-q,sy-q),*pt(sx+q,sy+q)],26,334,fill="#f6f5de")
    for i in range(1,4):
        dx,dy=orbit(a+i*.048,1.64)
        draw.ellipse([*pt(dx-1.6,dy-1.6),*pt(dx+1.6,dy+1.6)], fill="#d9f880")
    logo_w=int(w*(.32 if menu else .23))
    logo_h=int(logo_w*logo.height/logo.width)
    mark=logo.resize((logo_w*SCALE,logo_h*SCALE),Image.Resampling.LANCZOS)
    left=w*.08
    top=h*(.225 if menu else .425)
    canvas.paste(mark,pt(left,top),mark)
    def label(x,y,s,sz,color,bold=False):
        family="DejaVuSansMono-Bold.ttf" if bold else "DejaVuSansMono.ttf"
        font=ImageFont.truetype(family,round(sz*SCALE))
        draw.text(pt(x,y),s,font=font,fill=color,stroke_width=0)
    if menu:
        label(left,h*.39,"Boot menu",max(15,h*.026),"#c8d9c9")
        line([(left,h*.895),(w*.92,h*.895)],"#3c514f")
        label(left,h*.913,"↑ ↓  Select       Enter  Boot       E  Edit",max(12,h*.020),"#a3b8b0")
    else:
        line([(left,h*.75),(left+w*.231,h*.75)],"#3a5151")
        for i,(keys,action) in enumerate([("SUPER + ENTER","Terminal"),("SUPER + B","Firefox"),("SUPER + F2","Files")]):
            ty=h*(.771+i*.033)
            label(left,ty,keys,w*.009,"#cde68c",True)
            label(left+w*.131,ty,action,w*.0095,"#afc0ba")
    result=canvas.resize(size,Image.Resampling.LANCZOS)
    result.save(destination,optimize=True)


if __name__=="__main__":
    with tempfile.TemporaryDirectory() as tmp:
        logo = wordmark(Path(tmp))
        render((1600,1000),False,logo,HERE.parent/"desktop/orbit.png")
        render((1024,768),True,logo,HERE/"grub/menu.png")
    # Nine-slice selection remains crisp at every GRUB resolution.
    for piece in ("nw","n","ne","w","c","e","sw","s","se"):
        tile=Image.new("RGBA",(6,6),"#d8ff62")
        if piece=="nw": ImageDraw.Draw(tile).polygon([(0,0),(4,0),(0,4)],fill=(0,0,0,0))
        if piece=="se": ImageDraw.Draw(tile).polygon([(5,5),(1,5),(5,1)],fill=(0,0,0,0))
        tile.save(HERE/"grub"/f"active_{piece}.png")
