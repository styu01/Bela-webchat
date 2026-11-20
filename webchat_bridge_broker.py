#!/usr/bin/env python3
"""P5.9.7.2 Marveen-side, single-purpose Webchat IPC broker.

Owns the sensitive Marveen dashboard token and DB access. Neither is shared
with the sandboxed Webchat worker. Same-UID peers reach this process via a
mode-0600 AF_UNIX socket; only probe, submit_once and result_by_id exist.
No Claude CLI, tmux, Telegram service or broad API proxy is provided.
"""
import argparse
import json
import os
import signal
import re
import socket
import socketserver
import sqlite3
import stat
import struct
import sys
import threading
import time
import urllib.request
from contextlib import closing
from pathlib import Path

HOME = Path.home()
MARVEEN = HOME / 'marveen'
WEBROOT = HOME / 'bela-f0-dev/webchat-preview/project'
WEBDB = WEBROOT / 'store/f0-webchat.sqlite3'
SOCK = WEBROOT / 'store/p597-main-broker.sock'
LEDGER = WEBROOT / 'store/p597-main-broker-ledger.sqlite3'
SENDER = 'webchat-relay'
UID_FORMAT = re.compile(r'^[a-f0-9]{8}-[a-f0-9]{4}-[1-8][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$', re.I)
MAX_INPUT = 32768
MAX_OUTPUT = 2 * 1024 * 1024

class Stop(RuntimeError):
    pass


def dashboard_url():
    value = os.getenv('BELA_MARVEEN_WEB_PORT', '3420')
    if not re.fullmatch(r'[0-9]{4,5}', value) or not 1024 <= int(value) <= 65535:
        raise Stop('Invalid Marveen dashboard port')
    return 'http://127.0.0.1:' + str(int(value))


def dashboard_token(root=MARVEEN):
    path = root/'store/.dashboard-token'
    if path.is_symlink() or not path.is_file():
        raise Stop('Dashboard token not found by broker')
    fs = path.stat()
    if fs.st_uid != os.getuid() or fs.st_mode & (stat.S_IRWXG | stat.S_IRWXO) or not 16 <= fs.st_size <= 4096:
        raise Stop('Unsafe broker dashboard token permissions')
    token = path.read_text().strip()
    if not token or any(ch.isspace() for ch in token):
        raise Stop('Invalid broker dashboard token')
    return token


def request(url, token, method, path, data=None, opener=None):
    if not url.startswith('http://127.0.0.1:'):
        raise Stop('Refusing non-loopback Marveen URL')
    raw = None if data is None else json.dumps(data, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(url+path, data=raw, method=method, headers={
        'Authorization': 'Bearer '+token,
        'Content-Type': 'application/json; charset=utf-8',
    })
    client = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with client.open(req, timeout=7) as res:
        if res.status not in (200, 201):
            raise Stop('Marveen dashboard rejected request')
        result = json.loads(res.read(1024*1024))
    return result


def probe_main(url, token, opener=None):
    data = request(url, token, 'GET', '/api/messages?agent=bela&limit=1', opener=opener)
    if not isinstance(data, list):
        raise Stop('Unexpected dashboard probe response')


def validate_webchat_request(db, rid):
    """Restrict submit to a real, already-fenced Webchat owner request."""
    try:
        with closing(sqlite3.connect(db.resolve().as_uri()+'?mode=ro', uri=True, timeout=3)) as c:
            c.execute('PRAGMA query_only=ON')
            row = c.execute('''SELECT j.state,l.phase,m.value
            FROM f0_web_jobs j
            JOIN webchat_main_links l ON l.request_id=j.request_id
            JOIN webchat_isolation_meta m ON m.key='enabled'
            WHERE j.request_id=?''', (rid,)).fetchone()
    except sqlite3.Error as e:
        raise Stop('Webchat correlation table unreadable') from e
    if row != ('handed_off', 'posting', '1'):
        raise Stop('No enabled, fenced owner Webchat request')


def open_ro_marveen(db):
    if db.is_symlink() or not db.is_file():
        raise Stop('Marveen DB missing')
    c = sqlite3.connect(db.resolve().as_uri()+'?mode=ro', uri=True, timeout=3)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA query_only=ON')
    return c


def init_ledger(path):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink():
        raise Stop('Unsafe broker ledger path')
    with closing(sqlite3.connect(path, timeout=5)) as c:
        c.execute('PRAGMA busy_timeout=5000')
        c.execute('''CREATE TABLE IF NOT EXISTS submissions (
            request_id TEXT PRIMARY KEY, started_at INTEGER NOT NULL
        )''')
        c.commit()
    os.chmod(path, 0o600)


def fence_once(ledger, rid):
    with closing(sqlite3.connect(ledger, timeout=5)) as c:
        c.execute('PRAGMA busy_timeout=5000')
        try:
            c.execute('INSERT INTO submissions(request_id,started_at) VALUES(?,?)', (rid, int(time.time())))
            c.commit()  # DURABLE before any non-idempotent POST
        except sqlite3.IntegrityError as e:
            raise Stop('Duplicate dispatch blocked by broker ledger') from e


class Broker:
    def __init__(self, webdb=WEBDB, maindb=MARVEEN/'store/claudeclaw.db',
                 ledger=LEDGER, url=None, token=None, opener=None):
        self.webdb = Path(webdb)
        self.maindb = Path(maindb)
        self.ledger = Path(ledger)
        self.url = dashboard_url() if url is None else url
        self.token = dashboard_token() if token is None else token
        self.opener = opener

    def probe(self):
        probe_main(self.url, self.token, self.opener)
        with closing(open_ro_marveen(self.maindb)) as c:
            c.execute('SELECT id,from_agent,to_agent,status,result FROM agent_messages LIMIT 0')
        init_ledger(self.ledger)
        return {'ok': True}

    def post(self, rid, prompt):
        if not isinstance(rid, str) or not UID_FORMAT.fullmatch(rid):
            raise Stop('Invalid Webchat request id')
        if not isinstance(prompt, str) or not 0 < len(prompt) <= 20000 or rid not in prompt or not prompt.startswith('[WEBCHAT OWNER REQUEST / MAIN BELA SESSION]'):
            raise Stop('Invalid owner relay request')
        validate_webchat_request(self.webdb, rid)
        init_ledger(self.ledger)
        fence_once(self.ledger, rid)
        # NEVER retry this call if it fails: remote submission status is ambiguous.
        data = request(self.url, self.token, 'POST', '/api/messages', {
            'from': SENDER,
            'to': 'bela',
            'content': prompt,
            'origin_note': 'webchat-owner-relay',
        }, self.opener)
        if not isinstance(data, dict) or type(data.get('id')) is not int or data['id'] <= 0:
            raise Stop('Marveen did not return valid message id')
        return {'id': data['id']}

    def result(self, rid, message_id):
        if not isinstance(rid, str) or not UID_FORMAT.fullmatch(rid) or type(message_id) is not int or message_id <= 0:
            raise Stop('Invalid result query')
        with closing(sqlite3.connect(self.webdb.resolve().as_uri()+'?mode=ro', uri=True, timeout=3)) as c:
            c.execute('PRAGMA query_only=ON')
            link = c.execute('SELECT phase,remote_id FROM webchat_main_links WHERE request_id=?', (rid,)).fetchone()
            if not link or link != ('submitted', message_id):
                raise Stop('Webchat correlation mismatch')
        with closing(open_ro_marveen(self.maindb)) as c:
            m = c.execute('''SELECT id,from_agent,to_agent,content,status,result
                 FROM agent_messages WHERE id=?''', (message_id,)).fetchone()
        if m is None or m['from_agent'] != SENDER or m['to_agent'] != 'bela' or rid not in m['content']:
            raise Stop('Marveen provenance mismatch')
        if m['status'] not in ('pending', 'delivered', 'done', 'failed'):
            raise Stop('Invalid Marveen message status')
        # Return ONLY the verified caller's own status/result, not source prompt.
        result = m['result'] if m['status'] in ('done', 'failed') else None
        if result is not None and (not isinstance(result, str) or len(result.encode()) > 1024 * 1024):
            raise Stop('Marveen result too large')
        return {'id': message_id, 'status': m['status'], 'result': result}

    def handle(self, obj):
        if not isinstance(obj, dict) or set(obj.keys()) - {'op', 'rid', 'prompt', 'id'}:
            raise Stop('Invalid broker request shape')
        op = obj.get('op')
        if op == 'probe' and set(obj) == {'op'}:
            return self.probe()
        if op == 'post' and set(obj) == {'op','rid','prompt'}:
            return self.post(obj['rid'], obj['prompt'])
        if op == 'result' and set(obj) == {'op','rid','id'}:
            return self.result(obj['rid'], obj['id'])
        raise Stop('Broker operation forbidden')


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        conn = self.request
        conn.settimeout(8)
        # Linux SO_PEERCRED; no access from another UID even on same host.
        pid, uid, gid = struct.unpack('3i', conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
        if uid != os.getuid():
            return
        raw = self.rfile.readline(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT or not raw.endswith(b'\n'):
            return
        try:
            response = {'ok': True, 'data': self.server.broker.handle(json.loads(raw))}
        except (Stop, KeyError, TypeError, ValueError, sqlite3.Error, OSError) as e:
            # Error contains only a fixed class/short string, never a prompt, token or a DB row.
            response = {'ok': False, 'error': str(e)[:180]}
        payload = (json.dumps(response, ensure_ascii=False) + '\n').encode()
        if len(payload) <= MAX_OUTPUT:
            try:self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):pass


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = False
    def __init__(self, path, broker):
        self.broker = broker
        super().__init__(str(path), Handler)


def serve(sock=SOCK):
    broker=Broker()
    broker.probe()  # fail-closed before publishing socket
    sock=Path(sock)
    if sock.is_symlink():raise Stop('Broker socket symlink refused')
    if sock.exists():
        st=sock.lstat()
        if not stat.S_ISSOCK(st.st_mode) or st.st_uid != os.getuid():
            raise Stop('Unexpected existing broker socket path')
        # Refuse to replace an active service; remove only an unresponsive stale socket.
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                s.connect(str(sock))
                raise Stop('Broker already active')
        except (ConnectionRefusedError, FileNotFoundError):
            sock.unlink()
    with Server(sock, broker) as server:
        os.chmod(sock, 0o600)
        print('[P597 BROKER] ready; restricted AF_UNIX IPC, no secret forwarding', flush=True)
        previous=signal.getsignal(signal.SIGTERM)
        def terminate(signum,frame):
            # shutdown() must be called from ANOTHER thread than serve_forever().
            threading.Thread(target=server.shutdown,daemon=True).start()
        signal.signal(signal.SIGTERM,terminate)
        try:server.serve_forever(poll_interval=0.3)
        finally:
            signal.signal(signal.SIGTERM,previous)
            if sock.exists() and stat.S_ISSOCK(sock.lstat().st_mode):sock.unlink()


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--service', action='store_true')
    opts=ap.parse_args()
    if not opts.service:ap.error('Use --service')
    os.umask(0o077)
    serve()

if __name__ == '__main__':
    try: main()
    except (Stop, OSError, sqlite3.Error) as e:
        print('[P597 BROKER] STOP '+type(e).__name__+': '+str(e), file=sys.stderr)
        raise SystemExit(2)
