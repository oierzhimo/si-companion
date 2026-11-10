"""Render candidate art at desktop-pet canvas sizes for visual inspection."""
from pathlib import Path
import argparse
from PySide6.QtGui import QGuiApplication,QImage,QPainter,QColor,QFont,QPixmap
from PySide6.QtCore import QRect,Qt

root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--candidate',default='v11')
args=parser.parse_args()
if not args.candidate.startswith('v') or not args.candidate[1:].isdigit():
    raise SystemExit('Candidate must be a version such as v11')
app=QGuiApplication([])
source=QPixmap(str(root/f'art-source/v1.3/si-standing-candidate-{args.candidate}.png'))
if source.isNull():raise SystemExit('Candidate image missing')
canvas=QImage(1040,630,QImage.Format.Format_ARGB32);canvas.fill(QColor('#f1f4f0'))
p=QPainter(canvas);p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
p.setPen(QColor('#203d32'));p.setFont(QFont('Microsoft YaHei UI',15))
for x,y,w,h,title in [(65,85,192,208,'原始画布 192 × 208'),(385,85,384,416,'默认 2× 画布 384 × 416')]:
    p.drawText(x,y-16,title)
    p.fillRect(QRect(x,y,w,h),QColor('#dce5dc'))
    target=source.scaled(w,h,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
    p.drawPixmap(x+(w-target.width())//2,y+(h-target.height())//2,target)
    p.setPen(QColor('#a8b8aa'));p.drawRect(x,y,w,h);p.setPen(QColor('#203d32'))
p.end()
path=root/f'qa/v13-size-preview-{args.candidate}.png';path.parent.mkdir(exist_ok=True);canvas.save(str(path))
print(path)
