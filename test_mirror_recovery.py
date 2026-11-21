#!/usr/bin/env python3
"""P5.9.8.3 isolated Bot API recovery regression/fault tests. NO network or WSL services."""
import os
import json
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from contextlib import closing

import webchat_isolation as iso
import webchat_sync_sender as m

CHAT='12345678'

class Bot:
    def __init__(self,send='ok',probes=None):
        self.mode=send
        self.probes=list(probes or [])
        self.calls=[];self.probe_calls=0
    def text(self,chat,body):
        self.calls.append(('text',body))
        if self.mode=='timeout':return 0,{}
        if self.mode=='throw':raise TimeoutError('no network')
        if self.mode=='5xx':return 503,{'ok':False}
        if self.mode=='429':return 429,{'ok':False,'parameters':{'retry_after':35}}
        if self.mode=='403':return 403,{'ok':False,'description':'forbidden'}
        if self.mode=='400':return 400,{'ok':False}
        if self.mode=='401':return 401,{'ok':False}
        if self.mode=='badbody':return 200,{'ok':True,'result':{'message_id':999,'chat':{'id':int(chat)},'text':'WRONG'}}
        if self.mode=='badid':return 200,{'ok':True,'result':{'message_id':1,'chat':{'id':234},'text':body}}
        return 200,{'ok':True,'result':{'message_id':len(self.calls)+100,'chat':{'id':int(chat)},'text':body}}
    def file(self,chat,kind,data,filename,mime):
        self.calls.append(('file',kind))
        return 200,{'ok':True,'result':{'message_id':len(self.calls)+100,'chat':{'id':int(chat)},
                     'photo':[{'file_id':'f'}] if kind=='photo' else None,'document':{'file_id':'f'} if kind=='document' else None}}
    def probe(self):
        self.probe_calls+=1
        return self.probes.pop(0) if self.probes else (200,{'ok':True,'result':{'id':12345}})

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='bela-p5983-')
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        (self.root/'store').mkdir()
        (self.root/'uploads').mkdir(mode=0o700)
        self.db=self.root/'store'/'test.sqlite3'
        with sqlite3.connect(self.db) as c:
            iso.schema(c)
            c.execute("INSERT INTO webchat_sync_meta VALUES('enabled','1')")
            c.execute("INSERT INTO webchat_sync_meta VALUES('blocked_reason','')")
        m.migrate_schema(self.db)
    def event(self,body='Szia',role='user'):
        rid=str(uuid.uuid4())
        with iso.transaction(self.db) as c:
            cur=c.execute('INSERT INTO webchat_sync_events(request_id,role,content,state,created_at) VALUES(?,?,?,\'new\',?)',(rid,role,body,int(time.time())))
            return cur.lastrowid
    def row(self,eid):
        return m.ro_rows(self.db,"SELECT state,reason FROM webchat_sync_events WHERE id=?",(eid,))[0]
    def parts(self,eid):
        return m.ro_rows(self.db,"SELECT ordinal,state,reason FROM webchat_sync_parts WHERE event_id=? ORDER BY ordinal",(eid,))
    def flag(self,k):
        return m.ro_rows(self.db,"SELECT value FROM webchat_sync_meta WHERE key=?",(k,))[0]['value']
    def ready(self,eid):
        self.assertTrue(m.prepare_event(self.db,self.root))
        self.assertEqual(self.row(eid)['state'],'ready')
    def due(self):
        with iso.transaction(self.db) as c:
            c.execute("UPDATE webchat_sync_meta SET value='0' WHERE key='next_retry_at'")

    def test_normal_confirmed(self):
        eid=self.event();self.ready(eid)
        bot=Bot();self.assertTrue(m.send_once(self.db,CHAT,bot,self.root))
        self.assertEqual(self.row(eid)['state'],'done')
        self.assertEqual(self.flag('enabled'),'1')
        self.assertEqual(self.flag('recovery_state'),'healthy')
        self.assertEqual(self.flag('last_http_status'),'200')
        self.assertEqual(len(bot.calls),1)
    def check_quarantined_then_next(self,mode):
        a=self.event('ONE');b=self.event('TWO')
        self.ready(a);self.ready(b)
        bot=Bot(mode)
        self.assertTrue(m.send_once(self.db,CHAT,bot,self.root))
        self.assertEqual(self.row(a)['state'],'uncertain')
        self.assertEqual(self.flag('enabled'),'1')
        self.assertEqual(self.flag('recovery_state'),'recovering')
        self.assertFalse(m.send_once(self.db,CHAT,bot,self.root))
        self.assertEqual(len(bot.calls),1)
        self.due();bot.mode='ok'
        self.assertTrue(m.send_once(self.db,CHAT,bot,self.root))
        self.assertEqual(self.row(b)['state'],'done')
        self.assertEqual(self.row(a)['state'],'uncertain')
        self.assertEqual(len(bot.calls),2)
        self.assertEqual(bot.probe_calls,1)
        self.assertFalse(m.send_once(self.db,CHAT,bot,self.root))
        return a,b
    def test_timeout_does_not_stall_next(self):self.check_quarantined_then_next('timeout')
    def test_exception_does_not_stall_next(self):self.check_quarantined_then_next('throw')
    def test_5xx_does_not_stall_next(self):self.check_quarantined_then_next('5xx')
    def test_malformed_success_does_not_stall_next(self):self.check_quarantined_then_next('badbody')
    def test_wrong_chat_does_not_stall_next(self):self.check_quarantined_then_next('badid')
    def test_rate_limit_honors_retry_after(self):
        a=self.event('ONE');b=self.event('TWO');self.ready(a);self.ready(b)
        bot=Bot('429')
        self.assertTrue(m.send_once(self.db,CHAT,bot,self.root))
        self.assertGreaterEqual(int(self.flag('next_retry_at'))-int(time.time()),34)
        self.assertEqual(self.flag('last_failure_code'),'telegram_429_unconfirmed')
        self.assertEqual(self.flag('last_http_status'),'429')
        self.assertFalse(m.send_once(self.db,CHAT,bot,self.root))
        bot.mode='ok';self.due()
        self.assertTrue(m.send_once(self.db,CHAT,bot,self.root))
        self.assertEqual(self.row(a)['state'],'uncertain')
        self.assertEqual(self.row(b)['state'],'done')
    def test_probe_failure_delays_fresh_event(self):
        a=self.event('ONE');b=self.event('TWO');self.ready(a);self.ready(b)
        bot=Bot('timeout',probes=[(503,{}),(200,{'ok':True,'result':{'id':777}})])
        m.send_once(self.db,CHAT,bot,self.root);self.due()
        self.assertFalse(m.send_once(self.db,CHAT,bot,self.root))
        self.assertEqual(len(bot.calls),1)
        self.assertEqual(self.flag('recovery_state'),'recovering')
        self.assertGreater(int(self.flag('next_retry_at')),int(time.time()))
        self.due();bot.mode='ok'
        self.assertTrue(m.send_once(self.db,CHAT,bot,self.root))
        self.assertEqual(len(bot.calls),2)
        self.assertEqual(bot.probe_calls,2)
    def test_probe_401_blocks_until_operator(self):
        a=self.event('ONE');b=self.event('TWO');self.ready(a);self.ready(b)
        bot=Bot('timeout',probes=[(401,{'ok':False})])
        m.send_once(self.db,CHAT,bot,self.root);self.due()
        self.assertFalse(m.send_once(self.db,CHAT,bot,self.root))
        self.assertEqual(self.flag('enabled'),'0')
        self.assertEqual(self.flag('recovery_state'),'blocked')
        self.assertEqual(self.row(b)['state'],'ready')
    def test_4xx_permanent_blocks(self):
        for mode in ('400','401','403'):
            with self.subTest(mode=mode):
                with iso.transaction(self.db) as c:
                    c.execute("UPDATE webchat_sync_meta SET value='1' WHERE key='enabled'")
                    m.mark_healthy(c)
                eid=self.event(mode);self.ready(eid)
                bot=Bot(mode)
                self.assertTrue(m.send_once(self.db,CHAT,bot,self.root))
                self.assertEqual(self.flag('enabled'),'0')
                self.assertEqual(self.row(eid)['state'],'uncertain')
    def test_one_part_uncertain_holds_remaining_of_long_message(self):
        a=self.event('x'*11000);b=self.event('NEXT');self.ready(a);self.ready(b)
        self.assertGreater(len(self.parts(a)),1)
        bot=Bot('timeout');m.send_once(self.db,CHAT,bot,self.root)
        states=[p['state'] for p in self.parts(a)]
        self.assertEqual(states[0],'uncertain')
        self.assertTrue(all(x=='held' for x in states[1:]))
        self.due();bot.mode='ok'
        m.send_once(self.db,CHAT,bot,self.root)
        self.assertEqual(self.row(b)['state'],'done')
        self.assertEqual(len(bot.calls),2)
    def test_completed_chunk_never_replayed_after_subsequent_failure(self):
        a=self.event('x'*8000);b=self.event('NEXT');self.ready(a);self.ready(b)
        bot=Bot();m.send_once(self.db,CHAT,bot,self.root)
        bot.mode='timeout';m.send_once(self.db,CHAT,bot,self.root)
        self.assertEqual([p['state'] for p in self.parts(a)],['confirmed','uncertain','held'])
        self.due();bot.mode='ok';m.send_once(self.db,CHAT,bot,self.root)
        self.assertEqual(self.row(b)['state'],'done')
        self.assertEqual(len(bot.calls),3)
    def test_orphan_claim_is_quarantined_and_next_moves(self):
        a=self.event('ONE');b=self.event('TWO');self.ready(a);self.ready(b)
        with iso.transaction(self.db) as c:
            c.execute("UPDATE webchat_sync_parts SET state='sending' WHERE event_id=?",(a,))
        bot=Bot()
        self.assertFalse(m.send_once(self.db,CHAT,bot,self.root))
        self.assertEqual(self.row(a)['state'],'uncertain')
        self.assertEqual(self.flag('enabled'),'1')
        self.assertFalse(bot.calls)
        self.due();m.send_once(self.db,CHAT,bot,self.root)
        self.assertEqual(self.row(b)['state'],'done')
        self.assertEqual(len(bot.calls),1)
    def test_older_uncertain_event_untouched(self):
        a=self.event('OLD');self.ready(a)
        bot=Bot('timeout');m.send_once(self.db,CHAT,bot,self.root)
        stored=self.parts(a)
        m.migrate_schema(self.db)
        self.assertEqual(self.parts(a),stored)
        self.assertEqual(self.row(a)['state'],'uncertain')
    def test_invalid_presentation_isolated(self):
        a=self.event('');b=self.event('NEXT');self.assertTrue(m.prepare_event(self.db,self.root))
        self.assertEqual(self.row(a)['state'],'invalid')
        self.assertEqual(self.flag('enabled'),'1')
        self.ready(b)
        self.assertTrue(m.send_once(self.db,CHAT,Bot(),self.root))
        self.assertEqual(self.row(b)['state'],'done')
    def test_100_events_after_uncertain(self):
        a=self.event('BAD');self.ready(a)
        bot=Bot('timeout');m.send_once(self.db,CHAT,bot,self.root)
        ids=[self.event('BATCH-'+str(i)) for i in range(100)]
        for eid in ids:self.ready(eid)
        self.due();bot.mode='ok'
        for _ in ids:self.assertTrue(m.send_once(self.db,CHAT,bot,self.root))
        self.assertEqual(self.row(a)['state'],'uncertain')
        self.assertEqual(sum(self.row(i)['state']=='done' for i in ids),100)
        self.assertEqual(len(bot.calls),101)
    def test_restart_wait_persists_on_disk(self):
        a=self.event('ONE');self.ready(a)
        bot=Bot('timeout');m.send_once(self.db,CHAT,bot,self.root)
        snap=self.flag('next_retry_at')
        m.migrate_schema(self.db)
        self.assertEqual(self.flag('next_retry_at'),snap)
        self.assertFalse(m.send_once(self.db,CHAT,Bot(),self.root))
    def test_sigkill_during_inflight_network_fences_replay(self):
        a=self.event('CRASH');b=self.event('SAFE');self.ready(a);self.ready(b)
        code='''import sys,time,webchat_sync_sender as m
class Blackhole:
 def text(self,*a):time.sleep(60)
m.send_once(sys.argv[1],'12345678',Blackhole(),sys.argv[2])'''
        proc=subprocess.Popen([sys.executable,'-c',code,str(self.db),str(self.root)],
                              cwd=str(Path(__file__).parent),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                if self.parts(a)[0]['state']=='sending':break
                time.sleep(.03)
            self.assertEqual(self.parts(a)[0]['state'],'sending')
            proc.kill();proc.wait(timeout=5)
            bot=Bot()
            self.assertFalse(m.send_once(self.db,CHAT,bot,self.root))
            self.assertEqual(self.row(a)['state'],'uncertain')
            self.assertFalse(bot.calls)
            self.due();m.send_once(self.db,CHAT,bot,self.root)
            self.assertEqual(self.row(b)['state'],'done')
            self.assertEqual(len(bot.calls),1)
        finally:
            if proc.poll() is None:proc.kill();proc.wait(timeout=5)
    def test_sender_does_not_touch_telegram_inbound(self):
        src=Path(m.__file__).read_text()
        self.assertNotIn('getUpdates(',src)
        self.assertNotIn('tmux send-keys',src)
        self.assertNotIn('claudeclaw.db',src)

if __name__=='__main__':unittest.main(verbosity=2)
