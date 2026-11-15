# -*- coding: utf-8 -*-
"""Script Editor core (no UI): 區塊庫、專案模型、驗證與 .bat 匯出器。

資料模型（全部 JSON, 欄位說明見 README.md）:
    block   = 一個可重複使用的腳本區塊（一條指令 + 參數 + 可接受 rc）。
    project = 一個匯出腳本: 標題 + 設定 + Task 清單; Task = 主選單一項,
              內含依序執行的 step 清單, 每個 step 引用一個 block。

匯出器把 project 展開成單檔 .bat（純 ASCII, 仿 reference 的
script_tools.bat: VT 彩色、log 檔、:SE_RUN_STEP 引擎、ASK_YES、bundle 搜尋）。
匯出的腳本支援 /run <n> 與 /runall 免選單直跑, 方便自動化測試。
"""

import json
import os
import re

CORE_VERSION = '0.2.3'

# ---------------------------------------------------------------- 文字規則
BLOCK_ID_RE = re.compile(r'^[a-z0-9][a-z0-9._-]{1,47}$')
# 工具包資料夾名稱（可含 * ?）、子資料夾、檢查檔: 只有英數字與 . _ - * ?
BUNDLE_NAME_RE = re.compile(r'^[A-Za-z0-9._*?-]{1,64}$')
BUNDLE_DEFAULTS = {'bundleFolder': 'ToolBundle_*', 'bundleSub': 'win64', 'bundleCheck': ''}


def bundle_settings(p):
    """(資料夾樣式, 子資料夾, 檢查檔, 提示路徑) - 專案沒設定時用中性預設值。"""
    folder = p.get('bundleFolder') or BUNDLE_DEFAULTS['bundleFolder']
    sub = p.get('bundleSub', BUNDLE_DEFAULTS['bundleSub'])
    check = p.get('bundleCheck') or ''
    hint = '\\'.join(x for x in (folder.replace('*', 'DD_Month_YYYY') if folder.endswith('_*') else folder,
                                    sub, check) if x)
    return folder, sub, check, hint
# Windows 保留檔名（匯出 .bat 與專案檔存檔都不能用）
WIN_RESERVED_RE = re.compile(r'^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])$', re.I)
# 會進入 .bat 的文字（名稱/標題/提示/說明）: 純 ASCII 可印字元, 但排除
# ! " % & < > ^ | 與換行 - 這些字元會破壞 unquoted title/echo/set 的解析
# （& 會執行第二段指令, > 產生多餘檔案, ^ 為跳脫字元...）; \ 允許（路徑用）
SAFE_TEXT_RE = re.compile(r'^[\x20\x23-\x24\x27-\x3b\x3d\x3f-\x5d\x5f-\x7b\x7d-\x7e]+$')
PLACEHOLDER_RE = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}')
PARAM_NAME_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,31}$')
# validate regex 會放進 cmd 雙引號內的 PS 單引號字串: 只需排除 ' " %
# （& | < > ^ 在字串內安全, 且 regex 需要 | 做 alternation）
SAFE_RE_RE = re.compile(r'^[\x20-\x24\x26\x28-\x7e]+$')


def safe_text(s):
    return isinstance(s, str) and bool(s) and bool(SAFE_TEXT_RE.match(s))


# 執行時畫面上顯示的短指令（block.display）: 規則同 SAFE_TEXT 但允許雙引號
# （路徑引號在 echo 中無害）; 仍排除 ! % & < > ^ | 以免 unquoted echo 解析錯亂
SHOW_RE = re.compile(r'^[\x20\x22-\x24\x27-\x3b\x3d\x3f-\x5d\x5f-\x7b\x7d-\x7e]+$')
SHOW_MAX = 100
PS_INLINE_RE = re.compile(r'(?i)^\s*"?(powershell|pwsh)(\.exe)?"?\s.*\s-(c|command|encodedcommand)\s')


def display_command(block):
    """區塊執行時顯示的短指令（仿原版「[1/3] Tool64W.exe 參數」一行）。

    有 block.display 就用它; 否則由 cmd 自動產生: 去掉 {TOOLS}\\ 前綴與
    exe 外層引號, PowerShell 單行腳本收成一句, 過長截斷。log 仍記完整指令。
    raw 區塊沒有單一指令, 回傳空字串（只印步驟標題）。
    """
    show = block.get('display')
    if show:
        return show
    cmd = block.get('cmd') or ''
    if not cmd:
        return ''
    s = re.sub(r'"\{TOOLS\}\\([^"]+)"', r'\1', cmd).replace('{TOOLS}\\', '')
    if PS_INLINE_RE.match(s):
        return 'powershell.exe -Command [inline script]'
    if len(s) > SHOW_MAX:
        s = s[:SHOW_MAX - 3].rstrip() + '...'
    if SHOW_RE.match(s):
        return s
    head = s.split()[0].strip('"') if s.split() else ''
    return (head + ' ...') if head and SHOW_RE.match(head) else block.get('name', '')


# 寫進 unquoted set / %SE_CMD% 的值: 不得含批次分割字元（引號本身允許）
EXEC_UNSAFE_RE = re.compile(r'[&|<>\r\n]')


def unsafe_exec_value(s):
    """回傳不適合直接展開成批次指令的理由字串（None = OK）。"""
    if not isinstance(s, str):
        return '必須是字串'
    m = EXEC_UNSAFE_RE.search(s)
    if m:
        return '不得含 & | < > 或換行（批次會在此中斷）'
    return None


# ---------------------------------------------------------------- block 載入/驗證
def load_block_files(dirs):
    """讀多個區塊目錄（先讀的優先, 後讀的同 id 視為衝突）。

    dirs: [(來源標籤, 目錄), ...] -> (blocks: [dict(含 _src)], errors: [str])
    """
    blocks, errors, seen = [], [], {}
    for src, d in dirs:
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith('.json'):
                continue
            fp = os.path.join(d, fn)
            try:
                with open(fp, encoding='utf-8') as f:
                    b = json.load(f)
            except (OSError, ValueError) as exc:
                errors.append('%s: 檔毀損（%s）' % (fn, exc))
                continue
            errs = validate_block(b)
            if errs:
                errors.extend('%s: %s' % (fn, e) for e in errs)
                continue
            if b['id'] in seen:
                errors.append('%s: id %s 與 %s 重複' % (fn, b['id'], seen[b['id']]))
                continue
            seen[b['id']] = src
            b['_src'] = src
            blocks.append(b)
    return blocks, errors


def validate_block(b):
    """回傳錯誤清單（空 = OK）。欄位規範見 README.md。"""
    errs = []
    if not isinstance(b, dict):
        return ['必須是 JSON 物件']
    bid = b.get('id')
    if not isinstance(bid, str) or not BLOCK_ID_RE.match(bid):
        errs.append('id 不合（小寫英數字 . _ -，2~48 字）')
    if 'desc' in b and not isinstance(b.get('desc'), str):
        errs.append('desc 必須是字串')
    if not b.get('name'):
        errs.append('name 必填')
    elif not safe_text(b['name']):
        errs.append('name 需為可匯出字元（ASCII，不含 ! " % & < > ^ |）')
    cmd, raw = b.get('cmd'), b.get('raw')
    if not cmd and not raw:
        errs.append('cmd 或 raw 擇一必填')
    if cmd and (not isinstance(cmd, str) or '\n' in cmd or '\r' in cmd):
        errs.append('cmd 必須是單行字串')
    if raw and (not isinstance(raw, list) or not raw
                or any(not isinstance(l, str) or '\n' in l or '\r' in l for l in raw)):
        errs.append('raw 必須是非空白字串清單（每行一條批次指令）')
    if cmd and '{TOOLS}' in cmd and not b.get('needsBundle'):
        errs.append('cmd 用了 {TOOLS} 但未勾 needsBundle')
    params = b.get('params', [])
    names = set()
    if not isinstance(params, list):
        errs.append('params 必須是清單')
        params = []
    for p in params:
        if not isinstance(p, dict) or not PARAM_NAME_RE.match(str(p.get('name', ''))):
            errs.append('參數名稱不合（英數字底線，開頭非數字）')
            continue
        names.add(p['name'])
        if p.get('mode', 'ask') not in ('ask', 'fixed'):
            errs.append('參數 %s 的 mode 必須是 ask 或 fixed' % p['name'])
        v = p.get('validate', '')
        if v and (not SAFE_RE_RE.match(str(v)) or not _regex_ok(str(v))):
            errs.append('參數 %s 的 validate 需為 ASCII regex（不含 \' " %%）' % p['name'])
        if not safe_text(str(p.get('prompt', p['name']))):
            errs.append('參數 %s 的 prompt 需為可匯出字元' % p['name'])
        dflt = p.get('default', '')
        if dflt and not safe_text(str(dflt)):
            errs.append('參數 %s 的 default 需為可匯出字元' % p['name'])
        if p.get('mode') == 'fixed' and p.get('value'):
            why = unsafe_exec_value(p['value'])
            if why:
                errs.append('參數 %s 的固定值 %s' % (p['name'], why))
        errs.extend(_validate_param_limits(p, '參數 %s' % p['name']))
    if cmd:
        unknown = [n for n in PLACEHOLDER_RE.findall(cmd) if n not in names]
        if unknown:
            errs.append('cmd 引用了未宣告的參數: %s' % ', '.join(sorted(set(unknown))))
    show = b.get('display')
    if show is not None and show != '':
        if not isinstance(show, str) or len(show) > 120 or not SHOW_RE.match(show):
            errs.append('display 需為 120 字內的可匯出字元（ASCII，不含 ! % & < > ^ |）')
        else:
            unknown = [n for n in PLACEHOLDER_RE.findall(show) if n not in names]
            if unknown:
                errs.append('display 引用了未宣告的參數: %s' % ', '.join(sorted(set(unknown))))
    if 'restarts' in b and not isinstance(b['restarts'], bool):
        errs.append('restarts 必須是 true/false')
    rc = b.get('expectRc', [0])
    if not isinstance(rc, list) or not rc or not all(isinstance(c, int) for c in rc):
        errs.append('expectRc 必須是整數清單（如 [0] 或 [0, 1]）')
    return errs


def _regex_ok(s):
    try:
        re.compile(s)
        return True
    except re.error:
        return False


# ---------------------------------------------------------------- 專案驗證
ONFAIL = ('stop', 'continue', 'ask')


def validate_project(p, known_ids, known_blocks=None):
    """project -> 錯誤清單（空 = OK）。known_blocks: {id: block} 可給步進提示。"""
    errs = []
    if not isinstance(p, dict):
        return ['專案必須是 JSON 物件']
    if not safe_text(p.get('name', '')):
        errs.append('專案名稱請用 ASCII（不含 ! " % & < > ^ |）')
    elif WIN_RESERVED_RE.match(re.sub(r'[^A-Za-z0-9_-]+', '-', p['name']).strip('-')):
        errs.append('專案名稱（或其匯出檔名）與 Windows 保留名稱衝突（CON/NUL/COMx 等）')
    if not safe_text(p.get('title', '')):
        errs.append('視窗標題請用 ASCII（不含 ! " % & < > ^ |）')
    brow = p.get('bundleRoot', 'auto')
    if not isinstance(brow, str) or (brow != 'auto' and not brow):
        errs.append('bundleRoot 必須是 "auto" 或路徑字串')
    if brow != 'auto' and not SAFE_TEXT_RE.match(brow):
        errs.append('bundleRoot 路徑需為 ASCII 且不含 ! " % & < > ^ |')
    for k in ('bundleFolder', 'bundleSub', 'bundleCheck'):
        v = p.get(k, '')
        if not isinstance(v, str) or (v and not BUNDLE_NAME_RE.match(v)):
            errs.append('%s 只能用英數字與 . _ - %s' % (k, '* ?' if k == 'bundleFolder' else ''))
    if p.get('logFile') and not SAFE_TEXT_RE.match(p['logFile']):
        errs.append('logFile 路徑需為 ASCII 且不含 ! " % & < > ^ |')
    if 'summary' in p and not isinstance(p['summary'], bool):
        errs.append('summary 必須是 true/false')
    errs.extend(_validate_status(p.get('statusSteps'), known_blocks or known_ids))
    tasks = p.get('tasks')
    if not isinstance(tasks, list) or not tasks:
        errs.append('至少需要一個 Task')
        return errs
    seen_keys = {}
    for ti, t in enumerate(tasks, 1):
        if not isinstance(t, dict) or not safe_text(t.get('name', '')):
            errs.append('Task %d: 名稱請用 ASCII（不含 ! " % & < > ^ |）' % ti)
            continue
        if t.get('desc') and not SAFE_TEXT_RE.match(t['desc']):
            errs.append('Task「%s」: desc 需為 ASCII 且不含 ! " %% & < > ^ |' % t['name'])
        if t.get('group') and (not safe_text(t['group']) or len(t['group']) > 40):
            errs.append('Task「%s」: 分組標題需為 40 字內的 ASCII（不含 ! " %% & < > ^ |）' % t['name'])
        key = t.get('key')
        if key:
            k = str(key).upper()
            if not re.match(r'^[B-Z]$', k):
                errs.append('Task「%s」: 選單按鍵需為單一英文字母 B~Z（數字自動編號；A = Run all）' % t['name'])
            elif k in seen_keys:
                errs.append('Task「%s」: 選單按鍵 %s 與「%s」重複' % (t['name'], k, seen_keys[k]))
            else:
                seen_keys[k] = t['name']
        for flag in ('checkTools', 'bundleOptional', 'restarts'):
            if flag in t and not isinstance(t[flag], bool):
                errs.append('Task「%s」: %s 必須是 true/false' % (t['name'], flag))
        files = t.get('requireFiles')
        if files is not None:
            if not isinstance(files, list) or len(files) > 30:
                errs.append('Task「%s」: requireFiles 必須是 30 個以內的路徑清單' % t['name'])
            else:
                for f in files:
                    if not isinstance(f, str) or not f.strip() or not SAFE_TEXT_RE.match(f):
                        errs.append('Task「%s」: 必要檔案路徑需為 ASCII 且不含 ! " %% & < > ^ |: %s'
                                    % (t['name'], f))
        steps = t.get('steps')
        if not isinstance(steps, list) or not steps:
            errs.append('Task「%s」: 至少需要一個 step' % t['name'])
            continue
        kb = known_blocks if isinstance(known_blocks, dict) else {}
        if kb and any(('{TOOLS}' in f or '{BUNDLE}' in f) for f in (files or []) if isinstance(f, str)) \
                and not any((kb.get(s.get('block')) or {}).get('needsBundle') for s in steps if isinstance(s, dict)):
            errs.append('Task「%s」: 必要檔案用了 {TOOLS}/{BUNDLE}, 但任務裡沒有需要工具包的步驟' % t['name'])
        for si, s in enumerate(steps, 1):
            errs.extend(_validate_step(s, known_blocks or known_ids,
                                       '%s / step %d' % (t['name'], si)))
    return errs


def _validate_status(steps, known):
    """主選單狀態列: 唯讀、不互動 - 不可用 raw/工具包區塊, 參數須為固定值或有預設值。"""
    if steps is None:
        return []
    if not isinstance(steps, list) or len(steps) > 10:
        return ['statusSteps 必須是 10 個以內的步驟清單']
    errs = []
    for i, s in enumerate(steps, 1):
        where = '狀態列 / step %d' % i
        errs.extend(_validate_step(s, known, where))
        if not isinstance(s, dict) or s.get('block') not in known:
            continue
        for k in ('confirm', 'loop', 'askAfter'):
            if s.get(k):
                errs.append('%s: 狀態列步驟不可設 %s' % (where, k))
        b = known.get(s['block']) if isinstance(known, dict) else None
        if not isinstance(b, dict):
            continue
        if b.get('raw') or b.get('needsBundle'):
            errs.append('%s: 狀態列只能用單行指令、不需要工具包的區塊（%s）' % (where, b['id']))
        for p in _BatGen._step_params(s, b):
            if p['mode'] != 'fixed' and not p.get('default'):
                errs.append('%s: 參數 %s 在狀態列必須填死或有預設值（狀態列不會詢問）'
                            % (where, p['name']))
    return errs


def _validate_param_limits(p, where):
    errs = []
    if 'uppercase' in p and not isinstance(p['uppercase'], bool):
        errs.append('%s: uppercase 必須是 true/false' % where)
    for k in ('minLength', 'maxLength'):
        v = p.get(k)
        if v is not None and not (isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 256):
            errs.append('%s: %s 需為 0~256 的整數' % (where, k))
    lo, hi = p.get('minLength'), p.get('maxLength')
    if isinstance(lo, int) and isinstance(hi, int) and hi and lo > hi:
        errs.append('%s: minLength 不可大於 maxLength' % where)
    return errs


def _validate_step(s, known, where):
    errs = []
    if not isinstance(s, dict):
        return ['%s: 必須是物件' % where]
    bid = s.get('block')
    if bid not in known:
        errs.append('%s: 找不到區塊「%s」' % (where, bid))
        return errs
    if s.get('onFail', 'stop') not in ONFAIL:
        errs.append('%s: onFail 必須是 stop/continue/ask' % where)
    if s.get('askAfter') and (not safe_text(str(s['askAfter'])) or len(str(s['askAfter'])) > 80):
        errs.append('%s: 成功後確認的問題需為 80 字內的 ASCII（不含 ! " %% & < > ^ |）' % where)
    rc = s.get('expectRc')
    if rc is not None and (not isinstance(rc, list) or not rc
                           or not all(isinstance(c, int) for c in rc)):
        errs.append('%s: expectRc 覆蓋值不合法' % where)
    loop = s.get('loop')
    if loop is not None:
        mode = loop.get('mode') if isinstance(loop, dict) else None
        if mode not in ('times', 'duration'):
            errs.append('%s: loop.mode 必須是 times 或 duration' % where)
        elif mode == 'times' and not (isinstance(loop.get('times'), int)
                                      and 2 <= loop['times'] <= 9999):
            errs.append('%s: loop.times 需為 2~9999' % where)
        elif mode == 'duration' and not (isinstance(loop.get('minutes'), int)
                                         and 1 <= loop['minutes'] <= 1440):
            errs.append('%s: loop.minutes 需為 1~1440' % where)
        d = loop.get('delaySec', 0) if isinstance(loop, dict) else 0
        if not (isinstance(d, int) and 0 <= d <= 3600):
            errs.append('%s: loop.delaySec 需為 0~3600' % where)
    params = s.get('params')
    if params is not None and not isinstance(params, dict):
        errs.append('%s: params 必須是物件' % where)
    elif isinstance(params, dict):
        for pname, ov in params.items():
            if not isinstance(ov, dict):
                continue
            # 覆蓋欄位與區塊定義相同的字元規則（同樣會進 echo/set/PS 字串）
            if 'prompt' in ov and not safe_text(str(ov['prompt'])):
                errs.append('%s: 參數 %s 的 prompt 覆蓋需為可匯出字元' % (where, pname))
            if 'default' in ov and not safe_text(str(ov['default'])):
                errs.append('%s: 參數 %s 的 default 覆蓋需為可匯出字元' % (where, pname))
            v = ov.get('validate')
            if v and (not SAFE_RE_RE.match(str(v)) or not _regex_ok(str(v))):
                errs.append('%s: 參數 %s 的 validate 覆蓋需為 ASCII regex' % (where, pname))
            if ov.get('mode') == 'fixed' and 'value' in ov:
                why = unsafe_exec_value(ov['value'])
                if why:
                    errs.append('%s: 參數 %s 的固定值 %s' % (where, pname, why))
            errs.extend(_validate_param_limits(ov, '%s: 參數 %s' % (where, pname)))
        if isinstance(known, dict) and isinstance(known.get(bid), dict):
            # 固定值也要符合長度限制（匯出時就檢查, 不留到執行時）
            for p in _BatGen._step_params(s, known[bid]):
                if p['mode'] != 'fixed':
                    continue
                v = str(p.get('value', ''))
                lo, hi = p.get('minLength'), p.get('maxLength')
                if (isinstance(lo, int) and len(v) < lo) or (isinstance(hi, int) and hi and len(v) > hi):
                    errs.append('%s: 參數 %s 的固定值長度不符合限制' % (where, p['name']))
    return errs


# ---------------------------------------------------------------- .bat 匯出
CRLF = '\r\n'


def export_filename(project):
    slug = re.sub(r'[^A-Za-z0-9_-]+', '-', project['name']).strip('-') or 'tool'
    return slug + '.bat'


PARAM_OVERRIDE_KEYS = ('mode', 'prompt', 'default', 'validate', 'value',
                       'uppercase', 'minLength', 'maxLength')
TOOL_REF_RE = re.compile(r'"?(\{(?:TOOLS|BUNDLE)\}\\[^"\s]+)')


def required_files(task, blocks_by_id):
    """任務開始前要檢查存在的檔案: task.requireFiles, 加上 checkTools 時
    自動收集各步驟指令裡的 {TOOLS}\\xxx.exe / {BUNDLE}\\xxx。保持順序、去重。"""
    out = []
    for f in task.get('requireFiles') or []:
        if f not in out:
            out.append(f)
    if task.get('checkTools'):
        for s in task.get('steps', []):
            b = blocks_by_id.get(s.get('block')) or {}
            for line in [b.get('cmd') or ''] + list(b.get('raw') or []):
                for m in TOOL_REF_RE.finditer(line):
                    if '${' not in m.group(1) and m.group(1) not in out:
                        out.append(m.group(1))
    return out


# 自動找工具包（選單版 :SE_RESOLVE_BUNDLE 用; GUI 版 Resolve-Bundle 是同一套規則）:
#   1. 依序找 桌面（[Environment]::GetFolderPath('Desktop')）→ 腳本所在資料夾（$env:SE_HERE）
#   2. 每處找符合 SE_BUNDLE_GLOB 的資料夾, 工具在 <資料夾>\<SE_BUNDLE_SUB>, 或多一層
#      <資料夾>\<SE_BUNDLE_GLOB>\<SE_BUNDLE_SUB>（「解壓縮全部」的結果）
#   3. 有 SE_BUNDLE_CHECK 時該檔要存在; 同一處有多份取名稱日期（_DD_Month_YYYY）最新的
#   4. 輸出 SEARCH=/SKIP= 行（找過的位置、不合格的資料夾）, 找到時 BUNDLE= / TOOLS=
# 寫在批次 for /f 的 "..." 內: 不能有 " % ! ^ 字元。
_BUNDLE_FIND_PS = (
    "$g=$env:SE_BUNDLE_GLOB;$sub=$env:SE_BUNDLE_SUB;$chk=$env:SE_BUNDLE_CHECK;"
    "$roots=@([Environment]::GetFolderPath('Desktop'));"
    "if(-not $roots[0]){$roots=@(Join-Path $env:USERPROFILE 'Desktop')};"
    "if($env:SE_HERE){$h=$env:SE_HERE.TrimEnd([char]92);if($roots -notcontains $h){$roots+=$h}};"
    "foreach($r in $roots){"
    "Write-Output ('SEARCH='+(Join-Path $r $g));"
    "$best=$null;$bestDate=[datetime]::MinValue;"
    "foreach($f in @(Get-ChildItem -LiteralPath $r -Directory -Filter $g -ErrorAction SilentlyContinue)){"
    "$cands=@($f.FullName)+@(Get-ChildItem -LiteralPath $f.FullName -Directory -Filter $g "
    "-ErrorAction SilentlyContinue | ForEach-Object {$_.FullName});"
    "$p=$null;foreach($c in $cands){if($sub){$w=Join-Path $c $sub}else{$w=$c};"
    "if(-not (Test-Path -LiteralPath $w)){continue};"
    "if((-not $chk) -or (Test-Path -LiteralPath (Join-Path $w $chk))){$p=$w;break};"
    "Write-Output ('SKIP='+$w+' - '+$chk+' not found')};"
    "if(-not $p){if($sub -and $cands.Count -eq 1 -and -not (Test-Path -LiteralPath (Join-Path $f.FullName $sub)))"
    "{Write-Output ('SKIP='+$f.FullName+' - no '+$sub+' folder')};continue};"
    "$dt=$f.LastWriteTime;$parts=$f.Name.Split('_');"
    "if($parts.Count -ge 4){$n=$parts.Count;$s=$parts[$n-3]+' '+$parts[$n-2]+' '+$parts[$n-1];"
    "foreach($fmt in @('d MMMM yyyy','d MMM yyyy')){"
    "try{$dt=[datetime]::ParseExact($s,$fmt,[Globalization.CultureInfo]::InvariantCulture);break}catch{}}};"
    "if($dt -gt $bestDate){$bestDate=$dt;$best=[PSCustomObject]@{Name=$f.Name;Tools=$p}}};"
    "if($best){Write-Output ('BUNDLE='+$best.Name);Write-Output ('TOOLS='+$best.Tools);exit 0}};"
    "exit 10"
)


def paren_safe(text):
    """寫在批次 ( ... ) 區塊內的 echo 文字: ( ) 前加 ^, 否則名稱裡的 ) 會提早結束區塊。"""
    return text.replace('(', '^(').replace(')', '^)')


def task_restarts(task, blocks_by_id):
    """任務會不會讓機器重開（GUI 按鈕上的 Restarts 標籤）: task.restarts 明確設定優先,
    否則看步驟裡有沒有標了 restarts 的區塊（如 Restart Now、Restart into BIOS）。"""
    if isinstance(task.get('restarts'), bool):
        return task['restarts']
    return any((blocks_by_id.get(s.get('block')) or {}).get('restarts')
               for s in task.get('steps', []))


def task_keys(project):
    """每個任務在主選單的按鍵: 有 task.key（單一字母, 如 M）就用它, 其餘依序 1, 2, 3…。

    數字只發給沒有自訂按鍵的任務, 所以 [M] 插在中間不會打亂後面的編號（仿原版
    script_tools 的 [1]-[6] [M] [7]-[9]）。/run <按鍵> 與 {RUN} 也用這個值。
    """
    keys, n = [], 0
    for t in project.get('tasks', []):
        k = str(t.get('key') or '').strip().upper()
        if not k:
            n += 1
            k = str(n)
        keys.append(k)
    return keys


def export_bat(project, blocks_by_id):
    """project -> (檔名, .bat 內容字串)。呼叫端必須先過 validate_project。"""
    g = _BatGen(project, blocks_by_id)
    return g.filename, g.render()


class _BatGen(object):
    def __init__(self, project, blocks_by_id):
        self.p = project
        self.blocks = blocks_by_id
        self.filename = export_filename(project)
        self.uses_bundle = self._detect_bundle()
        self.title = project.get('title') or project['name']
        self.keys = task_keys(project)

    def _detect_bundle(self):
        for t in self.p.get('tasks', []):
            for s in t.get('steps', []):
                b = self.blocks.get(s.get('block'))
                if b and b.get('needsBundle'):
                    return True
        return False

    # ---------------- 主體
    def render(self):
        L = []
        a = L.append
        a('@echo off')
        a('setlocal EnableExtensions')
        a('chcp 65001 >nul')
        a('title %s' % self.title)
        a('')
        self._header(a)
        self._prologue(a)
        self._menu(a)
        for i, t in enumerate(self.p['tasks'], 1):
            self._task(a, i, t)
        self._subroutines(a)
        return CRLF.join(L) + CRLF

    def _header(self, a):
        a(':: ============================================================')
        a(':: %s' % self.title)
        a(':: Generated by Dev-Toolbox Script Editor %s. Do not edit by hand.'
          % CORE_VERSION)
        a('::')
        for k, t in zip(task_keys(self.p), self.p['tasks']):
            a('::   [%s] %s' % (k, t['name']))
        a('::')
        a(':: Usage:')
        a('::   %s            interactive menu' % self.filename)
        a('::   %s /run 2     run task 2 once, then exit (automation)' % self.filename)
        a('::   %s /runall    run every task in order, stop on failure' % self.filename)
        if self.uses_bundle:
            a('::')
            a(':: Tool bundle: %s'
              % ('newest %s on the Desktop, else beside this script' % bundle_settings(self.p)[0]
                 if (self.p.get('bundleRoot') or 'auto') == 'auto'
                 else self.p.get('bundleRoot')))
        a(':: ============================================================')
        a('')

    def _prologue(self, a):
        a('set "SE_ONESHOT="')
        a('if /i "%~1"=="/run" set "SE_ONESHOT=%~2"')
        a('if /i "%~1"=="/runall" set "SE_ONESHOT=all"')
        a('')
        if self.p.get('requireAdmin', True):
            a(':: ---------- Administrator privileges ----------')
            # 路徑與參數經環境變數交給 PowerShell（不嵌進 '...' 字串）:
            # 路徑或參數含單引號時也不會截斷指令
            a('net session >nul 2>&1')
            a('if %errorlevel% neq 0 (')
            a('    echo Requesting Administrator privileges...')
            a('    set "SE_SELF=%~f0"')
            a('    set "SE_ARGS=%*"')
            a('    if "%~1"=="" (')
            a('        powershell.exe -NoProfile -Command "Start-Process -FilePath $env:SE_SELF -Verb RunAs"')
            a('    ) else (')
            a('        powershell.exe -NoProfile -Command "Start-Process -FilePath $env:SE_SELF '
              '-Verb RunAs -ArgumentList $env:SE_ARGS"')
            a('    )')
            a('    exit /b')
            a(')')
            a('')
        # 提權後（RunAs）工作目錄會變成 System32: 一律切回腳本所在資料夾,
        # 相對路徑的指令才會跑在預期位置（pushd 也能處理 UNC 路徑）
        a(':: ---------- Work in the script folder ----------')
        a('pushd "%~dp0"')
        a('')
        a(':: ---------- Console colors (VT, Windows 10+) ----------')
        a("for /f %%E in ('echo prompt $E^| cmd') do set \"ESC=%%E\"")
        a('set "C_OK=%ESC%[92m"')
        a('set "C_ERR=%ESC%[91m"')
        a('set "C_WARN=%ESC%[93m"')
        a('set "C_INFO=%ESC%[96m"')
        a('set "C_END=%ESC%[0m"')
        a('')
        a(':: ---------- Settings ----------')
        log = self.p.get('logFile') or '%~dp0' + os.path.splitext(self.filename)[0] + '.log'
        a('set "SE_LOG=%s"' % log)
        if self.uses_bundle:
            bf, bs, bc, bh = bundle_settings(self.p)
            a('set "SE_BUNDLE_ROOT=%s"' % (self.p.get('bundleRoot') or 'auto'))
            a('set "SE_BUNDLE_GLOB=%s"' % bf)
            a('set "SE_BUNDLE_SUB=%s"' % bs)
            a('set "SE_BUNDLE_CHECK=%s"' % bc)
            a('set "SE_BUNDLE_HINT=%s"' % bh)
        a('')

    def _menu(self, a):
        tasks = self.p['tasks']
        run_all = self.p.get('runAll', True)
        keys = task_keys(self.p)
        a(':MENU')
        a('if not defined SE_ONESHOT cls')
        a('echo.')
        a('echo ============================================================')
        a('echo                  %s' % self.title)
        a('echo ============================================================')
        a('echo.')
        if self.p.get('statusSteps'):
            # 主選單上方的唯讀狀態（每次重畫都重新讀; /run /runall 不顯示）
            a('if not defined SE_ONESHOT call :SE_STATUS')
            a('if not defined SE_ONESHOT echo.')
        # 選單排版仿原版 script_tools.bat: ---- 分隔線 + 分組標題 + 縮排四格的 [N] 選項
        a('echo ------------------------------------------------------------')
        a('echo.')
        group = ''
        for t, k in zip(tasks, keys):
            g = t.get('group') or ''
            if g and g != group:
                a('echo   %s' % g)
            group = g or group
            a('echo     [%s] %s' % (k, t['name']))
            if t.get('desc'):
                a('echo            %s' % t['desc'])
        a('echo.')
        if run_all:
            a('echo     [A] Run all tasks in order')
        a('echo     [0] Exit')
        a('echo ============================================================')
        a('if defined SE_ONESHOT (')
        a('    set "SE_SEL=%SE_ONESHOT%"')
        a('    goto SE_DISPATCH')
        a(')')
        numbered = [k for k in keys if k.isdigit()]
        if len(numbered) <= 9:
            # 原版用 choice 一鍵選（按鍵即選, 免 Enter）; 編號任務 >9 時退回 set /p
            chars = ''.join(k for k in keys if k.isdigit()) \
                + ''.join(k for k in keys if not k.isdigit()) \
                + ('A' if run_all else '') + '0'
            a('choice /C %s /N /M "Select an option: "' % chars)
            a('set "SE_SEL="')
            for idx in range(len(chars), 0, -1):
                a('if %%errorlevel%%==%d set "SE_SEL=%s"' % (idx, chars[idx - 1]))
        else:
            a('set "SE_SEL="')
            a('set /p "SE_SEL=Select an option: "')
        a(':SE_DISPATCH')
        a('if "%SE_SEL%"=="0" goto EXIT')
        for i, k in enumerate(keys, 1):
            a('if /i "%%SE_SEL%%"=="%s" goto GO_T%d' % (k, i))
        if run_all:
            a('if /i "%SE_SEL%"=="A" goto RUNALL')
            a('if /i "%SE_SEL%"=="all" goto RUNALL')   # /runall 直跑用
        else:
            # 專案沒開 Run all: /runall 或按 A 給明確錯誤, 不留幽靈 goto
            a('if /i "%SE_SEL%"=="A" goto NO_RUNALL')
            a('if /i "%SE_SEL%"=="all" goto NO_RUNALL')
        a('echo.')
        a('echo %C_ERR%[ERROR] Invalid selection.%C_END%')
        a('if not defined SE_ONESHOT goto MENU')
        a('set "SE_EXIT_RC=2"')
        a('goto EXIT')
        a('')
        if self.p.get('runAll', True):
            a(':RUNALL')
            for i in range(1, len(self.p['tasks']) + 1):
                a('call :T%d' % i)
                a('if errorlevel 1 goto RUNALL_STOP')
            a('set "SE_EXIT_RC=0"')
            a('goto EXIT')
            a(':RUNALL_STOP')
            a('echo.')
            a('echo %C_ERR%[ERROR] Run all stopped early at the task above.%C_END%')
            a('set "SE_EXIT_RC=1"')
            a('goto EXIT')
            a('')
        # 選單分派: 逐行 goto（不用 paren 區塊, 避免 %SE_TASK_RC% 提前展開;
        # 每行單一命令, 不踩 if cond cmd1 & cmd2 的解析陷阱）
        for i in range(1, len(self.p['tasks']) + 1):
            a(':GO_T%d' % i)
            a('call :T%d' % i)
            a('goto SE_TASK_DONE')
        a('')
        a(':SE_TASK_DONE')
        a('if defined SE_ONESHOT goto SE_ONESHOT_DONE')
        a('pause')
        a('goto MENU')
        a(':SE_ONESHOT_DONE')
        a('set "SE_EXIT_RC=%SE_TASK_RC%"')
        a('goto EXIT')
        if not self.p.get('runAll', True):
            a('')
            a(':NO_RUNALL')
            a('echo.')
            a('echo %C_ERR%[ERROR] This project has no "Run all" option.%C_END%')
            a('set "SE_EXIT_RC=2"')
            a('goto EXIT')

    # ---------------- 一個 Task = 主選單一項
    def _task(self, a, idx, task):
        tag = 'T%d' % idx
        a(':: ============================================================')
        a(':: Task %d: %s' % (idx, task['name']))
        a(':: ============================================================')
        a(':%s' % tag)
        a('if not defined SE_ONESHOT cls')   # 原版風格: 每個操作換一屏（自動化保留完整輸出）
        a('set "SE_TASK_RC=0"')
        a('echo.')
        a('echo ============================================================')
        a('echo   %s' % task['name'])
        a('echo ============================================================')
        a('>>"%SE_LOG%" echo.')
        a('>>"%SE_LOG%" echo ---- %date% %time%  Task start: ' + task['name'])
        n = len(task['steps'])
        # 每步結果（結束時的 OK 清單用）: 預設 not run = 沒有執行到
        for si in range(1, n + 1):
            a('set "%s_S%d_R=not run"' % (tag, si))
        has_bundle = any(self.blocks.get(s.get('block'), {}).get('needsBundle')
                         for s in task['steps'])
        if has_bundle:
            a('set "SE_NOBUNDLE="')
            if task.get('bundleOptional'):
                # 找不到工具包不算失敗: [INFO] 後只略過需要工具包的步驟（仿原版 [9] 狀態）
                a('call :SE_RESOLVE_BUNDLE optional')
                a('if errorlevel 1 set "SE_NOBUNDLE=1"')
            else:
                a('call :SE_RESOLVE_BUNDLE')
                a('if errorlevel 1 (')
                a('    set "SE_TASK_RC=1"')
                a('    goto %s_END' % tag)
                a(')')
        files = required_files(task, self.blocks)
        if files:
            # 執行前檢查必要檔案: 缺任何一個就列出全部缺少的檔案, 任務不執行
            a('set "SE_MISS="')
            for f in files:
                path = self._expand_cmd(f)
                if '{TOOLS}' in f or '{BUNDLE}' in f:
                    a('if not defined SE_NOBUNDLE if not exist "%s" call :SE_MISSING "%s"' % (path, path))
                else:
                    a('if not exist "%s" call :SE_MISSING "%s"' % (path, path))
            a('if defined SE_MISS (')
            a('    echo.')
            a('    echo %C_ERR%[ERROR] Required files are missing - task not started.%C_END%')
            a('    set "SE_TASK_RC=1"')
            a('    goto %s_END' % tag)
            a(')')
        for si, s in enumerate(task['steps'], 1):
            self._step(a, tag, si, n, task, s)
        a(':%s_END' % tag)
        if n > 1 and self.p.get('summary', True):
            a('echo.')
            a('echo ------------------------------------------------------------')
            a('echo Step results')
            a('echo ------------------------------------------------------------')
            for si, s in enumerate(task['steps'], 1):
                name = self.blocks[s['block']]['name']
                a('echo   [%d/%d] %-40s %%%s_S%d_R%%' % (si, n, name, tag, si))
                a('>>"%%SE_LOG%%" echo   [%d/%d] %s  %%%s_S%d_R%%' % (si, n, name, tag, si))
        a('if not "%%SE_TASK_RC%%"=="0" goto %s_SUMMARY' % tag)
        a('echo.')
        a('echo ============================================================')
        a('echo %%C_OK%%[SUCCESS] Task completed: %s%%C_END%%' % task['name'])
        a('echo ============================================================')
        a('echo.')
        a('>>"%SE_LOG%" echo   Result: SUCCESS')
        a('exit /b 0')
        a(':%s_SUMMARY' % tag)
        a('echo.')
        a('echo ============================================================')
        a('echo %%C_ERR%%[FAILED] Task did not finish: %s%%C_END%%' % task['name'])
        a('echo ============================================================')
        a('echo.')
        a('>>"%SE_LOG%" echo   Result: FAILED')
        a('exit /b 1')
        a('')

    # ---------------- 一個 step
    def _step(self, a, tag, si, total, task, step):
        block = self.blocks[step['block']]
        label = '[%d/%d] %s' % (si, total, block['name'])
        var = '%s_S%d' % (tag, si)
        a(':: ---------- %s ----------' % label)
        blabel = paren_safe(label)       # 進 ( ) 區塊的文字: 括號要跳脫
        if block.get('needsBundle'):
            # bundleOptional 的任務找不到工具包時: 這步略過（不算失敗）
            a('if defined SE_NOBUNDLE (')
            a('    echo %%C_INFO%%[INFO] %s skipped - tool bundle not found.%%C_END%%' % blabel)
            a('    >>"%%SE_LOG%%" echo   %s -- SKIPPED, no bundle' % blabel)
            a('    set "%s_R=SKIPPED (no bundle)"' % var)
            a('    goto %s_NEXT' % var)
            a(')')
        if step.get('confirm'):
            a('call :SE_ASK_YES "Run this step? %s"' % block['name'])
            a('if errorlevel 2 (')
            a('    echo %C_INFO%[INFO] Step skipped by operator.%C_END%')
            a('    >>"%%SE_LOG%%" echo   %s -- SKIPPED' % blabel)
            a('    set "%s_R=SKIPPED"' % var)
            a('    goto %s_NEXT' % var)
            a(')')
        for p in self._step_params(step, block):
            self._param(a, var, p)
        if block.get('raw'):
            self._raw_block(a, block, label, var, tag)
            self._step_ok(a, tag, var, step)
            a(':%s_NEXT' % var)
            a('')
            return
        okcodes = step.get('expectRc') or block.get('expectRc', [0])
        a('set "SE_STEP=%s"' % label)
        a('set "SE_OKCODES=%s"' % ' '.join(str(c) for c in okcodes))
        # 注意: 不加外層引號 - cmd 的 set "V=..." 無法容納值裡的引號（工具包區塊路徑有引號）
        a('set SE_CMD=' + self._expand_cmd(block.get('cmd', '')))
        # 畫面只印短指令（SE_SHOW）; log 仍記完整的 SE_CMD
        a('set SE_SHOW=' + self._expand_cmd(display_command(block)))
        # 目標是 .bat/.cmd 時要用 call, 否則控制權交給它就不回來了; 其他指令不用 call
        # （call 會把引號內的 ^ 變兩個, 破壞 PowerShell 單行腳本裡的 regex）
        a('set "SE_CALL=%s"' % ('call' if self._runs_batch(block, step) else ''))
        loop = step.get('loop')
        if not loop:
            a('call :SE_RUN_STEP')
            a('if not errorlevel 1 goto %s_OK' % var)
            a('goto %s_ERR' % var)
        else:
            self._loop(a, var, loop)
        a(':%s_ERR' % var)
        a('set "%s_R=FAILED"' % var)
        self._on_fail(a, tag, var, step, label)
        a('goto %s_NEXT' % var)
        a(':%s_OK' % var)
        self._step_ok(a, tag, var, step)
        a(':%s_NEXT' % var)
        a('')

    def _runs_batch(self, block, step):
        """步驟的程式是不是批次檔（.bat/.cmd）: 以固定參數展開後的第一個 token 判斷。"""
        vals = {p['name']: str(p.get('value') if p['mode'] == 'fixed' else p.get('default', ''))
                for p in self._step_params(step, block)}
        cmd = PLACEHOLDER_RE.sub(lambda m: vals.get(m.group(1), ''), block.get('cmd') or '').strip()
        m = re.match(r'"([^"]+)"|(\S+)', cmd)
        prog = (m.group(1) or m.group(2)) if m else ''
        return prog.lower().endswith(('.bat', '.cmd'))

    @staticmethod
    def _step_ok(a, tag, var, step):
        """步驟成功: 記 OK; 有 askAfter 時再問一次, 選否就不跑本任務後續步驟（任務仍算成功）。"""
        a('set "%s_R=OK"' % var)
        if step.get('askAfter'):
            a('call :SE_ASK_YES "%s"' % step['askAfter'])
            a('if errorlevel 2 (')
            a('    echo %C_INFO%[INFO] Remaining steps skipped by operator.%C_END%')
            a('    >>"%%SE_LOG%%" echo   %s -- answered No, remaining steps skipped' % paren_safe(step['askAfter']))
            a('    goto %s_END' % tag)
            a(')')

    @staticmethod
    def _step_params(step, block):
        """合并 block.params 預設與 step 覆蓋。"""
        out = []
        for p in block.get('params', []):
            name = p['name']
            merged = {'name': name,
                      'prompt': p.get('prompt', name),
                      'mode': p.get('mode', 'ask'),
                      'default': p.get('default', ''),
                      'validate': p.get('validate', ''),
                      'value': p.get('value', ''),
                      'uppercase': bool(p.get('uppercase')),
                      'minLength': p.get('minLength'),
                      'maxLength': p.get('maxLength')}
            ov = (step.get('params') or {}).get(name)
            if isinstance(ov, dict):
                for k in PARAM_OVERRIDE_KEYS:
                    if k in ov:
                        merged[k] = ov[k]
            merged['uppercase'] = bool(merged.get('uppercase'))
            if merged['uppercase'] and merged['mode'] == 'fixed':
                merged['value'] = str(merged.get('value', '')).upper()
            out.append(merged)
        return out

    def _param(self, a, var, p):
        bvar = 'SP_%s' % p['name']
        if p['mode'] == 'fixed':
            # 值不包在引號內: set "V=a"b" 會被 cmd 截斷; 固定值驗證已排除 & | < >
            a('set ' + bvar + '=' + str(p.get('value', '')))
            return
        lbl = '%s_P_%s' % (var, p['name'])
        hint = ' [%s]' % p['default'] if p.get('default') else ''
        lo, hi = p.get('minLength'), p.get('maxLength')
        a(':%s' % lbl)
        a('set "%s="' % bvar)
        a('set /p "%s=%s%s: "' % (bvar, p['prompt'], hint))
        if p.get('default'):
            a('if not defined %s set "%s=%s"' % (bvar, bvar, p['default']))
        if not p.get('validate') and not lo:
            a('if defined %s goto %s_OK' % (bvar, lbl))
            a('echo %C_ERR%[ERROR] A value is required.%C_END%')
            a('goto %s' % lbl)
            a(':%s_OK' % lbl)
        elif not p.get('validate'):
            a('if not defined %s (' % bvar)
            a('    echo %C_ERR%[ERROR] A value is required.%C_END%')
            a('    goto %s' % lbl)
            a(')')
        if p.get('uppercase'):
            # 轉大寫交給 PowerShell 讀環境變數（不經 cmd 展開, 輸入值裡的符號不會被解析）
            a('for /f "usebackq delims=" %%%%A in (`powershell.exe -NoProfile -Command '
              '"$env:%s.ToUpperInvariant()" ^<nul`) do set "%s=%%%%A"' % (bvar, bvar))
        if lo or hi:
            cond = []
            if lo:
                cond.append('$v.Length -lt %d' % int(lo))
            if hi:
                cond.append('$v.Length -gt %d' % int(hi))
            a('powershell.exe -NoProfile -Command '
              '"$v=[string]$env:%s;if(%s){exit 1}else{exit 0}" <nul' % (bvar, ' -or '.join(cond)))
            a('if errorlevel 1 (')
            if lo and hi and lo == hi:
                need = 'exactly %d characters' % int(lo)
            elif lo and hi:
                need = '%d to %d characters' % (int(lo), int(hi))
            elif lo:
                need = 'at least %d characters' % int(lo)
            else:
                need = 'at most %d characters' % int(hi)
            a('    echo %%C_ERR%%[ERROR] The value must be %s.%%C_END%%' % need)
            a('    goto %s' % lbl)
            a(')')
        if not p.get('validate'):
            return
        a('powershell.exe -NoProfile -Command '
          '"if($env:%s -match \'%s\'){exit 0}else{exit 1}" <nul' % (bvar, p['validate']))
        a('if errorlevel 1 (')
        # 注意: 不把 regex 原文 echo 出來 - 括號區塊內 regex 的 | ) & 會解析錯亂
        a('    echo %C_ERR%[ERROR] Invalid value - does not match the required pattern.%C_END%')
        a('    goto %s' % lbl)
        a(')')

    def _raw_block(self, a, block, label, var, tag):
        """raw 區塊: 原批次行直接展開（自帶流程, 不做 rc 檢查）。

        行內可用 {LBL}（本 step 唯一標籤, 如 T1_S3）、{TASK}（所属 Task
        標籤, 如 T1）與 {RUN}（本 Task 的選單編號, 供 RunOnce 重跑
        script /run N）產生不重複的 label/goto。
        """
        run = self.keys[int(tag[1:]) - 1]     # T3 -> 該任務的選單按鍵（/run <按鍵>）
        a('echo.')
        a('echo ------------------------------------------------------------')
        a('echo %s' % label)
        a('echo ------------------------------------------------------------')
        a('>>"%SE_LOG%" echo.')
        a('>>"%SE_LOG%" echo ' + label + '  ::  (raw block)')
        for line in block['raw']:
            a(self._expand_cmd(line, lbl=var, task=tag, run=run))
        a('>>"%SE_LOG%" echo    Raw block done (last exit code %errorlevel%)')

    def _loop(self, a, var, loop):
        delay = loop.get('delaySec', 0)
        a('set "SE_I=0"')
        if loop['mode'] == 'duration':
            a('for /f %%T in (\'powershell.exe -NoProfile -Command '
              '"[DateTimeOffset]::Now.ToUnixTimeSeconds()"\') do set "SE_T0=%%T"')
            a('set /a "SE_LIMIT=%d*60"' % loop['minutes'])
        a(':%s_LOOP' % var)
        if loop['mode'] == 'times':
            a('set /a SE_I+=1')
            a('if %%SE_I%% GTR %d goto %s_OK' % (loop['times'], var))
        a('call :SE_RUN_STEP')
        a('if errorlevel 1 goto %s_ERR' % var)
        if delay:
            # ping 延遲: 不依賴 console 輸入（timeout /t 在 stdin 被重定向時會失敗）
            a('ping -n %d -w 1000 127.0.0.1 >nul' % (delay + 1))
        if loop['mode'] == 'duration':
            a('for /f %%T in (\'powershell.exe -NoProfile -Command '
              '"[DateTimeOffset]::Now.ToUnixTimeSeconds()"\') do set "SE_TN=%%T"')
            a('set /a "SE_EL=SE_TN-SE_T0"')
            a('if %%SE_EL%% LSS %%SE_LIMIT%% goto %s_LOOP' % var)
            a('goto %s_OK' % var)
        else:
            a('goto %s_LOOP' % var)

    def _on_fail(self, a, tag, var, step, label):
        mode = step.get('onFail', 'stop')
        if mode == 'continue':
            a('set "%s_R=FAILED (continued)"' % var)
            a('echo %C_WARN%[WARN] Step failed - continuing (on-fail: continue).%C_END%')
            a('>>"%SE_LOG%" echo   ' + label + ' -- failed, continued')
        elif mode == 'ask':
            a('call :SE_ASK_YES "Step failed. Continue anyway?"')
            a('if errorlevel 2 goto %s_ABORT' % var)
            a('goto %s_NEXT' % var)
            a(':%s_ABORT' % var)
            a('set "SE_TASK_RC=1"')
            a('goto %s_END' % tag)
        else:
            a('set "SE_TASK_RC=1"')
            a('goto %s_END' % tag)

    @staticmethod
    def _expand_cmd(text, lbl=None, task=None, run=None):
        s = text.replace('{TOOLS}', '%TOOLS%').replace('{BUNDLE}', '%BUNDLE%')
        if lbl:
            s = s.replace('{LBL}', lbl)
        if task:
            s = s.replace('{TASK}', task)
        if run:
            s = s.replace('{RUN}', run)
        return PLACEHOLDER_RE.sub(lambda m: '%%%s%%' % ('SP_' + m.group(1)), s)

    # ---------------- 共用 subroutines（仿 reference :RUN_TOOL/:ASK_YES/:FIND_BUNDLE）
    def _subroutines(self, a):
        a(':: ============================================================')
        a(':: Engine: SE_RUN_STEP runs %SE_CMD% (%SE_STEP% / %SE_OKCODES%),')
        a(':: prints and appends the output to %SE_LOG%, and maps the')
        a(':: exit code to 0 (accepted) or 1 (rejected).')
        a(':: ============================================================')
        a(':SE_RUN_STEP')
        a('echo.')
        a('echo ------------------------------------------------------------')
        a('echo %SE_STEP%')
        a('if defined SE_SHOW echo   %SE_SHOW%')
        a('echo ------------------------------------------------------------')
        a('>>"%SE_LOG%" echo.')
        a('>>"%SE_LOG%" echo %SE_STEP%  ::  %SE_CMD%')
        a('set "SE_OUT=%TEMP%\\se_step_out_%RANDOM%.txt"')
        # 內建指令(echo/set)不更新 ERRORLEVEL: 先歸零, 否則會沿用上一指令的失敗 rc
        a('cmd /c exit 0')
        a('%SE_CALL% %SE_CMD% > "%SE_OUT%" 2>&1')
        a('set "SE_RC=%errorlevel%"')
        a('type "%SE_OUT%"')
        a('type "%SE_OUT%" >>"%SE_LOG%"')
        a('del "%SE_OUT%" >nul 2>&1')
        a('set "SE_OK="')
        a('for %%C in (%SE_OKCODES%) do if "%%C"=="%SE_RC%" set "SE_OK=1"')
        a('if defined SE_OK goto SE_RS_OK')
        a('echo.')
        a('echo ============================================================')
        a('echo %C_ERR%[FAILED] %SE_STEP%  exit code: %SE_RC%   '
          'accepted: %SE_OKCODES%%C_END%')
        a('echo ============================================================')
        a('>>"%SE_LOG%" echo    Exit code: %SE_RC%  -- FAILED  (accepted: %SE_OKCODES%)')
        a('exit /b 1')
        a(':SE_RS_OK')
        a('echo.')
        a('echo ============================================================')
        a('echo %C_OK%[SUCCESS] %SE_STEP%  exit code: %SE_RC%%C_END%')
        a('echo ============================================================')
        a('>>"%SE_LOG%" echo    Exit code: %SE_RC%  -- OK')
        a('exit /b 0')
        a('')
        a(':: ---------- Yes/No (Enter/Y = yes -> 1, N/Esc/Ctrl+C = no -> 2) ----------')
        a(':SE_ASK_YES')
        a('set "ASK_TEXT=%~1"')
        a('powershell.exe -NoProfile -Command "try{[Console]::TreatControlCAsInput=$true;'
          '[Console]::Write($env:ASK_TEXT+\' [Y/n]: \');while($true){$k=[Console]::ReadKey($true);'
          'if($k.Key -eq \'C\' -and ($k.Modifiers -band 4)){$a=\'N\'}elseif($k.Key -eq \'Enter\' '
          '-or $k.Key -eq \'Y\'){$a=\'Y\'}elseif($k.Key -eq \'N\' -or $k.Key -eq \'Escape\')'
          '{$a=\'N\'}else{continue};[Console]::WriteLine($a);if($a -eq \'Y\'){exit 1};exit 2}}'
          'catch{[Console]::WriteLine();exit 2}"')
        a('if "%errorlevel%"=="1" exit /b 1')
        a('exit /b 2')
        a('')
        if any(required_files(t, self.blocks) for t in self.p['tasks']):
            a(':: ---------- Required file check: list one missing file ----------')
            a(':SE_MISSING')
            a('if not defined SE_MISS echo %C_ERR%[ERROR] Required files not found:%C_END%')
            a('set "SE_MISS=1"')
            a('echo   %~1')
            a('>>"%SE_LOG%" echo   Missing file: %~1')
            a('exit /b 0')
            a('')
        if self.p.get('statusSteps'):
            a(':: ---------- Status lines above the main menu (read-only) ----------')
            a(':SE_STATUS')
            for s in self.p['statusSteps']:
                block = self.blocks[s['block']]
                for p in self._step_params(s, block):
                    a('set SP_%s=%s' % (p['name'], p.get('value') or p.get('default', '')))
                a('set SE_CMD=' + self._expand_cmd(block.get('cmd', '')))
                a('cmd /c exit 0')
                a('%SE_CMD% 2>nul')
            a('exit /b 0')
            a('')
        if self.uses_bundle:
            self._bundle_sub(a)
        a(':EXIT')
        a('if not defined SE_EXIT_RC set "SE_EXIT_RC=0"')
        a('>>"%SE_LOG%" echo Finished with exit code %SE_EXIT_RC%')
        a('endlocal & exit /b %SE_EXIT_RC%')

    def _bundle_sub(self, a):
        a(':: ---------- Locate the tool bundle ----------')
        a(':: SE_BUNDLE_ROOT = literal path, or "auto": newest')
        a(':: SE_BUNDLE_GLOB folder (Desktop, then script folder) with SE_BUNDLE_SUB [+ SE_BUNDLE_CHECK].')
        a(':: Sets BUNDLE / TOOLS (once). Returns 1 when none was found.')
        a(':: %1 = optional: report [INFO] instead of [ERROR] (caller skips bundle steps).')
        # 不用 ( ) 區塊: 路徑（如 C:\Program Files (x86)）或訊息裡的 ) 會提早結束區塊
        a(':SE_RESOLVE_BUNDLE')
        a('if defined TOOLS exit /b 0')
        a('if /i "%SE_BUNDLE_ROOT%"=="auto" goto SE_RB_AUTO')
        a('set "BUNDLE=%SE_BUNDLE_ROOT%"')
        a('set "TOOLS=%SE_BUNDLE_ROOT%"')
        a('if defined SE_BUNDLE_SUB set "TOOLS=%SE_BUNDLE_ROOT%\\%SE_BUNDLE_SUB%"')
        a('if exist "%TOOLS%" exit /b 0')
        a('set "SE_RB_TRIED=%TOOLS%"')
        a('set "TOOLS="')
        a('set "BUNDLE="')
        a('>>"%SE_LOG%" echo Bundle not found: %SE_RB_TRIED%')
        a('if /i "%~1"=="optional" goto SE_RB_FIXED_OPT')
        a('echo %C_ERR%[ERROR] Bundle path not found: %SE_RB_TRIED%%C_END%')
        a('exit /b 1')
        a(':SE_RB_FIXED_OPT')
        a('echo %C_INFO%[INFO] Tool bundle not found: %SE_RB_TRIED% - bundle steps skipped.%C_END%')
        a('exit /b 1')
        a(':SE_RB_AUTO')
        a('echo Searching for %SE_BUNDLE_GLOB% on the Desktop...')
        # 與原版選單腳本的找法相同: 桌面（GetFolderPath, 含 OneDrive 轉向）的
        # <資料夾>\<子資料夾>, 取資料夾名稱日期最新的。另外容許兩種常見放法:
        # 「解壓縮全部」多出的一層同名資料夾, 以及桌面找不到時找腳本所在資料夾。
        # 找過的位置與不合格的 win64 都會印出完整路徑。
        a('set "SE_HERE=%~dp0"')
        a('for /f "usebackq tokens=1,* delims==" %%K in (`powershell.exe -NoProfile '
          '-ExecutionPolicy Bypass -Command "' + _BUNDLE_FIND_PS + '"`) do (')
        a('    if /i "%%K"=="BUNDLE" set "BUNDLE=%%L"')
        a('    if /i "%%K"=="TOOLS"  set "TOOLS=%%L"')
        a('    if /i "%%K"=="SEARCH" echo   Looking in: %%L')
        a('    if /i "%%K"=="SEARCH" >>"%SE_LOG%" echo Bundle search: %%L')
        a('    if /i "%%K"=="SKIP"   echo   Skipped   : %%L')
        a('    if /i "%%K"=="SKIP"   >>"%SE_LOG%" echo Bundle skipped: %%L')
        a(')')
        a('if defined TOOLS goto SE_RB_FOUND')
        a('>>"%SE_LOG%" echo Bundle not found - Desktop / script folder search')
        a('if /i "%~1"=="optional" goto SE_RB_AUTO_OPT')
        a('echo %C_ERR%[ERROR] Valid tool bundle %SE_BUNDLE_GLOB% was not found.%C_END%')
        a('echo Unzip the bundle so this exists, on the Desktop or beside this script:')
        a('echo   %SE_BUNDLE_HINT%')
        a('echo Or set SE_BUNDLE_ROOT in this script to the bundle folder.')
        a('exit /b 1')
        a(':SE_RB_AUTO_OPT')
        a('echo %C_INFO%[INFO] Tool bundle %SE_BUNDLE_GLOB% not found - bundle steps skipped.%C_END%')
        a('exit /b 1')
        a(':SE_RB_FOUND')
        a('echo Bundle: %BUNDLE%')
        a('echo Tools : %TOOLS%')
        a('>>"%SE_LOG%" echo Bundle: %BUNDLE%  Tools: %TOOLS%')
        a('exit /b 0')


# ---------------------------------------------------------------- GUI .bat 匯出
# 單檔 bat + PowerShell WinForms 混合腳本, 形態仿 reference/script_tools_gui.bat:
# 前段 <# : ... #> 是批次部分（提昇權限 + 隱窗啟動 PS）, 後段是視窗程式。
# 任務、指令、可接受 rc、log 檔與選單版一致; 命令列輸入框只有這個有。
# 自動化（/run /runall）請用選單版 .bat。raw 區塊以暫存 .cmd 重放（見
# export_gui_bat docstring 的拒絕清單）。
GUI_TEMPLATE = r"""<# :
@echo off
setlocal EnableExtensions
title __TITLE__

:: ============================================================
:: __TITLE__ - window version
::
:: Generated by Dev-Toolbox Script Editor __VER__.
:: Do not edit by hand.
::
:: This batch part only requests Administrator privileges and
:: starts the PowerShell part (everything after the closing
:: comment line below) without a console window.  The tasks,
:: commands, accepted exit codes and log file match the menu
:: version, but run from buttons in a window.  The command
:: line under the output pane is only in this window.  Use
:: the menu .bat (/run, /runall) for automation.
:: ============================================================
__ADMIN__
set "SE_GUI_SELF=%~f0"
start "" powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -WindowStyle Hidden -Command "try{iex ([IO.File]::ReadAllText($env:SE_GUI_SELF))}catch{Add-Type -AssemblyName System.Windows.Forms;[void][System.Windows.Forms.MessageBox]::Show($_.ToString(),'Script Editor Tool')}"
exit /b
#>

# ============================================================
# PowerShell part - window UI
# ============================================================
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$script:Project = @'
__DATA__
'@ | ConvertFrom-Json

$script:Busy    = $false
$script:Bundle  = $null
$script:Tools   = $null
$script:LogFile = $null
$script:Abort   = $false
$script:Stop    = $false
$script:RawFail = $false
$script:ToolOut = Join-Path $env:TEMP ('se_gui_out_' + $PID + '.txt')
$script:Hist    = New-Object System.Collections.Generic.List[string]
$script:HistAt  = 0
$script:RunButtons = New-Object System.Collections.Generic.List[object]

# Output colors: the cmd console's (Campbell scheme)
$script:Colors = @{
    Text = [System.Drawing.Color]::White
    Ok   = [System.Drawing.Color]::FromArgb( 22, 198,  12)
    Err  = [System.Drawing.Color]::FromArgb(231,  72,  86)
    Warn = [System.Drawing.Color]::FromArgb(249, 241, 165)
    Info = [System.Drawing.Color]::FromArgb( 97, 214, 214)
    Back = [System.Drawing.Color]::FromArgb( 20,  20,  20)
    Head = [System.Drawing.Color]::FromArgb(255, 216,   0)
    Dim  = [System.Drawing.Color]::FromArgb(150, 150, 150)
}

$script:MonoFont = New-Object System.Drawing.Font('Consolas', 10.5)

# ---------- log file: beside the script (or the project setting)
$seSelf = $env:SE_GUI_SELF
$seDir  = if ($seSelf) { Split-Path -Parent $seSelf } else { [Environment]::GetFolderPath('Desktop') }
# Commands run in the script folder, like the menu version (after
# RunAs the start folder would be System32).
Set-Location -LiteralPath $seDir
[Environment]::CurrentDirectory = $seDir
$seLog  = [string]$script:Project.logFile
if ($seLog) {
    $seLog = [Environment]::ExpandEnvironmentVariables($seLog)
    if (-not [IO.Path]::IsPathRooted($seLog)) { $seLog = Join-Path $seDir $seLog }
} else {
    $seBase = if ($seSelf) { [IO.Path]::GetFileNameWithoutExtension($seSelf) } else { 'script_editor' }
    $seLog = Join-Path $seDir ($seBase + '.log')
}
$script:LogFile = $seLog


# ============================================================
# Output pane, status bar and log file
# ============================================================
function Write-Out {
    param([string]$Text)

    $c = $script:Colors.Text
    if     ($Text -match '\[ERROR\]|\[FAILED\]')          { $c = $script:Colors.Err }
    elseif ($Text -match '\[SUCCESS\]')                   { $c = $script:Colors.Ok }
    elseif ($Text -match '\[INFO\]|\[DONE\]|^>')          { $c = $script:Colors.Info }
    elseif ($Text -match '\[WARN\]|WARNING')              { $c = $script:Colors.Warn }
    $script:OutBox.SelectionStart  = $script:OutBox.TextLength
    $script:OutBox.SelectionLength = 0
    $script:OutBox.SelectionColor  = $c
    $script:OutBox.AppendText($Text + "`r`n")
    $script:OutBox.SelectionColor = $script:Colors.Text
    $script:OutBox.ScrollToCaret()
}

function Write-OutBytes {
    param($Bytes)

    if ($null -eq $Bytes -or $Bytes.Count -eq 0) { return }
    $t = [System.Text.Encoding]::UTF8.GetString($Bytes)
    if ($t.IndexOf([char]0xFFFD) -ge 0) { $t = [System.Text.Encoding]::Default.GetString($Bytes) }
    foreach ($ln in ($t -split "`r?`n")) { Write-Out $ln }
}

function Banner {
    param([string]$Text)

    Write-Out ''
    Write-Out ('=' * 60)
    Write-Out $Text
    Write-Out ('=' * 60)
}

function Set-Status { param([string]$Text) $script:StatusLabel.Text = $Text }

function Write-Log {
    param([string]$Text)

    try { [System.IO.File]::AppendAllText($script:LogFile, $Text + "`r`n", [System.Text.Encoding]::UTF8) } catch {}
}

function Add-LogBytes {
    param($Bytes)

    if ($null -eq $Bytes -or $Bytes.Count -eq 0) { return }
    $t = [System.Text.Encoding]::UTF8.GetString($Bytes)
    if ($t.IndexOf([char]0xFFFD) -ge 0) { $t = [System.Text.Encoding]::Default.GetString($Bytes) }
    foreach ($ln in ($t -split "`r?`n")) { Write-Log $ln }
}


# ============================================================
# Dialogs and the command runner
# ============================================================
# Any error inside a button / key handler: show it in the output and the
# log plus one friendly dialog - never the .NET unhandled-exception box.
function Report-Error {
    param($Err, [string]$Where)

    $msg = [string]$Err
    try { Write-Out ('[ERROR] ' + $Where + ' failed: ' + $msg) } catch {}
    try { Write-Log ('ERROR in ' + $Where + ': ' + $msg) } catch {}
    try {
        [void][System.Windows.Forms.MessageBox]::Show(
            ($Where + ' failed:' + [Environment]::NewLine + $msg),
            [string]$script:Project.title,
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Error)
    } catch {}
}

function Confirm-Yes {
    param([string]$Text)

    $r = [System.Windows.Forms.MessageBox]::Show(
        $Text, [string]$script:Project.title,
        [System.Windows.Forms.MessageBoxButtons]::YesNo,
        [System.Windows.Forms.MessageBoxIcon]::Question,
        [System.Windows.Forms.MessageBoxDefaultButton]::Button1)
    return ($r -eq [System.Windows.Forms.DialogResult]::Yes)
}

function Read-Param {
    param([string]$Prompt, [string]$Default, [string]$Validate,
          [bool]$Upper = $false, [int]$MinLen = 0, [int]$MaxLen = 0)

    $dlg      = New-Object System.Windows.Forms.Form
    $dlg.Text = [string]$script:Project.title
    $dlg.FormBorderStyle = 'FixedDialog'
    $dlg.StartPosition   = 'CenterScreen'
    $dlg.ClientSize = New-Object System.Drawing.Size(440, 150)
    $dlg.BackColor  = [System.Drawing.Color]::Black

    $lbl      = New-Object System.Windows.Forms.Label
    $lbl.Text = $Prompt
    $lbl.Location  = New-Object System.Drawing.Point(12, 12)
    $lbl.Size      = New-Object System.Drawing.Size(416, 22)
    $lbl.ForeColor = [System.Drawing.Color]::White

    $box      = New-Object System.Windows.Forms.TextBox
    $box.Location = New-Object System.Drawing.Point(12, 40)
    $box.Size     = New-Object System.Drawing.Size(416, 24)
    $box.Text     = $Default
    $box.Font     = $script:MonoFont
    if ($Upper)      { $box.CharacterCasing = 'Upper' }
    if ($MaxLen -gt 0) { $box.MaxLength = $MaxLen }

    $err      = New-Object System.Windows.Forms.Label
    $err.Location  = New-Object System.Drawing.Point(12, 72)
    $err.Size      = New-Object System.Drawing.Size(416, 20)
    $err.ForeColor = $script:Colors.Err

    $okb      = New-Object System.Windows.Forms.Button
    $okb.Text = 'OK'
    $okb.Location = New-Object System.Drawing.Point(272, 106)
    $okb.Size     = New-Object System.Drawing.Size(75, 26)
    $cb         = New-Object System.Windows.Forms.Button
    $cb.Text  = 'Cancel'
    $cb.Location = New-Object System.Drawing.Point(353, 106)
    $cb.Size     = New-Object System.Drawing.Size(75, 26)
    $dlg.AcceptButton  = $okb
    $dlg.CancelButton  = $cb
    $okb.Add_Click({
        # same order as the menu _param loop: default fills empty input,
        # validate checks the result, and without validate a value is
        # required (menu rejects empty and re-asks)
        $v = $box.Text.Trim()
        if (-not $v -and $Default) { $v = $Default }
        if ($Upper) { $v = $v.ToUpperInvariant() }
        if (($MinLen -gt 0 -and $v.Length -lt $MinLen) -or ($MaxLen -gt 0 -and $v.Length -gt $MaxLen)) {
            if ($MinLen -gt 0 -and $MinLen -eq $MaxLen) { $need = "exactly $MinLen characters" }
            elseif ($MinLen -gt 0 -and $MaxLen -gt 0) { $need = "$MinLen to $MaxLen characters" }
            elseif ($MinLen -gt 0) { $need = "at least $MinLen characters" }
            else { $need = "at most $MaxLen characters" }
            $err.Text = "The value must be $need."
            return
        }
        if ($Validate) {
            $m = $false
            # menu version uses PS -match (case-insensitive): same here
            try { $m = [regex]::IsMatch($v, $Validate, 'IgnoreCase') } catch { $m = $false }
            if (-not $m) { $err.Text = 'Invalid value - does not match the required pattern.'; return }
        } elseif (-not $v -and $MinLen -le 0) {
            $err.Text = 'A value is required.'
            return
        }
        $dlg.Tag = $v
        $dlg.DialogResult = 'OK'
    })
    [void]$dlg.Controls.Add($lbl)
    [void]$dlg.Controls.Add($box)
    [void]$dlg.Controls.Add($err)
    [void]$dlg.Controls.Add($okb)
    [void]$dlg.Controls.Add($cb)
    if ($dlg.ShowDialog() -ne 'OK') { return $null }
    return [string]$dlg.Tag
}

function Wait-Seconds {
    param([double]$Seconds)

    $end = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $end) {
        [System.Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 50
    }
}

# Same console code page (chcp 65001) and redirection as the menu
# .bat.  The window stays responsive while the command runs.
# Returns .Rc (exit code) and .Bytes (the command's output).
function Invoke-Cmd {
    param([string]$CommandLine)

    Remove-Item -LiteralPath $script:ToolOut -ErrorAction SilentlyContinue

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName        = Join-Path $env:SystemRoot 'System32\cmd.exe'
    $psi.Arguments       = '/d /c "chcp 65001 >nul & ' + $CommandLine + ' > "' + $script:ToolOut + '" 2>&1"'
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow  = $true
    $psi.WorkingDirectory = [Environment]::CurrentDirectory

    $p = [System.Diagnostics.Process]::Start($psi)
    $stopped = $false
    while (-not $p.WaitForExit(50)) {
        [System.Windows.Forms.Application]::DoEvents()
        if ($script:StopRequested -and -not $stopped) {
            # Stop button / Esc: end the command and everything it started
            $stopped = $true
            & (Join-Path $env:SystemRoot 'System32\taskkill.exe') /F /T /PID $p.Id 2>&1 | Out-Null
        }
    }
    $rc = $p.ExitCode
    if ($stopped) { $rc = -1 }
    $p.Dispose()

    $bytes = [byte[]]@()
    if (Test-Path -LiteralPath $script:ToolOut) { $bytes = [System.IO.File]::ReadAllBytes($script:ToolOut) }
    return [pscustomobject]@{ Rc = $rc; Bytes = $bytes }
}

function Expand-Cmd {
    param($Tpl, $Vals)

    $s = [string]$Tpl
    if ($null -ne $script:Tools) {
        $s = $s.Replace('{TOOLS}', [string]$script:Tools).Replace('{BUNDLE}', [string]$script:Bundle)
    }
    foreach ($m in [regex]::Matches($s, '\$\{([A-Za-z_][A-Za-z0-9_]*)\}')) {
        $v = ''
        if ($Vals.Contains($m.Groups[1].Value)) { $v = [string]$Vals[$m.Groups[1].Value] }
        $s = $s.Replace($m.Value, $v)
    }
    return $s
}

# Locate the tool bundle (fixed root, or newest Desktop / script folder copy)
function Resolve-Bundle {
    param([switch]$Optional)

    if ($script:Tools) { return $true }
    $root = [string]$script:Project.bundleRoot
    if (-not $root) { $root = 'auto' }
    $g   = [string]$script:Project.bundleFolder
    $sub = [string]$script:Project.bundleSub
    $chk = [string]$script:Project.bundleCheck
    if ($root -ne 'auto') {
        $t = if ($sub) { Join-Path $root $sub } else { $root }
        if (Test-Path -LiteralPath $t) {
            $script:Bundle = $root
            $script:Tools  = $t
            return $true
        }
        if ($Optional) { Write-Out "[INFO] Tool bundle not found: $t - bundle steps skipped." }
        else           { Write-Out "[ERROR] Bundle path not found: $t" }
        Write-Log "Bundle not found: $t"
        return $false
    }
    # Same rules as the menu version: Desktop first, then the script folder;
    # <folder>\<sub> or one nested <folder> level (Extract All); <sub> must
    # hold the check file when one is set; newest _DD_Month_YYYY wins.
    Write-Out ('[INFO] Searching for ' + $g + ' on the Desktop...')
    $desktop = [Environment]::GetFolderPath('Desktop')
    if (-not $desktop) { $desktop = Join-Path $env:USERPROFILE 'Desktop' }
    $roots = @($desktop)
    if ($seDir -and ($roots -notcontains $seDir.TrimEnd('\'))) { $roots += $seDir.TrimEnd('\') }
    $best = $null
    foreach ($r in $roots) {
        Write-Out ('  Looking in: ' + (Join-Path $r $g))
        Write-Log ('Bundle search: ' + (Join-Path $r $g))
        $bestDate = [datetime]::MinValue
        foreach ($f in @(Get-ChildItem -LiteralPath $r -Directory -Filter $g -ErrorAction SilentlyContinue)) {
            $cands = @($f.FullName) + @(Get-ChildItem -LiteralPath $f.FullName -Directory -Filter $g -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
            $p = $null
            foreach ($c in $cands) {
                $w = if ($sub) { Join-Path $c $sub } else { $c }
                if (-not (Test-Path -LiteralPath $w)) { continue }
                if ((-not $chk) -or (Test-Path -LiteralPath (Join-Path $w $chk))) { $p = $w; break }
                Write-Out ('  Skipped   : ' + $w + ' - ' + $chk + ' not found')
                Write-Log ('Bundle skipped: ' + $w + ' - ' + $chk + ' not found')
            }
            if (-not $p) {
                if ($sub -and $cands.Count -eq 1 -and -not (Test-Path -LiteralPath (Join-Path $f.FullName $sub))) {
                    Write-Out ('  Skipped   : ' + $f.FullName + ' - no ' + $sub + ' folder')
                    Write-Log ('Bundle skipped: ' + $f.FullName + ' - no ' + $sub + ' folder')
                }
                continue
            }
            $d     = $f.LastWriteTime
            $parts = $f.Name.Split('_')
            if ($parts.Count -ge 4) {
                $n = $parts.Count
                $s = $parts[$n - 3] + ' ' + $parts[$n - 2] + ' ' + $parts[$n - 1]
                foreach ($fmt in @('d MMMM yyyy', 'd MMM yyyy')) {
                    try { $d = [datetime]::ParseExact($s, $fmt, [Globalization.CultureInfo]::InvariantCulture); break } catch {}
                }
            }
            if ($d -gt $bestDate) { $bestDate = $d; $best = [PSCustomObject]@{ Name = $f.Name; Tools = $p } }
        }
        if ($best) { break }
    }
    if ($null -eq $best) {
        if ($Optional) {
            Write-Out ('[INFO] Tool bundle ' + $g + ' not found - bundle steps skipped.')
        } else {
            Write-Out ('[ERROR] Valid tool bundle ' + $g + ' was not found.')
            Write-Out 'Unzip the bundle so this exists, on the Desktop or beside this script:'
            Write-Out ('  ' + [string]$script:Project.bundleHint)
        }
        Write-Log 'Bundle not found (Desktop / script folder search)'
        return $false
    }
    $script:Tools  = $best.Tools
    $script:Bundle = Split-Path -Parent $best.Tools
    Write-Out "Bundle: $($best.Name)"
    Write-Out "Tools : $script:Tools"
    Write-Log "Bundle: $script:Bundle  Tools: $script:Tools"
    return $true
}


# ============================================================
# Task runner (same step semantics as the menu .bat)
# ============================================================
function Run-Step {
    param($St, [int]$I, [int]$N)

    $label = '[' + $I + '/' + $N + '] ' + [string]$St.label
    $script:StepResult = 'OK'
    if ($St.bundle -and $script:NoBundle) {
        # bundleOptional task without a bundle: skip, not a failure (menu parity)
        Write-Out ('[INFO] ' + $label + ' skipped - tool bundle not found.')
        Write-Log ($label + ' -- SKIPPED (no bundle)')
        $script:StepResult = 'SKIPPED (no bundle)'
        return $true
    }
    if ($St.confirm) {
        if (-not (Confirm-Yes ('Run this step? ' + [string]$St.label))) {
            Write-Out '[INFO] Step skipped by operator.'
            Write-Log ($label + ' -- SKIPPED')
            $script:StepResult = 'SKIPPED'
            return $true
        }
    }
    if ($St.bundle) {
        if (-not (Resolve-Bundle)) {
            # defensive only: Run-TaskCore pre-scans the bundle at task
            # start (menu version does the same), so this is normally
            # unreachable - keep it as a guard, task fails outright
            Write-Log ($label + ' -- BUNDLE NOT FOUND')
            $script:Abort = $true
            return $false
        }
    }

    $vals = @{}
    foreach ($pp in @($St.params)) {
        if ([string]$pp.mode -eq 'fixed') { $vals[[string]$pp.name] = [string]$pp.value; continue }
        $v = Read-Param ([string]$pp.prompt) ([string]$pp.default) ([string]$pp.validate) ([bool]$pp.upper) ([int]$pp.minLen) ([int]$pp.maxLen)
        if ($null -eq $v) {
            Write-Out '[INFO] Input cancelled - task aborted.'
            Write-Log ($label + ' -- CANCELLED')
            $script:Abort = $true
            return $false
        }
        $vals[[string]$pp.name] = $v
    }

    # raw block: replay its batch lines through a temp .cmd.  The temp
    # script gets the same environment as the menu bat (TOOLS/BUNDLE/SE_LOG)
    # and a SE_TASK_END label, and reports the menu engine's outcome back
    # through its exit code (rc contract right below the temp body).
    if ($St.raw) {
        Write-Out ''
        Write-Out ('-' * 60)
        Write-Out $label
        Write-Out ('-' * 60)
        Write-Log ''
        Write-Log ($label + '  ::  (raw block)')
        Set-Status ('Running: ' + $label)
        $rawPath = Join-Path $env:TEMP ('se_gui_raw_' + $PID + '_' + $I + '_' + (Get-Random) + '.cmd')
        try {
            $seBody = @('@echo off', 'set "SE_TASK_RC=0"')
            if ($script:Tools) {
                $seBody += 'set "TOOLS=' + [string]$script:Tools + '"'
                $seBody += 'set "BUNDLE=' + [string]$script:Bundle + '"'
            }
            $seBody += 'set "SE_LOG=' + [string]$script:LogFile + '"'
            foreach ($seLn in @($St.raw)) { $seBody += (Expand-Cmd ([string]$seLn) $vals) }
            # rc contract with the menu version (SE_TASK_RC init 0 at task start):
            #   0 = raw done, rc clean            -> continue, ok
            #   1 = raw done, SE_TASK_RC != 0     -> continue, task ends FAILED
            #   2 = "goto {TASK}_END" with rc!=0  -> stop, FAILED
            #   3 = "goto {TASK}_END" with rc==0  -> stop, SUCCESS
            $seBody += 'if not "%SE_TASK_RC%"=="0" exit /b 1'
            $seBody += 'exit /b 0'
            $seBody += ':SE_TASK_END'
            $seBody += 'if not "%SE_TASK_RC%"=="0" exit /b 2'
            $seBody += 'exit /b 3'
            $seTxt = $seBody -join "`r`n"
            # (check per line joined with spaces: CR/LF are not printable)
            if (($seBody -join ' ') -match '[^\x09\x20-\x7E]') {
                # temp .cmd is written as ASCII: paths (bundle/log) or typed
                # parameter values with non-ASCII would be mangled into '?',
                # and control chars in raw lines have no reliable replay
                Write-Out '[ERROR] Raw replay needs printable-ASCII paths, values and raw lines - task aborted.'
                Write-Log ($label + ' -- RAW REPLAY NOT ASCII-SAFE')
                $script:Abort = $true
                return $false
            }
            if ($script:Tools -match '%' -or [string]$script:LogFile -match '%') {
                # '%' in a path would be re-expanded inside the temp .cmd
                Write-Out '[ERROR] Raw replay cannot use paths containing % - task aborted.'
                Write-Log ($label + ' -- RAW REPLAY PATH UNSAFE')
                $script:Abort = $true
                return $false
            }
            try {
                [System.IO.File]::WriteAllText($rawPath, $seTxt + "`r`n",
                                               [System.Text.Encoding]::ASCII)
            } catch {
                # disk full / AV blocked TEMP write: fail the task, never
                # let the exception escape into the Click handler (Busy lock)
                Write-Out ('[ERROR] Cannot write raw replay temp file - task aborted. ' + $_.Exception.Message)
                Write-Log ($label + ' -- RAW REPLAY TEMP WRITE FAILED')
                $script:Abort = $true
                return $false
            }
            $r = Invoke-Cmd ('"' + $rawPath + '"')
            Write-OutBytes $r.Bytes
            Add-LogBytes $r.Bytes
            if ($r.Rc -eq 2) {
                Write-Log '   raw block ended the task (reported failure)'
                $script:Abort = $true
                return $false
            }
            if ($r.Rc -eq 3) {
                Write-Log '   raw block ended the task early (menu parity: skip the rest)'
                $script:Stop = $true
                return $true
            }
            if ($r.Rc -eq 1) {
                Write-Out '[ERROR] Raw block reported a failure (task will end FAILED).'
                Write-Log '   raw block reported SE_TASK_RC (task will end FAILED)'
                $script:RawFail = $true
                return $true
            }
            Write-Log ('   Raw block done (last exit code ' + $r.Rc + ')')
        } finally {
            Remove-Item -LiteralPath $rawPath -Force -ErrorAction SilentlyContinue
        }
        return $true
    }

    $cmd    = Expand-Cmd ([string]$St.cmd) $vals
    $show   = if ($St.show) { Expand-Cmd ([string]$St.show) $vals } else { '' }
    $okList = @($St.ok)
    $okText = $okList -join ' '
    $loop   = $St.loop
    $count  = 0
    $t0     = Get-Date
    while ($true) {
        Write-Out ''
        Write-Out ('-' * 60)
        Write-Out $label
        if ($show) { Write-Out ('  ' + $show) }
        Write-Out ('-' * 60)
        Write-Log ''
        Write-Log ($label + '  ::  ' + $cmd)
        Set-Status ('Running: ' + $label)

        $r = Invoke-Cmd $cmd
        Write-OutBytes $r.Bytes
        Add-LogBytes $r.Bytes

        if ($okList -contains $r.Rc) {
            Banner ('[SUCCESS] ' + $label + '  exit code: ' + $r.Rc)
            Write-Log ('   Exit code: ' + $r.Rc + '  -- OK')
        } else {
            Banner ('[FAILED] ' + $label + '  exit code: ' + $r.Rc + '   accepted: ' + $okText)
            Write-Log ('   Exit code: ' + $r.Rc + '  -- FAILED  (accepted: ' + $okText + ')')
            $script:StepResult = 'FAILED'
            return $false
        }
        if ($null -eq $loop) { return $true }
        if ([string]$loop.mode -eq 'times') {
            $count++
            if ($count -ge [int]$loop.times) { return $true }
        } else {
            if (((Get-Date) - $t0).TotalSeconds -ge ([int]$loop.minutes * 60)) { return $true }
        }
        $d = [int]$loop.delaySec
        if ($d -gt 0) { Wait-Seconds $d }
    }
}

function Run-TaskCore {
    param($Task)

    Write-Log ''
    Write-Log ('---- ' + (Get-Date -Format 's') + '  Task start: ' + [string]$Task.name)
    $script:Abort    = $false
    $script:Stop     = $false
    $script:RawFail  = $false
    $script:NoBundle = $false
    $steps = @($Task.steps)
    $results = @()
    foreach ($seSt in $steps) { $results += 'not run' }

    # menu version resolves the bundle at task start - before any step can
    # have side effects - and fails the whole task when it is missing
    # (bundleOptional: [INFO], then only the bundle steps are skipped)
    $seNeedBundle = $false
    foreach ($seSt in $steps) { if ($seSt.bundle) { $seNeedBundle = $true; break } }
    if ($seNeedBundle) {
        if ($Task.bundleOptional) {
            if (-not (Resolve-Bundle -Optional)) { $script:NoBundle = $true }
        } elseif (-not (Resolve-Bundle)) {
            Write-Log '  Result: FAILED (tool bundle not found)'
            Banner ('[FAILED] Task did not finish: ' + [string]$Task.name)
            return $false
        }
    }

    # required files: list every missing one, then do not start the task
    $missing = @()
    foreach ($f in @($Task.files)) {
        if (-not $f) { continue }
        $f = [string]$f
        if (($f.Contains('{TOOLS}') -or $f.Contains('{BUNDLE}')) -and $script:NoBundle) { continue }
        $path = Expand-Cmd $f @{}
        if (-not (Test-Path -LiteralPath $path)) { $missing += $path }
    }
    if ($missing.Count -gt 0) {
        Write-Out ''
        Write-Out '[ERROR] Required files not found:'
        foreach ($m in $missing) { Write-Out ('  ' + $m); Write-Log ('  Missing file: ' + $m) }
        Write-Out '[ERROR] Required files are missing - task not started.'
        Write-Log '  Result: FAILED (required files missing)'
        Banner ('[FAILED] Task did not finish: ' + [string]$Task.name)
        return $false
    }

    $okAll = $true
    for ($i = 0; $i -lt $steps.Count; $i++) {
        if ($script:StopRequested) {
            Write-Out '[ERROR] Stopped by operator - remaining steps skipped.'
            Write-Log '   stopped by operator'
            $okAll = $false
            break
        }
        if (Run-Step $steps[$i] ($i + 1) $steps.Count) {
            $results[$i] = $script:StepResult
            if ($script:Stop) { break }   # raw "goto {TASK}_END": skip the rest
            $ask = [string]$steps[$i].askAfter
            if ($ask -and $script:StepResult -eq 'OK' -and -not (Confirm-Yes $ask)) {
                Write-Out '[INFO] Remaining steps skipped by operator.'
                Write-Log ('   ' + $ask + ' -- answered No, remaining steps skipped')
                break
            }
            continue
        }
        $results[$i] = 'FAILED'
        if ($script:StopRequested) {
            $results[$i] = 'STOPPED'
            Write-Out '[ERROR] Stopped by operator - remaining steps skipped.'
            Write-Log '   stopped by operator'
            $okAll = $false
            break
        }
        if ($script:Abort) { $okAll = $false; break }
        $mode = [string]$steps[$i].onFail
        if ($mode -eq 'continue') {
            $results[$i] = 'FAILED (continued)'
            Write-Out '[WARN] Step failed - continuing (on-fail: continue).'
            Write-Log '   failed, continued'
        } elseif ($mode -eq 'ask') {
            if (-not (Confirm-Yes 'Step failed. Continue anyway?')) { $okAll = $false; break }
        } else { $okAll = $false; break }
    }
    if ($script:RawFail) { $okAll = $false }
    if ($steps.Count -gt 1 -and $script:Project.summary) {
        Write-Out ''
        Write-Out ('-' * 60)
        Write-Out 'Step results'
        Write-Out ('-' * 60)
        for ($i = 0; $i -lt $steps.Count; $i++) {
            $line = ('  [' + ($i + 1) + '/' + $steps.Count + '] ' + [string]$steps[$i].label).PadRight(48) + $results[$i]
            Write-Out $line
            Write-Log $line
        }
    }
    if ($okAll) {
        Banner ('[SUCCESS] Task completed: ' + [string]$Task.name)
        Write-Log '  Result: SUCCESS'
    } else {
        Banner ('[FAILED] Task did not finish: ' + [string]$Task.name)
        Write-Log '  Result: FAILED'
    }
    return $okAll
}

function Set-Busy {
    param([bool]$B)

    $script:Busy = $B
    # NB: never name the loop variable $b - PowerShell is case-insensitive,
    # and the [bool] $B param type constraint would coerce every Button to $true
    foreach ($seBtn in $script:RunButtons) { if ($seBtn) { $seBtn.Enabled = -not $B } }
    $script:RunBtn.Enabled  = -not $B
    $script:StopBtn.Enabled = $B
    if ($B) { $script:StopRequested = $false }
    if (-not $B) { Set-Status 'Ready' }
}

# Status cards above the output pane: the project's read-only status
# commands, read at start, after every task and with F5.  "Name : Value"
# output lines become cards; other lines are shown as they are.
function Update-StatusPanel {
    if (-not $script:StatusPanel) { return }
    $lines = @()
    foreach ($c in @($script:Project.status)) {
        if (-not $c) { continue }
        $r = Invoke-Cmd ([string]$c)
        $txt = [System.Text.Encoding]::UTF8.GetString($r.Bytes)
        foreach ($ln in ($txt -split "`r?`n")) { if ($ln.Trim()) { $lines += $ln } }
    }
    $script:StatusPanel.SuspendLayout()
    $script:StatusPanel.Controls.Clear()
    foreach ($ln in $lines) {
        $card = New-Object System.Windows.Forms.Label
        $card.AutoSize  = $true
        $card.Margin    = New-Object System.Windows.Forms.Padding(4)
        $card.Padding   = New-Object System.Windows.Forms.Padding(8, 4, 8, 4)
        $card.BackColor = [System.Drawing.Color]::FromArgb(32, 32, 32)
        $card.ForeColor = [System.Drawing.Color]::White
        $card.Font      = New-Object System.Drawing.Font('Segoe UI', 9.5)
        if ($ln -match '^\s*([^:]{1,40}?)\s*:\s*(.+?)\s*$') {
            $card.Text = $Matches[1] + "`n" + $Matches[2]
        } else {
            $card.Text = $ln.Trim()
        }
        [void]$script:StatusPanel.Controls.Add($card)
    }
    $script:StatusPanel.ResumeLayout()
    # Dock=Top panels do not grow by themselves: size it to the wrapped cards
    $seW = [Math]::Max(200, $script:StatusPanel.ClientSize.Width)
    $sePref = $script:StatusPanel.GetPreferredSize((New-Object System.Drawing.Size($seW, 0)))
    $script:StatusPanel.Height = [Math]::Max(40, $sePref.Height)
}

function Refresh-Status {
    if ($script:Busy -or -not $script:StatusPanel) { return }
    Set-Busy $true
    Set-Status 'Reading status...'
    try { Update-StatusPanel } finally { Set-Busy $false }
}

function Start-Task {
    param([int]$Ti)

    if ($script:Busy) { return }
    Set-Busy $true
    try {
        [void](Run-TaskCore $script:Project.tasks[$Ti])
        Set-Status 'Reading status...'
        Update-StatusPanel
    } catch {
        Report-Error $_ ('Task "' + [string]$script:Project.tasks[$Ti].name + '"')
    } finally {
        # a step that throws must never leave the window all-disabled
        Set-Busy $false
    }
}

function Start-TaskAll {
    if ($script:Busy) { return }
    Set-Busy $true
    try {
        for ($i = 0; $i -lt $script:Project.tasks.Count; $i++) {
            if (-not (Run-TaskCore $script:Project.tasks[$i])) {
                Write-Out ''
                Write-Out '[ERROR] Run all stopped early at the task above.'
                break
            }
        }
        Update-StatusPanel
    } catch {
        Report-Error $_ 'Run all'
    } finally {
        Set-Busy $false
    }
}

function Invoke-CmdLine {
    if ($script:Busy) { return }
    $c = $script:CmdBox.Text.Trim()
    if (-not $c) { return }
    $script:Hist.Add($c)
    $script:HistAt = $script:Hist.Count
    Write-Out ''
    Write-Out ('> ' + $c)
    Write-Log ''
    Write-Log ('CWD> ' + $c)
    Set-Busy $true
    Set-Status ('Command: ' + $c)
    try {
        $r = Invoke-Cmd $c
        Write-OutBytes $r.Bytes
        Write-Log ('   exit code: ' + $r.Rc)
        Write-Out ''
        Write-Out ('[DONE] exit code: ' + $r.Rc)
    } finally {
        Set-Busy $false
    }
}


# ============================================================
# Window layout
# ============================================================
$form      = New-Object System.Windows.Forms.Form
$form.Text = [string]$script:Project.title
$form.Size = New-Object System.Drawing.Size(1160, 800)
# on a short screen start at the work area's height (like the original)
$seWork = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
if ($form.Height -gt $seWork.Height) { $form.Height = $seWork.Height }
$form.StartPosition = 'CenterScreen'
$form.BackColor     = [System.Drawing.Color]::Black

$script:OutBox = New-Object System.Windows.Forms.RichTextBox
$script:OutBox.Dock         = 'Fill'
$script:OutBox.ReadOnly     = $true
$script:OutBox.BackColor    = [System.Drawing.Color]::Black
$script:OutBox.ForeColor    = [System.Drawing.Color]::White
$script:OutBox.BorderStyle  = 'None'
$script:OutBox.Font         = $script:MonoFont
$script:OutBox.WordWrap     = $false
$script:OutBox.DetectUrls   = $false
$script:OutBox.HideSelection = $true

$script:StatusLabel      = New-Object System.Windows.Forms.Label
$script:StatusLabel.Dock       = 'Bottom'
$script:StatusLabel.Height     = 22
$script:StatusLabel.BackColor  = $script:Colors.Back
$script:StatusLabel.ForeColor  = $script:Colors.Dim
$script:StatusLabel.Font       = New-Object System.Drawing.Font('Segoe UI', 9)
$script:StatusLabel.Padding    = New-Object System.Windows.Forms.Padding(8, 4, 0, 0)
$script:StatusLabel.Text       = 'Ready'

$cmdPanel           = New-Object System.Windows.Forms.Panel
$cmdPanel.Dock      = 'Bottom'
$cmdPanel.Height    = 38
$cmdPanel.Size      = New-Object System.Drawing.Size(1064, 38)
$cmdPanel.BackColor = [System.Drawing.Color]::Black

# NB: pre-compute sizes - New-Object Type(expr - 1, n) misparses in PS 5.1
$seCmdW  = $cmdPanel.ClientSize.Width - 184
$seRunX  = $cmdPanel.ClientSize.Width - 168
$seStopX = $cmdPanel.ClientSize.Width - 84
$script:CmdBox      = New-Object System.Windows.Forms.TextBox
$script:CmdBox.Location   = New-Object System.Drawing.Point(8, 7)
$script:CmdBox.Size       = New-Object System.Drawing.Size($seCmdW, 24)
$script:CmdBox.Anchor     = 'Top,Left,Right'
$script:CmdBox.BackColor  = [System.Drawing.Color]::Black
$script:CmdBox.ForeColor  = [System.Drawing.Color]::White
$script:CmdBox.BorderStyle = 'FixedSingle'
$script:CmdBox.Font       = $script:MonoFont

$script:RunBtn      = New-Object System.Windows.Forms.Button
$script:RunBtn.Text   = 'Run'
$script:RunBtn.Size   = New-Object System.Drawing.Size(76, 25)
$script:RunBtn.Location = New-Object System.Drawing.Point($seRunX, 6)
$script:RunBtn.Anchor = 'Top,Right'
$script:RunBtn.FlatStyle = 'Flat'
$script:RunBtn.BackColor = $script:Colors.Head
$script:RunBtn.ForeColor = [System.Drawing.Color]::Black
$script:RunBtn.Add_Click({ try { Invoke-CmdLine } catch { Report-Error $_ 'Command' } })
$script:StopBtn      = New-Object System.Windows.Forms.Button
$script:StopBtn.Text   = 'Stop (Esc)'
$script:StopBtn.Size   = New-Object System.Drawing.Size(76, 25)
$script:StopBtn.Location = New-Object System.Drawing.Point($seStopX, 6)
$script:StopBtn.Anchor = 'Top,Right'
$script:StopBtn.FlatStyle = 'Flat'
$script:StopBtn.BackColor = $script:Colors.Err
$script:StopBtn.ForeColor = [System.Drawing.Color]::White
$script:StopBtn.Enabled = $false
$script:StopBtn.Add_Click({ $script:StopRequested = $true; Set-Status 'Stopping...' })
$script:CmdBox.Add_KeyDown({
    param($s, $e)
    if ($e.KeyCode -eq 'Enter') { $e.SuppressKeyPress = $true; try { Invoke-CmdLine } catch { Report-Error $_ 'Command' }; return }
    if ($e.KeyCode -eq 'Up') {
        $e.SuppressKeyPress = $true
        if ($script:Hist.Count -gt 0 -and $script:HistAt -gt 0) { $script:HistAt--; $s.Text = $script:Hist[$script:HistAt] }
        return
    }
    if ($e.KeyCode -eq 'Down') {
        $e.SuppressKeyPress = $true
        if ($script:HistAt -lt ($script:Hist.Count - 1)) { $script:HistAt++; $s.Text = $script:Hist[$script:HistAt] }
        else { $script:HistAt = $script:Hist.Count; $s.Text = '' }
        return
    }
})
[void]$cmdPanel.Controls.Add($script:CmdBox)
[void]$cmdPanel.Controls.Add($script:RunBtn)
[void]$cmdPanel.Controls.Add($script:StopBtn)

# Status cards (only when the project has status commands)
$script:StatusPanel = $null
if (@($script:Project.status).Count -gt 0) {
    $script:StatusPanel = New-Object System.Windows.Forms.FlowLayoutPanel
    $script:StatusPanel.Dock         = 'Top'
    $script:StatusPanel.Height       = 40
    $script:StatusPanel.Padding      = New-Object System.Windows.Forms.Padding(6)
    $script:StatusPanel.BackColor    = $script:Colors.Back
    $script:StatusPanel.WrapContents = $true
}

$leftPanel         = New-Object System.Windows.Forms.FlowLayoutPanel
$leftPanel.Dock         = 'Left'
$leftPanel.Width        = 332
$leftPanel.BackColor    = [System.Drawing.Color]::Black
$leftPanel.FlowDirection = 'TopDown'
$leftPanel.WrapContents = $false
$leftPanel.AutoScroll   = $true
$leftPanel.BorderStyle  = 'None'

$head      = New-Object System.Windows.Forms.Label
$head.Dock       = 'Top'
$head.Height     = 36
$head.Text       = [string]$script:Project.title
$head.TextAlign  = 'MiddleCenter'
$head.Font       = New-Object System.Drawing.Font('Segoe UI', 11, [System.Drawing.FontStyle]::Bold)
$head.ForeColor  = $script:Colors.Head
$head.BackColor  = [System.Drawing.Color]::Black

function New-TaskButton {
    param([string]$Text, [int]$Ti, [System.Drawing.Color]$Back, [System.Drawing.Color]$Fore)

    $b     = New-Object System.Windows.Forms.Button
    $b.Text    = $Text
    $b.Width   = 296
    $b.Height  = 44
    $b.Margin  = New-Object System.Windows.Forms.Padding(6, 5, 6, 1)
    $b.Tag     = $Ti
    $b.TextAlign = 'MiddleLeft'
    $b.Padding   = New-Object System.Windows.Forms.Padding(8, 0, 4, 0)
    $b.AutoEllipsis = $true
    $b.FlatStyle = 'Flat'
    $b.BackColor = $Back
    $b.ForeColor = $Fore
    $b.FlatAppearance.MouseOverBackColor = [System.Drawing.Color]::FromArgb(51, 51, 51)
    $b.FlatAppearance.MouseDownBackColor = [System.Drawing.Color]::FromArgb(77, 77, 77)
    $b.Font = New-Object System.Drawing.Font('Segoe UI', 10)
    return $b
}

# Orange "Restarts" tag on the buttons of tasks that restart the unit
$script:TagFont = New-Object System.Drawing.Font('Segoe UI', 8, [System.Drawing.FontStyle]::Bold)
$script:KeyButtons = @{}
$seGroup = ''
for ($i = 0; $i -lt $script:Project.tasks.Count; $i++) {
    $t = $script:Project.tasks[$i]
    $g = [string]$t.group
    if ($g -and $g -ne $seGroup) {
        $gh = New-Object System.Windows.Forms.Label
        $gh.Text      = $g
        $gh.AutoSize  = $false
        $gh.Size      = New-Object System.Drawing.Size(296, 24)
        $gh.Margin    = New-Object System.Windows.Forms.Padding(8, 10, 6, 0)
        $gh.TextAlign = 'BottomLeft'
        $gh.Font      = New-Object System.Drawing.Font('Segoe UI', 9.5, [System.Drawing.FontStyle]::Bold)
        $gh.ForeColor = $script:Colors.Head
        [void]$leftPanel.Controls.Add($gh)
    }
    if ($g) { $seGroup = $g }
    $seTxt = [string]$t.key + '.  ' + [string]$t.name
    $tb = New-TaskButton $seTxt $i $script:Colors.Back ([System.Drawing.Color]::White)
    $tb.Add_Click({ param($s, $e) Start-Task ([int]$s.Tag) })
    if ($t.restarts) {
        $tb.Padding = New-Object System.Windows.Forms.Padding(8, 0, 70, 0)
        $tb.Add_Paint({
            param($s, $e)
            $flags = [System.Windows.Forms.TextFormatFlags]'HorizontalCenter, VerticalCenter, NoPadding'
            $r = New-Object System.Drawing.Rectangle(($s.Width - 70), [int](($s.Height - 18) / 2), 62, 18)
            $br = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 159, 26))
            $e.Graphics.FillRectangle($br, $r)
            $br.Dispose()
            [System.Windows.Forms.TextRenderer]::DrawText($e.Graphics, 'Restarts', $script:TagFont, $r, [System.Drawing.Color]::Black, $flags)
        })
    }
    [void]$leftPanel.Controls.Add($tb)
    [void]$script:RunButtons.Add($tb)
    $script:KeyButtons[[string]$t.key] = $tb
}
if ($script:Project.runAll) {
    $ab = New-TaskButton 'A.  Run all tasks in order' (-1) $script:Colors.Head ([System.Drawing.Color]::Black)
    $ab.Add_Click({ Start-TaskAll })
    $ab.Margin = New-Object System.Windows.Forms.Padding(6, 14, 6, 1)
    [void]$leftPanel.Controls.Add($ab)
    [void]$script:RunButtons.Add($ab)
    $script:KeyButtons['A'] = $ab
}
$xb = New-TaskButton '0.  Exit' (-2) ([System.Drawing.Color]::FromArgb(60, 60, 60)) ([System.Drawing.Color]::White)
$xb.Add_Click({ $form.Close() })
[void]$leftPanel.Controls.Add($xb)
$script:KeyButtons['0'] = $xb

# Keyboard: the task keys press the matching button (not while typing in
# the command box); F5 reads the status cards again; Esc stops.
$form.KeyPreview = $true
$form.Add_KeyPress({
    param($s, $e)
    if ($script:CmdBox.Focused -or $script:Busy) { return }
    $k = ([string]$e.KeyChar).ToUpperInvariant()
    if ($script:KeyButtons.ContainsKey($k)) { $e.Handled = $true; $script:KeyButtons[$k].PerformClick() }
})
$form.Add_KeyDown({
    param($s, $e)
    if ($e.KeyCode -eq [System.Windows.Forms.Keys]::F5) { $e.Handled = $true; try { Refresh-Status } catch { Report-Error $_ 'Status' } }
    elseif ($e.KeyCode -eq [System.Windows.Forms.Keys]::Escape -and $script:Busy) {
        $e.Handled = $true; $script:StopRequested = $true; Set-Status 'Stopping...'
    }
})
$form.Add_FormClosing({ param($s, $e) if ($script:Busy) { $e.Cancel = $true } })

# Dock layout: controls added later take the outer edge first
[void]$form.Controls.Add($script:OutBox)
if ($script:StatusPanel) { [void]$form.Controls.Add($script:StatusPanel) }
[void]$form.Controls.Add($script:StatusLabel)
[void]$form.Controls.Add($cmdPanel)
[void]$form.Controls.Add($leftPanel)
[void]$form.Controls.Add($head)

# ---------- startup banner (mirrors the menu version)
Banner (' ' + [string]$script:Project.title)
Write-Out ('Generated by Dev-Toolbox Script Editor ' + '__VER__' + ' (window version).')
Write-Out ''
Write-Out 'Tasks:'
for ($i = 0; $i -lt $script:Project.tasks.Count; $i++) {
    $t = $script:Project.tasks[$i]
    Write-Out ('  [' + [string]$t.key + '] ' + [string]$t.name)
    if ([string]$t.desc) { Write-Out ('        ' + [string]$t.desc) }
}
Write-Out ''
Write-Out ('[INFO] Log file: ' + $script:LogFile)
Write-Out '[INFO] Mouse    : click a task button on the left'
Write-Out '       Keyboard : press the key shown on the button (1 - 9, letters, 0 = exit)'
Write-Out '       Status   : F5 reads the status cards again'
Write-Out '       Command  : type in the box at the bottom (Enter or Run); Stop or Esc ends it'
Set-Busy $false
$form.Add_Shown({ try { Refresh-Status } catch { Report-Error $_ 'Status' } })

# Last resort for anything a handler above does not catch
[System.Windows.Forms.Application]::add_ThreadException({ param($s, $e) Report-Error $e.Exception 'Window' })
[System.Windows.Forms.Application]::Run($form)
"""


def export_gui_filename(project):
    slug = re.sub(r'[^A-Za-z0-9_-]+', '-', project['name']).strip('-') or 'tool'
    return slug + '_gui.bat'


# raw 暫存 .cmd 重放的安全邊界: exit / goto :eof / 跳離暫存腳本的標籤
# （選單版全域標籤如 EXIT、動態標籤）會劫持或中斷 rc 契約, 只有選單版
# 跑得對 -> 匯出階段拒絕。跳進暫存檔內部（call x.bat）不受影響。
_RAW_LABEL_DEF_RE = re.compile(r'^\s*:\s*([A-Za-z_][A-Za-z0-9_]*)')
_RAW_JUMP_RE = re.compile(r'\b(goto|call)\s+(?:"([^"]*)"|(\S+))', re.I)
_RAW_EXIT_RE = re.compile(r'^\s*@?exit\b', re.I)
_RAW_TEXT_LINE_RE = re.compile(r'^\s*@?(?:echo|rem|set)\b', re.I)


def _check_raw_for_gui(raw_lines, bid):
    """逐行檢查 raw 是否能在 GUI 的暫存 .cmd 內忠實重放, 否則 ValueError。"""
    baked = [ln.replace('{LBL}', 'SE_LBL').replace('{TASK}', 'SE_TASK')
             for ln in raw_lines]
    defined = set()
    for ln in baked:
        m = _RAW_LABEL_DEF_RE.match(ln)
        if m:
            defined.add(m.group(1).lower())
    for ln in baked:
        if _RAW_EXIT_RE.match(ln):
            raise ValueError(
                'GUI 匯出不支援含 exit 的 raw 區塊: %s（選單版以 errorlevel '
                '結束整個腳本, 暫存 .cmd 會劫持 rc 契約, 請匯出選單版）' % bid)
        if _RAW_TEXT_LINE_RE.match(ln):
            continue                      # echo "goto x" 之类的文字行不是跳轉
        for jm in _RAW_JUMP_RE.finditer(ln):
            tgt = (jm.group(2) if jm.group(2) is not None else jm.group(3))
            tgt = tgt.strip()
            if tgt.startswith(':'):
                tgt = tgt[1:]
            tgt = tgt.lower()
            if '%' in tgt or '{' in tgt:
                raise ValueError(
                    'GUI 匯出不支援跳向動態標籤的 raw 區塊: %s（%s, '
                    '請匯出選單版）' % (bid, ln.strip()))
            if tgt == 'eof':
                raise ValueError(
                    'GUI 匯出不支援 goto :eof 的 raw 區塊: %s（會直接離開'
                    '暫存重放腳本, 請匯出選單版）' % bid)
            if re.search(r'[./\\]', tgt):
                continue                  # call x.bat 等檔案目標, 可重放
            if tgt == 'se_task_end' or tgt in defined:
                continue
            raise ValueError(
                'GUI 匯出不支援跳離暫存重放腳本的 raw 標籤: %s（%s, '
                '請匯出選單版）' % (bid, ln.strip()))


def export_gui_bat(project, blocks_by_id):
    """project -> (GUI 檔名, GUI .bat 內容字串)。呼叫端必須先過 validate_project。

    raw 區塊以暫存 .cmd 重放（附選單版同款 TOOLS/BUNDLE/SE_LOG 環境與
    SE_TASK_END 標籤, rc 語意與選單版任務引擎一致）。會被拒絕的專案
    （請匯出選單版 .bat）: 指令/raw/fixed 參數值含 %~（bat 自身路徑）、
    raw 含 {RUN}（自動續跑）或 exit/goto :eof/跳離暫存腳本的標籤、
    非 ASCII 指令/raw 行。GUI 版不提供 /run /runall。
    """
    gen = _GuiGen(project, blocks_by_id)
    return gen.filename, gen.render()


class _GuiGen(object):
    """把 project 展開成 GUI_TEMPLATE 的資料並產生單檔視窗版腳本。"""

    def __init__(self, project, blocks_by_id):
        self.p = project
        self.blocks = blocks_by_id
        self.filename = export_gui_filename(project)
        self.title = project.get('title') or project['name']
        for t in project.get('tasks', []):
            for s in t.get('steps', []):
                b = blocks_by_id.get(s.get('block'))
                if b is None:
                    raise ValueError('unknown block: %s' % s.get('block'))
                joined = '\n'.join(b['raw']) if b.get('raw') else ''
                if b.get('raw') and '{RUN}' in joined:
                    # {RUN} 跨重開續跑只有選單版跑得起來
                    raise ValueError(
                        'GUI 匯出不支援此 raw 區塊: %s（依賴選單版 bat 自動續跑, '
                        '請匯出選單版）' % b['id'])
                # %~（bat 自身路徑 family: %~f0/%~dp0/%~nx0...）: 選單版由
                # bat 位置展開, GUI 的暫存 cmd /c 展成空字元 -> 靜默錯路徑
                # 非 ASCII 同理: 暫存 .cmd 是純 ASCII, 只有選單版
                # （UTF-8 + chcp 65001）跑得對
                for txt in (b.get('cmd', ''), joined):
                    if '%~' in txt:
                        raise ValueError(
                            'GUI 匯出不支援含 %%~（bat 自身路徑）的指令/raw 區塊: '
                            '%s（請匯出選單版）' % b['id'])
                    if any(ord(c) > 127 for c in txt):
                        raise ValueError(
                            'GUI 匯出僅支援純 ASCII 指令/raw 行: %s'
                            '（請匯出選單版）' % b['id'])
                if b.get('raw'):
                    _check_raw_for_gui(b['raw'], b['id'])
                # fixed 參數值: 選單版 set SP_x=<value> 時 %~dp0 依 bat 位置
                # 展開, GUI 的 cmd /c 沒有批次脈絡 -> 靜默錯路徑, 一併拒絕
                # （prompt/default 走 safe_text 已無 %; validate 不参与展開）
                for pp in _BatGen._step_params(s, b):
                    if (pp['mode'] == 'fixed'
                            and '%~' in str(pp.get('value', ''))):
                        raise ValueError(
                            'GUI 匯出不支援含 %%~（bat 自身路徑）的 fixed '
                            '參數值: %s.%s（請匯出選單版）'
                            % (b['id'], pp['name']))

    @staticmethod
    def _step_data(step, block, lbl='', task=''):
        ok = step.get('expectRc') or block.get('expectRc', [0])
        loop = step.get('loop')
        return {
            'label': block.get('name', block['id']),
            'cmd': block.get('cmd', ''),
            'show': display_command(block),
            'raw': ([ln.replace('{LBL}', lbl).replace('{TASK}', task)
                     for ln in block['raw']] if block.get('raw') else None),
            'params': [{'name': p['name'], 'prompt': p['prompt'], 'mode': p['mode'],
                        'default': str(p.get('default', '')),
                        'validate': str(p.get('validate', '')),
                        'value': str(p.get('value', '')),
                        'upper': bool(p.get('uppercase')),
                        'minLen': int(p.get('minLength') or 0),
                        'maxLen': int(p.get('maxLength') or 0)}
                       for p in _BatGen._step_params(step, block)],
            'bundle': bool(block.get('needsBundle')),
            'ok': [int(c) for c in ok],
            'onFail': step.get('onFail', 'stop'),
            'confirm': bool(step.get('confirm')),
            'askAfter': str(step.get('askAfter') or ''),
            'loop': None if not loop else {
                'mode': loop['mode'],
                'times': int(loop.get('times', 0)),
                'minutes': int(loop.get('minutes', 0)),
                'delaySec': int(loop.get('delaySec', 0))},
        }

    def render(self):
        tasks = []
        for t, key in zip(self.p.get('tasks', []), task_keys(self.p)):
            # raw 的 {LBL}/{TASK} 對到暫存 .cmd 內的固定 label（Run-Step 補
            # :SE_TASK_END）; 每個 raw step 一個暫存檔, 不需逐任務編號
            steps = [self._step_data(s, self.blocks[s['block']], 'SE_LBL', 'SE_TASK')
                     for s in t.get('steps', [])]
            tasks.append({'name': t.get('name', ''), 'desc': t.get('desc', ''),
                          'key': key, 'group': t.get('group') or '',
                          'files': required_files(t, self.blocks),
                          'bundleOptional': bool(t.get('bundleOptional')),
                          'restarts': task_restarts(t, self.blocks),
                          'steps': steps})
        status = []
        for s in self.p.get('statusSteps') or []:
            b = self.blocks[s['block']]
            vals = {p['name']: str(p.get('value') or p.get('default', ''))
                    for p in _BatGen._step_params(s, b)}
            status.append(PLACEHOLDER_RE.sub(lambda m: vals.get(m.group(1), ''), b.get('cmd', '')))
        data = {'title': self.title, 'name': self.p['name'],
                'logFile': self.p.get('logFile') or '',
                'bundleRoot': self.p.get('bundleRoot') or 'auto',
                'bundleFolder': bundle_settings(self.p)[0],
                'bundleSub': bundle_settings(self.p)[1],
                'bundleCheck': bundle_settings(self.p)[2],
                'bundleHint': bundle_settings(self.p)[3],
                'runAll': bool(self.p.get('runAll', True)),
                'summary': bool(self.p.get('summary', True)),
                'status': status,
                'tasks': tasks}
        if self.p.get('requireAdmin', True):
            admin = ('net session >nul 2>&1\n'
                     '\n'
                     'if %errorlevel% neq 0 (\n'
                     '    echo Requesting Administrator privileges...\n'
                     '    set "SE_SELF=%~f0"\n'
                     '    powershell.exe -NoProfile -Command "Start-Process -FilePath $env:SE_SELF -Verb RunAs"\n'
                     '    exit /b\n'
                     ')')
        else:
            admin = ':: (this project does not require administrator privileges)'
        # 單次 regex 取代: 專案文字（title/任務名）若剛好含 "__DATA__" 等
        # 字樣, 逐個 replace 會被二次展開, 破壞 batch 段或嵌入了 JSON 的行
        tokens = {'__ADMIN__': admin,
                  '__VER__': CORE_VERSION,
                  '__TITLE__': self.title,
                  # 單行 JSON（無 indent/換行）: here-string 的 '@ 只在行首
                  # 才終止, 字串內的單引號因此安全 - 不可改成多行嵌入
                  '__DATA__': json.dumps(data, ensure_ascii=True)}
        text = re.sub(r'__ADMIN__|__VER__|__TITLE__|__DATA__',
                      lambda m: tokens[m.group(0)], GUI_TEMPLATE)
        text = text.replace('\n', CRLF)
        if any(ord(c) > 127 for c in text):
            raise ValueError('GUI 匯出內容含非 ASCII 字元, 拒絕匯出')
        return text
