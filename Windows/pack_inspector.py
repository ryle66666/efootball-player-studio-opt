"""Read-only inspection of .73pack manifests and embedded player descriptors."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import zipfile

from content_pack import BLOCK, Reader
import player_append
import player_updates


def inspect_pack(path: str | Path) -> dict:
    """Safely inspect a .73pack file without mutating game or studio state."""
    target = Path(path)
    if not target.is_file():
        return {
            'valid': False,
            'kind': 'missing',
            'kind_label': '文件不存在',
            'name': target.name,
            'bytes': 0,
            'error': '找不到指定的 .73pack 文件',
        }
    file_bytes = target.stat().st_size
    try:
        reader = Reader(target)
        manifest = reader.manifest
        base_result = {
            'valid': True,
            'name': target.name,
            'bytes': file_bytes,
            'pack_id': manifest['pack_id'],
            'program_id': manifest['program_id'],
            'package': manifest['package'],
            'outer_protocol': manifest['protocol'],
            'files_count': len(manifest['files']),
            'removed_count': len(manifest['removed']),
        }
        if player_append.MEMBER in reader.entries:
            with tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024) as spool:
                with reader.open(player_append.MEMBER) as stream:
                    shutil.copyfileobj(stream, spool, BLOCK)
                spool.seek(0)
                with zipfile.ZipFile(spool) as archive:
                    patch = player_append.read_descriptor(archive)
            players = []
            for entry in patch['players']:
                definition = entry['definition']
                identity = definition.get('identity', {})
                owned = definition.get('owned', {})
                players.append({
                    'package_id': identity.get('package_id'),
                    'name': owned.get('name') or identity.get('package_id'),
                    'card_id': identity.get('card_id'),
                    'art_key': entry.get('art_key'),
                    'revision': entry.get('revision'),
                    'content_hash': entry.get('content_hash'),
                })
            base_result.update({
                'kind': 'independent_player',
                'kind_label': '独立球员小包',
                'protocol': patch['protocol'],
                'target_game': patch.get('target_game'),
                'base': patch.get('base'),
                'created_at': patch.get('created_at'),
                'studio_version': patch.get('studio_version'),
                'chunks_count': len(patch.get('chunks', [])),
                'players': players,
            })
            return base_result

        if player_updates.MEMBER in reader.entries:
            with tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024) as spool:
                with reader.open(player_updates.MEMBER) as stream:
                    shutil.copyfileobj(stream, spool, BLOCK)
                spool.seek(0)
                with zipfile.ZipFile(spool) as archive:
                    update = player_updates.Update(archive, manifest['removed'])
                    inner_meta = json.loads(archive.read('manifest.json'))
            base_result.update({
                'kind': 'player_update',
                'kind_label': '球员差分更新包',
                'protocol': inner_meta.get('protocol', player_updates.PROTOCOL),
                'parent': update.parent,
                'entries': sorted(update.files)[:50],
                'entries_total': len(update.files),
            })
            return base_result

        base_result.update({
            'kind': 'content_pack',
            'kind_label': '完整内容包',
            'protocol': manifest['protocol'],
            'entries': [row['path'] for row in manifest['files'][:50]],
            'entries_total': len(manifest['files']),
        })
        return base_result
    except Exception as exc:
        return {
            'valid': False,
            'kind': 'invalid',
            'kind_label': '无效或损坏的内容包',
            'name': target.name,
            'bytes': file_bytes,
            'error': str(exc),
        }
