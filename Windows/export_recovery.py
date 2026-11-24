"""Bounded recovery decisions for an export, without changing saved designs."""
import copy
import json
import time
from pathlib import Path


class ExportCancelled(Exception):
    pass


FEATURE_LABELS = {
    'settings': '游戏设置', 'progress': '进度修改', 'stadium': '自定义球场',
    'tournaments': '本次赛事修改', 'gameplay': '比赛手感调校', 'draw': '抽卡演出修改',
    'card_motion': '动态卡面适配', 'private_motion': '自制动态视频',
    'stock_heads': '原版教练头模修改', 'texture_compression': '额外贴图压缩',
    'controller': '手柄键位修改', 'frame_rate': '帧率修改', 'display': '分辨率修改',
    'action_trace': '比赛记录扩展', 'ai_division_squads': 'AI 阵容修改',
    'ai_cut_in_shots': 'AI 决策修改', 'skill_effects': '附加技能效果',
    'skill_capacity': '技能列表扩展', 'ai_style': 'AI 风格显示扩展',
    'competition_menu': '赛事菜单扩展', 'coaches': '本次教练修改',
    'packs': '本次卡包修改', 'players': '本次球员修改', 'pack_covers': '卡包封面修改',
    'content_sync': '自动补齐的增量内容', 'source_art': '本次新增美术资源',
}

STAGE_FEATURES = {
    'options': ('settings', 'progress'), 'world': ('stadium', 'tournaments'),
    'gameplay': ('gameplay',), 'draw': ('draw',), 'card_motion': ('card_motion',),
    'private_motion': ('private_motion',), 'stock_heads': ('stock_heads',),
    'stadium': ('stadium',), 'tournaments': ('tournaments',),
    'texture_compression': ('texture_compression',),
    'stock_containers': ('stock_heads', 'draw', 'stadium', 'tournaments'),
    'controller': ('controller',), 'display': ('display', 'frame_rate'),
    'frame_rate': ('frame_rate',), 'action_trace': ('action_trace',),
    'ai_match': ('ai_division_squads', 'tournaments'),
    'ai_cut_in_shots': ('ai_cut_in_shots', 'gameplay'),
    'skill_effects': ('skill_effects', 'gameplay'), 'skill_capacity': ('skill_capacity',),
    'ai_style': ('ai_style',), 'competition_menu': ('competition_menu', 'tournaments'),
    'coaches': ('coaches',), 'coach_models': ('coaches',), 'packs': ('packs',),
    'pack_covers': ('pack_covers',), 'content_sync': ('content_sync',),
    'iostore': ('draw', 'stadium', 'tournaments', 'stock_heads', 'card_motion'),
}


def cause(error):
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        following = getattr(error, '__cause__', None)
        if following is None:
            return error
        error = following
    return error


def explanation(error):
    value = getattr(error, 'reason', None) or str(cause(error) or error)
    return value or type(error).__name__


def identities(request, kind):
    field = {'players': 'selected_ids', 'coaches': 'coach_ids', 'packs': 'pack_ids',
             'tournaments': 'tournament_ids'}[kind]
    values = request.get(field)
    if not isinstance(values, list):
        values = [request.get('project', {}).get('package_id')] if kind == 'players' else []
    return [value for value in values if isinstance(value, str) and value]


class Recovery:
    def __init__(self, request):
        self.original = copy.deepcopy(request)
        self.disabled = set()
        self.skipped = {kind: set() for kind in ('players', 'coaches', 'packs', 'tournaments')}
        self.warnings = []
        self.retries = {}
        self.attempts = 0
        self.refresh_source = False
        self.use_library_source = False
        self.checked_source = None
        self.stage = 'source'
        self.failure = None

    def warn(self, code, item, message, detail='', *, error=None):
        row = {'code': code, 'item': str(item or ''), 'message': message, 'detail': detail, 'stage': self.stage}
        if error is not None:
            row.update(detail=str(error), error_type=getattr(error, 'original_type', type(cause(error)).__name__),
                       contexts=copy.deepcopy(getattr(error, 'contexts', [])))
        if not any(old['code'] == code and old['item'] == row['item'] for old in self.warnings):
            self.warnings.append(row)

    def record_failure(self, error, stage):
        self.stage = stage
        contexts = copy.deepcopy(getattr(error, 'contexts', []))
        self.failure = {'code': 'blocked', 'item': contexts[0]['subject'] if contexts else str(self.original.get('apk') or ''),
                        'message': '本次导出未能继续，来源文件和工程已保留。', 'stage': stage,
                        'detail': str(error), 'error_type': getattr(error, 'original_type', type(cause(error)).__name__),
                        'contexts': contexts}

    def diagnostics(self):
        return copy.deepcopy(self.warnings + ([self.failure] if self.failure else []))

    def request(self):
        value = copy.deepcopy(self.original)
        value['_recovery'] = {'disabled': sorted(self.disabled),
            'skipped': {kind: sorted(values) for kind, values in self.skipped.items()}}
        if self.checked_source is not None:
            value['_source_preflight'] = copy.deepcopy(self.checked_source)
        if self.refresh_source:
            value.pop('write_source_token', None)
            value.pop('_source_preflight', None)
        if self.use_library_source:
            value['apk'] = None
            value['explicit_source_selection'] = False
            value['resource_files'] = []
            value['resource_packages'] = {}
        return value

    def source_ready(self, checked):
        self.checked_source = copy.deepcopy(checked)
        self.refresh_source = False

    def disable(self, feature, error):
        if feature in self.disabled:
            return False
        self.disabled.add(feature)
        self.warn('preserve_feature', feature,
            FEATURE_LABELS.get(feature, feature) + '暂未更新，继续使用原有内容。', error=error)
        return True

    def retry(self, key, limit, error):
        count = self.retries.get(key, 0)
        if count >= limit:
            return False
        self.retries[key] = count + 1
        self.warn('retry', key, '正在重新准备' + key + '，已保留当前工程。', error=error)
        return True

    def plan(self, error, stage):
        self.stage = stage
        self.attempts += 1
        contexts = getattr(error, 'contexts', ())
        for kind, words in (('players', ('球员', '脸模')), ('coaches', ('教练',)),
                            ('packs', ('卡包',)), ('tournaments', ('赛事', '锦标赛'))):
            candidates = identities(self.original, kind)
            for context in contexts:
                if not any(word in context.get('kind', '') for word in words):
                    continue
                subject = context.get('subject', '')
                selected = next((value for value in candidates if '[' + value + ']' in subject or
                                 '[身份 ' + value + ']' in subject or subject == value), None)
                if selected and selected not in self.skipped[kind]:
                    self.skipped[kind].add(selected)
                    self.warn('preserve_item', selected, subject + '暂未更新，已保留原内容，其它项目继续。', error=error)
                    return True
        root = cause(error)
        if stage in {'source', 'source_selection'} or type(root).__name__ == 'SourceChanged':
            self.refresh_source = True
            if self.retry('来源快照', 2, error):
                return True
            if not self.use_library_source:
                self.use_library_source = True
                self.warn('source_fallback', 'apk', '改用当前资料库登记的可用游戏来源。', error=error)
                return True
            return False
        if isinstance(root, (OSError, TimeoutError, MemoryError)) and self.retry('临时处理', 2, error):
            return True
        for feature in STAGE_FEATURES.get(stage, ()):
            if self.disable(feature, error):
                return True
        if stage in {'player_data', 'player_art', 'model_binding'}:
            return self.disable('players', error)
        if stage == 'native_binding':
            for feature in ('gameplay', 'ai_cut_in_shots', 'ai_division_squads', 'action_trace',
                            'controller', 'card_motion', 'competition_menu', 'skill_effects', 'skill_capacity'):
                if self.disable(feature, error):
                    return True
        return self.retry('当前步骤', 1, error)

    def save(self, root, request=None, *, status='paused'):
        from content_pack import atomic_json
        path = Path(root) / 'export-resume.json'
        value = {'version': 1, 'status':status, 'request': self.original, 'disabled': sorted(self.disabled),
                 'skipped': {kind: sorted(values) for kind, values in self.skipped.items()},
                 'warnings': self.warnings, 'diagnostics': self.diagnostics(), 'saved_at': time.time()}
        try:
            atomic_json(path, value)
            return str(path)
        except (OSError, ValueError, TypeError) as error:
            self.warn('resume_record', path, '恢复记录暂未保存，请保留当前工坊窗口和来源文件。', error=error)
            return None


def disabled(request, feature):
    return feature in request.get('_recovery', {}).get('disabled', ())


def skipped(request, kind):
    return set(request.get('_recovery', {}).get('skipped', {}).get(kind, ()))


def retain_selection(request, kind, selected, source_ids):
    omit = skipped(request, kind)
    if disabled(request, kind):
        omit.update(selected)
    selected = [value for value in selected if value not in omit or value in source_ids]
    return selected, omit


def pause_message(error, stage):
    root = cause(error)
    if isinstance(root, OSError):
        if getattr(root, 'errno', None) == 28 or getattr(root, 'winerror', None) == 112:
            return '已暂停并保留工程和可用文件。请释放空间或更换保存位置后继续。'
        return '已暂停并保留工程和可用文件。请恢复文件访问或保存位置后继续。'
    if stage in {'source', 'source_selection'}:
        return '已保留工程。请补选可用的游戏 APK 或对应内容包后继续。'
    return '已保留来源文件和工程，本次修改尚未全部写入。请查看下方具体原因，处理后继续生成。'
