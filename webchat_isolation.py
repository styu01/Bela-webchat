#!/usr/bin/env python3
"""P5.9.2 Webchat-only transactional queue. Never imports Claude or Telegram tools."""
import json, os, secrets, sqlite3, time, uuid
from contextlib import contextmanager
from pathlib import Path

MAX_QUEUED=50
ACTIVE=('handed_off','seen')

@contextmanager
def transaction(db):
    db=Path(db)
    if db.is_symlink(): raise RuntimeError('Symlink queue denied')
    con=sqlite3.connect(db,timeout=3,isolation_level=None)
    con.row_factory=sqlite3.Row
    try:
        con.execute('PRAGMA busy_timeout=3000')
        con.execute('BEGIN IMMEDIATE')
        yield con
        con.commit()
    except BaseException:
        con.rollback();raise
    finally:con.close()

def schema(c):
    c.execute('CREATE TABLE IF NOT EXISTS webchat_isolation_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS webchat_held_requests(request_id TEXT PRIMARY KEY,reason TEXT NOT NULL,created_at INTEGER NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS webchat_tombstones(request_id TEXT PRIMARY KEY,seq INTEGER NOT NULL,reason TEXT NOT NULL,closed_at INTEGER NOT NULL)')
    c.execute('''CREATE TABLE IF NOT EXISTS webchat_runs(request_id TEXT PRIMARY KEY,run_id TEXT NOT NULL UNIQUE,
        phase TEXT NOT NULL,child_pid INTEGER,child_start TEXT,boot_id TEXT,
        claimed_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,reason TEXT NOT NULL DEFAULT '')''')
    c.execute('''CREATE TABLE IF NOT EXISTS webchat_failure_events(request_id TEXT PRIMARY KEY,
        seq INTEGER NOT NULL, failure_code TEXT NOT NULL, created_at INTEGER NOT NULL)''')
    c.execute('CREATE TABLE IF NOT EXISTS preview_cancelled_requests(request_id TEXT PRIMARY KEY,cancelled_at INTEGER NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS preview_closed_requests(request_id TEXT PRIMARY KEY,job_json TEXT NOT NULL,closed_at INTEGER NOT NULL,reason TEXT NOT NULL)')
    # P5.9.6 Webchat -> Telegram Bot API durable outbox. NO Telegram-DB writes.
    c.execute("""CREATE TABLE IF NOT EXISTS webchat_sync_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id TEXT NOT NULL,role TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
        content TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'new',
        reason TEXT NOT NULL DEFAULT '',created_at INTEGER NOT NULL,
        UNIQUE(request_id,role))""")
    c.execute("""CREATE TABLE IF NOT EXISTS webchat_sync_parts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id INTEGER NOT NULL,ordinal INTEGER NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('text','media')),
        text TEXT NOT NULL,media_json TEXT NOT NULL,
        state TEXT NOT NULL,claimed_at INTEGER,chat_id TEXT,
        telegram_message_id INTEGER,reason TEXT NOT NULL DEFAULT '',
        UNIQUE(event_id,ordinal))""")
    c.execute('CREATE TABLE IF NOT EXISTS webchat_sync_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)')


def archive(c,row,reason):
    """Atomic archival with no replay; never deletes a row before its tombstone exists."""
    j=dict(row);now=int(time.time());j['state']=reason
    c.execute('INSERT INTO preview_closed_requests(request_id,job_json,closed_at,reason) VALUES (?,?,?,?)',
              (j['request_id'],json.dumps(j,ensure_ascii=False),now,reason))
    c.execute('INSERT INTO webchat_tombstones(request_id,seq,reason,closed_at) VALUES(?,?,?,?)',
              (j['request_id'],j['seq'],reason,now))
    if c.execute('DELETE FROM f0_web_jobs WHERE request_id=? AND state=?',(j['request_id'],row['state'])).rowcount!=1:
        raise RuntimeError('Concurrent change, transaction rolled back')
    c.execute('DELETE FROM webchat_runs WHERE request_id=?',(j['request_id'],))

def historical_75(c):
    """Find a real UUID from an existing legacy closed-job record, if present.

    Do not invent a UUID when #75 was removed before migration.
    """
    matches=[]
    for row in c.execute('SELECT request_id,job_json FROM preview_closed_requests'):
        try:
            old=json.loads(row['job_json'])
        except (ValueError,TypeError):
            # A damaged unrelated historic record is not evidence for #75.
            continue
        if isinstance(old,dict) and old.get('seq')==75:
            if old.get('request_id')!=row['request_id']:
                raise RuntimeError('Historical #75 UUID mismatch; STOP')
            try:
                rid=str(uuid.UUID(row['request_id']))
            except (TypeError,ValueError):
                raise RuntimeError('Invalid historical #75 UUID; STOP')
            if rid!=row['request_id']:
                raise RuntimeError('Noncanonical historical #75 UUID; STOP')
            matches.append(rid)
    if len(matches)>1:
        raise RuntimeError('Multiple historical #75 records; STOP')
    return matches[0] if matches else None


def migrate(db):
    """One-time idempotent migration after external SQLite backup.

    Accepts (a) #75 unknown or (b) #75 ALREADY ABSENT, but only if
    #76-#79 are all queued and there is no other active legacy job.
    Absent UUID is never fabricated. Missing original UUID means that an
    external resend of the old unknown UUID cannot be identified here.
    """
    with transaction(db) as c:
        schema(c)
        version=c.execute("SELECT value FROM webchat_isolation_meta WHERE key='migration'").fetchone()
        if version:
            if version[0]!='p591-1':raise RuntimeError('Unexpected migration version')
            return False
        rows={r['seq']:r for r in c.execute('SELECT * FROM f0_web_jobs WHERE seq BETWEEN 75 AND 79')}
        if any(n not in rows or rows[n]['state']!='queued' for n in range(76,80)):
            raise RuntimeError('STOP: #76..#79 must remain queued and present')
        old=rows.get(75)
        if old is not None and old['state']!='unknown':
            raise RuntimeError('STOP: #75 present but not unknown')
        others=c.execute("SELECT COUNT(*) FROM f0_web_jobs WHERE state IN ('handed_off','seen','unknown') AND seq!=75").fetchone()[0]
        if others:raise RuntimeError('STOP: another active/unknown legacy job exists')
        history=historical_75(c)
        if old is not None:
            if history is not None:raise RuntimeError('STOP: #75 both active and archived')
            archive(c,old,'legacy_75_failed_unverified')
            status='archived_from_unknown'
        elif history is not None:
            # Already removed and archived before P5.9.2; preserve original history.
            c.execute('INSERT INTO webchat_tombstones(request_id,seq,reason,closed_at) VALUES(?,?,?,?)',
                      (history,75,'legacy_75_previously_removed',int(time.time())))
            status='historically_archived_uuid_known'
        else:
            # Record the missing legacy seq, NOT a fictitious request UUID.
            status='previously_absent_uuid_unavailable'
        for n in range(76,80):
            c.execute('INSERT INTO webchat_held_requests VALUES(?,?,?)',
                      (rows[n]['request_id'],'legacy_preserved_pending_explicit_review',int(time.time())))
        c.execute("INSERT INTO webchat_isolation_meta VALUES('legacy_75_status',?)",(status,))
        c.execute("INSERT INTO webchat_isolation_meta VALUES('migration','p591-1')")
        return True

def set_enabled(db,enabled):
    with transaction(db) as c:
        schema(c)
        if not c.execute("SELECT 1 FROM webchat_isolation_meta WHERE key='migration'").fetchone():raise RuntimeError('Not migrated')
        c.execute("INSERT INTO webchat_isolation_meta VALUES('enabled',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",('1' if enabled else '0',))

def enabled(db):
    with sqlite3.connect(Path(db).resolve().as_uri()+'?mode=ro',uri=True,timeout=2) as c:
        try:row=c.execute("SELECT value FROM webchat_isolation_meta WHERE key='enabled'").fetchone()
        except sqlite3.OperationalError:return False
        return bool(row and row[0]=='1')

def queue_sync(c,rid,role,content):
    """Called in the SAME SQLite transaction as enqueue/answer/archive.

    A fresh P5.9.6 installation does not backfill any historical requests;
    only events created after installation are eligible for dispatch.
    """
    if role not in ('user','assistant','system') or not isinstance(content,str):
        raise ValueError('Invalid mirror event')
    c.execute("INSERT OR IGNORE INTO webchat_sync_events(request_id,role,content,state,created_at) VALUES(?,?,?,'new',?)",
              (rid,role,content,int(time.time())))

def enqueue(c,rid,text):
    rid=str(uuid.UUID(rid))
    if len(rid)!=36 or not isinstance(text,str):raise ValueError('Invalid request')
    if c.execute('SELECT 1 FROM webchat_tombstones WHERE request_id=?',(rid,)).fetchone():return 409,'Archived request; no replay'
    if c.execute('SELECT 1 FROM preview_closed_requests WHERE request_id=?',(rid,)).fetchone():return 409,'Legacy archived request; no replay'
    if c.execute('SELECT 1 FROM preview_cancelled_requests WHERE request_id=?',(rid,)).fetchone():return 409,'Cancelled request; no replay'
    row=c.execute('SELECT request_text,state FROM f0_web_jobs WHERE request_id=?',(rid,)).fetchone()
    if row:return (202,row['state']) if row['request_text']==text else (409,'UUID conflict')
    if c.execute("SELECT COUNT(*) FROM f0_web_jobs WHERE state='queued'").fetchone()[0]>=MAX_QUEUED:return 429,'Queue full'
    now=int(time.time())
    c.execute("INSERT INTO f0_web_jobs(request_id,request_text,state,created_at,updated_at) VALUES(?,?,'queued',?,?)",(rid,text,now,now))
    queue_sync(c,rid,'user',text)
    return 202,'queued'

def claim(db):
    with transaction(db) as c:
        schema(c)
        flag=c.execute("SELECT value FROM webchat_isolation_meta WHERE key='enabled'").fetchone()
        if flag is None or flag[0]!='1':return None
        if c.execute("SELECT 1 FROM f0_web_jobs WHERE state IN ('handed_off','seen','unknown')").fetchone():return None
        row=c.execute('''SELECT * FROM f0_web_jobs WHERE state='queued' AND request_id NOT IN
           (SELECT request_id FROM webchat_held_requests) ORDER BY seq LIMIT 1''').fetchone()
        if row is None:return None
        run=str(uuid.uuid4());token=secrets.token_urlsafe(32);now=int(time.time())
        sid=str(uuid.uuid4())
        if c.execute("UPDATE f0_web_jobs SET state='handed_off',session_id=?,handoff_token=?,updated_at=? WHERE request_id=? AND state='queued'",
                     (sid,token,now,row['request_id'])).rowcount!=1:raise RuntimeError('Claim race')
        c.execute("INSERT INTO webchat_runs(request_id,run_id,phase,claimed_at,updated_at) VALUES(?,?,'claimed',?,?)",
                  (row['request_id'],run,now,now))
        return dict(row)|{'run_id':run,'session_id':sid,'handoff_token':token}

def identity(pid):
    """Linux PID identity; fail closed if /proc cannot be examined."""
    raw=Path(f'/proc/{pid}/stat').read_text()
    start=raw[raw.rfind(')')+2:].split()[19] # stat field 22
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    return start,boot

def mark_running(db,rid,run,pid):
    start,boot=identity(pid)
    with transaction(db) as c:
        r=c.execute('SELECT phase FROM webchat_runs WHERE request_id=? AND run_id=?',(rid,run)).fetchone()
        if not r or r[0]!='claimed':raise RuntimeError('Execution binding changed')
        if c.execute("UPDATE f0_web_jobs SET state='seen',updated_at=? WHERE request_id=? AND state='handed_off'",(int(time.time()),rid)).rowcount!=1:raise RuntimeError('Queue state changed')
        c.execute("UPDATE webchat_runs SET phase='running',child_pid=?,child_start=?,boot_id=?,updated_at=? WHERE request_id=? AND run_id=?",
                  (pid,start,boot,int(time.time()),rid,run))

def finish(db,rid,run,markdown=None,error=None,*,pause_on_failure=False,failure_code=None):
    with transaction(db) as c:
        r=c.execute('SELECT phase FROM webchat_runs WHERE request_id=? AND run_id=?',(rid,run)).fetchone()
        if not r or r[0] not in ('claimed','running'):raise RuntimeError('Execution identity mismatch')
        j=c.execute('SELECT * FROM f0_web_jobs WHERE request_id=? AND state IN (\'handed_off\',\'seen\')',(rid,)).fetchone()
        if not j:raise RuntimeError('Unresolved claim missing')
        if error is not None:
            # Preserve request text in preview_closed_requests and its UUID tombstone.
            # The classification is deliberately bounded: never persist stderr, token,
            # user secrets or raw provider error messages in the event log.
            code = failure_code or 'unverified'
            if code not in ('quota','auth','timeout','empty_result','cli_failure',
                            'invalid_result','invalid_request','internal','unverified'):
                code='unverified'
            archive(c,j,'failed_unverified')
            c.execute('INSERT INTO webchat_failure_events VALUES (?,?,?,?)',
                      (rid,j['seq'],code,int(time.time())))
            queue_sync(c,rid,'system','A Webchat-kérés feldolgozása sikertelen, kézi ellenőrzés szükséges. Hibakód: '+code)
            if pause_on_failure:
                c.execute("INSERT INTO webchat_isolation_meta VALUES ('enabled','0') "
                          "ON CONFLICT(key) DO UPDATE SET value='0'")
        else:
            if not isinstance(markdown,str) or not markdown.strip() or len(markdown.encode('utf-8'))>1024*1024:raise RuntimeError('Unverified output')
            if c.execute("UPDATE f0_web_jobs SET state='answered',markdown=?,telegram_state='not_started',updated_at=? WHERE request_id=? AND state='seen'",
                         (markdown,int(time.time()),rid)).rowcount!=1:raise RuntimeError('Cannot mark answered without running')
            c.execute('DELETE FROM webchat_runs WHERE request_id=? AND run_id=?',(rid,run))
            queue_sync(c,rid,'assistant',markdown)

def old_child_alive(row):
    pid=row['child_pid']
    if not pid:return False
    try:
        start,boot=identity(pid)
        return start==row['child_start'] and boot==row['boot_id']
    except FileNotFoundError:return False
    except (OSError,ValueError,IndexError):return True

def reconcile(db,*,pause_on_archive=False):
    """Never continue if a previous Claude child of a claimed request is alive."""
    with transaction(db) as c:
        schema(c)
        runs=c.execute('SELECT * FROM webchat_runs').fetchall()
        for r in runs:
            if old_child_alive(r):return False
        if runs and pause_on_archive:
            c.execute("INSERT INTO webchat_isolation_meta VALUES('enabled','0') "
                      "ON CONFLICT(key) DO UPDATE SET value='0'")
        for r in runs:
            row=c.execute("SELECT * FROM f0_web_jobs WHERE request_id=? AND state IN ('handed_off','seen')",(r['request_id'],)).fetchone()
            if row:archive(c,row,'failed_unverified')
            else:c.execute('DELETE FROM webchat_runs WHERE request_id=?',(r['request_id'],))
        # Crash between state transition and run-table initialization must be a STOP.
        if c.execute("SELECT 1 FROM f0_web_jobs WHERE state IN ('handed_off','seen')").fetchone():raise RuntimeError('Orphan legacy active state')
        return True

if __name__=='__main__':
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('--db',type=Path,required=True)
    gp=ap.add_mutually_exclusive_group(required=True);gp.add_argument('--status',action='store_true');gp.add_argument('--enable',action='store_true');gp.add_argument('--pause',action='store_true')
    a=ap.parse_args()
    if a.status:
        with sqlite3.connect(a.db) as con:
            print(json.dumps({'enabled':enabled(a.db),'jobs':con.execute('SELECT seq,state FROM f0_web_jobs ORDER BY seq DESC LIMIT 12').fetchall(),'held':con.execute('SELECT COUNT(*) FROM webchat_held_requests').fetchone()[0]},ensure_ascii=False))
    else:set_enabled(a.db,a.enable)
