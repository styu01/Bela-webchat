"""Offline P5.9.8.2 installer regression tests. Never touches live WSL."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE=Path(__file__).resolve().parent
ORIGINAL=HERE/'test-fixtures'/'p5981'
BASE_CSS=ORIGINAL/'style.css'
SCRIPT=HERE/'install_p5982.py'

class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='bela-p5982-test-')
        self.addCleanup(self.temp.cleanup)
        self.tgt=Path(self.temp.name)/'webchat-p2'
        self.tgt.mkdir()
        for name in ('app.js','index.html'):
            shutil.copy2(ORIGINAL/name,self.tgt/name)
        shutil.copy2(BASE_CSS,self.tgt/'style.css')
        self.old_backup=self.tgt/'p5981-theme-backup-20261120-120000Z'
        self.old_backup.mkdir()
        (self.tgt/'.bela-theme-p5981.json').write_text(json.dumps({
            'version':'P5.9.8.1','backup':str(self.old_backup),
            'patched':{'app.js':'24ccc50aca7f89ed729e7c24add302c60719304e07947bc5b6735eafe45d944e',
                       'index.html':'93e28915996c5fa14010b8a254f5777cc3536d7d5e2eea2dcf239a20e455ecfc'}
        }),encoding='utf-8')
        self.start={name:(self.tgt/name).read_bytes() for name in ['app.js','index.html','style.css']}
    def run_cmd(self,flag,expect=0):
        env={**os.environ,'BELA_THEME_TARGET':str(self.tgt)}
        p=subprocess.run([sys.executable,'-B',str(SCRIPT),flag],env=env,text=True,capture_output=True,timeout=12)
        if expect==0:self.assertEqual(p.returncode,0,p.stdout+'\n'+p.stderr)
        else:self.assertNotEqual(p.returncode,0,p.stdout+'\n'+p.stderr)
        return p
    def test_install_verify_rollback_exact(self):
        self.assertIn('"state": "ready"',self.run_cmd('--check').stdout)
        self.assertIn('P5.9.8.2 telepítve',self.run_cmd('--apply').stdout)
        self.assertIn('PASS',self.run_cmd('--verify').stdout)
        self.assertIn('"state": "installed"',self.run_cmd('--check').stdout)
        self.assertIn('ALREADY INSTALLED',self.run_cmd('--apply').stdout)
        backup=Path(json.loads((self.tgt/'.bela-theme-p5982.json').read_text())['backup'])
        self.assertTrue(backup.exists())
        self.assertEqual({n:(backup/n).read_bytes() for n in self.start},self.start)
        self.run_cmd('--rollback')
        self.assertEqual({n:(self.tgt/n).read_bytes() for n in self.start},self.start)
        self.assertTrue((self.tgt/'.bela-theme-p5981.json').exists())
        self.assertFalse((self.tgt/'.bela-theme-p5982.json').exists())
        self.assertTrue(backup.exists())
    def test_unexpected_style_drift_refused_without_changes(self):
        (self.tgt/'style.css').write_bytes(b'unexpected CSS!')
        prev={n:(self.tgt/n).read_bytes() for n in self.start}
        self.run_cmd('--apply',expect=1)
        self.assertEqual({n:(self.tgt/n).read_bytes() for n in self.start},prev)
        self.assertFalse((self.tgt/'.bela-theme-p5982.json').exists())
    def test_unexpected_frontend_drift_refused_without_changes(self):
        (self.tgt/'app.js').write_bytes(b'unexpected code!')
        prev={n:(self.tgt/n).read_bytes() for n in self.start}
        self.run_cmd('--apply',expect=1)
        self.assertEqual({n:(self.tgt/n).read_bytes() for n in self.start},prev)
    def test_missing_p5981_marker_refused(self):
        (self.tgt/'.bela-theme-p5981.json').unlink()
        self.run_cmd('--apply',expect=1)
    def test_bad_p5981_marker_refused(self):
        (self.tgt/'.bela-theme-p5981.json').write_text('{}')
        self.run_cmd('--apply',expect=1)
    def test_symlink_target_refused(self):
        target=self.tgt/'style.css';target.unlink();target.symlink_to(BASE_CSS)
        self.run_cmd('--apply',expect=1)
    def test_drift_after_install_blocks_rollback(self):
        self.run_cmd('--apply')
        (self.tgt/'app.js').write_bytes(b'drift-after-install')
        self.run_cmd('--rollback',expect=1)
        self.assertEqual((self.tgt/'app.js').read_bytes(),b'drift-after-install')
        self.assertTrue((self.tgt/'.bela-theme-p5982.json').exists())
    def test_repeat_install_is_idempotent(self):
        self.run_cmd('--apply')
        before={n:(self.tgt/n).read_bytes() for n in self.start}
        self.run_cmd('--apply')
        self.assertEqual(before,{n:(self.tgt/n).read_bytes() for n in self.start})
        self.assertEqual(len(list(self.tgt.glob('p5982-theme-backup-*'))),1)

if __name__=='__main__':unittest.main(verbosity=2)
