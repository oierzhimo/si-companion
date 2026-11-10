import json
from collections import OrderedDict
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QIcon, QRegion, QPainter, QColor
from .storage import resource_root

class Sprites:
    def __init__(self):
        root=resource_root()/'assets'
        manifest=json.loads((root/'animations.json').read_text(encoding='utf-8'))
        self.frames={};self.durations={};self.cache=OrderedDict();self.mask_cache=OrderedDict();self.outline_cache=OrderedDict();self.sources=OrderedDict()
        for state,spec in manifest.items():
            self.frames[state]=[root/name for name in spec['files']]
            self.durations[state]=spec['durations']
            if any(not p.is_file() for p in self.frames[state]): raise RuntimeError(f'缺少动作素材：{state}')

    @staticmethod
    def remember(cache,key,value,limit):
        cache[key]=value;cache.move_to_end(key)
        while len(cache)>limit:cache.popitem(last=False)
        return value

    def source(self,state,index):
        key=(state,index)
        if key in self.sources:self.sources.move_to_end(key);return self.sources[key]
        return self.remember(self.sources,key,QPixmap(str(self.frames[state][index])),6)

    def index(self,state,elapsed_ms):
        ds=self.durations[state];t=elapsed_ms%sum(ds)
        for i,d in enumerate(ds):
            if t<d:return i
            t-=d
        return 0

    def pixmap(self,state,index,scale,dpr=1):
        key=(state,index,round(scale,3),round(dpr,3))
        if key not in self.cache:
            pix=self.source(state,index).scaled(round(192*scale*dpr),round(208*scale*dpr),Qt.AspectRatioMode.IgnoreAspectRatio,Qt.TransformationMode.SmoothTransformation)
            pix.setDevicePixelRatio(dpr);self.remember(self.cache,key,pix,12)
        self.cache.move_to_end(key)
        return self.cache[key]

    def mask(self,state,index,scale):
        key=(state,index,round(scale,2))
        if key not in self.mask_cache:self.remember(self.mask_cache,key,QRegion(self.pixmap(state,index,scale).mask()),16)
        return QRegion(self.mask_cache[key])

    def outline(self,state,index,scale,dpr=1):
        key=(state,index,round(scale,2),round(dpr,3))
        if key not in self.outline_cache:
            pix=QPixmap(self.pixmap(state,index,scale,dpr));pix.setDevicePixelRatio(1)
            tinted=QPixmap(pix.size());tinted.fill(Qt.GlobalColor.transparent)
            p=QPainter(tinted);p.drawPixmap(0,0,pix)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
            p.fillRect(tinted.rect(),QColor('#bde6d0'));p.end()
            tinted.setDevicePixelRatio(dpr);self.remember(self.outline_cache,key,tinted,6)
        return self.outline_cache[key]

    def icon(self):
        return QIcon(self.pixmap('idle',0,1).copy(45,35,100,115))
