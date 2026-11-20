#!/usr/bin/env python3
"""Webchat-only HTTP and read-only Telegram history projection. NO tmux/Claude dispatch."""
import argparse, atexit, fcntl, json, os, queue, shutil, signal, sqlite3, subprocess, sys, threading, time
from contextlib import closing
from pathlib import Path
import source_guard
from webchat_presentation import merge_timeline,decorate_uploads,display_status
from preview_media import PhotoIndex
from preview_video import VideoIndex,token_from
from preview_outbound_video import OutboundVideoIndex
from preview_outbound_files import OutboundFileIndex
from preview_outbound_photo import OutboundPhotoIndex,merge_confirmed_photos
import webchat_isolation as iso

HERE=Path(__file__).resolve().parent
HOME=Path.home()
DEV=HOME/'bela-f0-dev';SRC=DEV/'source';PROD=HOME/'marveen'
PREVIEW=DEV/'webchat-preview';PROJECT=PREVIEW/'project';DB=PROJECT/'store/f0-webchat.sqlite3'

def atomic_json(path,obj):
    import tempfile
    fd,tmp=tempfile.mkstemp(prefix='.timeline-',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf8') as f:
            json.dump(obj,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
        os.chmod(tmp,0o600);os.replace(tmp,path)
    finally:Path(tmp).unlink(missing_ok=True)

def ro(db,sql,args=()):
    with closing(sqlite3.connect(Path(db).resolve().as_uri()+'?mode=ro',uri=True,timeout=1)) as c:
        c.execute('PRAGMA query_only=ON')
        c.row_factory=sqlite3.Row
        return [dict(x) for x in c.execute(sql,args).fetchall()]

class Projection:
    def __init__(self,chat):
        self.chat=chat;self.last=[];self.gallery=[];self.video_gallery=[]
        self.ledger=PROD/'store/claudeclaw.db'
        self.tdir=HOME/'.claude/projects/-home-kisss-marveen'
        roots=[PROD/'.channels-config/channels/telegram/inbox',HOME/'.claude/channels/telegram/inbox',PROD/'.claude/channels/telegram/inbox']
        media=PROJECT/'media';media.mkdir(mode=0o700,exist_ok=True)
        self.photos=PhotoIndex(chat,self.tdir,roots,media)
        self.videos=VideoIndex(media,token_from([p.parent/'.env' for p in roots]+[PROD/'.env']))
        self.files=OutboundFileIndex(chat,self.tdir,media)
        self.outphotos=OutboundPhotoIndex(chat,self.tdir,None,media)
        atexit.register(self.videos.close)
        atexit.register(self.files.close)

    def snapshot(self):
        jobs=ro(DB,'SELECT seq,request_id,request_text,state,markdown,telegram_state,created_at,updated_at FROM f0_web_jobs ORDER BY seq')
        waiting=0
        for job in jobs:
            job['webchat_isolated']=job['seq']>79
            if job['state']=='queued':waiting+=1;job['queue_position']=waiting
            proof=PREVIEW/(job['request_id']+'.delivery.json')
            if proof.is_file() and not proof.is_symlink():
                try:
                    info=json.loads(proof.read_text());job['delivery']=info
                except (ValueError,OSError):pass
            job.update(display_status(job))
        problem=''
        try:
            rows=ro(self.ledger,"""SELECT id,direction,text,created_at,message_id,attachment_kind,attachment_file_id
            FROM conversation_log WHERE agent_id IN (?,?) AND chat_id=? AND direction IN ('in','out') ORDER BY id DESC LIMIT 80""",
            ('bela','bela:webchat-mirror',self.chat))
            self.last=[{'id':r['id'],'direction':r['direction'],'text':str(r['text'] or '')[:16000],
                        'truncated':len(str(r['text'] or ''))>16000,'created_at':r['created_at'],'message_id':r['message_id'],
                        'attachment_kind':r['attachment_kind'],'attachment_file_id':r['attachment_file_id']} for r in reversed(rows)]
            self.photos.enrich(self.last)
            for item in self.last:self.videos.enrich(item)
            found=self.photos.scan();self.gallery=[]
            for mid,paths in sorted(found.items(),key=lambda kv:int(kv[0]),reverse=True):
                if len(paths)!=1:continue
                matches=ro(self.ledger,"SELECT id,created_at,text FROM conversation_log WHERE agent_id='bela' AND chat_id=? AND direction='in' AND message_id=?",(self.chat,mid))
                if len(matches)!=1:continue
                name=self.photos.copy_photo(next(iter(paths)))
                if name:self.gallery.append({'key':'t:'+str(matches[0]['id']),'image':name,'at':matches[0]['created_at'],'text':str(matches[0]['text'] or '')[:1000]})
                if len(self.gallery)>=20:break
            self.video_gallery=[]
            for row in ro(self.ledger,"SELECT id,created_at,text,attachment_file_id FROM conversation_log WHERE agent_id='bela' AND chat_id=? AND direction='in' AND attachment_kind IN ('video','video_note','animation') ORDER BY id DESC LIMIT 20",(self.chat,)):
                name,state=self.videos.get(row['attachment_file_id'])
                if name:self.video_gallery.append({'key':'t:'+str(row['id']),'video':name,'at':row['created_at'],'text':str(row['text'] or '')[:1000]})
        except (OSError,sqlite3.Error,ValueError):problem='Telegram-napló átmenetileg nem elérhető; utolsó ismert nézet.'
        failure_codes={r['request_id']:r['failure_code'] for r in ro(DB,'SELECT request_id,failure_code FROM webchat_failure_events')}
        archived=[json.loads(r['job_json']) for r in ro(DB,'SELECT job_json FROM preview_closed_requests')]
        for j in archived:
            if j.get('request_id') in failure_codes:j['failure_code']=failure_codes[j['request_id']]
        # Historical closed owner records are not retryable work.
        for j in archived:
            if j['state'] not in ('failed_unverified','legacy_75_failed_unverified'):j['state']='closed'
        canceled=[]
        try:
            for r in ro(DB,'SELECT job_json,reason FROM preview_cancelled_details ORDER BY cancelled_at'):
                j=json.loads(r['job_json']);j['state']='cancelled_by_user' if r['reason']=='cancelled_by_user' else 'cancelled_too_long';canceled.append(j)
        except sqlite3.Error:pass
        entries=merge_timeline(jobs+archived+canceled,self.last)
        # The outbound Bot API is its own independent, auditable queue; no
        # assumption that Telegram's channel ledger receives bot-sent messages.
        sync_rows=ro(DB,"""SELECT e.request_id,e.role,e.state,e.reason,
              (SELECT COUNT(*) FROM webchat_sync_parts p WHERE p.event_id=e.id) parts,
              (SELECT COUNT(*) FROM webchat_sync_parts p WHERE p.event_id=e.id AND p.state='confirmed') confirmed
              FROM webchat_sync_events e""")
        sync_lookup={(r['request_id'],r['role']):r for r in sync_rows}
        for job in jobs:
            job['sync_in']=sync_lookup.get((job['request_id'],'user'))
            job['sync_out']=sync_lookup.get((job['request_id'],'assistant'))
        for item in entries:
            key=item['key']
            if not key.startswith('w:'):continue
            rid=key.split(':')[1]
            role=('user' if key.endswith(':in') else
                  'assistant' if key.endswith(':out') else
                  'system' if key.endswith(':status') else None)
            if role:
                sync=sync_lookup.get((rid,role))
                if sync:
                    item['sync']=sync
                    if sync['state']=='done':item['source']='web+telegram'
        sync_info=dict((r['key'],r['value']) for r in ro(DB,"SELECT key,value FROM webchat_sync_meta"))

        uploads=decorate_uploads(entries,PROJECT)
        out_items=self.files.entries();photolist=self.outphotos.entries()
        existing={str(t['message_id']):'t:'+str(t['id']) for t in self.last if t.get('direction')=='out' and t.get('message_id')}
        bykey={e['key']:e for e in entries};outvideo=[]
        for item in out_items:
            target=bykey.get(existing.get(item.get('telegram_message_id')))
            if target:
                for field in ('video','video_state','attachment_file','attachment_name','attachment_bytes','attachment_state','attachment'):
                    if field in item:target[field]=item[field]
            else:entries.append(item);target=item
            if item.get('video'):outvideo.append({'key':target['key'],'video':item['video'],'at':item['at'],'text':'Elküldött videó'})
        outgoing=merge_confirmed_photos(entries,self.last,photolist)
        entries.sort(key=lambda e:(e['at'],e['order'],e['seq'],e['key']))
        running=sum(j['state'] in ('handed_off','seen') for j in jobs)
        held_ids={r['request_id'] for r in ro(DB,'SELECT request_id FROM webchat_held_requests')}
        held=sum(j['request_id'] in held_ids and j['state']=='queued' for j in jobs)
        enabled=iso.enabled(DB)
        return {'jobs':jobs,'entries':entries,'queue':{'waiting':waiting,'limit':50,'held':held,'active':running,'blocked':False},
                'problem':problem,'automatic':enabled,'sync_enabled':sync_info.get('enabled')=='1','sync_problem':sync_info.get('blocked_reason',''),'gallery':sorted(self.gallery+uploads+outgoing,key=lambda e:(e['at'],e['key']),reverse=True)[:20],
                'video_gallery':sorted(self.video_gallery+outvideo,key=lambda e:(e['at'],e['key']),reverse=True)[:20],
                'service':True,'generated_at':int(time.time()),'transport_capacity':None,
                'session_binding':{'verified':False,'source':'marveen-main-bus','reason':'pilot_requires_live_end_to_end_verification','registry_reason':'not_applicable'}}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--service',action='store_true');args=ap.parse_args()
    if not args.service:raise SystemExit('P591 requires user service auth; interactive old mode disabled')
    os.umask(0o077)
    source_guard.verify(SRC,HERE/'vendor-guard.json')
    for p in (PREVIEW,PROJECT,PROJECT/'scripts',PROJECT/'scripts/hooks',PROJECT/'store'):
        if p.is_symlink():raise RuntimeError('Unsafe Webchat root')
        p.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd=os.open(PREVIEW/'preview.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise SystemExit('Webchat HTTP lock in use')
    auth=PREVIEW/'service-auth.json'
    if auth.is_symlink() or not auth.is_file() or auth.stat().st_mode&0o077:raise RuntimeError('Unsafe existing owner auth')
    if not DB.is_file() or DB.is_symlink():raise RuntimeError('Missing Webchat queue')
    if not ro(DB,"SELECT value FROM webchat_isolation_meta WHERE key='migration'"):
        raise RuntimeError('Migration not installed')
    cfg={}
    for line in (PROD/'.env').read_text().splitlines():
        if '=' in line:
            k,v=line.split('=',1)
            if k in ('MAIN_AGENT_ID','LEDGER_OWNER_CHAT','ALLOWED_CHAT_ID'):cfg[k]=v.strip().strip('"').strip("'")
    import re
    chat=cfg.get('LEDGER_OWNER_CHAT') or cfg.get('ALLOWED_CHAT_ID') or ''
    if cfg.get('MAIN_AGENT_ID')!='bela' or not re.fullmatch(r'-?[1-9][0-9]*',chat):raise RuntimeError('Invalid owner chat')
    for source,dest in ((SRC/'scripts/hooks/webchat_f0_queue.py',PROJECT/'scripts/hooks/webchat_f0_queue.py'),
                         (HERE/'webchat-api-bridge.py',PROJECT/'scripts/hooks/webchat-f0-api-bridge.py'),
                         (HERE/'webchat_isolation.py',PROJECT/'scripts/hooks/webchat_isolation.py')):
        if dest.is_symlink():raise RuntimeError('Unsafe bridge destination')
        tmp=dest.with_suffix('.new');shutil.copyfile(source,tmp);os.chmod(tmp,0o600);os.replace(tmp,dest)
    proj=Projection(chat)
    def publish():
        try:atomic_json(PROJECT/'timeline.json',proj.snapshot())
        except Exception as exc:print('[SNAPSHOT] '+type(exc).__name__,flush=True)
    # Initial timeline without any Telegram disk reads, so HTTP can start rapidly.
    jobs=ro(DB,'SELECT seq,request_id,request_text,state,markdown,telegram_state,created_at,updated_at FROM f0_web_jobs ORDER BY seq')
    for job in jobs:job['webchat_isolated']=job['seq']>79
    initial_held={r['request_id'] for r in ro(DB,'SELECT request_id FROM webchat_held_requests')}
    atomic_json(PROJECT/'timeline.json',{'jobs':jobs,'entries':merge_timeline(jobs,[]),'queue':{'waiting':sum(j['state']=='queued' for j in jobs),'active':0,'limit':50,'held':sum(j['request_id'] in initial_held and j['state']=='queued' for j in jobs),'blocked':False},
        'problem':'','automatic':iso.enabled(DB),'sync_enabled':False,'sync_problem':'startup','gallery':[],'video_gallery':[],'service':True,'generated_at':int(time.time()),'transport_capacity':None,
        'session_binding':{'verified':False,'source':'marveen-main-bus','reason':'pilot_requires_live_end_to_end_verification','registry_reason':'none'}})
    stop=threading.Event()
    def refresher():
        while not stop.wait(4):publish()
    threading.Thread(target=refresher,daemon=True).start()
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','LANG','LC_ALL','USER','LOGNAME')}
    env['BELA_SERVICE_AUTH']=str(auth)
    srv=subprocess.Popen(['node',str(HERE/'bela-preview-server.mjs'),str(SRC),str(PROJECT)],cwd=SRC,env=env,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
    def cleanup(*args):
        stop.set()
        if srv.poll() is None:
            srv.terminate()
            try:srv.wait(timeout=5)
            except subprocess.TimeoutExpired:srv.kill();srv.wait()
    atexit.register(cleanup)
    signal.signal(signal.SIGTERM,lambda *_:sys.exit(0))
    ready=queue.Queue()
    def stdout():
        for line in srv.stdout:
            if line.startswith('PREVIEW_READY '):ready.put(line.strip())
        ready.put(None)
    threading.Thread(target=stdout,daemon=True).start()
    try:ready_line=ready.get(timeout=45)
    except queue.Empty:raise RuntimeError('HTTP startup timeout')
    if not ready_line:raise RuntimeError('HTTP failed')
    print('[WEBCHAT] HTTP ready, Telegram read projection; independent outbound mirror',flush=True)
    while srv.poll() is None:time.sleep(1)
    raise SystemExit('HTTP process exited')

if __name__=='__main__':main()
