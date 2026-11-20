#!/usr/bin/env python3
"""P5.9.8 core shared-main-session installer (used by unified release). Safe-off, targeted rollback.

Never stops/restarts Telegram or Marveen; never tmux-writes; never POSTs messages.
The separate main bridge sends messages only after explicit --enable.
"""
import argparse
import hashlib
import json
import os
import shutil
import stat
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from contextlib import closing
from datetime import datetime
from pathlib import Path

import webchat_isolation as iso
import webchat_main_bridge as bridge
import webchat_bridge_broker as broker

HOME=Path.home()
APP=HOME/'bela-f0-dev/webchat-p2'
DB=HOME/'bela-f0-dev/webchat-preview/project/store/f0-webchat.sqlite3'
MARVEEN=HOME/'marveen'
RELAY=MARVEEN/'agents/webchat-relay'
MARKER=RELAY/'.p597-owner-webchat-bridge'
HERE=Path(__file__).resolve().parent
DROPIN=HOME/'.config/systemd/user/bela-webchat-worker.service.d/30-p597-main.conf'
BROKER_UNIT=HOME/'.config/systemd/user/bela-webchat-broker.service'
BROKER_SOCK=HOME/'bela-f0-dev/webchat-preview/project/store/p597-main-broker.sock'
TG='bela-channels.service'
WEB='bela-webchat.service'
WORK='bela-webchat-worker.service'
MIRROR='bela-webchat-mirror.service'
BROKER='bela-webchat-broker.service'
FILES=('webchat_http.py','index.html','webchat_main_bridge.py','webchat_bridge_broker.py','app.js')
BASE=json.loads((HERE/'BASE_P5961_SHA256.json').read_text())
class Stop(Exception):pass


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def command(*args,timeout=25,check=True):
    p=subprocess.run(args,text=True,capture_output=True,timeout=timeout)
    if check and p.returncode:
        raise Stop('Command failed: '+str(args)+', exit '+str(p.returncode)+', stderr: '+p.stderr[-700:].replace('\n',' | '))
    return p.stdout.strip()

def props(unit,keys):
    return dict(s.split('=',1) for s in command('systemctl','--user','show',unit,*[v for k in keys for v in ('-p',k)]).splitlines() if '=' in s)

def telegram():
    s=props(TG,['ActiveState','MainPID','ControlGroup'])
    if s.get('ActiveState')!='active' or int(s.get('MainPID','0'))<=1:raise Stop('Telegram not active')
    panes=command('tmux','list-panes','-t','bela-channels','-F','#{pane_id} #{pane_pid}')
    if not panes:raise Stop('Telegram pane missing')
    return s|{'panes':panes}

def manifest():
    expected=json.loads((HERE/'MANIFEST_SHA256.json').read_text())
    for rel,digest in expected.items():
        path=Path(rel)
        if path.is_absolute() or '..' in path.parts or not (HERE/path).is_file() or (HERE/path).is_symlink() or sha(HERE/path)!=digest:
            raise Stop('Release package integrity failed: '+rel)
    for rel in FILES+('install_p597.py','test_p597.py','worker-dropin.conf','broker.service','BASE_P5961_SHA256.json'):
        if rel not in expected:raise Stop('Missing release item: '+rel)

def main_source():
    if MARVEEN.is_symlink() or not MARVEEN.is_dir():raise Stop('Marveen directory not trusted')
    head=command('git','-C',str(MARVEEN),'rev-parse','--short','HEAD')
    if head!='bfd79f75':raise Stop('Marveen source commit differs; manual review needed')
    expected={
        'src/web/routes/agents.ts':('drain-inbox','claimPendingForAgent('),
        'src/web/routes/messages.ts':("isOwnerSender",'isKnownAgent(sanitizeAgentIdent(from))','markMessageDone(id, result)','alreadyTerminal'),
        'src/web/agent-message-wrap.ts':('wrapAgentMessageForDelivery','msg_id:'),
        'src/web/message-router.ts':('if (isMainAgent)','sendPromptToSession(MAIN_CHANNELS_SESSION'),
        'src/web/agent-config.ts':('export function isKnownAgent(', 'statSync(dir).isDirectory()'),
        'src/team-trust.ts':('if (from === ctx.mainAgentId || to === ctx.mainAgentId) return true',),
    }
    for rel,marks in expected.items():
        file=MARVEEN/rel
        if file.is_symlink() or not file.is_file() or any(m not in file.read_text() for m in marks):raise Stop('Main Marveen source changed: '+rel)
    return head

def read_queue():
    if DB.is_symlink() or not DB.is_file():raise Stop('Webchat DB unsafe/missing')
    with closing(sqlite3.connect(DB.resolve().as_uri()+'?mode=ro',uri=True,timeout=3)) as c:
        c.execute('PRAGMA query_only=ON')
        enabled=c.execute("SELECT value FROM webchat_isolation_meta WHERE key='enabled'").fetchone()
        if not enabled or enabled[0] not in ('0','1'):raise Stop('Webchat dispatch flag unknown')
        active=c.execute("SELECT COUNT(*) FROM f0_web_jobs WHERE state IN ('handed_off','seen','unknown')").fetchone()[0]
        runs=c.execute('SELECT COUNT(*) FROM webchat_runs').fetchone()[0]
        queued=c.execute("SELECT seq,request_id FROM f0_web_jobs WHERE state='queued'").fetchall()
        held={v[0] for v in c.execute('SELECT request_id FROM webchat_held_requests')}
        links=0
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='webchat_main_links'").fetchone():
            links=c.execute('SELECT COUNT(*) FROM webchat_main_links').fetchone()[0]
        return {'enabled':enabled[0], 'active':active, 'runs':runs, 'unheld':[seq for seq,rid in queued if rid not in held],
                'queued':[seq for seq,_ in queued], 'links':links}

def relay_state(stage):
    if stage=='pre':
        if RELAY.exists() or RELAY.is_symlink() or DROPIN.exists() or DROPIN.is_symlink() or BROKER_UNIT.exists() or BROKER_UNIT.is_symlink() or BROKER_SOCK.exists() or BROKER_SOCK.is_symlink():
            raise Stop('Relay or P597 drop-in already exists; do not overwrite')
        if (APP/'webchat_main_bridge.py').exists() or (APP/'webchat_bridge_broker.py').exists():raise Stop('P597 bridge already installed')
    else:
        if not MARKER.is_file() or MARKER.is_symlink() or MARKER.read_text()!='P597 owner authenticated Webchat relay\n':
            raise Stop('Relay marker mismatch')
        if not DROPIN.is_file() or DROPIN.is_symlink() or sha(DROPIN)!=sha(HERE/'worker-dropin.conf'):
            raise Stop('Worker drop-in mismatch')
        if sha(APP/'webchat_main_bridge.py')!=sha(HERE/'webchat_main_bridge.py'):
            raise Stop('Main bridge hash mismatch')
        if BROKER_UNIT.is_symlink() or not BROKER_UNIT.is_file() or sha(BROKER_UNIT)!=sha(HERE/'broker.service'):
            raise Stop('Broker unit hash mismatch')
        if sha(APP/'webchat_bridge_broker.py')!=sha(HERE/'webchat_bridge_broker.py'):
            raise Stop('Broker code hash mismatch')

def services(stage='pre'):
    tg=telegram()
    web=props(WEB,['ActiveState','MainPID','ControlGroup','KillMode'])
    worker=props(WORK,['ActiveState','MainPID','ControlGroup','KillMode'])
    mirror=props(MIRROR,['ActiveState','MainPID','ControlGroup','KillMode'])
    if any(x.get('KillMode')!='control-group' for x in (web,worker,mirror)):
        raise Stop('Webchat service KillMode unsafe')
    inactive=[unit for unit,state in ((WEB,web),(WORK,worker),(MIRROR,mirror)) if state.get('ActiveState')!='active']
    if inactive:raise Stop('Webchat service not active: '+', '.join(inactive))
    if stage=='post':
        b=props(BROKER,['ActiveState','MainPID','ControlGroup','KillMode'])
        if b.get('ActiveState')!='active' or b.get('KillMode')!='control-group':raise Stop('Webchat broker service not active or unsafe')
        if b['ControlGroup']==tg['ControlGroup']:raise Stop('Broker shares Telegram cgroup')
    if any(x['ControlGroup']==tg['ControlGroup'] for x in (web,worker,mirror)):
        raise Stop('Shared Telegram/Webchat cgroup')
    for unit in ((WEB,WORK,MIRROR,BROKER) if stage=='post' else (WEB,WORK,MIRROR)):
        dep=props(unit,['PartOf','BindsTo','BoundBy','Conflicts','PropagatesStopTo','StopPropagatedFrom','Requires','ExecStop','ExecStopPost','OnFailure'])
        if any(TG in s for s in dep.values()):raise Stop('Webchat unit coupled to Telegram')
    return tg

def check(stage='pre'):
    manifest()
    main_source()
    if APP.is_symlink() or not APP.is_dir():raise Stop('Webchat directory unsafe')
    for rel in FILES:
        path=APP/rel
        if stage=='pre' and rel in ('webchat_main_bridge.py','webchat_bridge_broker.py'):continue
        if path.is_symlink() or not path.is_file():raise Stop('Missing installed file: '+rel)
        reference=BASE[rel] if stage=='pre' else sha(HERE/rel)
        if sha(path)!=reference:raise Stop('Installed file hash mismatch: '+rel)
    for rel,digest in BASE.items():
        if rel in FILES:continue
        if sha(APP/rel)!=digest:raise Stop('Existing P596.1 component has drifted: '+rel)
    relay_state(stage)
    tg=services(stage)
    q=read_queue()
    if q['active'] or q['runs'] or q['unheld']:
        raise Stop('In-flight or unheld Webchat work; do not replace worker')
    if stage=='pre' and q['links']:raise Stop('P597 links already present')
    if stage=='post' and q['enabled']!='0':raise Stop('Pilot must remain paused after install')
    if stage=='post':
        unit=props(WORK,['ExecStart','ExecStartPre'])
        effective=unit.get('ExecStart','')
        if 'webchat_main_bridge.py' not in effective or 'webchat_worker.py' in effective:
            raise Stop('Systemd is still launching old separate Claude worker')
        if 'webchat_main_bridge.py --check' not in unit.get('ExecStartPre',''):
            raise Stop('Worker sandbox preflight not installed')
    # During --check only the human install process checks dashboard auth.
    # The sandboxed Webchat-worker never receives the bearer token.
    if stage=='pre':
        token=broker.dashboard_token(MARVEEN)
        broker.probe_main(broker.dashboard_url(),token)
        with closing(broker.open_ro_marveen(MARVEEN/'store/claudeclaw.db')) as c:
            c.execute('SELECT id FROM agent_messages LIMIT 0')
    else:
        bridge.BrokerClient(BROKER_SOCK).probe()
    if stage=='post':
        with urllib.request.urlopen('http://127.0.0.1:3421/',timeout=3) as r:
            if r.status!=200:raise Stop('HTTP not ready')
    return {'telegram':tg,'webchat':props(WEB,['ActiveState','MainPID']),'worker':props(WORK,['ActiveState','MainPID']),
            'broker':props(BROKER,['ActiveState','MainPID']) if stage=='post' else {'ActiveState':'not-installed'},
            'mirror':props(MIRROR,['ActiveState','MainPID']),'queue':q,'marveen_source':'bfd79f75','pilot':'not_yet_live_proven'}

def atomic_copy(src,dst):
    if dst.is_symlink():raise Stop('Unsafe overwrite symlink')
    dst.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.p597-',dir=dst.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(Path(src).read_bytes());f.flush();os.fsync(f.fileno())
        os.chmod(tmp,0o600)
        os.replace(tmp,dst)
    finally:Path(tmp).unlink(missing_ok=True)

def backup():
    path=APP/('p597-backup-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    path.mkdir(mode=0o700)
    for rel in FILES:
        p=APP/rel
        if p.exists():shutil.copy2(p,path/rel)
    with closing(sqlite3.connect(DB.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as source:
        with closing(sqlite3.connect(path/'before.sqlite3')) as dest:source.backup(dest)
    os.chmod(path/'before.sqlite3',0o600)
    return path

def wait_http():
    for _ in range(25):
        try:
            with urllib.request.urlopen('http://127.0.0.1:3421/',timeout=1) as r:
                if r.status==200:return
        except OSError:pass
        time.sleep(1)
    raise Stop('Webchat HTTP did not return')

def cleanup_broker_socket():
    """Remove ONLY our inactive broker's owned Unix socket, never an arbitrary file."""
    state=props(BROKER,['ActiveState'])
    if state.get('ActiveState')=='active':raise Stop('Broker still active: refuse socket cleanup')
    if BROKER_SOCK.is_symlink():raise Stop('Unsafe broker socket symlink')
    if BROKER_SOCK.exists():
        fs=BROKER_SOCK.lstat()
        if not stat.S_ISSOCK(fs.st_mode) or fs.st_uid!=os.getuid():
            raise Stop('Unexpected broker socket path; refuse removal')
        BROKER_SOCK.unlink()


def wait_broker():
    for _ in range(20):
        try:
            bridge.BrokerClient(BROKER_SOCK).probe()
            return
        except (bridge.Stop, OSError, ValueError):
            time.sleep(0.3)
    raise Stop('Restricted Marveen broker did not become ready')


def apply():
    state=check('pre')
    before=state['telegram']
    iso.set_enabled(DB,False)
    q=read_queue()
    if q['active'] or q['runs'] or q['unheld']:raise Stop('New in-flight job; paused, no changes applied')
    if telegram()!=before:raise Stop('Telegram state changed; paused')
    backup_path=backup()
    copied=[];created_agent=False;installed_dropin=False;installed_broker=False
    try:
        command('systemctl','--user','stop',WORK)
        if telegram()!=before:raise Stop('Telegram changed after worker stop')
        command('systemctl','--user','stop',WEB)
        if telegram()!=before:raise Stop('Telegram changed after Webchat HTTP stop')
        for rel in FILES:
            atomic_copy(HERE/rel,APP/rel);copied.append(rel)
        RELAY.mkdir(mode=0o700);created_agent=True
        MARKER.write_text('P597 owner authenticated Webchat relay\n')
        os.chmod(MARKER,0o600)
        atomic_copy(HERE/'worker-dropin.conf',DROPIN);installed_dropin=True
        atomic_copy(HERE/'broker.service',BROKER_UNIT);installed_broker=True
        with iso.transaction(DB) as c:bridge.bridge_schema(c)
        command('systemctl','--user','daemon-reload')
        command('systemctl','--user','enable',BROKER)
        command('systemctl','--user','start',BROKER)
        wait_broker()
        command('systemctl','--user','start',WEB)
        wait_http()
        command('systemctl','--user','start',WORK)
        time.sleep(1)
        after=check('post')
        if after['telegram']!=before:raise Stop('Telegram fingerprint changed after installation')
    except BaseException:
        iso.set_enabled(DB,False)
        command('systemctl','--user','stop',WORK,check=False)
        command('systemctl','--user','stop',WEB,check=False)
        command('systemctl','--user','stop',BROKER,check=False)
        if installed_broker:command('systemctl','--user','disable',BROKER,check=False)
        if installed_broker:cleanup_broker_socket()
        if installed_broker:BROKER_UNIT.unlink(missing_ok=True)
        if installed_dropin:DROPIN.unlink(missing_ok=True)
        if created_agent:
            MARKER.unlink(missing_ok=True)
            try:RELAY.rmdir()
            except OSError:pass
        for rel in copied:
            p=backup_path/rel
            if p.exists():atomic_copy(p,APP/rel)
            else:(APP/rel).unlink(missing_ok=True)
        command('systemctl','--user','daemon-reload',check=False)
        command('systemctl','--user','start',WEB,check=False)
        command('systemctl','--user','start',WORK,check=False)
        print('P597.2 failed, Webchat+broker rollback attempted, dispatch PAUSED.',file=sys.stderr)
        print('BACKUP:',backup_path,file=sys.stderr)
        raise
    print('P5.9.8 integrated base installed (UI included), restricted broker preflight passed, processing PAUSED (enabled=0).')
    print('BACKUP:',backup_path)
    print(json.dumps(after,ensure_ascii=False,indent=2))


def installed():
    manifest()
    for rel in FILES:
        if (APP/rel).is_symlink() or not (APP/rel).is_file() or sha(APP/rel)!=sha(HERE/rel):
            raise Stop('Unified P598 installed file differs: '+rel)
    if sha(APP/'webchat_main_bridge.py')!=sha(HERE/'webchat_main_bridge.py'):
        raise Stop('Bridge file differs')
    relay_state('post')
    state=services('post')
    unit=props(WORK,['ExecStart','ExecStartPre'])
    effective=unit.get('ExecStart','')
    if 'webchat_main_bridge.py' not in effective or 'webchat_worker.py' in effective:
        raise Stop('Systemd worker ExecStart does not point to the main bridge')
    if 'webchat_main_bridge.py --check' not in unit.get('ExecStartPre',''):
        raise Stop('Systemd worker ExecStartPre missing')
    return state

def verify():
    tg=installed()
    q=read_queue()
    bridge.BrokerClient(BROKER_SOCK).probe()
    state={'telegram':tg,'queue':q,'worker':props(WORK,['ActiveState','MainPID','ExecStart']),
           'http':props(WEB,['ActiveState','MainPID']),
           'mirror':props(MIRROR,['ActiveState','MainPID']),
           'broker':props(BROKER,['ActiveState','MainPID'])}
    with closing(sqlite3.connect(DB.resolve().as_uri()+'?mode=ro',uri=True,timeout=3)) as c:
        c.execute('PRAGMA query_only=ON')
        c.row_factory=sqlite3.Row
        state['recent_links']=[dict(x) for x in c.execute(
            'SELECT seq,remote_id,phase,reason FROM webchat_main_links ORDER BY seq DESC LIMIT 5')]
    # Avoid printing user prompt contents or private dashboard token.
    print(json.dumps(state,ensure_ascii=False,indent=2))


def enable():
    before=installed()
    q=read_queue()
    if q['active'] or q['runs'] or q['unheld']:
        raise Stop('Cannot enable: in-flight/unheld queued work')
    bridge.BrokerClient(BROKER_SOCK).probe()
    iso.set_enabled(DB,True)
    if telegram()!=before:
        iso.set_enabled(DB,False)
        raise Stop('Telegram changed; Webchat paused')
    print('P597.2 PILOT ENABLED. Send ONE harmless new Webchat message for live proof.')


def pause():
    installed()
    iso.set_enabled(DB,False)
    print('Webchat new-request ingress PAUSED; existing main-session jobs not cancelled.')



def _rollback_backup():
    """Use the newest verified P597 backup; never trust an arbitrary path."""
    matches=[]
    for folder in APP.glob('p597-backup-*'):
        if folder.is_symlink() or not folder.is_dir():continue
        if all((folder/name).is_file() and not (folder/name).is_symlink() and
               sha(folder/name)==BASE[name] for name in ('webchat_http.py','index.html','app.js')):
            matches.append(folder)
    if not matches:raise Stop('No verified P597 backup; rollback refused')
    return max(matches,key=lambda p:p.name)


def rollback():
    """Return to pre-P597 Webchat-only worker. Never rollback active Marveen work."""
    before=installed()
    q=read_queue()
    if q['active'] or q['runs'] or q['unheld']:
        raise Stop('Active/unheld Webchat work; pause, inspect before rollback')
    with closing(sqlite3.connect(DB.resolve().as_uri()+'?mode=ro',uri=True,timeout=3)) as c:
        c.execute('PRAGMA query_only=ON')
        bad=c.execute("SELECT COUNT(*) FROM webchat_main_links WHERE phase IN ('posting','submitted')").fetchone()[0]
    if bad:raise Stop('Outstanding Marveen job! Rollback cannot cancel it safely')
    folder=_rollback_backup()
    if {p.name for p in RELAY.iterdir()}!={MARKER.name}:
        raise Stop('Relay directory has other files; refusing unsafe rollback')
    iso.set_enabled(DB,False)
    if telegram()!=before:raise Stop('Telegram changed before rollback; Webchat paused')
    command('systemctl','--user','stop',WORK)
    command('systemctl','--user','stop',WEB)
    command('systemctl','--user','stop',BROKER)
    command('systemctl','--user','disable',BROKER)
    cleanup_broker_socket()
    if telegram()!=before:raise Stop('Telegram fingerprint changed; no further rollback actions')
    # Only files and unit created by P597 are changed. Keep its history/audit table.
    for rel in ('webchat_http.py','index.html','app.js'):
        atomic_copy(folder/rel,APP/rel)
    (APP/'webchat_main_bridge.py').unlink(missing_ok=True)
    (APP/'webchat_bridge_broker.py').unlink(missing_ok=True)
    BROKER_UNIT.unlink(missing_ok=True)
    DROPIN.unlink(missing_ok=True)
    MARKER.unlink(missing_ok=True)
    RELAY.rmdir()
    command('systemctl','--user','daemon-reload')
    command('systemctl','--user','start',WEB)
    wait_http()
    command('systemctl','--user','start',WORK)
    if telegram()!=before:raise Stop('Telegram fingerprint changed after rollback')
    effective=props(WORK,['ExecStart']).get('ExecStart','')
    if 'webchat_worker.py' not in effective or 'webchat_main_bridge.py' in effective:
        raise Stop('Old isolated worker not restored: inspect systemd before enabling')
    print('ROLLBACK COMPLETE: P596.1 isolated worker restored, Webchat PAUSED (enabled=0).')
    print('BACKUP:',folder)


def main():
    ap=argparse.ArgumentParser()
    opts=ap.add_mutually_exclusive_group(required=True)
    for opt in ('check','apply','verify','enable','pause','rollback'):opts.add_argument('--'+opt,action='store_true')
    args=ap.parse_args()
    if args.check:print(json.dumps(check('pre'),ensure_ascii=False,indent=2))
    elif args.apply:apply()
    elif args.verify:verify()
    elif args.enable:enable()
    elif args.rollback:rollback()
    else:pause()

if __name__=='__main__':
    try:main()
    except (Stop,bridge.Stop,OSError,ValueError,sqlite3.Error,subprocess.TimeoutExpired) as e:
        print('STOP:',type(e).__name__,str(e),file=sys.stderr)
        raise SystemExit(2)
