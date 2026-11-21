#!/usr/bin/env python3
"""P5.9.8.3 fail-closed installer. Only Webchat mirror, HTTP and app.js.

TELEGRAM, shared Claude, worker and broker units are never controlled.
Use --check, --apply, --verify, --rollback. No automatic retry of Bot API effects.
"""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

HOME=Path.home()
APP=HOME/'bela-f0-dev/webchat-p2'
DB=HOME/'bela-f0-dev/webchat-preview/project/store/f0-webchat.sqlite3'
HERE=Path(__file__).resolve().parent
FILES=('webchat_sync_sender.py','webchat_http.py','app.js')
META=APP/'.p5983-rollback.json'
TG='bela-channels.service'
WORK='bela-webchat-worker.service'
BROKER='bela-webchat-broker.service'
HTTP='bela-webchat.service'
MIRROR='bela-webchat-mirror.service'
EXPECTED_BASE={
 'webchat_sync_sender.py':'84d16e56852adb23ce97a317ba52843e56ff17b1564dd12fe08577b96ae9c438',
 'webchat_http.py':'2ad75793a634d16aa5a7ae94f07799b0bf948cd6b60b9037dc6f2597a5067706',
 'app.js':'0612ee6f1a51a4c22d013db862c05e72fba2f93b84867187ec11c001838bd058',
}
MANIFEST='SHA256SUMS.json'

class Stop(Exception):pass

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def invoke(*argv,timeout=25,check=True):
    try:p=subprocess.run(argv,capture_output=True,text=True,timeout=timeout)
    except (subprocess.TimeoutExpired,FileNotFoundError) as exc:raise Stop('command unavailable/timeout: '+str(argv[:3])) from exc
    if check and p.returncode:raise Stop('command failed: '+str(argv[:3])+' exit='+str(p.returncode))
    return p.stdout.strip()

def service(unit):
    v=invoke('systemctl','--user','show',unit,'-p','ActiveState','-p','MainPID')
    result=dict(x.split('=',1) for x in v.splitlines() if '=' in x)
    try:pid=int(result.get('MainPID','0'))
    except ValueError:pid=0
    return result.get('ActiveState'),pid

def untouched_state():
    result={}
    for name in (TG,WORK,BROKER):
        state,pid=service(name)
        if state!='active' or pid<2:raise Stop('Protected service inactive: '+name)
        result[name]=pid
    panes=invoke('tmux','list-panes','-t','bela-channels','-F','#{pane_id} #{pane_pid}')
    if not panes:raise Stop('Telegram tmux pane unavailable')
    result['tmux']=panes
    return result

def assert_untouched(original):
    if untouched_state()!=original:raise Stop('STOP: protected Telegram / worker / broker PID or tmux changed')

def integrity():
    mpath=HERE/MANIFEST
    if mpath.is_symlink() or not mpath.is_file():raise Stop('Release SHA manifest missing')
    manifest=json.loads(mpath.read_text())
    for name,expected in manifest.items():
        p=HERE/name
        if p.is_symlink() or not p.is_file() or '..' in Path(name).parts or Path(name).is_absolute():
            raise Stop('Invalid release file: '+name)
        if digest(p)!=expected:raise Stop('Release hash mismatch: '+name)
    for name in FILES:
        if name not in manifest:raise Stop('Release missing file: '+name)
    return {name:manifest[name] for name in FILES}

def installed_state():
    if APP.is_symlink() or not APP.is_dir() or DB.is_symlink() or not DB.is_file():
        raise Stop('Production path invalid')
    expected=integrity()
    statuses={}
    for name in FILES:
        target=APP/name
        if target.is_symlink() or not target.is_file():raise Stop('Expected live file missing: '+name)
        h=digest(target)
        if h==EXPECTED_BASE[name]:statuses[name]='base'
        elif h==expected[name]:statuses[name]='patched'
        else:raise Stop('Unknown file version: '+name+' sha256='+h)
    states=set(statuses.values())
    if len(states)!=1:raise Stop('Mixed production versions (stopped, no writes)')
    return states.pop()

def db_status():
    with closing(sqlite3.connect(DB.resolve().as_uri()+'?mode=ro',uri=True,timeout=3)) as c:
        c.execute('PRAGMA query_only=ON')
        m=dict(c.execute('SELECT key,value FROM webchat_sync_meta'))
        if m.get('enabled') not in ('0','1'):raise Stop('Bad mirror-enabled DB flag')
        sending=c.execute("SELECT COUNT(*) FROM webchat_sync_parts WHERE state='sending'").fetchone()[0]
        active=c.execute("SELECT COUNT(*) FROM f0_web_jobs WHERE state IN ('handed_off','seen','unknown')").fetchone()[0]
        return m,sending,active

def manifest_unit():
    content=invoke('systemctl','--user','cat',MIRROR)
    if 'webchat_sync_sender.py' not in content or 'ExecStart=' not in content:
        raise Stop('Unexpected Webchat mirror unit')
    for unit in (MIRROR,HTTP):
        state,pid=service(unit)
        if state!='active' or pid<2:raise Stop('Service inactive: '+unit)

def check():
    state=installed_state()
    manifest_unit()
    protected=untouched_state()
    flags,sending,active=db_status()
    if active:raise Stop('Webchat has active jobs; wait until idle')
    if sending:raise Stop('A Telegram send is in progress; retry when idle')
    print(json.dumps({'preflight':'PASS','state':state,'target':str(APP),
          'sync_enabled':flags['enabled'],'active_jobs':active,'sending_parts':sending,
          'protected_pids':{k:v for k,v in protected.items() if k!='tmux'}},indent=2))
    return state

def tx_set_flag(value,expected=None):
    with closing(sqlite3.connect(DB,timeout=5,isolation_level=None)) as c:
        c.execute('BEGIN IMMEDIATE')
        try:
            cur=c.execute("SELECT value FROM webchat_sync_meta WHERE key='enabled'").fetchone()
            if not cur or (expected is not None and cur[0]!=expected):raise Stop('Sync flag changed, abort')
            c.execute("UPDATE webchat_sync_meta SET value=? WHERE key='enabled'",(value,))
            c.commit()
        except BaseException:c.rollback();raise

def await_idle(timeout=72):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        flags,sending,active=db_status()
        if active:raise Stop('New Webchat job became active')
        if not sending:return
        time.sleep(0.5)
    raise Stop('In-flight Telegram API request did not finish; do NOT terminate it')

def quiesce(original,flag):
    # DB transaction serializes with mirror send_once's send-intent transaction.
    # After enabled=0, no new claim is possible. Allow existing API call to
    # complete BEFORE stopping the mirror; never SIGKILL an active send.
    reason_before=db_status()[0].get('blocked_reason','')
    tx_set_flag('0',expected=flag)
    try:
        await_idle()
        assert_untouched(original)
        invoke('systemctl','--user','stop',MIRROR,timeout=25)
        if service(MIRROR)[0]=='active':raise Stop('Mirror unit did not stop')
        await_idle()
        assert_untouched(original)
    except BaseException:
        # Best-effort cleanup even for an interruption between flag gating and
        # service stop. Never re-enable after a newly detected fatal send error.
        try:
            if service(MIRROR)[0]!='active':
                invoke('systemctl','--user','start',MIRROR,timeout=35)
            flags,sending,active=db_status()
            if (not sending and not active and flags['enabled']=='0' and
                    flags.get('blocked_reason','')==reason_before):
                tx_set_flag(flag,expected='0')
        except BaseException:
            # Safer to leave mirror disabled than to risk an in-flight replay.
            pass
        raise

def atomic_copy(source,target):
    if target.is_symlink():raise Stop('Symlink file refused')
    mode=target.stat().st_mode&0o777 if target.exists() else 0o600
    fd,tmp=tempfile.mkstemp(prefix='.p5983-',dir=target.parent)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(Path(source).read_bytes());f.flush();os.fsync(f.fileno())
        os.chmod(tmp,mode)
        os.replace(tmp,target)
    finally:Path(tmp).unlink(missing_ok=True)

def make_backup(old_flag):
    folder=Path(tempfile.mkdtemp(prefix='p5983-mirror-backup-',dir=APP))
    os.chmod(folder,0o700)
    hashes={}
    for name in FILES:
        target=APP/name
        shutil.copy2(target,folder/name)
        hashes[name]=digest(folder/name)
    snap=folder/'webchat-db-snapshot.sqlite3'
    with closing(sqlite3.connect(DB.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as source:
        with closing(sqlite3.connect(snap)) as dst:source.backup(dst)
    os.chmod(snap,0o600)
    record={'backup':str(folder),'flag_before':old_flag,'hashes':hashes,
            'new_hashes':integrity(),'created_at':datetime.now(timezone.utc).isoformat(),
            'db_snapshot':str(snap)}
    (folder/'install-record.json').write_text(json.dumps(record,indent=2))
    os.chmod(folder/'install-record.json',0o600)
    return record

def save_rollback(record):
    tmp=META.with_suffix('.tmp')
    if tmp.is_symlink() or META.is_symlink():raise Stop('Unsafe rollback pointer')
    tmp.write_text(json.dumps(record,indent=2))
    os.chmod(tmp,0o600)
    os.replace(tmp,META)

def valid_backup(record):
    path=Path(record.get('backup',''))
    if path.is_symlink() or path.parent!=APP or not path.name.startswith('p5983-mirror-backup-') or not path.is_dir():
        raise Stop('Invalid backup directory')
    hashes=record.get('hashes',{})
    for name in FILES:
        f=path/name
        if f.is_symlink() or not f.is_file() or digest(f)!=hashes.get(name):
            raise Stop('Backup corruption: '+name)
    return path

def resume(flag,original):
    # Only own units. No telegram session restart and no broker/worker restart.
    invoke('systemctl','--user','start',MIRROR,timeout=35)
    if service(MIRROR)[0]!='active':raise Stop('Webchat mirror did not start')
    invoke('systemctl','--user','restart',HTTP,timeout=35)
    if service(HTTP)[0]!='active':raise Stop('Webchat HTTP did not start')
    assert_untouched(original)
    tx_set_flag(flag,expected='0')
    assert_untouched(original)

def apply():
    state=check()
    if state=='patched':
        print('PASS: already installed, no service restart')
        return
    original=untouched_state()
    flag=db_status()[0]['enabled']
    backup=None
    quarantined=False
    try:
        quiesce(original,flag)
        quarantined=True
        backup=make_backup(flag)
        valid_backup(backup)
        expected=integrity()
        for name in FILES:
            atomic_copy(HERE/name,APP/name)
            if digest(APP/name)!=expected[name]:raise Stop('Post-copy integrity failure: '+name)
        save_rollback(backup)
        resume(flag,original)
        print('PASS: P5.9.8.3 installed; mirror and HTTP restarted; protected PIDs unchanged')
        print('BACKUP:',backup['backup'])
        print('DB SNAPSHOT:',backup['db_snapshot'],'(not automatically restored on rollback)')
    except BaseException as exc:
        print('ERROR during install:',type(exc).__name__,str(exc),file=sys.stderr)
        if backup is not None:
            try:
                # Protect current Telegram API calls from interruption.
                flags,sending,active=db_status()
                if sending:
                    raise Stop('Cannot rollback while a Bot API send is in flight; sync flag stays disabled')
                invoke('systemctl','--user','stop',MIRROR,timeout=25,check=False)
                folder=valid_backup(backup)
                for name in FILES:atomic_copy(folder/name,APP/name)
                if quarantined:
                    invoke('systemctl','--user','start',MIRROR,timeout=35)
                    invoke('systemctl','--user','restart',HTTP,timeout=35)
                    tx_set_flag(flag,expected='0')
                assert_untouched(original)
                META.unlink(missing_ok=True)
                print('PASS: original frontend and mirror files restored',file=sys.stderr)
            except BaseException as rollback_exc:
                print('STOP: automatic rollback incomplete: '+str(rollback_exc),file=sys.stderr)
        elif quarantined:
            try:
                invoke('systemctl','--user','start',MIRROR,timeout=35)
                tx_set_flag(flag,expected='0')
            except BaseException:pass
        raise

def verify():
    if installed_state()!='patched':raise Stop('Installed files do not match P5.9.8.3')
    manifest_unit();untouched_state()
    if META.is_symlink() or not META.is_file():raise Stop('Rollback metadata missing')
    record=json.loads(META.read_text())
    valid_backup(record)
    flags,sending,active=db_status()
    if flags['enabled'] not in ('0','1'):raise Stop('Invalid mirror flag')
    print('PASS: P5.9.8.3 hashes, backup, protected services and sync flag validated')
    print('SYNC ENABLED:',flags['enabled'],'RECOVERY:',flags.get('recovery_state','not yet initialized'))
    print('QUARANTINE_EVENTS:',end=' ')
    with closing(sqlite3.connect(DB.resolve().as_uri()+'?mode=ro',uri=True,timeout=3)) as c:
        print(c.execute("SELECT COUNT(*) FROM webchat_sync_events WHERE state IN ('uncertain','invalid')").fetchone()[0])

def rollback():
    if installed_state()!='patched':raise Stop('Unknown or non-patched live state; refusing rollback')
    if META.is_symlink() or not META.is_file():raise Stop('Rollback metadata missing')
    record=json.loads(META.read_text());folder=valid_backup(record)
    old_flag=record.get('flag_before')
    if old_flag not in ('0','1'):raise Stop('Invalid backup sync flag')
    original=untouched_state()
    flags,sending,active=db_status()
    if sending or active:raise Stop('Mirror send / Webchat job active; retry idle')
    current_flag=flags['enabled']
    quiesce(original,current_flag)
    for name in FILES:atomic_copy(folder/name,APP/name)
    for name in FILES:
        if digest(APP/name)!=record['hashes'][name]:raise Stop('Rollback hash mismatch')
    resume(old_flag,original)
    META.unlink()
    print('PASS: P5.9.8.3 code rolled back; SQLite event history preserved (no replay)')
    print('BACKUP:',folder)

def main():
    ap=argparse.ArgumentParser()
    group=ap.add_mutually_exclusive_group(required=True)
    for op in ('check','apply','verify','rollback'):group.add_argument('--'+op,action='store_true')
    args=ap.parse_args()
    try:
        if args.check:check()
        elif args.apply:apply()
        elif args.verify:verify()
        elif args.rollback:rollback()
    except (Stop,OSError,sqlite3.Error,ValueError,KeyError,PermissionError) as exc:
        print('STOP:',str(exc),file=sys.stderr)
        raise SystemExit(2)

if __name__=='__main__':main()
