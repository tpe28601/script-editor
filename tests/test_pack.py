"""Block pack (.sepack.json) tests: export, preview, conflicts, rename, backups, bad files."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import se_core  # noqa: E402
import se_pack  # noqa: E402

BLOCKS, _ = se_core.load_block_files([('builtin', str(ROOT / 'blocks'))])
BUILTIN = {b['id']: b for b in BLOCKS}


def custom_block(bid, name='Sample Tool', cmd='echo sample'):
    return {'id': bid, 'name': name, 'category': 'Samples', 'desc': 'sample', 'cmd': cmd}


def project(name, block_ids):
    return {'name': name, 'title': 'Sample Project', 'requireAdmin': False, 'runAll': True,
            'bundleRoot': 'auto',
            'tasks': [{'name': 'Task %d' % i, 'steps': [{'block': b}]} for i, b in enumerate(block_ids, 1)]}


def write(fp, obj):
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, 'w', encoding='utf-8') as f:
        json.dump(obj, f)


class PackTestBase(unittest.TestCase):
    def setUp(self):
        self.src = tempfile.mkdtemp(prefix='sepack-src-')
        self.dst = tempfile.mkdtemp(prefix='sepack-dst-')
        self.addCleanup(shutil.rmtree, self.src, True)
        self.addCleanup(shutil.rmtree, self.dst, True)
        for bid in ('sample.alpha', 'sample.beta'):
            write(se_pack.block_path(self.src, bid), custom_block(bid))
        write(se_pack.project_path(self.src, 'SampleProj'),
              project('SampleProj', ['sample.alpha', 'sample.beta', 'gen.echo']))

    def make_pack(self, blocks=('sample.alpha', 'sample.beta'), projects=('SampleProj',)):
        pack, warnings = se_pack.export_pack(self.src, list(blocks), list(projects), set(BUILTIN),
                                             name='Sample Pack', description='for tests', tool_version='9.9')
        return pack, warnings

    def text(self, pack):
        return se_pack.dumps(pack)


class ExportTests(PackTestBase):
    def test_format_fields(self):
        pack, warnings = self.make_pack()
        for k in ('format', 'version', 'name', 'description', 'createdAt', 'tool', 'blocks', 'projects'):
            self.assertIn(k, pack)
        self.assertEqual(pack['format'], 'script-editor-pack')
        self.assertEqual(pack['version'], 1)
        self.assertEqual(pack['tool'], {'id': 'script-editor', 'version': '9.9'})
        self.assertEqual(sorted(b['id'] for b in pack['blocks']), ['sample.alpha', 'sample.beta'])
        self.assertEqual([p['name'] for p in pack['projects']], ['SampleProj'])
        self.assertEqual(warnings, [])

    def test_no_machine_info(self):
        text = self.text(self.make_pack()[0])
        for leak in (self.src, os.environ.get('USERNAME', '\0'), os.environ.get('COMPUTERNAME', '\0')):
            if leak and len(leak) > 2:
                self.assertNotIn(leak, text)

    def test_warns_when_project_block_left_out(self):
        _, warnings = self.make_pack(blocks=('sample.alpha',))
        self.assertEqual(len(warnings), 1)
        self.assertIn('sample.beta', warnings[0])

    def test_missing_item_and_empty_selection(self):
        with self.assertRaises(se_pack.PackError):
            self.make_pack(blocks=('sample.nope',))
        with self.assertRaises(se_pack.PackError):
            self.make_pack(blocks=(), projects=())

    def test_inventory_lists_project_blocks(self):
        inv = se_pack.inventory(self.src)
        self.assertEqual([b['id'] for b in inv['blocks']], ['sample.alpha', 'sample.beta'])
        self.assertEqual(inv['projects'][0]['blocks'], ['gen.echo', 'sample.alpha', 'sample.beta'])


class ImportTests(PackTestBase):
    def test_round_trip_into_empty_folder(self):
        pack = se_pack.parse(self.text(self.make_pack()[0]))
        res = se_pack.apply(self.dst, pack, BUILTIN)
        self.assertEqual(res['unresolved'], [])
        self.assertIsNone(res['backups'])
        self.assertEqual(len(res['written']), 3)
        self.assertEqual(res['warnings'], [])
        for bid in ('sample.alpha', 'sample.beta'):
            self.assertTrue(os.path.isfile(se_pack.block_path(self.dst, bid)))
        with open(se_pack.project_path(self.dst, 'SampleProj'), encoding='utf-8') as f:
            self.assertEqual(json.load(f)['tasks'][0]['steps'][0]['block'], 'sample.alpha')

    def test_dry_run_writes_nothing(self):
        pack = se_pack.parse(self.text(self.make_pack()[0]))
        res = se_pack.apply(self.dst, pack, BUILTIN, dry_run=True)
        self.assertTrue(res['dryRun'])
        self.assertEqual(res['written'], [])
        self.assertEqual([it['action'] for it in res['items']], ['create'] * 3)
        self.assertFalse(os.path.exists(os.path.join(self.dst, 'blocks')))

    def test_conflicts_need_a_decision(self):
        pack = se_pack.parse(self.text(self.make_pack()[0]))
        se_pack.apply(self.dst, pack, BUILTIN)
        prev = se_pack.apply(self.dst, pack, BUILTIN, dry_run=True)
        self.assertEqual(sorted(prev['unresolved']),
                         ['block:sample.alpha', 'block:sample.beta', 'project:SampleProj'])
        with self.assertRaises(se_pack.PackError):
            se_pack.apply(self.dst, pack, BUILTIN)

    def test_overwrite_backs_up_old_files(self):
        pack = se_pack.parse(self.text(self.make_pack()[0]))
        se_pack.apply(self.dst, pack, BUILTIN)
        write(se_pack.block_path(self.dst, 'sample.alpha'), custom_block('sample.alpha', name='Local Edit'))
        pack['blocks'][0]['name'] = 'From Pack'
        res = se_pack.apply(self.dst, pack, BUILTIN, default='overwrite')
        self.assertTrue(res['backups'])
        self.assertIn('pack-import-', os.path.basename(res['backups']))
        self.assertEqual(os.path.dirname(res['backups']), os.path.join(self.dst, 'backups'))
        with open(os.path.join(res['backups'], 'blocks', 'sample.alpha.json'), encoding='utf-8') as f:
            self.assertEqual(json.load(f)['name'], 'Local Edit')
        self.assertTrue(os.path.isfile(os.path.join(res['backups'], 'projects', 'SampleProj.json')))
        with open(se_pack.block_path(self.dst, 'sample.alpha'), encoding='utf-8') as f:
            self.assertEqual(json.load(f)['name'], 'From Pack')

    def test_rename_rewrites_project_references(self):
        pack = se_pack.parse(self.text(self.make_pack()[0]))
        se_pack.apply(self.dst, pack, BUILTIN)
        res = se_pack.apply(self.dst, pack, BUILTIN, decisions={
            'block:sample.alpha': 'rename', 'block:sample.beta': 'skip', 'project:SampleProj': 'rename'})
        self.assertIsNone(res['backups'])
        self.assertEqual(res['renamed'], {'sample.alpha': 'sample.alpha-2'})
        self.assertTrue(os.path.isfile(se_pack.block_path(self.dst, 'sample.alpha-2')))
        with open(se_pack.project_path(self.dst, 'SampleProj-2'), encoding='utf-8') as f:
            p = json.load(f)
        self.assertEqual(p['name'], 'SampleProj-2')
        self.assertEqual([t['steps'][0]['block'] for t in p['tasks']],
                         ['sample.alpha-2', 'sample.beta', 'gen.echo'])

    def test_builtin_id_cannot_be_overwritten(self):
        pack, _ = self.make_pack(projects=())
        pack['blocks'].append(dict(BUILTIN['gen.echo']))
        pack = se_pack.parse(self.text(pack))
        with self.assertRaises(se_pack.PackError):
            se_pack.apply(self.dst, pack, BUILTIN, decisions={'block:gen.echo': 'overwrite'}, dry_run=True)
        res = se_pack.apply(self.dst, pack, BUILTIN, decisions={'block:gen.echo': 'rename'})
        self.assertIn({'kind': 'block', 'name': 'gen.echo-2'}, res['written'])
        self.assertFalse(os.path.exists(se_pack.block_path(self.dst, 'gen.echo')))

    def test_missing_block_reported_as_warning(self):
        pack, _ = self.make_pack(blocks=('sample.alpha',))
        res = se_pack.apply(self.dst, se_pack.parse(self.text(pack)), BUILTIN, dry_run=True)
        self.assertEqual(len(res['warnings']), 1)
        self.assertIn('SampleProj', res['warnings'][0])


class BadFileTests(unittest.TestCase):
    def bad(self, text):
        with self.assertRaises(se_pack.PackError):
            se_pack.parse(text)

    def test_rejects_bad_files(self):
        self.bad('not json')
        self.bad('[]')
        self.bad(json.dumps({'format': 'other', 'version': 1}))
        self.bad(json.dumps({'format': 'script-editor-pack', 'version': 99, 'blocks': [], 'projects': []}))
        self.bad(json.dumps({'format': 'script-editor-pack', 'version': 'x'}))
        self.bad(json.dumps({'format': 'script-editor-pack', 'version': 1, 'blocks': {}}))
        self.bad(json.dumps({'format': 'script-editor-pack', 'version': 1,
                             'blocks': [{'id': 'BAD ID', 'name': 'x', 'cmd': 'echo'}]}))
        dup = custom_block('sample.dup')
        self.bad(json.dumps({'format': 'script-editor-pack', 'version': 1, 'blocks': [dup, dup]}))
        self.bad(json.dumps({'format': 'script-editor-pack', 'version': 1, 'projects': [{'name': '...'}]}))

    def test_accepts_utf8_bom(self):
        text = json.dumps({'format': 'script-editor-pack', 'version': 1,
                           'blocks': [custom_block('sample.bom')], 'projects': []})
        pack = se_pack.parse(b'\xef\xbb\xbf' + text.encode('utf-8'))
        self.assertEqual(pack['blocks'][0]['id'], 'sample.bom')


if __name__ == '__main__':
    unittest.main()
