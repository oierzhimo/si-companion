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
        self.frames={};self.durations={};self.cache=OrderedDict();self.mask_cache=OrderedDict();self.outline_cache=OrderedDict();self.sources=OrderedDict();self.portrait_cache=OrderedDict()
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
        return self.remember(self.sources,key,QPixmap(str(self.frames[state][index])),20)

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
            pix.setDevicePixelRatio(dpr);self.remember(self.cache,key,pix,48)
        self.cache.move_to_end(key)
        return self.cache[key]

    def mask(self,state,index,scale):
        key=(state,index,round(scale,2))
        if key not in self.mask_cache:self.remember(self.mask_cache,key,QRegion(self.pixmap(state,index,scale).mask()),32)
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

    def portrait_path(self,mood='neutral'):
        root=resource_root()/'assets'/'portraits'
        cand=root/f'{mood}.png'
        if cand.is_file():return cand
        fallback=root/'neutral.png'
        return fallback if fallback.is_file() else None

    def portrait(self,mood='neutral',size=48):
        key=(mood,int(size))
        if key in self.portrait_cache:
            self.portrait_cache.move_to_end(key)
            return self.portrait_cache[key]
        path=self.portrait_path(mood)
        if path is not None:
            pix=QPixmap(str(path)).scaled(size,size,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
        else:
            pix=self.pixmap('idle',0,1).copy(45,35,100,115).scaled(size,size,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
        return self.remember(self.portrait_cache,key,pix,16)

    def icon(self):
        root=resource_root()/'assets'
        for rel in ('portraits/icon_badge.png','si.ico'):
            p=root/rel
            if p.is_file():return QIcon(str(p))
        return QIcon(self.pixmap('idle',0,1).copy(45,35,100,115))
