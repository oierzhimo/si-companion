"""Prepare runtime frames from generated originals. No drawing or invented poses."""
from pathlib import Path
from collections import deque
import json
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'art-source'
DEST=ROOT/'assets'

def remove_key(im):
    im=im.convert('RGBA');pixels=list(im.get_flattened_data());result=[]
    for r,g,b,a in pixels:
        distance=((255-r)**2+g*g+(255-b)**2)**.5
        if distance<125:result.append((0,0,0,0))
        else:
            # Edge decontamination for the deliberately absent magenta hue.
            spill=max(0,min(r,b)-g-24)
            if spill>0:r=max(g,r-spill);b=max(g,b-spill)
            result.append((r,g,b,a))
    im.putdata(result);return im

def components(im):
    width,height=im.size;alpha=bytearray(im.getchannel('A').tobytes());found=[]
    for seed in range(len(alpha)):
        if not alpha[seed]:continue
        alpha[seed]=0;todo=[seed];points=[]
        left=right=seed%width;top=bottom=seed//width
        while todo:
            i=todo.pop();points.append(i);x=i%width;y=i//width
            left=min(left,x);right=max(right,x);top=min(top,y);bottom=max(bottom,y)
            for n in ([i-1] if x else [])+([i+1] if x+1<width else [])+([i-width] if y else [])+([i+width] if y+1<height else []):
                if alpha[n]:alpha[n]=0;todo.append(n)
        if len(points)>30:found.append({'area':len(points),'box':(left,top,right+1,bottom+1),'center':(left+right)/2,'points':points})
    return found

def extract(name,count,target_height,max_width=174):
    im=remove_key(Image.open(SOURCE/name));parts=components(im)
    seeds=sorted(sorted(parts,key=lambda c:c['area'],reverse=True)[:count],key=lambda c:c['center'])
    if len(seeds)!=count or min(p['area'] for p in seeds)<1000:raise RuntimeError(f'{name}: not {count} separated complete figures')
    groups=[[p] for p in seeds]
    for p in parts:
        if any(p is seed for seed in seeds):continue
        groups[min(range(count),key=lambda i:abs(seeds[i]['center']-p['center']))].append(p)
    boxes=[(min(p['box'][0] for p in g),min(p['box'][1] for p in g),max(p['box'][2] for p in g),max(p['box'][3] for p in g)) for g in groups]
    scale=min(target_height/max(b[3]-b[1] for b in boxes),max_width/max(b[2]-b[0] for b in boxes))*8
    baseline=max(b[3] for b in boxes);top=min(b[1] for b in boxes)
    output=[]
    for i,(group,box) in enumerate(zip(groups,boxes)):
        crop=Image.new('RGBA',(box[2]-box[0],baseline-top))
        # Recover complete groups rather than cutting across neighboring source figures.
        source_pixels=im.load();target_pixels=crop.load()
        for part in group:
            for point in part['points']:
                x=point%im.width;y=point//im.width
                target_pixels[x-box[0],y-top]=source_pixels[x,y]
        crop=crop.resize((round(crop.width*scale),round(crop.height*scale)),Image.Resampling.LANCZOS)
        frame=Image.new('RGBA',(1536,1664));frame.alpha_composite(crop,((1536-crop.width)//2,1616-crop.height))
        output.append(frame)
    return output

def main():
    DEST.mkdir(exist_ok=True)
    states=['idle','running-right','running-left','waving','jumping','failed','waiting','running','review']
    counts=[6,8,8,4,5,8,6,6,6]
    ds=[[280,110,110,140,140,320],[120]*7+[220],[120]*7+[220],[140]*3+[280],[140]*4+[280],[140]*7+[240],[150]*5+[260],[120]*5+[220],[150]*5+[280]]
    manifest={}
    def save(state,frames,durations):
        files=[]
        for i,frame in enumerate(frames):
            dest=DEST/'hd'/state/f'{i:02d}.png';dest.parent.mkdir(parents=True,exist_ok=True);frame.save(dest)
            files.append(dest.relative_to(DEST).as_posix())
        manifest[state]={'files':files,'durations':durations,'master_size':[1536,1664]}
    for state,count,durations in zip(states,counts,ds):
        save(state,extract(f'original/{state}.png',count,163),durations)
    look=extract('original/look-row-9.png',8,163)+extract('original/look-row-10.png',8,163)
    save('look',look,[160]*16)
    all_extra=[]
    for name,filename,count,height,width in [('sitting','sitting-strip.png',4,132,174),('sleeping','sleeping-strip.png',4,86,176),('walk-right','v1.1/walk-right.png',8,158,174),('walk-left','v1.1/walk-left.png',8,158,174)]:
        frames=extract(filename,count,height,width)
        groups=[(name,frames)]
        for state,fs in groups:
            save(state,fs,[120]*len(fs) if state.startswith('walk') else [420,140,220,500])
            all_extra.extend((state,i,f.resize((192,208),Image.Resampling.LANCZOS)) for i,f in enumerate(fs))
    (DEST/'animations.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    icon=Image.open(DEST/manifest['idle']['files'][0]).resize((192,208)).crop((35,30,155,160));icon.save(DEST/'si.ico',sizes=[(16,16),(32,32),(48,48),(64,64),(128,128)])
    sheet=Image.new('RGBA',(192*8,208*3),'#edf3ed')
    for n,(state,i,frame) in enumerate(all_extra):sheet.alpha_composite(frame,((n%8)*192,(n//8)*208))
    (ROOT/'qa').mkdir(exist_ok=True);sheet.convert('RGB').save(ROOT/'qa/extra-contact.png')
    print(json.dumps({'animations':{k:len(v.get('files',[])) or v.get('count') for k,v in manifest.items()},'new_frames':len(all_extra)},indent=2))
if __name__=='__main__':main()
