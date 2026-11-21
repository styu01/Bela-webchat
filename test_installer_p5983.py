#!/usr/bin/env python3
"""Offline P5.9.8.3 installer lifecycle tests, all systemctl/tmux operations mocked."""
import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import install_p5983 as i
import webchat_isolation as iso

BASE=Path(__file__).parent/'test-fixtures/base'

class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='bela-p5983-install-')
        self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name)
        self.app=self.home/'webchat-p2';self.app.mkdir()
        self.db=self.home/'webchat.sqlite3'
        with closing(sqlite3.connect(self.db)) as c:
            c.executescript('''CREATE TABLE f0_web_jobs(
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT UNIQUE,request_text TEXT,state TEXT,
                created_at INTEGER,updated_at INTEGER);''')
            iso.schema(c)
            c.execute("INSERT INTO webchat_sync_meta VALUES('enabled','1')")
            c.execute("INSERT INTO webchat_sync_meta VALUES('blocked_reason','')")
            c.commit()
        for name in i.FILES:
            (self.app/name).write_bytes((BASE/name).read_bytes())
        self.units={k:100+n for n,k in enumerate((i.TG,i.WORK,i.BROKER,i.HTTP,i.MIRROR))}
        self.stop_calls=[]
        patches=[
            patch.object(i,'APP',self.app),patch.object(i,'DB',self.db),
            patch.object(i,'META',self.app/'.p5983-rollback.json'),
            patch.object(i,'service',self.mock_service),
            patch.object(i,'untouched_state',self.protected),
            patch.object(i,'invoke',self.mock_invoke),
            patch.object(i,'manifest_unit',lambda:None),
        ]
        self.stack=patches
        for p in patches:p.start();self.addCleanup(p.stop)
        self.assertEqual(i.installed_state(),'base')
    def mock_service(self,unit):
        if unit in self.units:return ('active',self.units[unit]) if self.units[unit] else ('inactive',0)
        return ('inactive',0)
    def protected(self):
        return {k:self.units[k] for k in (i.TG,i.WORK,i.BROKER)}|{'tmux':'%0 803'}
    def mock_invoke(self,*args,**kwargs):
        if args[:2]==('systemctl','--user'):
            action,unit=args[2],args[3]
            self.stop_calls.append((action,unit))
            if unit in (i.TG,i.WORK,i.BROKER):
                raise AssertionError('attempt to control protected unit '+unit)
            if action=='stop':self.units[unit]=0
            elif action in ('start','restart'):self.units[unit]=300+len(self.stop_calls)
            return ''
        raise AssertionError('Unexpected invoke: '+repr(args))
    def test_full_lifecycle(self):
        self.assertEqual(i.check(),'base')
        i.apply()
        self.assertEqual(i.installed_state(),'patched')
        i.verify()
        self.assertEqual(i.db_status()[0]['enabled'],'1')
        self.assertTrue(any(op=='stop' and unit==i.MIRROR for op,unit in self.stop_calls))
        self.assertFalse(any(unit in (i.TG,i.WORK,i.BROKER) for op,unit in self.stop_calls))
        i.rollback()
        self.assertEqual(i.installed_state(),'base')
        self.assertEqual(i.db_status()[0]['enabled'],'1')
    def test_idempotent_apply_no_restart(self):
        i.apply();self.stop_calls.clear()
        i.apply();self.assertFalse(self.stop_calls)
    def test_disabled_flag_stays_disabled(self):
        with closing(sqlite3.connect(self.db)) as c:
            c.execute("UPDATE webchat_sync_meta SET value='0' WHERE key='enabled'");c.commit()
        i.apply()
        self.assertEqual(i.db_status()[0]['enabled'],'0')
        i.rollback()
        self.assertEqual(i.db_status()[0]['enabled'],'0')
    def test_unknown_source_fails_before_any_service(self):
        (self.app/'app.js').write_text('modified')
        with self.assertRaises(i.Stop):i.apply()
        self.assertFalse(self.stop_calls)
    def test_symlink_rejected(self):
        target=self.app/'app.js';target.unlink();target.symlink_to(BASE/'app.js')
        with self.assertRaises(i.Stop):i.apply()
    def test_inflight_send_refuses_install(self):
        with closing(sqlite3.connect(self.db)) as c:
            c.execute("INSERT INTO webchat_sync_events(request_id,role,content,state,created_at) VALUES('rid','user','x','ready',1)")
            c.execute("INSERT INTO webchat_sync_parts(event_id,ordinal,kind,text,media_json,state) VALUES(1,1,'text','x','','sending')")
            c.commit()
        with self.assertRaises(i.Stop):i.apply()
        self.assertFalse(self.stop_calls)
        self.assertEqual(i.db_status()[0]['enabled'],'1')
    def test_active_job_refuses_install(self):
        with closing(sqlite3.connect(self.db)) as c:
            c.execute("INSERT INTO f0_web_jobs(request_id,request_text,state,created_at,updated_at) VALUES('job','x','seen',1,1)")
            c.commit()
        with self.assertRaises(i.Stop):i.apply()
        self.assertFalse(self.stop_calls)
    def test_tampered_backup_denies_rollback(self):
        i.apply()
        r=json.loads(i.META.read_text())
        (Path(r['backup'])/'webchat_sync_sender.py').write_text('tampered')
        with self.assertRaises(i.Stop):i.rollback()
        self.assertEqual(i.installed_state(),'patched')
    def test_failed_copy_recovers_without_touching_telegram(self):
        old=i.atomic_copy
        calls=[0]
        def fail_on_second(src,dst):
            if str(src).startswith(str(i.HERE)):
                calls[0]+=1
                if calls[0]==2:raise OSError('simulated disk full')
            return old(src,dst)
        with patch.object(i,'atomic_copy',side_effect=fail_on_second):
            with self.assertRaises(OSError):i.apply()
        self.assertEqual(i.installed_state(),'base')
        self.assertEqual(i.db_status()[0]['enabled'],'1')
        self.assertFalse(any(unit in (i.TG,i.WORK,i.BROKER) for op,unit in self.stop_calls))
    def test_backup_contains_consistent_sqlite(self):
        i.apply();r=json.loads(i.META.read_text())
        snap=Path(r['db_snapshot'])
        with closing(sqlite3.connect(snap)) as c:
            self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok')
    def test_quiesce_abort_restores_prior_flag(self):
        i.check()
        original=self.protected()
        with patch.object(i,'await_idle',side_effect=i.Stop('simulated quiesce timeout')):
            with self.assertRaises(i.Stop):i.quiesce(original,'1')
        self.assertEqual(i.db_status()[0]['enabled'],'1')
        self.assertEqual(self.units[i.MIRROR],104)
    def test_quiesce_stop_failure_recovers_mirror(self):
        original=self.protected()
        old=self.mock_invoke
        once=[0]
        def fail_first_stop(*args,**kwargs):
            if args[2:4]==('stop',i.MIRROR) and not once[0]:
                once[0]+=1
                raise i.Stop('unit stop failure')
            return old(*args,**kwargs)
        with patch.object(i,'invoke',side_effect=fail_first_stop):
            with self.assertRaises(i.Stop):i.quiesce(original,'1')
        self.assertEqual(i.db_status()[0]['enabled'],'1')
        self.assertEqual(self.units[i.MIRROR],104)
    def test_pid_change_on_protected_service_stops_install(self):
        # assert_untouched is called after mirror stop, compare with original.
        initial=self.protected()
        self.units[i.TG]+=1
        with self.assertRaises(i.Stop):i.assert_untouched(initial)

if __name__=='__main__':unittest.main(verbosity=2)
