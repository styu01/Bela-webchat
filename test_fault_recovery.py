#!/usr/bin/env python3
"""OFFLINE process-kill fault harness for P5.9.8.

Uses a temporary Webchat SQLite, temporary Marveen SQLite, local mock HTTP
server, real AF_UNIX broker subprocess, and real worker subprocess. SIGKILL
NEVER targets the user's services, Telegram, Claude, tmux, or actual WSL.
"""
import json
import multiprocessing as mp
import os
import signal
import socket
import sqlite3
import tempfile
import threading
import time
import unittest
import uuid
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import webchat_isolation as iso
import webchat_main_bridge as bridge
import webchat_bridge_broker as br

CTX=mp.get_context('spawn')


def open_db(path):
    return sqlite3.connect(path,timeout=5)


def broker_subprocess(sock, webdb, maindb, ledger, port):
    b=br.Broker(webdb=Path(webdb),maindb=Path(maindb),ledger=Path(ledger),
                url=f'http://127.0.0.1:{port}',token='OFFLINE_FAKE_ONLY')
    server=br.Server(sock,b)
    os.chmod(sock,0o600)
    try:server.serve_forever(poll_interval=0.1)
    finally:server.server_close()


def worker_subprocess(sock, webdb):
    with patch.object(bridge,'require_registered',return_value=None):
        bridge.loop(Path(webdb),bus=bridge.BrokerClient(Path(sock)),once=False)


class Harness(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='p598-fault-')
        self.addCleanup(self.temp.cleanup)
        p=Path(self.temp.name)
        self.project=p/'project';(self.project/'store').mkdir(parents=True)
        self.db=self.project/'store/f0-webchat.sqlite3'
        self.main=p/'main.sqlite3'
        self.sock=str(p/'broker.sock')
        self.ledger=p/'ledger.sqlite3'
        self.broker=None;self.worker=None
        self.release_http=threading.Event()
        self.post_saved=threading.Event()
        self.hold_responses=False
        with closing(open_db(self.db)) as c:
            c.executescript('''CREATE TABLE f0_web_jobs(
                seq INTEGER PRIMARY KEY AUTOINCREMENT,request_id TEXT UNIQUE,
                request_text TEXT,state TEXT,markdown TEXT,telegram_state TEXT,
                created_at INTEGER,updated_at INTEGER,session_id TEXT,handoff_token TEXT);''')
            iso.schema(c);bridge.bridge_schema(c)
            c.execute("INSERT INTO webchat_isolation_meta VALUES('migration','p591-1')")
            c.execute("INSERT INTO webchat_isolation_meta VALUES('enabled','1')")
            c.execute("INSERT INTO webchat_sync_meta VALUES('enabled','0')")
            for i in range(76,83):
                rid=str(uuid.uuid4())
                c.execute('INSERT INTO f0_web_jobs(seq,request_id,request_text,state,created_at,updated_at) VALUES(?,?,?,?,1,1)',
                          (i,rid,'LEGACY_HELD','queued'))
                c.execute('INSERT INTO webchat_held_requests VALUES(?,?,1)',(rid,'held'))
            c.commit()
        with closing(open_db(self.main)) as c:
            c.execute('''CREATE TABLE agent_messages(
                id INTEGER PRIMARY KEY AUTOINCREMENT,from_agent TEXT,to_agent TEXT,
                content TEXT,status TEXT,result TEXT)''')
            c.commit()
        harness=self
        class Api(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def response(self,data,status=200):
                raw=json.dumps(data).encode()
                self.send_response(status)
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(raw)))
                self.end_headers()
                try:self.wfile.write(raw)
                except OSError:pass
            def do_GET(self):
                if self.path!='/api/messages?agent=bela&limit=1':
                    return self.response({'error':'not allowed'},404)
                self.response([])
            def do_POST(self):
                if self.path!='/api/messages':return self.response({'error':'not allowed'},404)
                try:
                    length=int(self.headers['Content-Length'])
                    assert 0<length<100_000
                    body=json.loads(self.rfile.read(length))
                    assert body['from']=='webchat-relay' and body['to']=='bela'
                    with closing(open_db(harness.main)) as c:
                        cur=c.execute('INSERT INTO agent_messages(from_agent,to_agent,content,status) VALUES(?,?,?,?)',
                            ('webchat-relay','bela',body['content'],'delivered'))
                        message_id=cur.lastrowid
                        c.commit()
                    harness.post_saved.set()
                    if harness.hold_responses:harness.release_http.wait(15)
                    self.response({'id':message_id})
                except Exception as e:
                    self.response({'error':str(e)},500)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Api)
        self.server.daemon_threads=True
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        self.release_http.set()
        for p in (self.worker,self.broker):
            if p and p.is_alive():
                p.kill();p.join(3)
            elif p:p.join(0.1)
        self.server.shutdown();self.server.server_close();self.thread.join(3)

    def tick_until(self,fn,timeout=15):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            val=fn()
            if val:return val
            time.sleep(.08)
        self.fail('Timed out waiting for isolated condition')

    def wrow(self,rid):
        with closing(open_db(self.db)) as c:
            return c.execute('''SELECT j.state,j.markdown,l.phase,l.remote_id
                FROM f0_web_jobs j JOIN webchat_main_links l ON l.request_id=j.request_id
                WHERE j.request_id=?''',(rid,)).fetchone()

    def post_count(self):
        with closing(open_db(self.main)) as c:
            return c.execute('SELECT COUNT(*) FROM agent_messages').fetchone()[0]

    def start_broker(self):
        if self.broker and self.broker.is_alive():self.fail('Broker already running')
        s=Path(self.sock)
        if s.exists():
            assert s.is_socket() and s.stat().st_uid==os.getuid()
            s.unlink() # only mock temp owned socket, never live WSL socket
        self.broker=CTX.Process(target=broker_subprocess,args=(self.sock,str(self.db),str(self.main),str(self.ledger),self.server.server_port))
        self.broker.start()
        client=bridge.BrokerClient(self.sock)
        self.tick_until(lambda: self._probe(client))

    def _probe(self,client):
        try:return client.probe()
        except (OSError,ValueError,bridge.Stop):return False

    def start_worker(self):
        if self.worker and self.worker.is_alive():self.fail('Worker already running')
        self.worker=CTX.Process(target=worker_subprocess,args=(self.sock,str(self.db)))
        self.worker.start()
        self.tick_until(lambda: self.worker.is_alive())

    def enqueue(self):
        rid=str(uuid.uuid4())
        with iso.transaction(self.db) as c:
            self.assertEqual(iso.enqueue(c,rid,'Offline long-running fake task'),(202,'queued'))
        return rid

    def remote_done(self,message_id,result):
        with closing(open_db(self.main)) as c:
            c.execute("UPDATE agent_messages SET status='done',result=? WHERE id=?",(result,message_id))
            c.commit()

    def check_once(self,rid,text):
        state,mark,phase,remote_id=self.wrow(rid)
        self.assertEqual((state,mark,phase),('answered',text,'answered'))
        self.assertEqual(self.post_count(),1)
        with closing(open_db(self.db)) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM webchat_sync_events WHERE request_id=? AND role='assistant'",(rid,)).fetchone()[0],1)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM webchat_held_requests').fetchone()[0],7)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM f0_web_jobs WHERE seq BETWEEN 76 AND 82 AND state='queued'").fetchone()[0],7)
        return remote_id

    def test_01_worker_killed_during_long_task_then_resumes_without_duplicate_post(self):
        self.start_broker();self.start_worker()
        rid=self.enqueue()
        remote=self.tick_until(lambda: (row[3] if (row:=self.wrow(rid)) and row[2]=='submitted' else None))
        self.assertEqual(self.post_count(),1)
        self.worker.kill();self.worker.join(3)
        self.assertEqual(self.wrow(rid)[0],'seen')
        self.assertEqual(self.post_count(),1)
        self.start_worker()
        self.remote_done(remote,'WORKER_RESTORED_OK')
        self.tick_until(lambda: self.wrow(rid)[2]=='answered',timeout=12)
        self.check_once(rid,'WORKER_RESTORED_OK')
        print('PASS: SIGKILL worker during long task; resume by same ID, 1 POST, 1 answer')

    def test_02_broker_killed_while_main_is_working_then_recovers(self):
        self.start_broker();self.start_worker()
        rid=self.enqueue()
        remote=self.tick_until(lambda: (row[3] if (row:=self.wrow(rid)) and row[2]=='submitted' else None))
        self.broker.kill();self.broker.join(3)
        self.remote_done(remote,'BROKER_RESTORED_OK')
        self.assertEqual(self.wrow(rid)[2],'submitted')
        self.assertEqual(self.post_count(),1)
        # Let worker experience broker IPC outage before re-start.
        time.sleep(1.2)
        self.assertTrue(self.worker.is_alive())
        self.start_broker()
        self.tick_until(lambda: self.wrow(rid)[2]=='answered',timeout=12)
        self.check_once(rid,'BROKER_RESTORED_OK')
        print('PASS: SIGKILL broker during long task; same ID, 1 POST, 1 answer')

    def test_03_broker_crash_after_real_post_before_ack_never_replays(self):
        self.hold_responses=True
        self.start_broker();self.start_worker()
        rid=self.enqueue()
        self.tick_until(lambda: self.post_saved.is_set(),timeout=12)
        row=self.wrow(rid)
        self.assertEqual((row[0],row[2]),('handed_off','posting'))
        self.assertEqual(self.post_count(),1)
        self.broker.kill();self.broker.join(3)
        self.release_http.set()
        self.tick_until(lambda: (x:=self._phase(rid))=='uncertain',timeout=12)
        self.start_broker()
        time.sleep(1.3)
        self.assertEqual(self._phase(rid),'uncertain')
        self.assertEqual(self.post_count(),1)
        with closing(open_db(self.db)) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM webchat_tombstones WHERE request_id=?',(rid,)).fetchone()[0],1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM webchat_sync_events WHERE request_id=? AND role='assistant'",(rid,)).fetchone()[0],0)
        # Broker ledger survives restart. A second explicit fence attempt fails.
        with self.assertRaises(br.Stop):br.fence_once(self.ledger,rid)
        print('PASS: Broker crash after POST and before ACK -> UNCERTAIN, 1 POST, NO replay')

    def _phase(self,rid):
        with closing(open_db(self.db)) as c:
            row=c.execute('SELECT phase FROM webchat_main_links WHERE request_id=?',(rid,)).fetchone()
            return row[0] if row else None

    def test_04_worker_crash_in_posting_window_refuses_second_dispatch(self):
        self.hold_responses=True
        self.start_broker();self.start_worker()
        rid=self.enqueue()
        self.tick_until(lambda:self.post_saved.is_set(),timeout=12)
        self.worker.kill();self.worker.join(3)
        self.release_http.set()
        self.start_worker()
        self.tick_until(lambda:self._phase(rid)=='uncertain',timeout=9)
        self.assertEqual(self.post_count(),1)
        with self.assertRaises(br.Stop):br.fence_once(self.ledger,rid)
        print('PASS: Worker crash mid-POST -> UNCERTAIN, 1 POST, NO retry')

if __name__=='__main__':
    unittest.main(verbosity=2)
