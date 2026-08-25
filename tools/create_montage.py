from pathlib import Path
from PIL import Image, ImageDraw
import argparse

ap = argparse.ArgumentParser()
ap.add_argument('--indir', required=True)
ap.add_argument('--output', required=True)
a = ap.parse_args()
files = sorted(Path(a.indir).glob('slide-*.png'))
thumbs = []
for f in files:
    im = Image.open(f).convert('RGB')
    im.thumbnail((480,270))
    thumbs.append((f.name, im.copy()))
canvas = Image.new('RGB', (960, ((len(thumbs)+1)//2)*300), 'white')
d = ImageDraw.Draw(canvas)
for i, (name, im) in enumerate(thumbs):
    x = (i % 2) * 480
    y = (i // 2) * 300
    canvas.paste(im, (x, y + 20))
    d.text((x + 8, y + 3), name, fill='black')
canvas.save(a.output)
