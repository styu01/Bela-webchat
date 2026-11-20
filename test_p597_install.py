#!/usr/bin/env python3
"""Offline P5.9.7 installer simulation; never touches real WSL/systemd."""
import json
import shutil
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from contextlib import contextmanager
from unittest.mock import patch
import install_p597 as ins
import webchat_isolation as iso
import webchat_bridge_broker as broker

SRC=Path(__file__).resolve().parent
BASE=Path(__file__).resolve().parent/'test-fixtures'/'p5961'


@contextmanager
def db_conn(path):
    con=sqlite3.connect(path)
    try:
        with con:
            yield con
    finally:
        con.close()


class FakeBus:
    def __init__(self,*args,**kw):pass
    def probe(self):return True


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='p597-inst-')
        self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        self.app=root/'webchat-p2';self.app.mkdir()
        self.marveen=root/'marveen';(self.marveen/'agents').mkdir(parents=True)
        self.relay=self.marveen/'agents/webchat-relay';self.marker=self.relay/'.p597-owner-webchat-bridge'
        self.dropin=root/'systemd/30-p597-main.conf'
        self.broker_unit=root/'systemd/bela-webchat-broker.service'
        self.broker_socket=root/'store/p597-main-broker.sock'
        self.db=root/'store/f0-webchat.sqlite3';self.db.parent.mkdir()
        (self.marveen/'store').mkdir()
        (self.marveen/'store/.dashboard-token').write_text('test-test-test-test-token-1234')
        (self.marveen/'store/.dashboard-token').chmod(0o600)
        with db_conn(self.marveen/'store/claudeclaw.db') as c:
            c.execute('CREATE TABLE agent_messages(id INTEGER PRIMARY KEY)')
        for file in ins.BASE:
            src=SRC/'test-fixtures'/'p5961'/file
            self.assertEqual(ins.sha(src),ins.BASE[file],file)
            shutil.copy2(src,self.app/file)
        with db_conn(self.db) as c:
            c.executescript('''CREATE TABLE f0_web_jobs(seq INTEGER PRIMARY KEY AUTOINCREMENT,request_id TEXT UNIQUE,
                request_text TEXT,state TEXT,created_at INTEGER,updated_at INTEGER,markdown TEXT,
                telegram_state TEXT,session_id TEXT,handoff_token TEXT);''')
            iso.schema(c)
            c.execute("INSERT INTO webchat_isolation_meta VALUES('migration','p591-1')")
            c.execute("INSERT INTO webchat_isolation_meta VALUES('enabled','1')")
            c.execute("INSERT INTO webchat_sync_meta VALUES('enabled','1')")
            c.execute("INSERT INTO webchat_sync_meta VALUES('blocked_reason','')")
            for seq in range(76,83):
                rid=str(uuid.uuid4())
                c.execute('INSERT INTO f0_web_jobs(seq,request_id,request_text,state,created_at,updated_at) VALUES(?,?,?,?,1,1)',
                          (seq,rid,'OLD','queued'))
                c.execute('INSERT INTO webchat_held_requests VALUES(?,?,1)',(rid,'legacy'))
        self.units={ins.WEB:'active',ins.WORK:'active',ins.MIRROR:'active',ins.BROKER:'inactive'}
        self.calls=[]
        mocks=[
            patch.object(ins,'APP',self.app), patch.object(ins,'DB',self.db),
            patch.object(ins,'MARVEEN',self.marveen), patch.object(ins,'RELAY',self.relay),
            patch.object(ins,'MARKER',self.marker), patch.object(ins,'DROPIN',self.dropin),
            patch.object(ins,'BROKER_UNIT',self.broker_unit), patch.object(ins,'BROKER_SOCK',self.broker_socket),
            patch.object(ins,'main_source',return_value='bfd79f75'),
            patch.object(ins,'manifest',return_value=None),
            patch.object(ins,'command',side_effect=self.command),
            patch.object(ins,'props',side_effect=self.props),
            patch.object(ins.urllib.request,'urlopen',side_effect=self.http),
            patch.object(ins.broker,'dashboard_url',return_value='http://127.0.0.1:3420'),
            patch.object(ins.broker,'probe_main',return_value=None),
            patch.object(ins.bridge,'BrokerClient',FakeBus),
            patch.object(ins,'wait_broker',return_value=None),
        ]
        for m in mocks:m.start();self.addCleanup(m.stop)

    def command(self,*args,**kw):
        self.calls.append(args)
        if args[:2]==('systemctl','--user'):
            if args[2] in ('start','stop'):
                service=args[3]
                if service==ins.TG:raise AssertionError('TELEGRAM MUST NOT BE RESTARTED')
                self.units[service]='active' if args[2]=='start' else 'inactive'
            return ''
        if args[:2]==('tmux','list-panes'):return '%0 854'
        raise AssertionError('Unexpected command: '+repr(args))

    def props(self,unit,keys):
        if unit==ins.TG:d={'ActiveState':'active','MainPID':'453','ControlGroup':'/tg','KillMode':'control-group'}
        else:
            if unit not in self.units:raise AssertionError(unit)
            state=self.units[unit]
            d={'ActiveState':state,'MainPID':'122' if state=='active' else '0',
               'ControlGroup':'/'+unit if state=='active' else '', 'KillMode':'control-group', 'ExecStart':('webchat_main_bridge.py' if self.dropin.exists() and unit==ins.WORK else 'webchat_worker.py'), 'ExecStartPre':('webchat_main_bridge.py --check' if self.dropin.exists() and unit==ins.WORK else '')}
        return {k:d.get(k,'') for k in keys}

    def http(self,*args,**kw):
        class HTTP:
            status=200
            def __enter__(self):return self
            def __exit__(self,*args):return False
        return HTTP()

    def test_safe_install_paused_and_preserves_telegram(self):
        self.assertEqual(ins.check('pre')['queue']['enabled'],'1')
        ins.apply()
        self.assertFalse(iso.enabled(self.db))
        self.assertEqual(self.units,{ins.WEB:'active',ins.WORK:'active',ins.MIRROR:'active',ins.BROKER:'active'})
        self.assertTrue(self.dropin.exists())
        self.assertTrue(self.broker_unit.exists())
        self.assertIn('ExecStartPre=',self.dropin.read_text())
        self.assertTrue(self.relay.is_dir())
        self.assertTrue((self.app/'webchat_bridge_broker.py').exists())
        self.assertEqual(ins.sha(self.app/'webchat_worker.py'),ins.BASE['webchat_worker.py'])
        self.assertEqual(ins.sha(self.app/'webchat_main_bridge.py'),ins.sha(SRC/'webchat_main_bridge.py'))
        with db_conn(self.db) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM webchat_main_links').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM webchat_held_requests').fetchone()[0],7)
            self.assertEqual(c.execute("SELECT value FROM webchat_sync_meta WHERE key='enabled'").fetchone()[0],'1')
        self.assertFalse(any(x[:3] in (('systemctl','--user','stop'),('systemctl','--user','start')) and ins.TG in x for x in self.calls))

    def test_enable_separate_and_pause(self):
        ins.apply()
        self.assertFalse(iso.enabled(self.db))
        ins.enable()
        self.assertTrue(iso.enabled(self.db))
        ins.pause()
        self.assertFalse(iso.enabled(self.db))

    def test_manual_rollback_after_safe_install(self):
        ins.apply()
        self.assertTrue(self.dropin.exists())
        ins.rollback()
        self.assertFalse(self.dropin.exists())
        self.assertFalse(self.broker_unit.exists())
        self.assertFalse(self.relay.exists())
        self.assertFalse((self.app/'webchat_main_bridge.py').exists())
        self.assertFalse((self.app/'webchat_bridge_broker.py').exists())
        self.assertEqual(ins.sha(self.app/'webchat_http.py'),ins.BASE['webchat_http.py'])
        self.assertEqual(ins.sha(self.app/'index.html'),ins.BASE['index.html'])
        self.assertFalse(iso.enabled(self.db))

    def test_rollback_rejects_outstanding_main_job(self):
        ins.apply()
        with db_conn(self.db) as c:
            c.execute("INSERT INTO webchat_main_links(request_id,seq,remote_id,phase,created_at,updated_at) VALUES(?,?,?,'submitted',1,1)",
                      (str(uuid.uuid4()),99,701))
        with self.assertRaises(ins.Stop):ins.rollback()
        self.assertTrue(self.dropin.exists())
        self.assertEqual(self.units[ins.WORK],'active')

    def test_active_job_refuses_before_stop(self):
        with db_conn(self.db) as c:c.execute("UPDATE f0_web_jobs SET state='seen' WHERE seq=82")
        with self.assertRaises(ins.Stop):ins.apply()
        self.assertEqual(self.units[ins.WORK],'active')
        self.assertFalse(self.dropin.exists())

    def test_unheld_legacy_refused(self):
        with db_conn(self.db) as c:
            c.execute("DELETE FROM webchat_held_requests WHERE request_id=(SELECT request_id FROM f0_web_jobs WHERE seq=82)")
        with self.assertRaises(ins.Stop):ins.check('pre')

    def test_http_failure_restores_original_worker_and_files(self):
        with patch.object(ins,'wait_http',side_effect=ins.Stop('HTTP simulated failure')):
            with self.assertRaises(ins.Stop):ins.apply()
        self.assertFalse(iso.enabled(self.db))
        self.assertFalse(self.dropin.exists())
        self.assertFalse(self.broker_unit.exists())
        self.assertFalse(self.relay.exists())
        self.assertEqual(self.units[ins.WEB],'active')
        self.assertEqual(self.units[ins.WORK],'active')
        for file in ('webchat_http.py','index.html'):
            self.assertEqual(ins.sha(self.app/file),ins.BASE[file])
        self.assertFalse((self.app/'webchat_main_bridge.py').exists())
        self.assertFalse((self.app/'webchat_bridge_broker.py').exists())

    def test_worker_preflight_failure_triggers_targeted_rollback(self):
        # Simulate systemd returning inactive when ExecStartPre denies a secret.
        old_props=self.props
        def failing_props(unit,keys):
            state=old_props(unit,keys)
            if self.dropin.exists() and unit==ins.WORK and 'ActiveState' in keys:
                state['ActiveState']='failed'
            return state
        with patch.object(ins,'props',side_effect=failing_props):
            with self.assertRaisesRegex(ins.Stop,'bela-webchat-worker.service'):
                ins.apply()
        self.assertFalse(self.dropin.exists())
        self.assertFalse(self.broker_unit.exists())
        self.assertFalse(self.relay.exists())
        self.assertFalse(iso.enabled(self.db))
        self.assertEqual(self.units[ins.WEB],'active')
        self.assertEqual(self.units[ins.WORK],'active')
        self.assertEqual(ins.sha(self.app/'webchat_http.py'),ins.BASE['webchat_http.py'])

    def test_broker_boot_start_and_cleanup_are_separate_from_telegram(self):
        ins.apply()
        self.assertIn('Wants=bela-webchat-broker.service',(self.dropin).read_text())
        self.assertIn('After=bela-webchat-broker.service',(self.dropin).read_text())
        self.assertIn(('systemctl','--user','enable',ins.BROKER),self.calls)
        self.assertIn(('systemctl','--user','start',ins.BROKER),self.calls)
        ins.rollback()
        self.assertIn(('systemctl','--user','disable',ins.BROKER),self.calls)
        self.assertIn(('systemctl','--user','stop',ins.BROKER),self.calls)
        self.assertFalse(any(ins.TG in tuple(str(a) for a in x) for x in self.calls if x[0]=='systemctl' and x[2] in ('start','stop','enable','disable')))

    def test_broker_start_failure_reverts_without_touching_telegram(self):
        original=self.command
        def broken(*args,**kwargs):
            if args==('systemctl','--user','start',ins.BROKER):
                raise ins.Stop('simulated broker startup failure')
            return original(*args,**kwargs)
        with patch.object(ins,'command',side_effect=broken):
            with self.assertRaisesRegex(ins.Stop,'simulated broker startup failure'):
                ins.apply()
        self.assertFalse(self.dropin.exists())
        self.assertFalse(self.broker_unit.exists())
        self.assertFalse(self.relay.exists())
        self.assertEqual(self.units[ins.WEB],'active')
        self.assertEqual(self.units[ins.WORK],'active')
        self.assertEqual(self.units[ins.BROKER],'inactive')
        self.assertFalse(iso.enabled(self.db))

    def test_existing_relay_refused_without_overwrite(self):
        self.relay.mkdir()
        with self.assertRaises(ins.Stop):ins.check('pre')

if __name__=='__main__':unittest.main(verbosity=2)
