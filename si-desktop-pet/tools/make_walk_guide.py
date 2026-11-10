"""Production-only gait landmarks; not character artwork or shipped frames."""
from PIL import Image,ImageDraw
from pathlib import Path
root=Path(__file__).resolve().parents[1]
phases=[((219,610),(245,740),(167,620),(130,734)),((205,610),(224,740),(143,610),(155,697)),((190,610),(190,740),(229,605),(201,690)),((168,615),(150,733),(225,610),(245,738))]
phases+= [(c,d,a,b) for a,b,c,d in phases]
for direction in ['right','left']:
    canvas=Image.new('RGB',(3072,1024),'#f5f5f5');draw=ImageDraw.Draw(canvas)
    for i,(nk,nf,fk,ff) in enumerate(phases):
        offset=i*384
        def point(p):return (offset+(p[0] if direction=='right' else 384-p[0]),p[1])
        draw.text((offset+20,60),f'PHASE {i+1}',fill='#555555')
        draw.line([point((192,325)),point((192,480))],fill='#555555',width=11)
        x,y=point((192,275));draw.ellipse((x-38,y-45,x+38,y+45),outline='#555555',width=7)
        draw.line([point((184,480)),point(fk),point(ff)],fill='#e38a3b',width=12)
        draw.line([point((195,480)),point(nk),point(nf)],fill='#3983d3',width=12)
        draw.line([point((140,740)),point((258,740))],fill='#aaaaaa',width=2)
    path=root/'art-source/v1.1';path.mkdir(parents=True,exist_ok=True)
    canvas.save(path/f'walk-guide-{direction}.png')
