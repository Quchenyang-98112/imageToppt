#!/usr/bin/env python3
"""Create the immutable 1600x900 SOURCE_REFERENCE canvas by contain+letterbox."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from PIL import Image

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--source',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--report',type=Path,required=True);args=ap.parse_args()
    image=Image.open(args.source).convert('RGB');ow,oh=image.size;tw,th=1600,900
    scale=min(tw/ow,th/oh);nw=max(1,round(ow*scale));nh=max(1,round(oh*scale));ox=(tw-nw)//2;oy=(th-nh)//2
    resized=image.resize((nw,nh),Image.Resampling.LANCZOS);canvas=Image.new('RGB',(tw,th),(255,255,255));canvas.paste(resized,(ox,oy))
    args.output.parent.mkdir(parents=True,exist_ok=True);canvas.save(args.output)
    report={'schema':'workbench-source-normalization/v3','originalSize':[ow,oh],'sourceReferenceSize':[tw,th],'mode':'contain_with_letterbox','scale':scale,'offset':[ox,oy],'contentSize':[nw,nh]}
    args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__':main()
