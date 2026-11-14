# -*- coding: utf-8 -*-
"""Script Editor web server (Dev-Toolbox web mode, TOOL-SPEC 5).

Pure stdlib (ThreadingHTTPServer), binds 127.0.0.1 only. 編輯器 UI 在 ui/,
區塊/專案資料與匯出結果放 dataDir, 內建區塊庫隨工具發佈（blocks/）。

Usage:
    python se_web.py [--port 8785] [--open-browser] [--parent-pid <pid>]
                     [--data-dir <path>] [--hub-origin http://127.0.0.1:<p>]

Prints "READY http://127.0.0.1:<port>" on stdout once listening.

API (POST needs header X-Toolbox-Request: 1, JSON body):
    GET  /api/health            ok/id/version/pid/port/uptimeSec
    GET  /api/blocks            區塊庫 {presets, custom, errors}
    POST /api/blocks/save       {block} 新增/更新自訂區塊（不可覆蓋內建 id）
    POST /api/blocks/delete     {id}
    GET  /api/projects          已存專案清單
    GET  /api/project?name=     讀一個專案
    POST /api/project/save      {project} 驗證後存到 dataDir
    POST /api/project/delete    {name}
    POST /api/validate          {project} -> {errors}
    POST /api/export            {project, format?} -> {filename, text}（前端下載;
                                format='gui' = 單檔 WinForms 視窗版 <名>_gui.bat,
                                預設 = 選單版 .bat）
    GET  /api/samples           內建範例清單（examples/*.json, 随工具發佈）
    GET  /api/sample?id=        讀一個內建範例（預設 id=sample）
    POST /api/shutdown          graceful stop (Hub 的乾淨關機路徑)
"""

import argparse
import ctypes
import json
import os
import re
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import se_core                                        # noqa: E402
import se_pack                                        # noqa: E402
from standalone_update import handle_update           # noqa: E402

VERSION = '0.2.1'
TOOL_ID = 'script-editor'
DEFAULT_PORT = 8785
MAX_PORT_SCAN = 20
MAX_BODY = 1 * 1024 * 1024
HERE = os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.path.join(HERE, 'ui')
UI_FILES = {'/': 'index.html', '/index.html': 'index.html', '/app.js': 'app.js',
            '/app.css': 'app.css', '/ui-kit.css': 'ui-kit.css',
            '/tb-theme.js': 'tb-theme.js', '/tb-icons.js': 'tb-icons.js',
            '/tb-standalone-update.js': 'tb-standalone-update.js'}
CSP_PAGE = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; ")
STARTED_AT = time.time()

STATE_LOCK = threading.Lock()                          # guards projects/blocks files
DATA_DIR = None
HTTPD = None


# ---------------------------------------------------------------- data helpers
def blocks_dirs():
    """(內建, 自訂) 區塊目錄; 自訂在 dataDir, 隨執行建立。"""
    custom = os.path.join(DATA_DIR, 'blocks')
    os.makedirs(custom, exist_ok=True)
    return [('builtin', os.path.join(HERE, 'blocks')), ('custom', custom)]


def load_all_blocks():
    pairs, errors = se_core.load_block_files(blocks_dirs())
    presets = [b for b in pairs if b['_src'] == 'builtin']
    custom = [b for b in pairs if b['_src'] == 'custom']
    presets.sort(key=lambda b: (b.get('category', ''), b.get('name', ''), b['id']))
    custom.sort(key=lambda b: b.get('name', b['id']))
    return presets, custom, errors


def blocks_by_id():
    presets, custom, _ = load_all_blocks()
    return {b['id']: b for b in presets + custom}


def project_file(name):
    """專案名 -> 檔路徑（slug 化, 不允許穿越資料夾）。"""
    slug = re.sub(r'[^A-Za-z0-9_-]+', '-', name).strip('-')
    if not slug:
        raise ValueError('專案名稱不合法')
    return os.path.join(DATA_DIR, 'projects', slug + '.json')


def read_project(fp):
    with open(fp, encoding='utf-8') as f:
        return json.load(f)


# ---------------------------------------------------------------- http handler
class Handler(BaseHTTPRequestHandler):
    server_version = 'ScriptEditor/' + VERSION
    protocol_version = 'HTTP/1.1'
    port = DEFAULT_PORT
    hub_origins = ('http://127.0.0.1:8700', 'http://localhost:8700')

    def log_message(self, fmt, *args):      # keep stdout clean (READY contract)
        pass

    # ---------------- helpers
    def _base_headers(self, status=200, ctype='application/json; charset=utf-8'):
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cache-Control', 'no-store')
        return self

    def _frame_ancestors(self, extra_csp=''):
        ancestors = 'frame-ancestors ' + ' '.join(["'self'"] + list(self.hub_origins))
        self.send_header('Content-Security-Policy', (extra_csp + ancestors) if extra_csp else ancestors)

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self._base_headers(status)
        self._frame_ancestors()
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _send_err(self, code, message, status=400):
        # 錯誤回應後一律關連線: 未讀完的 body 殘留在 keep-alive 連線會錯序
        self.close_connection = True
        self._send_json({'ok': False, 'error': {'code': code, 'message': message,
                                                'detail': None}}, status)

    def _send_bytes(self, data, ctype, csp=None):
        self._base_headers(200, ctype)
        self._frame_ancestors(csp or CSP_PAGE)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    def _host_ok(self):
        host = (self.headers.get('Host') or '').lower()
        return host in ('127.0.0.1:%d' % self.port, 'localhost:%d' % self.port)

    def _origin_ok(self):
        origin = (self.headers.get('Origin') or '').lower()
        if not origin:
            return True                      # same-origin form/non-browser call
        allowed = tuple('http://%s:%d' % (h, self.port) for h in ('127.0.0.1', 'localhost')) \
            + tuple(self.hub_origins)
        return origin in allowed

    # ---------------- GET
    def do_GET(self):
        if not self._host_ok():
            return self._send_err('BAD_HOST', 'Host 標頭不合法', 400)
        url = urlparse(self.path)
        path = url.path
        if path == '/api/health':
            return self._send_json({'ok': True, 'id': TOOL_ID, 'version': VERSION,
                                    'pid': os.getpid(), 'port': self.port,
                                    'uptimeSec': int(time.time() - STARTED_AT)})
        if path == '/api/blocks':
            with STATE_LOCK:
                presets, custom, errors = load_all_blocks()
            return self._send_json({'ok': True, 'result': {
                'presets': presets, 'custom': custom, 'errors': errors}})
        if path == '/api/projects':
            return self._api_projects()
        if path == '/api/project':
            return self._api_project_get(parse_qs(url.query))
        if path == '/api/pack/inventory':
            with STATE_LOCK:
                inv = se_pack.inventory(DATA_DIR)
            return self._send_json({'ok': True, 'result': inv})
        if path == '/api/samples':
            return self._api_samples()
        if path == '/api/sample':
            return self._api_sample(parse_qs(url.query))
        return self._static(path)

    do_HEAD = do_GET

    # ---------------- POST
    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BODY:
            return self._send_err('BAD_LENGTH', '內容長度不合法', 400)
        raw = self.rfile.read(length)
        if not self._host_ok():
            return self._send_err('BAD_HOST', 'Host 標頭不合法', 400)
        if self.headers.get('X-Toolbox-Request') != '1':
            return self._send_err('MISSING_TOOLBOX_HEADER', '缺少 X-Toolbox-Request 標頭', 403)
        if not self._origin_ok():
            return self._send_err('FORBIDDEN_ORIGIN', 'Origin 不接受', 403)
        ctype = (self.headers.get('Content-Type') or '').lower()
        if 'application/json' not in ctype:
            return self._send_err('BAD_CONTENT_TYPE', 'Content-Type 必須是 application/json', 400)
        try:
            body = json.loads(raw.decode('utf-8'))
        except Exception:
            return self._send_err('BAD_JSON', 'JSON 解析失敗', 400)
        body = body if isinstance(body, dict) else {}
        path = urlparse(self.path).path
        if path == '/api/standalone-update':
            if self.server.hub_managed:
                return self._send_err('HUB_MANAGED', '請從 Hub 更新此工具。', 403)
            return self._send_json(handle_update(Path(HERE), body))
        if path == '/api/blocks/save':
            return self._api_block_save(body)
        if path == '/api/blocks/delete':
            return self._api_block_delete(body)
        if path == '/api/project/save':
            return self._api_project_save(body)
        if path == '/api/project/delete':
            return self._api_project_delete(body)
        if path == '/api/validate':
            return self._api_validate(body)
        if path == '/api/export':
            return self._api_export(body)
        if path == '/api/pack/export':
            return self._api_pack_export(body)
        if path in ('/api/pack/preview', '/api/pack/import'):
            return self._api_pack_import(body, preview=path.endswith('/preview'))
        if path == '/api/shutdown':
            self._send_json({'ok': True, 'result': {'stopping': True}})
            threading.Thread(target=_graceful_stop, daemon=True).start()
            return
        return self._send_err('NOT_FOUND', '找不到端點: %s' % path, 404)

    # ---------------- blocks
    def _api_block_save(self, body):
        block = body.get('block')
        errs = se_core.validate_block(block)
        if errs:
            return self._send_err('BAD_BLOCK', '；'.join(errs))
        with STATE_LOCK:
            presets, _, _ = load_all_blocks()
            if any(b['id'] == block['id'] for b in presets):
                return self._send_err('ID_TAKEN', 'id %s 是內建區塊, 請改用其他 id'
                                      % block['id'])
            d = os.path.join(DATA_DIR, 'blocks')
            os.makedirs(d, exist_ok=True)
            fp = os.path.join(d, re.sub(r'[^A-Za-z0-9._-]', '_', block['id']) + '.json')
            _write_json(fp, block)
        return self._send_json({'ok': True, 'result': {'id': block['id']}})

    def _api_block_delete(self, body):
        bid = str(body.get('id') or '')
        fp = os.path.join(DATA_DIR, 'blocks', re.sub(r'[^A-Za-z0-9._-]', '_', bid) + '.json')
        with STATE_LOCK:
            if not os.path.isfile(fp):
                return self._send_err('NOT_FOUND', '自訂區塊不存在: %s' % bid, 404)
            os.remove(fp)
        return self._send_json({'ok': True, 'result': {'id': bid}})

    # ---------------- 區塊包（.sepack.json）
    def _api_pack_export(self, body):
        blocks, projects = body.get('blocks') or [], body.get('projects') or []
        if not all(isinstance(x, str) for x in list(blocks) + list(projects)):
            return self._send_err('BAD_ARGS', 'blocks／projects 必須是名稱清單')
        with STATE_LOCK:
            presets, _, _ = load_all_blocks()
            try:
                pack, warnings = se_pack.export_pack(
                    DATA_DIR, blocks, projects, {b['id'] for b in presets},
                    name=str(body.get('name') or ''), description=str(body.get('description') or ''),
                    tool_version=VERSION)
            except (se_pack.PackError, OSError, ValueError) as exc:
                return self._send_err('PACK_EXPORT_FAILED', str(exc))
        stem = re.sub(r'[^A-Za-z0-9._-]+', '_', pack['name']).strip('._') or 'script-editor-pack'
        return self._send_json({'ok': True, 'result': {
            'content': se_pack.dumps(pack), 'filename': stem + se_pack.SUFFIX,
            'counts': {'blocks': len(pack['blocks']), 'projects': len(pack['projects'])},
            'warnings': warnings}})

    def _api_pack_import(self, body, preview):
        content = body.get('content')
        decisions = body.get('decisions') or {}
        if not isinstance(content, str) or not content:
            return self._send_err('BAD_ARGS', '請選擇區塊包檔案')
        if not isinstance(decisions, dict):
            return self._send_err('BAD_ARGS', 'decisions 必須是物件')
        if not preview and body.get('confirm') is not True:
            return self._send_err('CONFIRM_REQUIRED', '匯入需要 confirm: true')
        try:
            pack = se_pack.parse(content)
            with STATE_LOCK:
                presets, _, _ = load_all_blocks()
                result = se_pack.apply(DATA_DIR, pack, {b['id']: b for b in presets},
                                       decisions, dry_run=preview)
        except se_pack.PackError as exc:
            return self._send_err('PACK_INVALID', str(exc))
        except OSError as exc:
            return self._send_err('PACK_IMPORT_FAILED', '寫入失敗: %s' % exc, 500)
        result['pack'] = {k: pack.get(k) for k in ('name', 'description', 'createdAt', 'tool', 'version')}
        return self._send_json({'ok': True, 'result': result})

    # ---------------- projects
    def _api_projects(self):
        d = os.path.join(DATA_DIR, 'projects')
        out = []
        with STATE_LOCK:
            if os.path.isdir(d):
                for fn in sorted(os.listdir(d)):
                    if not fn.endswith('.json'):
                        continue
                    try:
                        p = read_project(os.path.join(d, fn))
                    except (OSError, ValueError):
                        continue
                    out.append({'name': p.get('name', fn), 'title': p.get('title', ''),
                                'tasks': len(p.get('tasks', [])),
                                'updated': _fmt_mtime(os.path.join(d, fn))})
        return self._send_json({'ok': True, 'result': out})

    # ---------------- 內建範例（examples/*.json, 唯讀）
    def _api_samples(self):
        d = os.path.join(HERE, 'examples')
        out = []
        if os.path.isdir(d):
            for fn in sorted(os.listdir(d)):
                if not fn.endswith('.json'):
                    continue
                try:
                    p = read_project(os.path.join(d, fn))
                except (OSError, ValueError):
                    continue
                out.append({'id': fn[:-5], 'name': p.get('name', fn),
                            'title': p.get('title', ''), 'tasks': len(p.get('tasks', []))})
        return self._send_json({'ok': True, 'result': out})

    def _api_sample(self, qs):
        sid = (qs.get('id') or ['sample'])[0]
        if not re.match(r'^[a-z0-9_-]{1,40}$', sid):
            return self._send_err('BAD_ID', '範例 id 不合法', 400)
        fp = os.path.join(HERE, 'examples', sid + '.json')
        try:
            return self._send_json({'ok': True, 'result': read_project(fp)})
        except FileNotFoundError:
            return self._send_err('NOT_FOUND', '找不到範例: %s' % sid, 404)
        except (OSError, ValueError) as exc:
            return self._send_err('BAD_FILE', '範例載入失敗: %s' % exc, 500)

    def _api_project_get(self, qs):
        name = (qs.get('name') or [''])[0]
        try:
            fp = project_file(name)
            with STATE_LOCK:
                p = read_project(fp)
        except FileNotFoundError:
            return self._send_err('NOT_FOUND', '找不到專案: %s' % name, 404)
        except (OSError, ValueError) as exc:
            return self._send_err('BAD_FILE', '專案載入失敗: %s' % exc, 500)
        return self._send_json({'ok': True, 'result': p})

    def _api_project_save(self, body):
        p = body.get('project')
        with STATE_LOCK:
            errs = se_core.validate_project(p, blocks_by_id())
            if errs:
                return self._send_err('INVALID_PROJECT', '；'.join(errs))
            try:
                fp = project_file(p['name'])
            except ValueError as exc:
                return self._send_err('BAD_NAME', str(exc))
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            _write_json(fp, p)
        return self._send_json({'ok': True, 'result': {'name': p['name'],
                                                       'file': os.path.basename(fp)}})

    def _api_project_delete(self, body):
        try:
            fp = project_file(str(body.get('name') or ''))
        except ValueError as exc:
            return self._send_err('BAD_NAME', str(exc))
        with STATE_LOCK:
            if not os.path.isfile(fp):
                return self._send_err('NOT_FOUND', '找不到專案', 404)
            os.remove(fp)
        return self._send_json({'ok': True, 'result': {}})

    # ---------------- validate/export
    def _api_validate(self, body):
        with STATE_LOCK:
            errs = se_core.validate_project(body.get('project'), blocks_by_id())
        return self._send_json({'ok': True, 'result': {'errors': errs}})

    def _api_export(self, body):
        p = body.get('project')
        gui = str(body.get('format') or '').lower() == 'gui'
        with STATE_LOCK:
            known = blocks_by_id()
            errs = se_core.validate_project(p, known)
            if errs:
                return self._send_err('INVALID_PROJECT', '；'.join(errs))
            try:
                if gui:
                    fname, text = se_core.export_gui_bat(p, known)
                else:
                    fname, text = se_core.export_bat(p, known)
            except Exception as exc:                # 護欄: 匯出器異常不帶 stack 給前端
                return self._send_err('EXPORT_FAILED', '匯出失敗: %s' % exc, 500)
        return self._send_json({'ok': True, 'result': {'filename': fname, 'text': text,
                                                       'bytes': len(text.encode('ascii',
                                                                                 'replace'))}})

    # ---------------- static
    def _static(self, path):
        fn = UI_FILES.get(path)
        if fn is None:
            return self._send_err('NOT_FOUND', '找不到 %s' % path, 404)
        fp = os.path.join(UI_DIR, fn)
        if not os.path.isfile(fp):
            return self._send_err('MISSING_UI', 'ui/ 靜態檔缺失：%s' % fn, 500)
        ctype = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
                 '.css': 'text/css; charset=utf-8'}.get(os.path.splitext(fn)[1], 'text/plain')
        csp = CSP_PAGE if fn.endswith('.html') else None
        with open(fp, 'rb') as f:
            self._send_bytes(f.read(), ctype, csp=csp)


def _write_json(fp, obj):
    tmp = fp + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write('\n')
    os.replace(tmp, fp)


def _fmt_mtime(fp):
    try:
        return time.strftime('%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(fp)))
    except OSError:
        return ''


def _graceful_stop():
    if HTTPD is not None:
        HTTPD.shutdown()


# ---------------------------------------------------------------- lifecycle
def _parent_alive(pid):
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, pid)     # QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def watch_parent(pid):
    while True:
        time.sleep(1.0)
        if not _parent_alive(pid):
            print('Parent process exited, closing.', flush=True)
            os._exit(0)


def resolve_data_dir(override):
    # 優先序：--data-dir > DEVTOOLBOX_DATA\<id> > %LOCALAPPDATA%\DevToolbox\script-editor
    if override:
        d = override
    elif os.environ.get('DEVTOOLBOX_DATA'):
        d = os.path.join(os.environ['DEVTOOLBOX_DATA'], TOOL_ID)
    else:
        d = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
                         'DevToolbox', TOOL_ID)
    os.makedirs(d, exist_ok=True)
    return d


def main(argv=None):
    global DATA_DIR, HTTPD
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(description='Script Editor local web server (127.0.0.1 only)')
    ap.add_argument('--port', type=int, default=DEFAULT_PORT)
    ap.add_argument('--open-browser', action='store_true')
    ap.add_argument('--parent-pid', type=int, default=0)
    ap.add_argument('--data-dir', default=None)
    ap.add_argument('--hub-origin', default=None)
    args = ap.parse_args(argv)
    port_explicit = any(a == '--port' or a.startswith('--port=') for a in argv)

    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

    DATA_DIR = resolve_data_dir(args.data_dir)
    if args.parent_pid > 0 and not _parent_alive(args.parent_pid):
        print('parent process %d not running, exiting' % args.parent_pid, file=sys.stderr)
        return 1

    candidates = [args.port] if port_explicit else \
        list(range(args.port, args.port + MAX_PORT_SCAN + 1)) + [0]
    httpd = None
    last_error = None
    for port in candidates:
        try:
            httpd = ThreadingHTTPServer(('127.0.0.1', port), Handler)
            break
        except OSError as exc:
            last_error = exc
    if httpd is None:
        print('無法監聽 127.0.0.1:%s（%s）' % (args.port, last_error), file=sys.stderr)
        return 5

    Handler.port = httpd.server_address[1]
    httpd.hub_managed = args.parent_pid > 0
    HTTPD = httpd
    if args.hub_origin:
        m = re.match(r'^http://(127\.0\.0\.1|localhost):(\d{1,5})$', args.hub_origin.strip().lower())
        if m:
            Handler.hub_origins = ('http://127.0.0.1:%s' % m.group(2),
                                   'http://localhost:%s' % m.group(2))
    httpd.daemon_threads = True

    url = 'http://127.0.0.1:%d' % Handler.port
    print('READY %s' % url, flush=True)
    if args.open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url + '/')).start()
    if args.parent_pid > 0:
        threading.Thread(target=watch_parent, args=(args.parent_pid,), daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == '__main__':
    sys.exit(main())
