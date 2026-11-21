#!/usr/bin/env python3
"""P5.9.8.2: fail-closed, frontend-only appearance repair.

Changes only the three presentation files app.js, index.html, style.css.
Does NOT start, restart, stop or modify any systemd, Telegram, Marveen or worker service.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
TARGET = Path(os.environ.get('BELA_THEME_TARGET', str(Path.home() / 'bela-f0-dev/webchat-p2')))
FILES = ('app.js', 'index.html', 'style.css')
P5981 = TARGET / '.bela-theme-p5981.json'
P5982 = TARGET / '.bela-theme-p5982.json'
BASE = {
    'app.js': '24ccc50aca7f89ed729e7c24add302c60719304e07947bc5b6735eafe45d944e',
    'index.html': '93e28915996c5fa14010b8a254f5777cc3536d7d5e2eea2dcf239a20e455ecfc',
    'style.css': '35bcaccb33af3857b87528737ce51cc05cfef421c8579decc5c36defccd27596',
}
NEW = {'app.js': '0612ee6f1a51a4c22d013db862c05e72fba2f93b84867187ec11c001838bd058', 'index.html': 'e82ab16e9fbee64e8157c97e6fb4ba2d4ed7a8187d8c88493fe6541c7a559591', 'style.css': '94e1784068acb3c1b3fc6f0aa40c6f1466ac1e6138d544ba52f2563b0ba3a8c5'}


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as src:
        for chunk in iter(lambda: src.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def require_file(path: Path) -> None:
    if path.is_symlink() or not path.is_file() or not stat.S_ISREG(path.stat().st_mode):
        raise RuntimeError('Nem biztonságos vagy hiányzó fájl: ' + str(path))


def ensure_dir():
    if TARGET.is_symlink() or not TARGET.is_dir():
        raise RuntimeError('Nem létező vagy szimbolikus link célmappa: ' + str(TARGET))


def hashes(folder: Path) -> dict[str, str]:
    values = {}
    for name in FILES:
        p = folder / name
        require_file(p)
        values[name] = digest(p)
    return values


def marker(path: Path) -> dict:
    require_file(path)
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise RuntimeError('Érvénytelen telepítési marker: ' + str(path))
    return data


def validate_p5981():
    data = marker(P5981)
    if data.get('version') != 'P5.9.8.1' or data.get('patched') != {k: BASE[k] for k in ('app.js','index.html')}:
        raise RuntimeError('Nem az ellenőrzött P5.9.8.1 marker látható')
    original_backup = Path(data.get('backup',''))
    if original_backup.parent != TARGET or not original_backup.name.startswith('p5981-theme-backup-') or original_backup.is_symlink() or not original_backup.is_dir():
        raise RuntimeError('A P5.9.8.1 mentésének hivatkozása nem biztonságos')
    return data


def validate_package():
    if hashes(HERE) != NEW:
        raise RuntimeError('A javítócsomag sérült vagy módosult (SHA-256 eltérés)')


def preflight() -> str:
    ensure_dir()
    validate_package()
    validate_p5981()
    current = hashes(TARGET)
    if P5982.exists() or P5982.is_symlink():
        meta = marker(P5982)
        if meta.get('version') != 'P5.9.8.2' or meta.get('patched') != NEW or meta.get('base') != BASE:
            raise RuntimeError('A P5.9.8.2 marker eltér vagy sérült')
        if current != NEW:
            raise RuntimeError('DRIFT: telepítés utáni fájlok módosultak; automatikus javítás tiltva')
        return 'installed'
    if current != BASE:
        raise RuntimeError('DRIFT: a P5.9.8.1 alapfájlok nem egyeznek; nincs felülírás')
    return 'ready'


def atomic_copy(source: Path, target: Path) -> None:
    require_file(source)
    require_file(target)
    fd, tmp = tempfile.mkstemp(prefix='.bela-p5982-',suffix='.new',dir=target.parent)
    temp = Path(tmp)
    try:
        os.fchmod(fd, stat.S_IMODE(target.stat().st_mode))
        with os.fdopen(fd,'wb') as dest, source.open('rb') as inp:
            shutil.copyfileobj(inp,dest)
            dest.flush();os.fsync(dest.fileno())
        os.replace(temp,target)
    finally:
        temp.unlink(missing_ok=True)


def atomic_state(data: dict) -> None:
    fd, tmp = tempfile.mkstemp(prefix='.bela-p5982-state-',suffix='.json',dir=TARGET)
    p = Path(tmp)
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(data,f,ensure_ascii=False,indent=2)
            f.flush();os.fsync(f.fileno())
        os.replace(p,P5982)
    finally:
        p.unlink(missing_ok=True)


def backup_path(meta):
    raw = meta.get('backup')
    if not isinstance(raw,str) or not raw:
        raise RuntimeError('Hiányzó mentési hivatkozás')
    folder = Path(raw)
    if folder.parent != TARGET or not folder.name.startswith('p5982-theme-backup-') or folder.is_symlink() or not folder.is_dir():
        raise RuntimeError('Nem biztonságos mentési hivatkozás')
    return folder


def inspect_backup(meta):
    folder = backup_path(meta)
    if hashes(folder) != BASE:
        raise RuntimeError('Backup-integritás hiba')
    return folder


def run_apply():
    state=preflight()
    if state=='installed':
        print('ALREADY INSTALLED: P5.9.8.2; nincs módosítás')
        return
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%SZ')
    folder = TARGET / ('p5982-theme-backup-' + stamp)
    if folder.exists() or folder.is_symlink():
        raise RuntimeError('A mentés neve már foglalt')
    folder.mkdir(mode=0o700)
    touched = []
    try:
        for name in FILES:
            shutil.copy2(TARGET / name, folder / name)
        if hashes(folder) != BASE:
            raise RuntimeError('Mentés SHA-256 ellenőrzése sikertelen')
        for name in FILES:
            atomic_copy(HERE / name, TARGET / name)
            touched.append(name)
        if hashes(TARGET) != NEW:
            raise RuntimeError('A telepítés ellenőrzése sikertelen')
        record = {
            'version':'P5.9.8.2','created_utc':stamp,'backup':str(folder),
            'base':BASE,'patched':NEW,'p5981_marker_sha256':digest(P5981)
        }
        atomic_state(record)
    except BaseException:
        # Restore only files we actually changed and only from verified backup.
        if folder.is_dir() and not folder.is_symlink():
            try:
                if hashes(folder) == BASE:
                    for name in touched:
                        atomic_copy(folder / name, TARGET / name)
            except Exception as exc:
                print('KRITIKUS: automatikus visszaállítás hiba: '+str(exc),file=sys.stderr)
        raise
    print('PASS: P5.9.8.2 telepítve (frontend-only)')
    print('BACKUP: '+str(folder))
    print('A Telegram/worker/broker/HTTP szolgáltatások nem lettek újraindítva.')


def run_verify():
    if preflight()!='installed':
        raise RuntimeError('A P5.9.8.2 nincs telepítve')
    meta=marker(P5982)
    inspect_backup(meta)
    if meta.get('p5981_marker_sha256') != digest(P5981):
        raise RuntimeError('A P5.9.8.1 telepítési marker módosult')
    print('PASS: P5.9.8.2 frontend és teljes, SHA-256 mentés ellenőrizve')


def run_rollback():
    run_verify()
    meta=marker(P5982)
    folder=inspect_backup(meta)
    for name in FILES:
        atomic_copy(folder/name,TARGET/name)
    if hashes(TARGET)!=BASE:
        raise RuntimeError('Visszaállítás integritáshiba; kézi ellenőrzés szükséges')
    P5982.unlink()
    print('PASS: P5.9.8.1-re visszaállítva; a P5.9.8.1 marker és mentés megőrizve')
    print('A szolgáltatások nem lettek újraindítva.')


def main():
    parser=argparse.ArgumentParser(description='Béla Webchat P5.9.8.2: csak frontend javítása')
    group=parser.add_mutually_exclusive_group(required=True)
    for value in ('check','apply','verify','rollback'):
        group.add_argument('--'+value,action='store_true')
    args=parser.parse_args()
    try:
        if args.check:
            print(json.dumps({'preflight':'PASS','state':preflight(),'target':str(TARGET),'files':list(FILES)},ensure_ascii=False,indent=2))
        elif args.apply:run_apply()
        elif args.verify:run_verify()
        else:run_rollback()
    except (RuntimeError,OSError,TypeError,ValueError,KeyError) as e:
        print('STOP: '+str(e),file=sys.stderr)
        return 1
    return 0

if __name__=='__main__':
    raise SystemExit(main())
