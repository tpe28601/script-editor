"""Exporter tests: elevation, working folder, short display lines, and real runs (Windows)."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import se_core  # noqa: E402

BLOCKS, _ = se_core.load_block_files([('builtin', str(ROOT / 'blocks'))])
BY_ID = {b['id']: b for b in BLOCKS}
ANSI = re.compile(r'\x1b\[[0-9;]*m')


def fixed(name, value):
    return {name: {'mode': 'fixed', 'value': value}}


def safe_project(admin=False):
    """Only harmless steps: echo, dir, exit codes."""
    return {
        'name': 'SafeTest', 'title': 'Safe Test Tool', 'requireAdmin': admin, 'runAll': True,
        'bundleRoot': 'auto',
        'tasks': [
            {'name': 'Echo hello', 'steps': [{'block': 'gen.echo', 'params': fixed('TEXT', 'hello from test')}]},
            {'name': 'List folder', 'steps': [{'block': 'gen.command', 'params': fixed('CMD', 'dir /b')}]},
            {'name': 'Fail then continue', 'steps': [
                {'block': 'gen.command', 'params': fixed('CMD', 'cmd /c exit 3'), 'onFail': 'continue'},
                {'block': 'gen.echo', 'params': fixed('TEXT', 'still running')}]},
        ],
    }


def quicktool():
    return json.loads((ROOT / 'examples' / 'quicktool.json').read_text(encoding='utf-8'))


class ElevationTests(unittest.TestCase):
    def test_menu_elevation_passes_path_and_args_through_env(self):
        _, text = se_core.export_bat(quicktool(), BY_ID)
        self.assertIn('set "SE_SELF=%~f0"', text)
        self.assertIn('set "SE_ARGS=%*"', text)
        self.assertIn('Start-Process -FilePath $env:SE_SELF -Verb RunAs"', text)
        self.assertIn('-ArgumentList $env:SE_ARGS"', text)
        # The old form broke on paths with a single quote.
        self.assertNotIn("'%~f0'", text)

    def test_gui_elevation_line_is_valid(self):
        _, text = se_core.export_gui_bat(quicktool(), BY_ID)
        self.assertNotIn('RunAs\\"', text)          # regression: stray \" made PowerShell fail
        self.assertNotIn("'%~f0'", text)
        line = next(l for l in text.splitlines() if 'Verb RunAs' in l)
        self.assertEqual(line.strip(),
                         'powershell.exe -NoProfile -Command "Start-Process -FilePath $env:SE_SELF -Verb RunAs"')

    def test_no_admin_project_has_no_elevation(self):
        _, menu = se_core.export_bat(safe_project(), BY_ID)
        _, gui = se_core.export_gui_bat(safe_project(), BY_ID)
        self.assertNotIn('RunAs', menu)
        self.assertNotIn('Verb RunAs', gui)


class WorkingFolderTests(unittest.TestCase):
    def test_menu_switches_to_script_folder(self):
        _, text = se_core.export_bat(safe_project(admin=True), BY_ID)
        lines = text.splitlines()
        self.assertIn('pushd "%~dp0"', lines)
        self.assertLess(lines.index('pushd "%~dp0"'), lines.index(':MENU'))

    def test_gui_switches_to_script_folder(self):
        _, text = se_core.export_gui_bat(safe_project(), BY_ID)
        self.assertIn('Set-Location -LiteralPath $seDir', text)
        self.assertIn('$psi.WorkingDirectory = [Environment]::CurrentDirectory', text)


class DisplayTests(unittest.TestCase):
    def test_auto_display_strips_tools_prefix_and_quotes(self):
        block = {'id': 'x.tool', 'name': 'Tool', 'cmd': '"{TOOLS}\\Tool64W.exe" ${ARGS}', 'needsBundle': True,
                 'params': [{'name': 'ARGS'}]}
        self.assertEqual(se_core.display_command(block), 'Tool64W.exe ${ARGS}')

    def test_auto_display_collapses_powershell_one_liners(self):
        block = {'id': 'x.ps', 'name': 'PS', 'cmd': 'powershell.exe -NoProfile -Command "Get-Date | Out-Host"'}
        self.assertEqual(se_core.display_command(block), 'powershell.exe -Command [inline script]')

    def test_auto_display_truncates_and_avoids_unsafe_chars(self):
        long = {'id': 'x.long', 'name': 'Long', 'cmd': 'tool.exe ' + 'a' * 200}
        self.assertTrue(se_core.display_command(long).endswith('...'))
        self.assertLessEqual(len(se_core.display_command(long)), se_core.SHOW_MAX)
        piped = {'id': 'x.pipe', 'name': 'Pipe', 'cmd': 'dir | find "x"'}
        self.assertEqual(se_core.display_command(piped), 'dir ...')

    def test_explicit_display_is_validated(self):
        base = {'id': 'x.d', 'name': 'D', 'cmd': 'tool.exe ${A}', 'params': [{'name': 'A'}]}
        self.assertEqual(se_core.validate_block({**base, 'display': 'tool ${A}'}), [])
        self.assertTrue(se_core.validate_block({**base, 'display': 'bad & value'}))
        self.assertTrue(se_core.validate_block({**base, 'display': 'uses ${B}'}))

    def test_exports_print_short_line_and_log_full_command(self):
        p = {'name': 'Disp', 'title': 'Disp', 'requireAdmin': False, 'runAll': False,
             'tasks': [{'name': 'Status', 'steps': [{'block': 'sys.win_status'}]}]}
        _, menu = se_core.export_bat(p, BY_ID)
        self.assertIn('set SE_SHOW=' + BY_ID['sys.win_status']['display'], menu)
        self.assertIn('if defined SE_SHOW echo   %SE_SHOW%', menu)
        self.assertIn('>>"%SE_LOG%" echo %SE_STEP%  ::  %SE_CMD%', menu)   # log keeps the full command
        _, gui = se_core.export_gui_bat(p, BY_ID)
        self.assertIn('"show": "%s"' % BY_ID['sys.win_status']['display'], gui)

    def test_builtin_blocks_still_valid(self):
        for b in BLOCKS:
            clean = {k: v for k, v in b.items() if k != '_src'}
            self.assertEqual(se_core.validate_block(clean), [], b['id'])


TOOL_BLOCK = {'id': 't.tool', 'name': 'Bundle Tool', 'cmd': '"{TOOLS}\\FakeTool64W.exe" /x', 'needsBundle': True}
ASK_BLOCK = {'id': 't.ask', 'name': 'Ask Tag', 'cmd': 'echo TAG=${TAG}',
             'params': [{'name': 'TAG', 'prompt': 'Tag', 'mode': 'ask', 'uppercase': True,
                         'minLength': 3, 'maxLength': 5}]}


def feature_project(root):
    blocks = dict(BY_ID, **{'t.tool': TOOL_BLOCK, 't.ask': ASK_BLOCK})
    p = {
        'name': 'FeatTest', 'title': 'Feature Test', 'requireAdmin': False, 'runAll': False,
        'bundleRoot': str(Path(root) / 'nobundle'),
        'statusSteps': [{'block': 'gen.echo', 'params': fixed('TEXT', '  Status line : READY')}],
        'tasks': [
            {'name': 'Echo one', 'group': 'First group', 'steps': [{'block': 'gen.echo', 'params': fixed('TEXT', 'one')}]},
            {'name': 'Ask then stop', 'group': 'First group', 'steps': [
                {'block': 'gen.echo', 'params': fixed('TEXT', 'before ask'), 'askAfter': 'Continue to next step?'},
                {'block': 'gen.echo', 'params': fixed('TEXT', 'SHOULD NOT RUN')}]},
            {'name': 'Keyed task', 'group': 'Second group', 'key': 'm',
             'steps': [{'block': 'gen.echo', 'params': fixed('TEXT', 'keyed m')}]},
            {'name': 'Missing files', 'requireFiles': ['C:\\se_nope\\missing1.txt', 'C:\\se_nope\\missing2.txt'],
             'steps': [{'block': 'gen.echo', 'params': fixed('TEXT', 'SHOULD NOT RUN')}]},
            {'name': 'Optional bundle', 'bundleOptional': True, 'checkTools': True, 'steps': [
                {'block': 'gen.echo', 'params': fixed('TEXT', 'plain step runs')},
                {'block': 't.tool'},
                {'block': 'gen.command', 'params': fixed('CMD', 'cmd /c exit 4'), 'onFail': 'continue'}]},
            {'name': 'Ask param', 'steps': [{'block': 't.ask'}]},
            {'name': 'Restart task', 'steps': [{'block': 'sys.restart', 'confirm': True}]},
        ],
    }
    return p, blocks


class FeatureExportTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='se feat '))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.p, self.blocks = feature_project(self.root)

    def test_feature_project_validates(self):
        self.assertEqual(se_core.validate_project(self.p, set(self.blocks), self.blocks), [])

    def test_task_keys_skip_custom_letters(self):
        self.assertEqual(se_core.task_keys(self.p), ['1', '2', 'M', '3', '4', '5', '6'])
        _, menu = se_core.export_bat(self.p, self.blocks)
        self.assertIn('echo     [M] Keyed task', menu)
        self.assertIn('choice /C 123456M0 /N', menu)
        self.assertIn('if /i "%SE_SEL%"=="M" goto GO_T3', menu)

    def test_key_validation(self):
        bad = json.loads(json.dumps(self.p))
        bad['tasks'][0]['key'] = 'M'                 # duplicate of task 3
        self.assertTrue(any('重複' in e for e in se_core.validate_project(bad, set(self.blocks), self.blocks)))
        bad['tasks'][0]['key'] = '7'                 # digits are automatic
        self.assertTrue(any('B~Z' in e for e in se_core.validate_project(bad, set(self.blocks), self.blocks)))
        bad['tasks'][0]['key'] = 'A'                 # A = Run all
        self.assertTrue(any('B~Z' in e for e in se_core.validate_project(bad, set(self.blocks), self.blocks)))

    def test_group_headings_and_status(self):
        _, menu = se_core.export_bat(self.p, self.blocks)
        lines = menu.splitlines()
        self.assertEqual(lines.count('echo   First group'), 1)
        self.assertLess(lines.index('echo   First group'), lines.index('echo     [1] Echo one'))
        self.assertLess(lines.index('echo   Second group'), lines.index('echo     [M] Keyed task'))
        self.assertIn('if not defined SE_ONESHOT call :SE_STATUS', menu)
        self.assertIn(':SE_STATUS', menu)

    def test_status_steps_must_be_non_interactive(self):
        bad = json.loads(json.dumps(self.p))
        bad['statusSteps'] = [{'block': 't.ask'}]
        self.assertTrue(any('狀態列' in e for e in se_core.validate_project(bad, set(self.blocks), self.blocks)))
        bad['statusSteps'] = [{'block': 't.tool'}]
        self.assertTrue(any('工具包' in e for e in se_core.validate_project(bad, set(self.blocks), self.blocks)))

    def test_param_limit_validation(self):
        bad = dict(ASK_BLOCK, params=[dict(ASK_BLOCK['params'][0], minLength=6, maxLength=3)])
        self.assertTrue(se_core.validate_block(bad))
        p = json.loads(json.dumps(self.p))
        p['tasks'][5]['steps'][0]['params'] = {'TAG': {'mode': 'fixed', 'value': 'toolong'}}
        self.assertTrue(any('長度' in e for e in se_core.validate_project(p, set(self.blocks), self.blocks)))

    def test_checktools_collects_tool_files(self):
        files = se_core.required_files(self.p['tasks'][4], self.blocks)
        self.assertEqual(files, ['{TOOLS}\\FakeTool64W.exe'])

    def test_gui_carries_features(self):
        _, gui = se_core.export_gui_bat(self.p, self.blocks)
        data = json.loads(re.search(r"\$script:Project = @'\r\n(.*)\r\n'@", gui).group(1))
        self.assertEqual([t['key'] for t in data['tasks']], ['1', '2', 'M', '3', '4', '5', '6'])
        self.assertEqual(data['tasks'][2]['group'], 'Second group')
        self.assertTrue(data['tasks'][4]['bundleOptional'])
        self.assertEqual(data['tasks'][3]['files'], ['C:\\se_nope\\missing1.txt', 'C:\\se_nope\\missing2.txt'])
        self.assertTrue(data['tasks'][6]['restarts'])              # sys.restart is marked restarts
        self.assertFalse(data['tasks'][0]['restarts'])
        self.assertEqual(data['tasks'][1]['steps'][0]['askAfter'], 'Continue to next step?')
        param = data['tasks'][5]['steps'][0]['params'][0]
        self.assertEqual((param['upper'], param['minLen'], param['maxLen']), (True, 3, 5))
        self.assertEqual(data['status'], ['echo   Status line : READY'])
        for needle in ('Update-StatusPanel', 'Refresh-Status', "'Stop (Esc)'", 'StopRequested', "'Restarts'",
                       'KeyButtons', 'Step results'):
            self.assertIn(needle, gui)

    def test_summary_can_be_disabled(self):
        _, menu = se_core.export_bat(self.p, self.blocks)
        self.assertIn('echo Step results', menu)
        p = dict(self.p, summary=False)
        _, menu = se_core.export_bat(p, self.blocks)
        self.assertNotIn('echo Step results', menu)


@unittest.skipUnless(os.name == 'nt', 'Runs exported .bat files')
class FeatureRunTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='se feat run '))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        p, blocks = feature_project(self.root)
        name, text = se_core.export_bat(p, blocks)
        self.bat = self.root / name
        self.bat.write_bytes(text.encode('ascii'))
        # chcp 65001 makes set /p ignore redirected input: a copy without it lets the
        # tests type answers from a file (interactive consoles are not affected)
        self.nochcp = self.root / 'nochcp.bat'
        self.nochcp.write_bytes(text.replace('chcp 65001 >nul\r\n', '').encode('ascii'))

    def run_bat(self, bat, *args, stdin=b''):
        inp = self.root / 'stdin.txt'
        inp.write_bytes(stdin)
        with open(inp, 'rb') as f:
            proc = subprocess.run(['cmd.exe', '/d', '/c', str(bat), *args], stdin=f, capture_output=True,
                                  timeout=60, cwd=str(self.root))
        return proc.returncode, ANSI.sub('', proc.stdout.decode('utf-8', 'replace')).replace('\r', '')

    def test_custom_key_runs_task(self):
        rc, out = self.run_bat(self.bat, '/run', 'M')
        self.assertEqual(rc, 0, out)
        self.assertIn('keyed m', out)

    def test_missing_files_listed_and_task_not_started(self):
        rc, out = self.run_bat(self.bat, '/run', '3')
        self.assertEqual(rc, 1, out)
        self.assertIn('C:\\se_nope\\missing1.txt', out)
        self.assertIn('C:\\se_nope\\missing2.txt', out)
        self.assertNotIn('SHOULD NOT RUN', out.split('Missing files', 1)[1])

    def test_optional_bundle_skips_bundle_steps_and_prints_summary(self):
        rc, out = self.run_bat(self.bat, '/run', '4')
        self.assertEqual(rc, 0, out)
        self.assertIn('[INFO] Tool bundle not found', out)
        self.assertIn('plain step runs', out)
        self.assertRegex(out, r'\[2/3\] Bundle Tool\s+SKIPPED \(no bundle\)')
        self.assertRegex(out, r'\[3/3\] Run Any Command\s+FAILED \(continued\)')

    def test_ask_after_no_skips_rest(self):
        rc, out = self.run_bat(self.bat, '/run', '2')      # no console: the Y/n prompt answers No
        self.assertEqual(rc, 0, out)
        self.assertIn('Remaining steps skipped by operator', out)
        self.assertNotIn('SHOULD NOT RUN', out.split('Ask then stop', 1)[1])
        self.assertRegex(out, r'\[2/2\] Print Message\s+not run')

    def test_uppercase_and_length(self):
        rc, out = self.run_bat(self.nochcp, '/run', '5', stdin=b'ab\r\nabcdefg\r\nabcd\r\n')
        self.assertEqual(rc, 0, out)
        self.assertEqual(out.count('The value must be 3 to 5 characters.'), 2)
        self.assertIn('TAG=ABCD', out)

    def test_menu_shows_status_groups_and_keys(self):
        rc, out = self.run_bat(self.nochcp, stdin=b'0')
        self.assertIn('Status line : READY', out)
        menu = out.split('Status line : READY', 1)[1]
        for line in ('  First group', '    [1] Echo one', '  Second group', '    [M] Keyed task', '    [0] Exit'):
            self.assertIn(line, menu)


class QuickToolTemplateTests(unittest.TestCase):
    """examples/quicktool.json: the script_tools-style template (fake tool names only)."""

    def setUp(self):
        self.p = json.loads((ROOT / 'examples' / 'quicktool.json').read_text(encoding='utf-8'))

    def test_validates_and_exports_both_versions(self):
        self.assertEqual(se_core.validate_project(self.p, set(BY_ID), BY_ID), [])
        se_core.export_bat(self.p, BY_ID)
        se_core.export_gui_bat(self.p, BY_ID)

    def test_menu_layout_matches_original(self):
        self.assertEqual(se_core.task_keys(self.p), ['1', '2', '3', '4', '5', '6', 'M', '7', '8', '9'])
        groups = [t.get('group') for t in self.p['tasks']]
        self.assertEqual(sorted(set(groups), key=groups.index), ['Windows settings', 'Service settings', 'System'])
        restarts = [k for k, t in zip(se_core.task_keys(self.p), self.p['tasks']) if se_core.task_restarts(t, BY_ID)]
        self.assertEqual(restarts, ['4', '7', '8'])
        self.assertTrue(self.p['statusSteps'])

    def test_uses_only_sample_tools(self):
        # The template only calls placeholder programs under C:\SampleTools and no tool-bundle block.
        exes = [s['params']['EXE']['value'] for t in self.p['tasks'] for s in t['steps']
                if 'EXE' in (s.get('params') or {})]
        self.assertTrue(exes)
        self.assertEqual([e for e in exes if not e.startswith('C:\\SampleTools\\Sample')], [])
        self.assertFalse(any(s['block'] in BY_ID and BY_ID[s['block']].get('needsBundle')
                             for t in self.p['tasks'] for s in t['steps']))

    def test_builtin_blocks_need_no_tool_bundle(self):
        # Tool-bundle blocks are site specific: they live in the user's data folder or a block pack.
        self.assertEqual([b['id'] for b in BLOCKS if b.get('needsBundle')], [])


class BatchSafetyTests(unittest.TestCase):
    def test_batch_target_uses_call_other_commands_do_not(self):
        p = {'name': 'CallTest', 'title': 'Call Test', 'requireAdmin': False, 'tasks': [
            {'name': 'Bat tool', 'steps': [{'block': 'gen.tool', 'params': {
                'EXE': {'mode': 'fixed', 'value': 'C:\\x\\tool.cmd'}, 'ARGS': {'mode': 'fixed', 'value': 'a'}}}]},
            {'name': 'Exe tool', 'steps': [{'block': 'gen.tool', 'params': {
                'EXE': {'mode': 'fixed', 'value': 'C:\\x\\tool.exe'}}}]},
            {'name': 'Status', 'steps': [{'block': 'sys.win_status'}]}]}
        _, menu = se_core.export_bat(p, BY_ID)
        calls = re.findall(r'^set "SE_CALL=(.*)"\r?$', menu, re.M)
        self.assertEqual(calls, ['call', '', ''])
        self.assertIn('%SE_CALL% %SE_CMD% > "%SE_OUT%" 2>&1', menu)

    def test_paren_safe(self):
        self.assertEqual(se_core.paren_safe('Read Modes (x64)'), 'Read Modes ^(x64^)')


@unittest.skipUnless(os.name == 'nt', 'Runs exported .bat files')
class BatchSafetyRunTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='se safety (x) '))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def run_project(self, p, blocks, *args):
        name, text = se_core.export_bat(p, blocks)
        bat = self.root / name
        bat.write_bytes(text.encode('ascii'))
        proc = subprocess.run(['cmd.exe', '/d', '/c', str(bat), *args], stdin=subprocess.DEVNULL,
                              capture_output=True, timeout=60, cwd=str(self.root))
        return proc.returncode, ANSI.sub('', (proc.stdout + proc.stderr).decode('utf-8', 'replace'))

    def test_batch_tool_returns_to_the_engine(self):
        tool = self.root / 'fake tool.cmd'
        tool.write_text('@echo off\r\necho fake tool got %*\r\nexit /b 0\r\n', encoding='ascii')
        p = {'name': 'CallRun', 'title': 'Call Run', 'requireAdmin': False, 'tasks': [
            {'name': 'Two steps', 'steps': [
                {'block': 'gen.tool', 'params': {'EXE': {'mode': 'fixed', 'value': str(tool)},
                                                 'ARGS': {'mode': 'fixed', 'value': 'one two'}}},
                {'block': 'gen.echo', 'params': fixed('TEXT', 'second step ran')}]}]}
        rc, out = self.run_project(p, BY_ID, '/run', '1')
        self.assertEqual(rc, 0, out)
        self.assertIn('fake tool got one two', out)
        self.assertIn('second step ran', out)

    def test_names_with_parentheses_inside_blocks(self):
        blocks = dict(BY_ID, **{
            'p.tool': {'id': 'p.tool', 'name': 'Read Modes (x64)', 'cmd': '"{TOOLS}\\Fake64W.exe" x',
                       'needsBundle': True}})
        p = {'name': 'ParenTest', 'title': 'Paren Test', 'requireAdmin': False,
             'bundleRoot': str(self.root / 'no bundle (x86)'),
             'tasks': [{'name': 'Parens', 'bundleOptional': True, 'steps': [
                 {'block': 'gen.echo', 'params': fixed('TEXT', 'first'), 'confirm': True},
                 {'block': 'p.tool'},
                 {'block': 'sys.sata_mode', 'confirm': True}]}]}
        rc, out = self.run_project(p, blocks, '/run', '1')
        self.assertEqual(rc, 0, out)
        self.assertNotIn('was unexpected at this time', out)
        self.assertIn('[2/3] Read Modes (x64) skipped - tool bundle not found', out)
        self.assertRegex(out, r'\[3/3\] Show SATA Operation \(Windows view\)\s+SKIPPED')


class GuiDialogTests(unittest.TestCase):
    """MessageBox calls must match a real overload; handlers must not crash the window."""

    SHOW_RE = re.compile(r'\[System\.Windows\.Forms\.MessageBox\]::Show\((.*?)\)\s*(?:\n|$|\})', re.S)

    def gui_text(self):
        return se_core.export_gui_bat(quicktool(), BY_ID)[1]

    def show_calls(self):
        calls = []
        for m in self.SHOW_RE.finditer(self.gui_text()):
            enums = re.findall(r'\[System\.Windows\.Forms\.(MessageBox\w+)\]::\w+', m.group(1))
            calls.append(enums)
        return calls

    def test_enum_order_is_buttons_icon_default(self):
        calls = self.show_calls()
        self.assertGreaterEqual(len(calls), 2)
        order = ['MessageBoxButtons', 'MessageBoxIcon', 'MessageBoxDefaultButton']
        for enums in calls:
            self.assertEqual(enums, order[:len(enums)])

    @unittest.skipUnless(os.name == 'nt', 'Needs Windows Forms')
    def test_overloads_exist(self):
        checks = []
        for enums in self.show_calls():
            types = ', '.join(['[string]', '[string]'] + ['[System.Windows.Forms.%s]' % e for e in enums])
            checks.append('if($null -eq [System.Windows.Forms.MessageBox].GetMethod(\'Show\', [type[]]@(%s)))'
                          '{Write-Output \'MISSING %s\'}' % (types, ' '.join(enums)))
        script = 'Add-Type -AssemblyName System.Windows.Forms;' + ';'.join(checks)
        proc = subprocess.run(['powershell.exe', '-NoProfile', '-Command', script], capture_output=True, timeout=60)
        out = (proc.stdout + proc.stderr).decode('utf-8', 'replace')
        self.assertEqual(proc.returncode, 0, out)
        self.assertNotIn('MISSING', out)

    def test_handlers_report_errors(self):
        text = self.gui_text()
        self.assertIn('function Report-Error', text)
        self.assertIn("Report-Error $_ ('Task \"'", text)
        self.assertIn("Report-Error $_ 'Run all'", text)
        self.assertIn('add_ThreadException', text)
        for line in text.splitlines():
            if 'Invoke-CmdLine' in line and 'function' not in line:
                self.assertIn('Report-Error', line)


@unittest.skipUnless(os.name == 'nt', 'Runs exported .bat files')
class BundleSearchTests(unittest.TestCase):
    """Auto search: Desktop, then the script folder; one nested folder level is accepted."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='se bundle '))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        # echo instead of running a program: only the search is under test
        self.blocks = dict(BY_ID, **{'t.tool': {'id': 't.tool', 'name': 'Show Tools Folder',
                                                'cmd': 'echo tools at {TOOLS}', 'needsBundle': True}})
        self.p = {'name': 'BundleTest', 'title': 'Bundle Test', 'requireAdmin': False,
                  'bundleRoot': 'auto', 'bundleFolder': 'SeTestBundle_*', 'bundleSub': 'win64',
                  'bundleCheck': 'FakeTool64W.exe',
                  'tasks': [{'name': 'Tool', 'steps': [{'block': 't.tool'}]}]}

    def fake_tool(self, folder):
        folder.mkdir(parents=True)
        (folder / 'FakeTool64W.exe').write_bytes(b'')       # only the check file has to exist

    def run_menu(self):
        name, text = se_core.export_bat(self.p, self.blocks)
        bat = self.root / name
        bat.write_bytes(text.encode('ascii'))
        proc = subprocess.run(['cmd.exe', '/d', '/c', str(bat), '/run', '1'], input=b'\r\n' * 5,
                              capture_output=True, timeout=60, cwd=os.environ.get('SystemRoot', 'C:\\Windows'))
        return ANSI.sub('', (proc.stdout + proc.stderr).decode('utf-8', 'replace')).replace('\r', '')

    def test_nested_folder_beside_script_is_found(self):
        tools = self.root / 'SeTestBundle_01_January_2020' / 'SeTestBundle_01_January_2020' / 'win64'
        self.fake_tool(tools)
        out = self.run_menu()
        self.assertIn('Looking in: ' + str(self.root / 'SeTestBundle_*'), out)
        self.assertIn('Tools : ' + str(tools), out)

    def test_newest_dated_folder_wins(self):
        self.fake_tool(self.root / 'SeTestBundle_01_January_2020' / 'win64')
        self.fake_tool(self.root / 'SeTestBundle_03_March_2021' / 'win64')
        out = self.run_menu()
        self.assertIn('Bundle: SeTestBundle_03_March_2021', out)

    def test_not_found_prints_searched_paths_and_hint(self):
        (self.root / 'SeTestBundle_01_January_2020' / 'win64').mkdir(parents=True)   # no check file
        out = self.run_menu()
        self.assertIn('Skipped   : ' + str(self.root / 'SeTestBundle_01_January_2020' / 'win64')
                      + ' - FakeTool64W.exe not found', out)
        self.assertIn('[ERROR] Valid tool bundle SeTestBundle_* was not found.', out)
        self.assertIn('SeTestBundle_DD_Month_YYYY\\win64\\FakeTool64W.exe', out)

    def test_gui_carries_bundle_settings(self):
        _, text = se_core.export_gui_bat(self.p, self.blocks)
        data = json.loads(re.search(r"\$script:Project = @'\r?\n(.*?)\r?\n'@", text, re.S).group(1))
        self.assertEqual((data['bundleFolder'], data['bundleSub'], data['bundleCheck']),
                         ('SeTestBundle_*', 'win64', 'FakeTool64W.exe'))

    def test_defaults_are_neutral(self):
        p = dict(self.p)
        for k in ('bundleFolder', 'bundleSub', 'bundleCheck'):
            p.pop(k)
        self.assertEqual(se_core.bundle_settings(p)[:3], ('ToolBundle_*', 'win64', ''))
        self.assertEqual(se_core.validate_project(dict(p, bundleFolder='bad"name'), set(self.blocks), self.blocks)[:1],
                         ['bundleFolder 只能用英數字與 . _ - * ?'])


@unittest.skipUnless(os.name == 'nt', 'Runs exported .bat files')
class RunExportTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="se test it's "))     # space + single quote on purpose
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        name, text = se_core.export_bat(safe_project(), BY_ID)
        self.bat = self.dir / name
        self.bat.write_bytes(text.encode('ascii'))

    def run_bat(self, *args):
        proc = subprocess.run(['cmd.exe', '/d', '/c', str(self.bat), *args], input=b'\r\n' * 10,
                              capture_output=True, timeout=60, cwd=os.environ.get('SystemRoot', 'C:\\Windows'))
        return proc.returncode, ANSI.sub('', proc.stdout.decode('utf-8', 'replace'))

    def test_run_task_from_another_folder_uses_script_folder(self):
        rc, out = self.run_bat('/run', '2')
        self.assertEqual(rc, 0, out)
        self.assertIn(self.bat.name, out)                # dir /b listed the script's own folder
        self.assertIn('[SUCCESS] Task completed: List folder', out)

    def test_runall_with_continue_on_failure(self):
        rc, out = self.run_bat('/runall')
        self.assertIn('hello from test', out)
        self.assertIn('[FAILED] [1/2] Run Any Command  exit code: 3', out)
        self.assertIn('still running', out)
        self.assertTrue((self.dir / 'SafeTest.log').is_file())

    def test_gui_export_parses_as_powershell(self):
        _, text = se_core.export_gui_bat(quicktool(), BY_ID)
        gui = self.dir / 'gui.ps1'
        gui.write_text(text, encoding='ascii')
        script =("$e=$null;$t=$null;[void][System.Management.Automation.Language.Parser]::ParseFile("
                  "'" + str(gui).replace("'", "''") + "',[ref]$t,[ref]$e);exit $e.Count")
        proc = subprocess.run(['powershell.exe', '-NoProfile', '-Command', script], capture_output=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == '__main__':
    unittest.main()
