"""Reusable still and animated card backgrounds."""
import re
import copy
import json
import math
import os
from pathlib import Path
import threading
import time
import uuid

LIMIT = 500
IMAGE_ID = re.compile(r'[0-9a-f]{64}\.(?:png|jpg|jpeg|webp)')


class CardFaceLibrary:
    def __init__(self, store):
        self.root = Path(store.root); self.path = self.root / 'card-face-library.json'
        self.lock = store.lock

    def _read(self):
        rows = json.loads(self.path.read_bytes()) if self.path.is_file() else []
        if not isinstance(rows, list) or len(rows) > LIMIT: raise ValueError('自制卡面库格式无效；原文件已保留')
        return rows

    def _write(self, rows):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name('card-face-library-' + uuid.uuid4().hex + '.tmp')
        try:
            temporary.write_bytes((json.dumps(rows, ensure_ascii=False, indent=2) + '\n').encode())
            os.replace(temporary, self.path)
        finally: temporary.unlink(missing_ok=True)

    @staticmethod
    def _kind(row):
        return 'image' if 'image' in row else 'motion'

    @classmethod
    def _key(cls, row):
        if cls._kind(row) == 'image': return ('image', row['image']['asset_id'])
        motion = row['custom_motion']
        return (motion['large']['asset_id'], motion['mini']['asset_id'])

    @staticmethod
    def _validate(row):
        if 'image' in row:
            asset = row['image'].get('asset_id') if isinstance(row['image'], dict) else None
            if not isinstance(asset, str) or not IMAGE_ID.fullmatch(asset): raise ValueError('图片卡底素材编号无效')
        else:
            motion=row.get('custom_motion')
            if not isinstance(motion,dict)or set(motion)!={'large','mini'}:raise ValueError('动态卡面配置无效')
            for role in ('large','mini'):
                item=motion.get(role)
                if not isinstance(item,dict) or not re.fullmatch(r'[0-9a-f]{64}\.usm',str(item.get('asset_id',''))):
                    raise ValueError('动态卡面视频素材无效')
                preview=item.get('preview')
                if not isinstance(preview,dict) or not re.fullmatch(r'[0-9a-f]{64}\.webm',str(preview.get('asset_id',''))):
                    raise ValueError('动态卡面预览素材无效')
                expected=(182,256)if role=='large'else(128,128)
                duration=item.get('duration')
                if(item.get('width'),item.get('height'))!=expected or type(duration)not in(int,float)or not math.isfinite(duration)or not 0<duration<=30:
                    raise ValueError('动态卡面尺寸或时长无效')
                if any(type(item.get(k,0))not in(int,float)or not math.isfinite(item.get(k,0))or not -100<=item.get(k,0)<=100 for k in('crop_x','crop_y')):
                    raise ValueError('动态卡面裁切位置无效')

    def _assets(self, row):
        if self._kind(row) == 'image': return [row['image']['asset_id']]
        motion = row['custom_motion']
        return [a for role in ('large', 'mini') for a in (motion[role]['asset_id'], motion[role]['preview']['asset_id'])]

    def _available(self, row):
        # Every movie/preview/image must still be in this editor's asset store.
        return all((self.root / 'assets' / asset).is_file() for asset in self._assets(row))

    def list(self):
        with self.lock:
            rows = []
            for row in self._read():
                try: self._validate(row); rows.append(dict(row, kind=self._kind(row), available=self._available(row)))
                except (ValueError, KeyError, TypeError, AttributeError): continue
            return rows

    def save(self, body):
        if body.get('image') is not None:
            image = body['image']
            entry = {'image': {'asset_id': image.get('asset_id') if isinstance(image, dict) else None}}
            missing = '这张卡底图片已不在素材库，请重新导入'
        else:
            entry = {'custom_motion': copy.deepcopy(body.get('custom_motion'))}
            missing = '这个动态背景的视频素材已不在素材库，请重新导入'
        self._validate(entry)
        label = body.get('label')
        if not isinstance(label, str) or not label.strip() or len(label.strip()) > 60: raise ValueError('卡面名称需为 1–60 字')
        if not self._available(entry): raise ValueError(missing)
        with self.lock:
            rows = self._read()
            existing = next((r for r in rows if self._key(r) == self._key(entry)), None)
            if existing is not None: return {'row': existing, 'rows': self.list(), 'existing': True}
            if len(rows) >= LIMIT: raise ValueError(f'自制卡面库最多保存 {LIMIT} 个，请先删除不用的')
            row = {'id': str(uuid.uuid4()), 'label': label.strip(), 'created': time.time(), **entry}
            rows.append(row); self._write(rows)
            return {'row': row, 'rows': self.list(), 'existing': False}

    def rename(self, body):
        label = body.get('label')
        if not isinstance(label, str) or not label.strip() or len(label.strip()) > 60: raise ValueError('卡面名称需为 1–60 字')
        with self.lock:
            rows = self._read(); row = next((r for r in rows if r.get('id') == body.get('id')), None)
            if row is None: raise ValueError('卡面库中没有这一项')
            row['label'] = label.strip(); self._write(rows)
            return {'rows': self.list()}

    def remove(self, body):
        # Only the library entry; assets stay for projects that use them.
        with self.lock:
            rows = self._read(); kept = [r for r in rows if r.get('id') != body.get('id')]
            if len(kept) == len(rows): raise ValueError('卡面库中没有这一项')
            self._write(kept)
            return {'rows': self.list()}

    def get(self, body):
        row = next((r for r in self.list() if r['id'] == body.get('id')), None)
        if row is None: raise ValueError('卡面库中没有这一项')
        if not row['available']:
            raise ValueError('这张卡底图片已不在素材库，请重新导入' if row['kind'] == 'image' else '这个动态背景的视频素材已不在素材库，请重新导入')
        # {'kind': 'image', 'background_image': id} or {'kind': 'motion', 'custom_motion': {...}}
        if row['kind'] == 'image': return {'kind': 'image', 'background_image': row['image']['asset_id']}
        return {'kind': 'motion', 'custom_motion': copy.deepcopy(row['custom_motion'])}
