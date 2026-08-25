import fs from 'node:fs/promises';
import path from 'node:path';
import { Presentation, PresentationFile } from '@oai/artifact-tool';

const root = path.resolve(process.argv[2]);
const finalPath = path.resolve(process.argv[3]);
const repository = path.resolve('Image repository');
const inventoryDir = path.join(root, 'build-inventory');
const bgDir = path.join(root, 'bg-clean');
const previewDir = path.join(root, 'rendered-once');
const galleryDir = path.join(root, 'gallery-visual-match');
const manifest = JSON.parse(await fs.readFile(path.join(repository, 'manifest.json'), 'utf8'));
const inventories = (await fs.readdir(inventoryDir)).filter(x => x.endsWith('.inventory.json')).sort((a,b)=>a.localeCompare(b,'zh-CN'));

async function bytes(file) { const b = await fs.readFile(file); return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength); }
function tokenFill(semantic='') { const s=semantic.toLowerCase(); if(s.includes('red')) return '#C8152F'; if(s.includes('light blue')||s.includes('lightblue')) return '#DDECF9'; if(s.includes('blue')||s.includes('chart')||s.includes('arrow')) return '#1768B5'; return '#FFFFFF'; }
function geometry(item) { const s=`${item.kind||''} ${item.semantic||''}`.toLowerCase(); if(s.includes('chevron')) return 'chevron'; if(s.includes('arrow')) return 'rightArrow'; if(s.includes('circle')||s.includes('ellipse')) return 'ellipse'; if(s.includes('line')||s.includes('divider')) return 'line'; return 'roundRect'; }
function assetFor(stem, index) { const items = manifest.items.filter(x => x.asset_png && (x.keywords||[]).some(k=>String(k).includes(stem))); return items[index % Math.max(1,items.length)]; }
function addText(slide, line) { const [left,top,width,height]=line.sourceBBox; const text=String(line.text||''); const title=top<190 && height>=35; const shape=slide.shapes.add({geometry:'textbox',name:`ocr-${line.id}`,position:{left,top,width,height},fill:'none',line:{style:'solid',fill:'none',width:0}}); shape.text=text; shape.text.style={fontSize:Math.max(11,Math.min(52,height*(title?.72:.62))),fontFace:'Microsoft YaHei',bold:title || (/^[\d.]+[%万亿余]*$/.test(text) && height>30),color:title?'#135DA8':'#202020',margin:0,verticalAlignment:'middle'}; }
function addNative(slide, item) { const [left,top,width,height]=item.sourceBBox; const g=geometry(item); if(g==='line'){slide.shapes.add({geometry:'line',name:item.id,position:{left,top:top+height/2,width,height:0},fill:'none',line:{style:'solid',fill:item.stroke||'#CBD5E1',width:Math.max(1,Number(item.strokeWidth)||1)}});return;} const options={geometry:g,name:item.id,position:{left,top,width,height},fill:item.fill||tokenFill(item.semantic),line:{style:'solid',fill:item.stroke||'#D9E3F0',width:Math.max(0,Number(item.strokeWidth)||0)}}; if(g==='roundRect') options.borderRadius='rounded-md'; slide.shapes.add(options); }
async function main(){
  await fs.mkdir(path.dirname(finalPath),{recursive:true}); await fs.mkdir(previewDir,{recursive:true});
  const deck=Presentation.create({slideSize:{width:1600,height:900}}); const report=[];
  for(let slideIndex=0; slideIndex<inventories.length; slideIndex++){
    const filename=inventories[slideIndex]; const stem=filename.replace('.inventory.json',''); const record=JSON.parse(await fs.readFile(path.join(inventoryDir,filename),'utf8')); const slide=deck.slides.add(); slide.background.fill='#FFFFFF';
    const bg=path.join(bgDir,`${stem}.bg-clean.png`); if(await fs.stat(bg).then(()=>true).catch(()=>false)) slide.images.add({blob:await bytes(bg),contentType:'image/png',alt:`BG_CLEAN ${stem}`,fit:'stretch',position:{left:0,top:0,width:1600,height:900}});
    const native=record.elements.filter(x=>x.classification==='native_editable').sort((a,b)=>a.sourceBBox[1]-b.sourceBBox[1]||a.sourceBBox[0]-b.sourceBBox[0]); native.forEach(x=>addNative(slide,x));
    const matchName=(await fs.readdir(galleryDir).catch(()=>[])).find(name=>name===`${stem}.matches.json`); const matchMap=matchName?new Map((JSON.parse(await fs.readFile(path.join(galleryDir,matchName),'utf8')).results||[]).map(row=>[row.elementId,row.candidates?.[0]])):new Map();
    const assets=record.elements.filter(x=>['library_asset','qwen_image_asset','decorative_movable'].includes(x.classification)); let inserted=0;
    for(const [i,item] of assets.entries()){ const candidate=matchMap.get(item.id); const ranked=candidate ? manifest.items.find(x=>x.id===candidate.id && x.asset_png) : null; const asset=ranked || assetFor(stem,i); if(!asset?.asset_png) continue; const source=path.join(repository,asset.category,asset.asset_png); if(!await fs.stat(source).then(()=>true).catch(()=>false)) continue; const [left,top,width,height]=item.sourceBBox; slide.images.add({blob:await bytes(source),contentType:'image/png',alt:`${item.semantic||'gallery asset'}; source=${asset.id}`,fit:'contain',position:{left,top,width,height}}); inserted++; }
    record.elements.filter(x=>x.classification==='ocr_text').sort((a,b)=>a.sourceBBox[1]-b.sourceBBox[1]||a.sourceBBox[0]-b.sourceBBox[0]).forEach(x=>addText(slide,x));
    report.push({slide:slideIndex+1,stem,native:native.length,assetsRequested:assets.length,assetsInserted:inserted,text:record.elements.filter(x=>x.classification==='ocr_text').length});
    const png=await deck.export({slide,format:'png',scale:1}); await fs.writeFile(path.join(previewDir,`${String(slideIndex+1).padStart(2,'0')}-${stem}.png`),new Uint8Array(await png.arrayBuffer()));
  }
  const montage=await deck.export({format:'webp',montage:true,scale:1}); await fs.writeFile(path.join(previewDir,'montage.webp'),new Uint8Array(await montage.arrayBuffer()));
  const file=await PresentationFile.exportPptx(deck); await file.save(finalPath); await fs.writeFile(path.join(root,'one_pass_build_report.json'),JSON.stringify({schema:'candidate-grounded-one-pass-build/v1',output:finalPath,slides:report,postExportModificationForbidden:true},null,2));
}
main().catch(error=>{console.error(error);process.exitCode=1});
