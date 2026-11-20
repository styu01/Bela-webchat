#!/usr/bin/env python3
"""Offline P5.9.7 bridge tests. No Telegram, tmux, Claude, live dashboard."""
import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from contextlib import contextmanager
from unittest.mock import patch

import webchat_isolation as iso
import webchat_main_bridge as b
import webchat_bridge_broker as br


@contextmanager
def db_conn(path):
    con=sqlite3.connect(path)
    try:
        with con:
            yield con
    finally:
        con.close()


class FakeBus:
    def __init__(self, post_id=701, fail=False, db=None):
        self.calls=[]
        self.post_id=post_id
        self.fail=fail
        self.db=db
    def probe(self):return True
    def post(self,rid,body):
        self.calls.append(body)
        if self.fail:raise OSError('timeout')
        return self.post_id
    def result(self,rid,remote_id):
        if self.db is None:raise b.Stop('No mock main DB')
        with db_conn(self.db) as c:
            c.row_factory=sqlite3.Row
            m=c.execute('SELECT * FROM agent_messages WHERE id=?',(remote_id,)).fetchone()
            if m is None or m['from_agent']!=b.SENDER or m['to_agent']!='bela' or rid not in m['content']:
                raise b.Stop('Marveen provenance mismatch')
            return {'id':remote_id,'status':m['status'],'result':m['result']}


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='p597-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'project'
        (self.root/'store').mkdir(parents=True)
        (self.root/'uploads').mkdir(mode=0o700)
        self.db=self.root/'store/f0-webchat.sqlite3'
        self.main=Path(self.temp.name)/'marveen.sqlite3'
        with db_conn(self.db) as c:
            c.executescript('''CREATE TABLE f0_web_jobs(
                seq INTEGER PRIMARY KEY AUTOINCREMENT,request_id TEXT UNIQUE,
                request_text TEXT,state TEXT,markdown TEXT,telegram_state TEXT,
                created_at INTEGER,updated_at INTEGER,session_id TEXT,handoff_token TEXT);
            ''')
            iso.schema(c)
            c.execute("INSERT INTO webchat_isolation_meta VALUES('migration','p591-1')")
            c.execute("INSERT INTO webchat_isolation_meta VALUES('enabled','1')")
            c.execute("INSERT INTO webchat_sync_meta VALUES('enabled','0')")
            c.execute("INSERT INTO webchat_sync_meta VALUES('blocked_reason','')")
            for seq in range(76,83):
                rid=str(uuid.uuid4())
                c.execute('INSERT INTO f0_web_jobs(seq,request_id,request_text,state,created_at,updated_at) VALUES(?,?,?,?,1,1)',
                          (seq,rid,'old','queued'))
                c.execute('INSERT INTO webchat_held_requests VALUES(?,?,1)',(rid,'held'))
        with db_conn(self.main) as c:
            c.execute('CREATE TABLE agent_messages(id INTEGER PRIMARY KEY,from_agent TEXT,to_agent TEXT,content TEXT,status TEXT,result TEXT)')

    def enqueue(self,text='Kedves Bela, irj egy szot'):
        rid=str(uuid.uuid4())
        with iso.transaction(self.db) as c:status=iso.enqueue(c,rid,text)
        self.assertEqual(status,(202,'queued'))
        return rid

    def remote(self,id,rid,status='done',result='KESZ',sender=b.SENDER):
        with db_conn(self.main) as c:
            c.execute('INSERT INTO agent_messages VALUES(?,?,?,?,?,?)',
                      (id,sender,'bela','Webchat-azonosítója: '+rid,status,result))

    def jobs(self):
        with db_conn(self.db) as c:
            return c.execute('SELECT seq,state,markdown FROM f0_web_jobs ORDER BY seq').fetchall()

    def test_claim_single_owner_request_preserves_legacy(self):
        rid=self.enqueue()
        job=b.claim(self.db)
        self.assertEqual(job['request_id'],rid)
        self.assertIsNone(b.claim(self.db))
        with db_conn(self.db) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM webchat_held_requests').fetchone()[0],7)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM f0_web_jobs WHERE seq BETWEEN 76 AND 82 AND state='queued'").fetchone()[0],7)
            self.assertEqual(c.execute("SELECT phase FROM webchat_main_links WHERE request_id=?",(rid,)).fetchone()[0],'posting')

    def test_response_by_exact_remote_message_id_only(self):
        rid=self.enqueue()
        job=b.claim(self.db)
        prompt=b.prompt_for(rid,job['request_text'],self.root)
        self.assertIn('Marveen inbox-fejlécben kapott msg_id',prompt)
        b.confirm(self.db,rid,701)
        self.remote(701,rid,status='delivered',result='IGNORED')
        self.assertEqual(b.finish_from_main(self.db,FakeBus(db=self.main)),0)
        with db_conn(self.main) as c:c.execute("UPDATE agent_messages SET status='done',result='Bela igazi valasza' WHERE id=701")
        self.assertEqual(b.finish_from_main(self.db,FakeBus(db=self.main)),1)
        self.assertEqual(b.finish_from_main(self.db,FakeBus(db=self.main)),0)
        with db_conn(self.db) as c:
            self.assertEqual(c.execute('SELECT state,markdown FROM f0_web_jobs WHERE request_id=?',(rid,)).fetchone(),('answered','Bela igazi valasza'))
            self.assertEqual(c.execute("SELECT COUNT(*) FROM webchat_sync_events WHERE request_id=? AND role='assistant'",(rid,)).fetchone()[0],1)

    def test_worker_one_shot_end_to_end_fake_bus(self):
        rid=self.enqueue('Eredeti Bela proba')
        bus=FakeBus(post_id=701,db=self.main)
        with patch.object(b,'require_registered',return_value=None):
            b.loop(self.db,bus,once=True)
        self.assertEqual(len(bus.calls),1)
        self.assertIn(rid,bus.calls[0])
        self.assertIn('eredeti',bus.calls[0].lower())
        with db_conn(self.db) as c:
            self.assertEqual(c.execute("SELECT state FROM f0_web_jobs WHERE request_id=?",(rid,)).fetchone()[0],'seen')
        self.remote(701,rid,status='done',result='MARVEEN_IGAZOLT')
        with patch.object(b,'require_registered',return_value=None):
            b.loop(self.db,bus,once=True)
        self.assertEqual(len(bus.calls),1)
        with db_conn(self.db) as c:
            self.assertEqual(c.execute("SELECT markdown FROM f0_web_jobs WHERE request_id=?",(rid,)).fetchone()[0],'MARVEEN_IGAZOLT')

    def test_worker_failed_network_never_retries(self):
        rid=self.enqueue('Halo')
        bus=FakeBus(fail=True)
        with patch.object(b,'require_registered',return_value=None):
            b.loop(self.db,bus,once=True)
            b.loop(self.db,bus,once=True)
        self.assertEqual(len(bus.calls),1)
        with db_conn(self.db) as c:
            self.assertEqual(c.execute("SELECT phase FROM webchat_main_links WHERE request_id=?",(rid,)).fetchone()[0],'uncertain')

    def test_unrelated_telegram_reply_not_matched(self):
        rid=self.enqueue();b.claim(self.db);b.confirm(self.db,rid,701)
        self.remote(701,str(uuid.uuid4()))
        with self.assertRaises(b.Stop):b.finish_from_main(self.db,FakeBus(db=self.main))
        with db_conn(self.db) as c:self.assertEqual(c.execute('SELECT state FROM f0_web_jobs WHERE request_id=?',(rid,)).fetchone()[0],'seen')

    def test_remote_sender_provenance_mismatch_refused(self):
        rid=self.enqueue();b.claim(self.db);b.confirm(self.db,rid,701)
        self.remote(701,rid,sender='untrusted')
        with self.assertRaises(b.Stop):b.finish_from_main(self.db,FakeBus(db=self.main))

    def test_post_crash_recover_never_retries_and_preserves_next(self):
        first=self.enqueue();b.claim(self.db)
        second=self.enqueue()
        self.assertEqual(b.recover(self.db),[first])
        self.assertTrue(b.uncertain(self.db,first))
        self.assertFalse(b.uncertain(self.db,first))
        self.assertEqual(b.claim(self.db)['request_id'],second)
        with db_conn(self.db) as c:
            self.assertEqual(c.execute("SELECT reason FROM webchat_tombstones WHERE request_id=?",(first,)).fetchone()[0],'failed_unverified')
            self.assertEqual(c.execute("SELECT phase FROM webchat_main_links WHERE request_id=?",(first,)).fetchone()[0],'uncertain')
            self.assertEqual(c.execute("SELECT COUNT(*) FROM webchat_held_requests").fetchone()[0],7)

    def test_failed_main_job_archived_not_replayed(self):
        rid=self.enqueue();b.claim(self.db);b.confirm(self.db,rid,701)
        self.remote(701,rid,status='failed',result='Nincs hozzaferes')
        self.assertEqual(b.finish_from_main(self.db,FakeBus(db=self.main)),1)
        with db_conn(self.db) as c:
            self.assertEqual(c.execute('SELECT reason FROM webchat_tombstones WHERE request_id=?',(rid,)).fetchone()[0],'failed_unverified')
            self.assertEqual(c.execute("SELECT phase FROM webchat_main_links WHERE request_id=?",(rid,)).fetchone()[0],'failed')

    def test_empty_done_does_not_claim_answer(self):
        rid=self.enqueue();b.claim(self.db);b.confirm(self.db,rid,701)
        self.remote(701,rid,status='done',result='')
        with self.assertRaises(b.Stop):b.finish_from_main(self.db,FakeBus(db=self.main))
        with db_conn(self.db) as c:self.assertEqual(c.execute('SELECT state FROM f0_web_jobs WHERE request_id=?',(rid,)).fetchone()[0],'seen')

    def test_twenty_queued_owner_messages_only_one_active(self):
        uuids=[self.enqueue('Q%02d'%i) for i in range(20)]
        claimed=b.claim(self.db)
        self.assertEqual(claimed['request_id'],uuids[0])
        self.assertIsNone(b.claim(self.db))
        b.confirm(self.db,uuids[0],701)
        self.remote(701,uuids[0])
        self.assertEqual(b.finish_from_main(self.db,FakeBus(db=self.main)),1)
        second=b.claim(self.db)
        self.assertEqual(second['request_id'],uuids[1])
        with db_conn(self.db) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM f0_web_jobs WHERE state='queued' AND seq>82").fetchone()[0],18)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM f0_web_jobs WHERE state='answered'").fetchone()[0],1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM f0_web_jobs WHERE state='handed_off'").fetchone()[0],1)

    def test_no_dispatch_when_paused(self):
        rid=self.enqueue()
        iso.set_enabled(self.db,False)
        self.assertIsNone(b.claim(self.db))
        with db_conn(self.db) as c:self.assertEqual(c.execute('SELECT state FROM f0_web_jobs WHERE request_id=?',(rid,)).fetchone()[0],'queued')

    def test_upload_manifest_hash_and_length(self):
        name='a'*32+'.txt'
        data=b'hello world';path=self.root/'uploads'/name
        path.write_bytes(data);os.chmod(path,0o600)
        manifest=self.root/'uploads'/('b'*32+'.json')
        manifest.write_text(json.dumps({'version':2,'files':[{'id':name,'size':len(data),'sha256':hashlib.sha256(data).hexdigest(),'name':'notes.txt'}]}));os.chmod(manifest,0o600)
        text='Dolgozd fel\nCsatolt fajlok jegyzeke: '+str(manifest)
        body,uploads=b.safe_request(text,self.root)
        self.assertEqual(body,'Dolgozd fel')
        self.assertIn(str(path),uploads)
        path.write_bytes(b'invalidated');os.chmod(path,0o600)
        with self.assertRaises(b.Stop):b.safe_request(text,self.root)

    def test_worker_refuses_direct_marveen_secret_access(self):
        source=(Path(__file__).parent/'webchat_main_bridge.py').read_text()
        self.assertNotIn('def secret(',source)
        self.assertNotIn('claudeclaw.db',source)
        self.assertNotIn('urllib.request',source)
        self.assertIn('socket.AF_UNIX',source)

    def test_broker_config_rejects_nonlocal_ports(self):
        for value in ('80','0','99999','3420a','3420\nBAD=1',' 3420 '):
            with self.subTest(value=value):
                with patch.dict(os.environ,{'BELA_MARVEEN_WEB_PORT':value}):
                    with self.assertRaises(br.Stop):br.dashboard_url()

    def test_broker_rejects_unreadable_token_permissions(self):
        folder=Path(self.temp.name)/'marveen-token'
        (folder/'store').mkdir(parents=True)
        token=folder/'store/.dashboard-token'
        token.write_text('a'*30)
        os.chmod(token,0o644)
        with self.assertRaises(br.Stop):br.dashboard_token(folder)
        os.chmod(token,0o600)
        self.assertEqual(br.dashboard_token(folder),'a'*30)

    def test_broker_main_db_read_only(self):
        with br.open_ro_marveen(self.main) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM agent_messages').fetchone()[0],0)
            with self.assertRaises(sqlite3.OperationalError):
                c.execute('INSERT INTO agent_messages(id) VALUES(777)')

    def test_broker_cannot_post_without_queued_owner_job(self):
        rid=self.enqueue()
        bus=br.Broker(webdb=self.db,maindb=self.main,
                      ledger=Path(self.temp.name)/'fence.sqlite3',token='test')
        prompt=b.prompt_for(rid,'Halo',self.root)
        with self.assertRaises(br.Stop):bus.post(rid,prompt)
        self.assertFalse(bus.ledger.exists())

    def test_broker_strict_read_provenance(self):
        rid=self.enqueue();b.claim(self.db);b.confirm(self.db,rid,701)
        self.remote(701,rid,status='done',result='Szep valasz')
        bus=br.Broker(webdb=self.db,maindb=self.main,token='test')
        self.assertEqual(bus.result(rid,701)['result'],'Szep valasz')
        with self.assertRaises(br.Stop):bus.result(str(uuid.uuid4()),701)
        with db_conn(self.main) as c:
            c.execute("UPDATE agent_messages SET from_agent='telegram' WHERE id=701")
        with self.assertRaises(br.Stop):bus.result(rid,701)

    def test_broker_durable_one_post_only(self):
        rid=self.enqueue();b.claim(self.db)
        calls=[]
        class MockHTTP:
            def __init__(self):self.status=200
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self,*a):return b'{"id":701}'
        class MockOpener:
            def open(self,req,timeout):
                calls.append(req)
                return MockHTTP()
        bus=br.Broker(webdb=self.db,maindb=self.main,
                      ledger=Path(self.temp.name)/'fence.sqlite3',token='test',opener=MockOpener())
        prompt=b.prompt_for(rid,'Halo',self.root)
        self.assertEqual(bus.post(rid,prompt),{'id':701})
        with self.assertRaises(br.Stop):bus.post(rid,prompt)
        self.assertEqual(len(calls),1)

    def test_broker_socket_roundtrip_and_mode(self):
        import threading
        import socketserver
        class FakeBridge:
            def handle(self, data):
                if data=={'op':'probe'}:return {'ok':True}
                raise br.Stop('Forbidden')
        sock=Path(self.temp.name)/'p597-test.sock'
        with br.Server(sock,FakeBridge()) as server:
            os.chmod(sock,0o600)
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            try:
                self.assertEqual(b.BrokerClient(sock).probe(),True)
                self.assertEqual(os.stat(sock).st_mode&0o777,0o600)
                with self.assertRaises(b.Stop):b.BrokerClient(sock)._call({'op':'delete_all'})
            finally:
                server.shutdown();thread.join(timeout=3)

    def test_worker_preflight_write_then_rollback(self):
        original=self.db.read_bytes()
        b.webchat_db_preflight(self.db)
        with db_conn(self.db) as c:
            self.assertEqual(c.execute("SELECT value FROM webchat_isolation_meta WHERE key='enabled'").fetchone()[0],'1')
            self.assertEqual(c.execute("SELECT COUNT(*) FROM f0_web_jobs WHERE state='queued'").fetchone()[0],7)
        # The check never commits a state change.
        self.assertEqual(original,self.db.read_bytes())
        with self.assertRaises(b.Stop):b.webchat_db_preflight(Path(self.temp.name)/'absent.db')

    def test_end_to_end_real_unix_broker_fake_marveen(self):
        import threading
        rid=self.enqueue('Teszteld ugyanazt a Belat')
        calls=[]
        class MockHTTP:
            def __init__(self,body):self.status=200;self.body=body
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self,*a):return self.body
        class MockOpener:
            def open(self,req,timeout):
                if req.get_method()=='GET':return MockHTTP(b'[]')
                calls.append(req)
                return MockHTTP(b'{"id":701}')
        ledger=Path(self.temp.name)/'broker-ledger.sqlite3'
        sock=Path(self.temp.name)/'broker.sock'
        priv=br.Broker(webdb=self.db,maindb=self.main,ledger=ledger,
                       token='test',opener=MockOpener())
        with br.Server(sock,priv) as server:
            os.chmod(sock,0o600)
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            client=b.BrokerClient(sock)
            try:
                with patch.object(b,'require_registered',return_value=None):
                    b.loop(self.db,client,once=True)
                self.assertEqual(len(calls),1)
                self.assertIn(rid,calls[0].data.decode())
                self.remote(701,rid,status='done',result='KOZOS_BELA_OK')
                with patch.object(b,'require_registered',return_value=None):
                    b.loop(self.db,client,once=True)
            finally:
                server.shutdown();thread.join(timeout=3)
        with db_conn(self.db) as c:
            self.assertEqual(c.execute("SELECT markdown FROM f0_web_jobs WHERE request_id=?",(rid,)).fetchone()[0],'KOZOS_BELA_OK')
            self.assertEqual(c.execute("SELECT phase FROM webchat_main_links WHERE request_id=?",(rid,)).fetchone()[0],'answered')
        self.assertEqual(len(calls),1)

    def test_repo_has_no_tmux_or_claude_execution(self):
        code=(Path(__file__).parent/'webchat_main_bridge.py').read_text()
        for text in ('tmux send-keys','tmux kill','systemctl --user restart bela-channels','subprocess.Popen([\'claude\''):
            self.assertNotIn(text,code)

if __name__=='__main__':unittest.main(verbosity=2)
