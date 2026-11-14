# -*- coding: utf-8 -*-
"""Script-Editor 區塊包（.sepack.json）: 自訂區塊＋專案的匯出／匯入。

格式（UTF-8 JSON）:
    { "format": "script-editor-pack", "version": 1, "name", "description", "createdAt",
      "tool": {"id": "script-editor", "version"}, "blocks": [...], "projects": [...] }

* 只打包自訂區塊（資料夾 <dataDir>/blocks）; 內建區塊隨工具發佈, 不進包。
* 匯入先預覽: 每個項目是「新增」或「名稱重複」; 重複的要選 overwrite / rename / skip。
  - 內建區塊的 id 不能覆蓋（只能改名或略過）。
  - 區塊改名（id 加 -2）時, 包內專案引用它的步驟一併改成新 id。
  - 覆蓋前把原檔備份到 <dataDir>/backups/pack-import-<時間>/。
"""

import copy
import json
import os
import re
import shutil
import time

import se_core

FORMAT = 'script-editor-pack'
VERSION = 1
SUFFIX = '.sepack.json'
MAX_ITEMS = 500
ACTIONS = ('overwrite', 'rename', 'skip')


class PackError(ValueError):
    """包格式或匯入決定不合法（訊息可直接給使用者看）。"""


# ---------------------------------------------------------------- 檔案位置
def block_path(data_dir, bid):
    return os.path.join(data_dir, 'blocks', re.sub(r'[^A-Za-z0-9._-]', '_', bid) + '.json')


def project_slug(name):
    return re.sub(r'[^A-Za-z0-9_-]+', '-', str(name)).strip('-')


def project_path(data_dir, name):
    slug = project_slug(name)
    if not slug:
        raise PackError('專案名稱不合法: %s' % name)
    return os.path.join(data_dir, 'projects', slug + '.json')


def _read(fp):
    with open(fp, encoding='utf-8') as f:
        return json.load(f)


def _write(fp, obj):
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    tmp = fp + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write('\n')
    os.replace(tmp, fp)


def _clean_block(b):
    return {k: v for k, v in b.items() if not k.startswith('_')}


# ---------------------------------------------------------------- 匯出
def inventory(data_dir):
    """可打包的項目: 自訂區塊 id 與已存專案名稱。"""
    blocks, projects = [], []
    bd = os.path.join(data_dir, 'blocks')
    if os.path.isdir(bd):
        for fn in sorted(os.listdir(bd)):
            if fn.endswith('.json'):
                try:
                    b = _read(os.path.join(bd, fn))
                except (OSError, ValueError):
                    continue
                if isinstance(b, dict) and b.get('id'):
                    blocks.append({'id': b['id'], 'name': b.get('name', b['id']),
                                   'category': b.get('category', '')})
    pd = os.path.join(data_dir, 'projects')
    if os.path.isdir(pd):
        for fn in sorted(os.listdir(pd)):
            if fn.endswith('.json'):
                try:
                    p = _read(os.path.join(pd, fn))
                except (OSError, ValueError):
                    continue
                if isinstance(p, dict) and p.get('name'):
                    projects.append({'name': p['name'], 'title': p.get('title', ''),
                                     'tasks': len(p.get('tasks') or []),
                                     'blocks': sorted(set(_project_block_ids(p)))})
    return {'blocks': blocks, 'projects': projects}


def _project_block_ids(p):
    for t in p.get('tasks') or []:
        for s in (t or {}).get('steps') or []:
            if isinstance(s, dict) and s.get('block'):
                yield s['block']
    for s in p.get('statusSteps') or []:
        if isinstance(s, dict) and s.get('block'):
            yield s['block']


def export_pack(data_dir, block_ids, project_names, builtin_ids, name='', description='',
                tool_version=''):
    """依勾選的自訂區塊 id 與專案名稱組出包。回傳 (pack, warnings)。"""
    inv = inventory(data_dir)
    have_b = {b['id'] for b in inv['blocks']}
    have_p = {p['name'] for p in inv['projects']}
    block_ids = list(dict.fromkeys(block_ids or []))
    project_names = list(dict.fromkeys(project_names or []))
    missing = [b for b in block_ids if b not in have_b] + [p for p in project_names if p not in have_p]
    if missing:
        raise PackError('找不到要匯出的項目: %s' % ', '.join(missing))
    if not block_ids and not project_names:
        raise PackError('請至少勾選一個區塊或專案')
    blocks = [_clean_block(_read(block_path(data_dir, b))) for b in block_ids]
    projects = [_read(project_path(data_dir, p)) for p in project_names]
    warnings = []
    packed = set(block_ids)
    for p in projects:
        need = sorted({b for b in _project_block_ids(p) if b not in builtin_ids and b not in packed})
        if need:
            warnings.append('專案「%s」用到的自訂區塊沒有一起打包: %s（同事匯入後會找不到）'
                            % (p['name'], ', '.join(need)))
    pack = {'format': FORMAT, 'version': VERSION,
            'name': (name or '').strip() or 'script-editor-pack',
            'description': description or '',
            'createdAt': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'tool': {'id': 'script-editor', 'version': tool_version},
            'blocks': blocks, 'projects': projects}
    return pack, warnings


def dumps(pack):
    return json.dumps(pack, ensure_ascii=False, indent=1) + '\n'


# ---------------------------------------------------------------- 匯入
def parse(text):
    """檢查包的外框與每個區塊/專案的基本格式; 回傳 pack dict。"""
    if isinstance(text, bytes):
        text = text.decode('utf-8-sig')
    try:
        pack = json.loads(text)
    except ValueError:
        raise PackError('不是有效的 JSON 檔')
    if not isinstance(pack, dict) or pack.get('format') != FORMAT:
        raise PackError('不是 Script-Editor 區塊包（format 必須是 %s）' % FORMAT)
    ver = pack.get('version')
    if not isinstance(ver, int) or isinstance(ver, bool) or ver < 1:
        raise PackError('區塊包 version 不合法')
    if ver > VERSION:
        raise PackError('區塊包版本 %d 太新，這個 Script-Editor 只支援到 %d' % (ver, VERSION))
    blocks, projects = pack.get('blocks', []), pack.get('projects', [])
    if not isinstance(blocks, list) or not isinstance(projects, list):
        raise PackError('blocks 與 projects 必須是清單')
    if len(blocks) + len(projects) > MAX_ITEMS:
        raise PackError('一個區塊包最多 %d 個項目' % MAX_ITEMS)
    seen = set()
    for b in blocks:
        errs = se_core.validate_block(b)
        if errs:
            raise PackError('區塊 %s 格式不合: %s' % ((b or {}).get('id', '?') if isinstance(b, dict) else '?',
                                               '；'.join(errs)))
        if b['id'] in seen:
            raise PackError('區塊 id 重複: %s' % b['id'])
        seen.add(b['id'])
    seen = set()
    for p in projects:
        if not isinstance(p, dict) or not se_core.safe_text(p.get('name', '')) or not project_slug(p['name']):
            raise PackError('專案格式不合（name 需為 ASCII）')
        if project_slug(p['name']).lower() in seen:
            raise PackError('專案名稱重複: %s' % p['name'])
        seen.add(project_slug(p['name']).lower())
    pack['blocks'] = [_clean_block(b) for b in blocks]
    return pack


def _free_block_id(base, taken):
    for n in range(2, 1000):
        suffix = '-%d' % n
        cand = base[:48 - len(suffix)] + suffix
        if cand not in taken and se_core.BLOCK_ID_RE.match(cand):
            return cand
    raise PackError('找不到可用的區塊 id: %s' % base)


def _free_project_name(base, data_dir, taken):
    for n in range(2, 1000):
        cand = '%s-%d' % (base, n)
        slug = project_slug(cand).lower()
        if slug not in taken and not os.path.exists(project_path(data_dir, cand)):
            return cand
    raise PackError('找不到可用的專案名稱: %s' % base)


def plan(data_dir, pack, builtin_ids, decisions=None, default=None):
    """每個項目要怎麼處理。decisions: {"block:<id>" | "project:<name>": overwrite|rename|skip}。"""
    decisions = decisions or {}
    for v in list(decisions.values()) + ([default] if default else []):
        if v not in ACTIONS:
            raise PackError('處理方式必須是 overwrite / rename / skip')
    inv = inventory(data_dir)
    custom = {b['id'] for b in inv['blocks']}
    taken_ids = set(custom) | set(builtin_ids) | {b['id'] for b in pack['blocks']}
    items, unresolved, renamed = [], [], {}
    for b in pack['blocks']:
        bid = b['id']
        key = 'block:' + bid
        builtin = bid in builtin_ids
        conflict = builtin or bid in custom
        action = 'create'
        target = bid
        if conflict:
            action = decisions.get(key, default)
            if action == 'overwrite' and builtin:
                raise PackError('區塊 %s 是內建區塊，不能覆蓋（請選改名或略過）' % bid)
            if action is None:
                unresolved.append(key)
                action = 'conflict'
            elif action == 'rename':
                target = _free_block_id(bid, taken_ids)
                taken_ids.add(target)
                renamed[bid] = target
        items.append({'kind': 'block', 'key': key, 'name': bid, 'label': b.get('name', bid),
                      'conflict': conflict, 'builtin': builtin, 'action': action, 'target': target})
    taken_slugs = set()
    for p in pack['projects']:
        name = p['name']
        key = 'project:' + name
        conflict = os.path.exists(project_path(data_dir, name))
        action, target = 'create', name
        if conflict:
            action = decisions.get(key, default)
            if action is None:
                unresolved.append(key)
                action = 'conflict'
            elif action == 'rename':
                target = _free_project_name(name, data_dir, taken_slugs)
        taken_slugs.add(project_slug(target).lower())
        items.append({'kind': 'project', 'key': key, 'name': name, 'label': p.get('title', ''),
                      'conflict': conflict, 'builtin': False, 'action': action, 'target': target})
    return {'items': items, 'unresolved': unresolved, 'renamed': renamed}


def _remap_project(p, renamed):
    p = copy.deepcopy(p)
    for t in p.get('tasks') or []:
        for s in (t or {}).get('steps') or []:
            if isinstance(s, dict) and s.get('block') in renamed:
                s['block'] = renamed[s['block']]
    for s in p.get('statusSteps') or []:
        if isinstance(s, dict) and s.get('block') in renamed:
            s['block'] = renamed[s['block']]
    return p


def apply(data_dir, pack, builtin_blocks, decisions=None, default=None, dry_run=False):
    """依決定寫入; dry_run 只回傳計畫。還有未決定的重複項目時不寫任何檔。

    builtin_blocks: {id: block}（內建區塊, 用來驗證匯入後的專案）。
    """
    result = plan(data_dir, pack, set(builtin_blocks), decisions, default)
    result.update(written=[], backups=None, warnings=[], dryRun=bool(dry_run))
    # 匯入後的區塊全集: 內建 + 既有自訂 + 這次會寫入的
    final = dict(builtin_blocks)
    bd = os.path.join(data_dir, 'blocks')
    if os.path.isdir(bd):
        for fn in os.listdir(bd):
            if fn.endswith('.json'):
                try:
                    b = _read(os.path.join(bd, fn))
                    if isinstance(b, dict) and b.get('id'):
                        final[b['id']] = b
                except (OSError, ValueError):
                    pass
    blocks = {b['id']: b for b in pack['blocks']}
    for it in result['items']:
        if it['kind'] == 'block' and it['action'] in ('create', 'overwrite', 'rename'):
            final[it['target']] = dict(blocks[it['name']], id=it['target'])
    for it in result['items']:
        if it['kind'] != 'project' or it['action'] not in ('create', 'overwrite', 'rename'):
            continue
        p = _remap_project(next(x for x in pack['projects'] if x['name'] == it['name']), result['renamed'])
        p['name'] = it['target']
        errs = se_core.validate_project(p, set(final), final)
        if errs:
            result['warnings'].append('專案「%s」匯入後驗證有 %d 個問題（可在編輯器開啟後修正）: %s'
                                      % (it['target'], len(errs), errs[0]))
    if dry_run or result['unresolved']:
        if result['unresolved'] and not dry_run:
            raise PackError('還有名稱重複的項目沒有選擇處理方式: %s' % ', '.join(result['unresolved']))
        return result

    stamp = time.strftime('%Y%m%d-%H%M%S')
    backup = os.path.join(data_dir, 'backups', 'pack-import-' + stamp)
    for it in result['items']:
        if it['action'] not in ('create', 'overwrite', 'rename'):
            continue
        if it['kind'] == 'block':
            obj = dict(blocks[it['name']], id=it['target'])
            fp = block_path(data_dir, it['target'])
            sub = 'blocks'
        else:
            obj = _remap_project(next(x for x in pack['projects'] if x['name'] == it['name']),
                                 result['renamed'])
            obj['name'] = it['target']
            fp = project_path(data_dir, it['target'])
            sub = 'projects'
        if os.path.exists(fp):
            os.makedirs(os.path.join(backup, sub), exist_ok=True)
            shutil.copy2(fp, os.path.join(backup, sub, os.path.basename(fp)))
            result['backups'] = backup
        _write(fp, obj)
        result['written'].append({'kind': it['kind'], 'name': it['target']})
    return result
