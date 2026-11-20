#!/usr/bin/env python3
"""P5.9.8 unified release installation/adoption/rollback mock tests; no live systemd."""
import json
import shutil
import sqlite3
import unittest
import uuid
from contextlib import closing
from pathlib import Path

import test_p597_install as fixtures
import install_p597 as core
import install_p598 as u
import webchat_isolation as iso


class UnifiedReleaseTests(unittest.TestCase):
    def setUp(self):
        self.fx=fixtures.InstallerTests(methodName='test_safe_install_paused_and_preserves_telegram')
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)

    def check_files_and_services(self):
        self.assertEqual(core.sha(core.APP/'app.js'),core.sha(core.HERE/'app.js'))
        self.assertEqual(core.telegram()['MainPID'],'453')
        for k in (core.WEB,core.WORK,core.MIRROR,core.BROKER):
            self.assertEqual(self.fx.units[k],'active')

    def test_fresh_p5961_install_ui_and_reversible_rollback(self):
        self.assertEqual(u.detect()['origin'],'fresh-p5961')
        u.apply()
        marker=u.stamp_read()
        self.assertEqual(marker['origin'],'fresh-p5961')
        self.check_files_and_services()
        self.assertFalse(iso.enabled(core.DB))
        self.assertEqual(u.verify()['ui_sha256'],core.sha(core.HERE/'app.js'))
        u.rollback()
        self.assertFalse(u.stamp_path().exists())
        self.assertEqual(core.sha(core.APP/'app.js'),core.BASE['app.js'])
        self.assertFalse(core.RELAY.exists())
        self.assertFalse(core.BROKER_UNIT.exists())
        self.assertEqual(self.fx.units[core.WORK],'active')
        self.assertEqual(self.fx.units[core.BROKER],'inactive')
        print('PASS fresh P5961 → merged UI+broker → original byte-for-byte rollback')

    def test_adopt_live_p5972_and_ui_without_any_service_restart(self):
        core.apply() # simulate existing P5972+UI pilot
        core.enable() # production had processing enabled
        self.assertTrue(iso.enabled(core.DB))
        before_calls=len(self.fx.calls)
        before_bytes={rel:(core.APP/rel).read_bytes() for rel in u.BASE_FILES}
        self.assertEqual(u.detect()['origin'],'adopted-p5972-hotfix')
        u.apply()
        self.assertEqual(u.stamp_read()['origin'],'adopted-p5972-hotfix')
        self.assertFalse([c for c in self.fx.calls[before_calls:] if c[:2]==('systemctl','--user') and len(c)>2 and c[2] in ('start','stop','restart','enable','disable')], 'adoption must not restart ANY service')
        self.assertTrue(iso.enabled(core.DB),'adoption must preserve enabled state')
        self.check_files_and_services()
        for rel in u.BASE_FILES:self.assertEqual((core.APP/rel).read_bytes(),before_bytes[rel])
        backup=Path(u.stamp_read()['backup_folder'])
        self.assertTrue((backup/'webchat-snapshot.sqlite3').exists())
        self.assertTrue((backup/'app.js').exists())
        self.assertEqual(u.verify()['origin'],'adopted-p5972-hotfix')
        u.rollback()
        self.assertFalse(u.stamp_path().exists())
        self.assertFalse([c for c in self.fx.calls[before_calls:] if c[:2]==('systemctl','--user') and len(c)>2 and c[2] in ('start','stop','restart','enable','disable')])
        self.assertTrue(iso.enabled(core.DB))
        self.check_files_and_services()
        print('PASS current active P5972+UI adopted and reversed with ZERO service changes')

    def test_adopt_rejects_inflight_and_uncertain_without_modification(self):
        core.apply()
        before=len(self.fx.calls)
        with closing(sqlite3.connect(core.DB)) as c:
            c.execute("INSERT INTO webchat_main_links(request_id,seq,remote_id,phase,created_at,updated_at) VALUES(?,?,?,'submitted',1,1)",
                      (str(uuid.uuid4()),91,901))
            c.commit()
        with self.assertRaisesRegex(u.Stop,'Outstanding'):u.apply()
        self.assertFalse(u.stamp_path().exists())
        self.assertFalse([c for c in self.fx.calls[before:] if c[:2]==('systemctl','--user') and len(c)>2 and c[2] in ('start','stop','restart','enable','disable')])
        print('PASS in-flight Marveen request refuses release adoption')

    def test_adopt_rejects_preexisting_ui_drift(self):
        core.apply()
        before=len(self.fx.calls)
        (core.APP/'app.js').write_text('tampered')
        with self.assertRaises(core.Stop):u.detect()
        self.assertFalse(u.stamp_path().exists())
        self.assertFalse([c for c in self.fx.calls[before:] if c[:2]==('systemctl','--user') and len(c)>2 and c[2] in ('start','stop','restart','enable','disable')])
        print('PASS drift fails closed before services or release marker')

    def test_drift_after_adoption_blocks_unsafe_rollback(self):
        core.apply();u.apply()
        original=(core.APP/'app.js').read_bytes()
        (core.APP/'app.js').write_text('unexpected overwrite')
        with self.assertRaises(core.Stop):u.verify()
        with self.assertRaises(core.Stop):u.rollback()
        self.assertTrue(u.stamp_path().exists())
        (core.APP/'app.js').write_bytes(original)
        u.rollback()
        self.assertFalse(u.stamp_path().exists())
        print('PASS modified files are never silently overwritten on rollback')

    def test_fresh_install_failure_rolls_back_app_js(self):
        from unittest.mock import patch
        baseline=(core.APP/'app.js').read_bytes()
        with patch.object(core,'wait_http',side_effect=core.Stop('fake HTTP failure')):
            with self.assertRaisesRegex(core.Stop,'fake HTTP failure'):
                u.apply()
        self.assertEqual((core.APP/'app.js').read_bytes(),baseline)
        self.assertFalse(u.stamp_path().exists())
        self.assertFalse(core.RELAY.exists())
        self.assertFalse(iso.enabled(core.DB))
        print('PASS failed unified deployment restores original app.js')

if __name__=='__main__':unittest.main(verbosity=2)
