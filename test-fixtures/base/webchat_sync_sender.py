#!/usr/bin/env python3
"""P5.9.6 Telegram presentation mirror. Independent Bot API outbox; never polls
Telegram and never interacts with tmux, Claude or Telegram's SQLite database.

At-most-once per outbox part. On ambiguous network outcome the mirror PAUSES;
no automatic retry and no fabricated claim of successful delivery.
"""
import argparse
import fcntl
import hashlib
import json
import os
import re
import secrets
import sqlite3
import ssl
import stat
import sys
import time
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path

import webchat_isolation as iso
from webchat_presentation import (split_batch, batch_attachments,
                                  verified_long_content, LONG_MARKER, web_attachment)

HOME=Path.home()
APP=HOME/'bela-f0-dev/webchat-p2'
PROD=HOME/'marveen'
ROOT=HOME/'bela-f0-dev/webchat-preview/project'
DB=ROOT/'store/f0-webchat.sqlite3'
TOKEN_RE=re.compile(r'[0-9]{4,}:[A-Za-z0-9_-]{20,}')
UID_RE=re.compile(r'[a-f0-9]{32}\.(?:txt|md|csv|json|pdf|png|jpg|jpeg|webp)')
MIME={'txt':'text/plain','md':'text/markdown','csv':'text/csv','json':'application/json',
      'pdf':'application/pdf','png':'image/png','jpg':'image/jpeg','jpeg':'image/jpeg','webp':'image/webp'}
MAX_PARTS=350

class SyncError(Exception):pass

def _file_secret(path):
    if path.is_symlink() or not path.is_file():raise SyncError('credential_path_invalid')
    s=path.stat()
    if s.st_uid not in (os.getuid(),0) or s.st_mode&0o077 or not 0<s.st_size<=131072:
        raise SyncError('credential_permissions_invalid')
    return path.read_text(encoding='utf8')

def _env_text(raw):
    result={}
    for line in raw.splitlines():
        if '=' not in line or line.lstrip().startswith('#'):continue
        k,v=line.split('=',1)
        result[k.strip()]=v.strip().strip('"').strip("'")
    return result

def credentials(home=HOME,prod=PROD):
    owner=_env_text(_file_secret(prod/'.env'))
    if owner.get('MAIN_AGENT_ID')!='bela':raise SyncError('main_agent_not_bela')
    chat=owner.get('LEDGER_OWNER_CHAT') or owner.get('ALLOWED_CHAT_ID') or ''
    if not re.fullmatch(r'[1-9][0-9]{1,19}',chat):raise SyncError('owner_chat_invalid')
    configs=[prod/'.channels-config/channels/telegram',home/'.claude/channels/telegram',prod/'.claude/channels/telegram']
    candidates=[]
    for root in configs:
        if root.is_symlink() or not root.is_dir():continue
        env,access=root/'.env',root/'access.json'
        if not env.is_file() or not access.is_file():continue
        try:
            token=_env_text(_file_secret(env)).get('TELEGRAM_BOT_TOKEN','')
            acl=json.loads(_file_secret(access))
            if not TOKEN_RE.fullmatch(token):continue
            if not isinstance(acl,dict) or not isinstance(acl.get('allowFrom'),list) or chat not in [str(v) for v in acl['allowFrom']]:continue
        except (OSError,SyncError,ValueError,TypeError):continue
        candidates.append((token,chat,root))
    if not candidates:raise SyncError('telegram_credentials_or_allowlist_unavailable')
    if len({t[0] for t in candidates})!=1:raise SyncError('conflicting_telegram_bot_tokens')
    return candidates[0]

def chunks(text,max_units=3500):
    if not isinstance(text,str) or not text:raise SyncError('text_empty')
    pieces=[];start=0
    while start<len(text):
        units=0;end=start;last_nl=-1
        while end<len(text):
            cost=2 if ord(text[end])>65535 else 1
            if units+cost>max_units:break
            units+=cost;end+=1
            if text[end-1]=='\n':last_nl=end
        if end<len(text) and last_nl>start+max_units//5:end=last_nl
        if end==start:raise SyncError('text_split_failed')
        pieces.append(text[start:end]);start=end
        if len(pieces)>MAX_PARTS:raise SyncError('too_many_telegram_parts')
    return pieces

def verified_upload(spec,root=ROOT):
    """Verify content through the same nofollow fd subsequently used for HTTP."""
    uid=spec.get('id','');size=spec.get('size');digest=spec.get('sha256')
    if not isinstance(uid,str) or not UID_RE.fullmatch(uid) or type(size)is not int or not 0<size<=30*1024*1024:
        raise SyncError('media_spec_invalid')
    if not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest):raise SyncError('media_hash_invalid')
    folder=root/'uploads'
    if folder.is_symlink() or not folder.is_dir() or folder.resolve()!=folder:raise SyncError('media_root_invalid')
    dfd=os.open(folder,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        fd=os.open(uid,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=dfd)
        try:
            before=os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_mode&0o077 or before.st_size!=size:raise SyncError('media_file_changed')
            out=bytearray()
            while len(out)<size:
                part=os.read(fd,min(1024*1024,size-len(out)))
                if not part:break
                out.extend(part)
            after=os.fstat(fd)
            if len(out)!=size or before.st_ino!=after.st_ino or before.st_mtime_ns!=after.st_mtime_ns:
                raise SyncError('media_file_changed')
            if hashlib.sha256(out).hexdigest()!=digest:raise SyncError('media_hash_changed')
            ext=uid.rsplit('.',1)[1]
            if ext=='png' and not bytes(out).startswith(b'\x89PNG\r\n\x1a\n'):raise SyncError('image_magic_invalid')
            if ext in ('jpg','jpeg') and not bytes(out).startswith(b'\xff\xd8\xff'):raise SyncError('image_magic_invalid')
            if ext=='webp' and (bytes(out[:4])!=b'RIFF' or bytes(out[8:12])!=b'WEBP'):raise SyncError('image_magic_invalid')
            return bytes(out)
        finally:os.close(fd)
    finally:os.close(dfd)

def request_parts(raw,root=ROOT):
    """Return displayed content and exact v2 manifest media, not a local path."""
    if not isinstance(raw,str) or not raw:raise SyncError('request_invalid')
    body,manifest=split_batch(raw)
    specs=[]
    if manifest:
        mf=Path(manifest)
        if mf.parent!=root/'uploads' or not UID_RE.fullmatch(mf.name) or not mf.name.endswith('.json'):
            raise SyncError('manifest_location_invalid')
        if mf.is_symlink() or not mf.is_file() or mf.stat().st_size>65536:raise SyncError('manifest_invalid')
        doc=json.loads(mf.read_text())
        entries=doc.get('files')
        if doc.get('version')!=2 or not isinstance(entries,list) or not 1<=len(entries)<=20:raise SyncError('manifest_version_invalid')
        seen=set()
        for entry in entries:
            if not isinstance(entry,dict):raise SyncError('manifest_entry_invalid')
            spec={k:entry.get(k) for k in ('id','size','sha256')}
            if spec['id'] in seen:raise SyncError('manifest_duplicate')
            seen.add(spec['id'])
            # Metadata is planned transactionally; exact bytes are reverified
            # through a single nofollow fd immediately before network sending.
            if not isinstance(spec['id'],str) or not UID_RE.fullmatch(spec['id']) or type(spec['size']) is not int or not 0<spec['size']<=30*1024*1024 or not isinstance(spec['sha256'],str) or not re.fullmatch('[a-f0-9]{64}',spec['sha256']):
                raise SyncError('manifest_media_metadata_invalid')
            if entry.get('name')!='bela-webchat-teljes-keres.txt':
                original=str(entry.get('name') or '')
                label=original.replace('\\','/').split('/')[-1]
                label=''.join(ch for ch in label if ord(ch)>=32 and ord(ch)!=127).strip()[:120]
                spec['display_name']=label or ('fajl.'+spec['id'].rsplit('.',1)[-1])
                specs.append(spec)
        if body==LONG_MARKER:body=verified_long_content(raw,root)
        if not body.strip():raise SyncError('empty_request')
    else:
        body,attachment=web_attachment(raw,root)
        if attachment:
            # Non-manifest media without hash evidence: fail closed, don't
            # leak absolute upload paths or claim synchronized delivery.
            raise SyncError('unverified_single_attachment')
    if not body.strip():raise SyncError('empty_request')
    out=[('text',v,'') for v in chunks('🖥️ Webchat · Te\n'+body)]
    if specs:
        for media in specs:
            out.extend(('text',v,'') for v in chunks('📎 Webchat-csatolmány: '+media['display_name']))
            out.append(('media','',json.dumps({k:media[k] for k in ('id','size','sha256')},separators=(',',':'))))
    if len(out)>MAX_PARTS:raise SyncError('too_many_telegram_parts')
    return out

def answer_parts(raw):
    if not isinstance(raw,str) or not raw.strip():raise SyncError('answer_invalid')
    return [('text',v,'') for v in chunks('🤖 Webchat · Béla\n'+raw)]

class BotAPI:
    def __init__(self,token,opener=None):
        if not TOKEN_RE.fullmatch(token):raise SyncError('token_format_invalid')
        self.token=token
        self.opener=opener or urllib.request.build_opener(urllib.request.ProxyHandler({}),
            type('NoRedirect',(urllib.request.HTTPRedirectHandler,),{'redirect_request':lambda self,*a:None})(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    def _call(self,method,data,headers,timeout):
        # Never bubble HTTPError URL; it embeds the bot token.
        req=urllib.request.Request('https://api.telegram.org/bot'+self.token+'/'+method,
                                   data=data,headers=headers,method='POST')
        try:
            with self.opener.open(req,timeout=timeout) as r:
                return r.status,json.loads(r.read(262145))
        except urllib.error.HTTPError as e:
            try:return e.code,json.loads(e.read(65537))
            except Exception:return e.code,{}
        except Exception:return 0,{}
    def text(self,chat,body):
        return self._call('sendMessage',json.dumps({'chat_id':chat,'text':body,'disable_web_page_preview':True},ensure_ascii=False).encode(),
                          {'Content-Type':'application/json; charset=utf-8'},20)
    def file(self,chat,kind,data,filename,mime):
        boundary='----BelaSync'+secrets.token_hex(16)
        if kind not in ('photo','document') or not re.fullmatch('webchat-[0-9]+\\.(?:txt|md|csv|json|pdf|png|jpg|jpeg|webp)',filename):
            raise SyncError('media_metadata_invalid')
        if not data or len(data)>30*1024*1024:raise SyncError('media_size_invalid')
        form=(f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{chat}\r\n'
              f'--{boundary}\r\nContent-Disposition: form-data; name="{kind}"; filename="{filename}"\r\n'
              f'Content-Type: {mime}\r\n\r\n').encode()
        payload=form+data+f'\r\n--{boundary}--\r\n'.encode()
        return self._call('sendPhoto' if kind=='photo' else 'sendDocument',payload,
                          {'Content-Type':'multipart/form-data; boundary='+boundary},50)

def ro_rows(db,sql,args=()):
    with closing(sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True,timeout=3)) as c:
        c.row_factory=sqlite3.Row
        return [dict(r) for r in c.execute(sql,args).fetchall()]

def pause(c,reason):
    c.execute("INSERT INTO webchat_sync_meta(key,value) VALUES('enabled','0') ON CONFLICT(key) DO UPDATE SET value='0'")
    c.execute("INSERT INTO webchat_sync_meta(key,value) VALUES('blocked_reason',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(reason,))

def migrate_schema(db=DB):
    with iso.transaction(db) as c:
        iso.schema(c)
        c.execute("INSERT OR IGNORE INTO webchat_sync_meta(key,value) VALUES('enabled','0')")
        c.execute("INSERT OR IGNORE INTO webchat_sync_meta(key,value) VALUES('blocked_reason','')")
        # Don't mirror any pre-existing Webchat job; only requests accepted
        # after this module's installation are inserted into the outbox.

def prepare_event(db,root=ROOT):
    with iso.transaction(db) as c:
        iso.schema(c)
        if c.execute("SELECT value FROM webchat_sync_meta WHERE key='enabled'").fetchone()[0]!='1':return False
        event=c.execute("SELECT * FROM webchat_sync_events WHERE state='new' ORDER BY id LIMIT 1").fetchone()
        if not event:return False
        try:
            parts=request_parts(event['content'],root) if event['role']=='user' else answer_parts(event['content']) if event['role']=='assistant' else [('text',v,'') for v in chunks('⚠️ Webchat · állapot\n'+event['content'])]
        except (ValueError,TypeError,KeyError,OSError,SyncError,UnicodeError):
            c.execute("UPDATE webchat_sync_events SET state='invalid',reason='presentation_unverified' WHERE id=?",(event['id'],))
            pause(c,'presentation_unverified')
            return True
        for i,(kind,text,meta) in enumerate(parts):
            c.execute("INSERT INTO webchat_sync_parts(event_id,ordinal,kind,text,media_json,state) VALUES(?,?,?,?,?,'pending')",
                      (event['id'],i+1,kind,text,meta))
        c.execute("UPDATE webchat_sync_events SET state='ready' WHERE id=?",(event['id'],))
        return True

def send_once(db,chat,api,root=ROOT):
    """At-most-once network effect with a committed 'sending' fence."""
    with iso.transaction(db) as c:
        iso.schema(c)
        flag=c.execute("SELECT value FROM webchat_sync_meta WHERE key='enabled'").fetchone()
        if not flag or flag[0]!='1':return False
        # Old claim after crash has unknown remote outcome, never retry.
        orphan=c.execute("SELECT id,event_id FROM webchat_sync_parts WHERE state='sending' LIMIT 1").fetchone()
        if orphan:
            c.execute("UPDATE webchat_sync_parts SET state='uncertain',reason='unconfirmed_after_restart' WHERE id=?",(orphan['id'],))
            c.execute("UPDATE webchat_sync_events SET state='uncertain',reason='unconfirmed_after_restart' WHERE id=?",(orphan['event_id'],))
            pause(c,'unconfirmed_after_restart')
            return False
        part=c.execute("""SELECT p.*,e.role,e.request_id FROM webchat_sync_parts p
                       JOIN webchat_sync_events e ON p.event_id=e.id
                       WHERE p.state='pending' AND e.state='ready'
                       ORDER BY e.id,p.ordinal LIMIT 1""").fetchone()
        if not part:return False
        # Media bytes must be verified before the irreversible send intent.
        data=None;kind=part['kind'];spec={}
        try:
            if kind=='media':
                spec=json.loads(part['media_json'])
                data=verified_upload(spec,root)
        except (OSError,ValueError,SyncError,TypeError):
            c.execute("UPDATE webchat_sync_parts SET state='invalid',reason='media_changed' WHERE id=?",(part['id'],))
            c.execute("UPDATE webchat_sync_events SET state='invalid',reason='media_changed' WHERE id=?",(part['event_id'],))
            pause(c,'media_changed')
            return False
        c.execute("UPDATE webchat_sync_parts SET state='sending',claimed_at=?,chat_id=? WHERE id=? AND state='pending'",
                  (int(time.time()),chat,part['id']))
        pid=part['id'];eid=part['event_id'];body=part['text']
    # Network IO outside transaction. We intentionally do not retry 0/429/5xx;
    # Telegram may have received a message despite a lost HTTP response.
    status=0;reply={};method='sendMessage'
    try:
        if kind=='text':status,reply=api.text(chat,body)
        else:
            ext=spec['id'].rsplit('.',1)[1]
            media_type='photo' if ext in ('jpg','jpeg','png','webp') and len(data)<=10*1024*1024 else 'document'
            method='sendPhoto' if media_type=='photo' else 'sendDocument'
            status,reply=api.file(chat,media_type,data,'webchat-'+str(pid)+'.'+ext,MIME[ext])
    except Exception:
        status=0;reply={}
    if not isinstance(reply,dict):reply={}
    response=reply.get('result')
    mid=response.get('message_id') if isinstance(response,dict) else None
    actual_chat=(response.get('chat') or {}).get('id') if isinstance(response,dict) else None
    valid=bool(status==200 and reply.get('ok') is True and type(mid)is int and mid>0
               and str(actual_chat)==chat and (kind!='text' or response.get('text')==body)
               and (kind!='media' or (response.get('photo') if method=='sendPhoto' else response.get('document'))))
    reason='confirmed' if valid else ('remote_rejected' if status in (400,401,403) and reply.get('ok') is False else 'transport_unconfirmed')
    with iso.transaction(db) as c:
        res=c.execute("UPDATE webchat_sync_parts SET state=?,telegram_message_id=?,reason=? WHERE id=? AND state='sending'",
                      ('confirmed' if valid else 'uncertain',mid if valid else None,reason,pid))
        if res.rowcount!=1:raise SyncError('delivery_claim_changed')
        if not valid:
            c.execute("UPDATE webchat_sync_events SET state='uncertain',reason=? WHERE id=?",(reason,eid))
            pause(c,reason)
        elif not c.execute("SELECT 1 FROM webchat_sync_parts WHERE event_id=? AND state!='confirmed' LIMIT 1",(eid,)).fetchone():
            c.execute("UPDATE webchat_sync_events SET state='done',reason='' WHERE id=?",(eid,))
        # Confirmed parts are immutable; no poller to echo Telegram bot messages.
    return True

def status(db=DB):
    return ro_rows(db,"""SELECT e.request_id,e.role,e.state,e.reason,
      (SELECT COUNT(*) FROM webchat_sync_parts p WHERE p.event_id=e.id) AS parts,
      (SELECT COUNT(*) FROM webchat_sync_parts p WHERE p.event_id=e.id AND p.state='confirmed') AS confirmed
      FROM webchat_sync_events e ORDER BY e.id DESC LIMIT 25""")

def loop(db=DB,root=ROOT,api_factory=BotAPI,once=False):
    os.umask(0o077)
    lock=APP/'webchat-telegram-mirror.lock'
    fd=os.open(lock,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise SystemExit('Another Webchat mirror is active')
    token,chat,location=credentials()
    api=api_factory(token)
    last_send=0
    while True:
        rows=ro_rows(db,"SELECT value FROM webchat_sync_meta WHERE key='enabled'")
        if not rows or rows[0]['value']!='1':
            if once:return
            time.sleep(2);continue
        if prepare_event(db,root):continue
        if last_send:time.sleep(max(0,1-(time.monotonic()-last_send)))
        more=send_once(db,chat,api,root)
        if more:last_send=time.monotonic()
        if once or not more:
            if once:return
            time.sleep(2)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--check',action='store_true')
    ap.add_argument('--once',action='store_true')
    a=ap.parse_args()
    try:
        if a.check:
            _,chat,loc=credentials()
            print('CHECK OK: Telegram Bot API sender owner allowlisted, source:',loc,'chat ID: verified (redacted)')
            return
        loop(once=a.once)
    except (SyncError,OSError,sqlite3.Error,ValueError) as exc:
        # Error codes only, never token-bearing URLs or raw messages.
        print('STOP: Webchat mirror configuration/dispatch unavailable:',type(exc).__name__,file=sys.stderr)
        raise SystemExit(2)

if __name__=='__main__':main()
