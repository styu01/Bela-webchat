#!/usr/bin/env python3
"""P5.9.8 unified release manager for *two* precisely verified baselines.

- P5.9.6.1: full install, app.js included; processing remains PAUSED.
- Live P5.9.7.2 + UI hotfix: byte-identical adoption, NO service restarts,
  no changes to Marveen, user messages, or dispatch-enabled flag.
- Both: one package for --check/--apply/--verify/--pause/--enable/--rollback.

No Telegram service commands, no tmux writes, no Claude CLI calls.
"""
import argparse
import json
import os
import sqlite3
import stat
import sys
import time
from pathlib import Path
from contextlib import closing

import install_p597 as core

VERSION = 'P5.9.8'
STAMP_NAME = '.p598-unified-release.json'
BASE_FILES = ('webchat_http.py','index.html','webchat_main_bridge.py','webchat_bridge_broker.py','app.js')


class Stop(core.Stop): pass


def stamp_path():
    return core.APP/STAMP_NAME


def stamp_read():
    p = stamp_path()
    if p.is_symlink(): raise Stop('Unsafe unified release marker symlink')
    if not p.is_file(): raise Stop('Unified release marker missing; use --check then --apply')
    if p.stat().st_uid != os.getuid():raise Stop('Unified release marker owner mismatch')
    obj=json.loads(p.read_text())
    if not isinstance(obj,dict) or obj.get('version')!=VERSION or obj.get('origin') not in ('adopted-p5972-hotfix','fresh-p5961'):
        raise Stop('Invalid unified release marker')
    if obj.get('bundle_app_sha256')!=core.sha(core.HERE/'app.js'):
        raise Stop('Release app hash changed')
    return obj


def open_job_count():
    with closing(sqlite3.connect(core.DB.resolve().as_uri()+'?mode=ro',uri=True,timeout=3)) as c:
        c.execute('PRAGMA query_only=ON')
        table=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='webchat_main_links'").fetchone()
        if not table:return 0
        return c.execute("SELECT COUNT(*) FROM webchat_main_links WHERE phase IN ('posting','submitted','uncertain')").fetchone()[0]


def require_quiescent():
    q=core.read_queue()
    if q['active'] or q['runs'] or q['unheld'] or open_job_count():
        raise Stop('Outstanding, uncertain or unheld Webchat jobs; freeze changes and inspect first')
    return q


def pilot_ready():
    core.manifest()
    core.main_source()
    if not core.APP.is_dir() or core.APP.is_symlink():raise Stop('Webchat app path unsafe')
    tg=core.installed() # verifies all integrated hashes, relay, service unit and cgroups
    for rel,h in core.BASE.items():
        if rel in BASE_FILES:continue
        if core.sha(core.APP/rel)!=h:raise Stop('Unrelated P596 component drift: '+rel)
    core.bridge.BrokerClient(core.BROKER_SOCK).probe()
    with closing(sqlite3.connect(core.DB.resolve().as_uri()+'?mode=ro',uri=True,timeout=3)) as c:
        c.execute('PRAGMA query_only=ON')
        c.execute('SELECT request_id,phase,remote_id FROM webchat_main_links LIMIT 0')
    return {'origin':'adopted-p5972-hotfix','telegram':tg,'queue':core.read_queue()}


def detect():
    if stamp_path().exists() or stamp_path().is_symlink():
        raise Stop('Unified release already registered; use --verify')
    if (core.APP/'webchat_main_bridge.py').exists() or core.RELAY.exists() or core.BROKER_UNIT.exists():
        return pilot_ready() # fail closed; never mistake a partial pilot for legacy baseline
    return {'origin':'fresh-p5961',**core.check('pre')}


def copy_backup_and_mark(state):
    # Only called for byte-identical adoption. The original services are untouched.
    tg=core.telegram()
    folder=core.APP/('p598-backup-'+time.strftime('%Y%m%d-%H%M%S')+'-'+str(os.getpid()))
    if folder.exists() or folder.is_symlink():raise Stop('Backup name collision')
    folder.mkdir(mode=0o700)
    try:
        for rel in BASE_FILES:
            p=core.APP/rel
            if p.is_symlink() or not p.is_file() or core.sha(p)!=core.sha(core.HERE/rel):
                raise Stop('Installed payload changed before snapshot: '+rel)
            core.atomic_copy(p,folder/rel)
        with closing(sqlite3.connect(core.DB.resolve().as_uri()+'?mode=ro',uri=True,timeout=6)) as src:
            with closing(sqlite3.connect(folder/'webchat-snapshot.sqlite3',timeout=6)) as dst:
                src.backup(dst)
        os.chmod(folder/'webchat-snapshot.sqlite3',0o600)
        if core.telegram()!=tg:raise Stop('Telegram fingerprint changed during adoption')
        require_quiescent()
        obj={
            'version':VERSION,'origin':state['origin'],'created_at':int(time.time()),
            'backup_folder':str(folder),'bundle_app_sha256':core.sha(core.HERE/'app.js'),
            'note':'Adopted byte-identical already-running pilot; no services restarted',
        }
        tmp=folder/'release-info.json'
        tmp.write_text(json.dumps(obj,indent=2,ensure_ascii=False)+'\n')
        os.chmod(tmp,0o600)
        core.atomic_copy(tmp,stamp_path())
        return folder
    except BaseException:
        # Do not remove backup evidence on failure; marker never written until end.
        raise


def apply():
    state=detect()
    require_quiescent()
    if state['origin']=='adopted-p5972-hotfix':
        folder=copy_backup_and_mark(state)
        verify()
        print('P5.9.8 ADOPTED: byte-identical P5972 + UI hotfix, NO service restart, no dispatch flag change')
        print('BACKUP:',folder)
        return
    # Fresh baseline: core performs isolated Webchat-only install and backup.
    before=set(p.name for p in core.APP.glob('p597-backup-*'))
    core.apply()
    after=sorted(p for p in core.APP.glob('p597-backup-*') if p.name not in before)
    if len(after)!=1:raise Stop('Fresh install succeeded but unique backup cannot be identified; do not blindly retry')
    obj={'version':VERSION,'origin':'fresh-p5961','created_at':int(time.time()),
         'backup_folder':str(after[0]),'bundle_app_sha256':core.sha(core.HERE/'app.js')}
    # Marker is written only after core deploy and backups were confirmed.
    tmp=after[0]/'release-info.json'
    tmp.write_text(json.dumps(obj,indent=2,ensure_ascii=False)+'\n')
    os.chmod(tmp,0o600)
    core.atomic_copy(tmp,stamp_path())
    verify()
    print('P5.9.8 FRESH INSTALL SUCCESSFUL; DISPATCH PAUSED (enabled=0).')


def verify():
    marker=stamp_read()
    core.manifest()
    core.main_source()
    for rel in BASE_FILES:
        p=core.APP/rel
        if p.is_symlink() or not p.is_file() or core.sha(p)!=core.sha(core.HERE/rel):
            raise Stop('Unified installed code mismatch: '+rel)
    for rel,h in core.BASE.items():
        if rel in BASE_FILES:continue
        if core.sha(core.APP/rel)!=h:raise Stop('Unrelated base component changed: '+rel)
    core.installed()
    core.bridge.BrokerClient(core.BROKER_SOCK).probe()
    backup=Path(marker['backup_folder'])
    if backup.is_symlink() or not backup.is_dir() or not backup.resolve().is_relative_to(core.APP.resolve()):raise Stop('Unsafe unified backup folder')
    out={'version':VERSION,'origin':marker['origin'],'telegram':core.telegram(),
         'worker':core.props(core.WORK,['ActiveState','MainPID']),
         'broker':core.props(core.BROKER,['ActiveState','MainPID']),
         'http':core.props(core.WEB,['ActiveState','MainPID']),
         'mirror':core.props(core.MIRROR,['ActiveState','MainPID']),
         'webchat_queue':core.read_queue(),
         'open_marveen_links':open_job_count(),
         'ui_sha256':core.sha(core.APP/'app.js'),'backup':str(backup)}
    print(json.dumps(out,indent=2,ensure_ascii=False))
    return out


def rollback():
    marker=stamp_read()
    require_quiescent()
    if marker['origin']=='adopted-p5972-hotfix':
        # Since adoption changed no program, no services and no dispatch flag,
        # undoing release registration is a no-op on runtime; preserve backup.
        tg=core.telegram()
        pilot_ready()
        if core.telegram()!=tg:raise Stop('Telegram state changed; refusing release marker rollback')
        stamp_path().unlink()
        print('P5.9.8 ADOPTION ROLLED BACK: unchanged P5.9.7.2+UI pilot remains active.')
        print('No service restarted; backup retained:',marker['backup_folder'])
        return
    # Do not let core's newest-backup heuristic select a *different* snapshot.
    expected=Path(marker['backup_folder'])
    if expected.resolve()!=core._rollback_backup().resolve():
        raise Stop('Fresh install backup is not the verified latest backup; manual review required')
    core.rollback()
    stamp_path().unlink()
    print('P5.9.8 FRESH INSTALL ROLLED BACK to P5.9.6.1; processing paused. Backup retained.')


def main():
    p=argparse.ArgumentParser(description='P5.9.8 single bundle installer')
    g=p.add_mutually_exclusive_group(required=True)
    for action in ('check','apply','verify','pause','enable','rollback'):
        g.add_argument('--'+action,action='store_true')
    a=p.parse_args()
    if a.check:
        result=detect()
        require_quiescent()
        print(json.dumps({'preflight':'PASS','version':VERSION,**result},indent=2,ensure_ascii=False))
    elif a.apply:apply()
    elif a.verify:verify()
    elif a.rollback:rollback()
    elif a.pause:
        verify();core.pause()
    elif a.enable:
        verify();core.enable()

if __name__=='__main__':
    try:main()
    except (Stop,core.Stop,core.bridge.Stop,OSError,ValueError,sqlite3.Error) as e:
        print('STOP:',type(e).__name__,str(e),file=sys.stderr)
        sys.exit(2)
