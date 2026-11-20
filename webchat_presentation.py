"""Read-only presentation helpers extracted from P5.8.4.8. No tmux, CLI or Telegram sending."""
import json, re, os, unicodedata, hashlib, stat, uuid
from pathlib import Path
LONG_MARKER='A teljes keresem a csatolt bela-webchat-teljes-keres.txt fajlban van. Olvasd el es hajtsd vegre.'
LONG_FILENAME='bela-webchat-teljes-keres.txt'
BATCH_SUFFIX=re.compile(r'\nCsatolt fajlok jegyzeke: (/[^\r\n]+/uploads/[a-f0-9]{32}\.json)\Z')
def echo_text(text):
    return '🌐 Webről írtad: ' + text

def split_batch(text):
    m = BATCH_SUFFIX.search(text)
    return (text[:m.start()], m[1]) if m else (text, None)

def verified_long_content(text, root):
    """Read exact owner-generated TXT from the v2 manifest, never from a browser path.

    The original request is a private upload, not a general privilege escalation
    for arbitrary user-controlled files. This function is read-only.
    """
    import hashlib, stat
    body, manifest = split_batch(text)
    if body != LONG_MARKER or not manifest:
        raise ValueError('not_long_transport')
    directory = root / 'uploads'
    path = Path(manifest)
    if not re.fullmatch('[a-f0-9]{32}\\.json', path.name) or path.parent != directory or directory.is_symlink() or (directory.resolve() != directory) or path.is_symlink() or (not path.is_file()):
        raise ValueError('long_manifest_location')
    meta = json.loads(path.read_text(encoding='utf-8'))
    items = meta.get('files')
    if meta.get('version') != 2 or not isinstance(items, list) or (not 1 <= len(items) <= 20) or (path.stat().st_size > 65536):
        raise ValueError('long_manifest_invalid')
    special = [x for x in items if isinstance(x, dict) and x.get('name') == LONG_FILENAME]
    if len(special) != 1:
        raise ValueError('long_original_missing_or_ambiguous')
    entry = special[0]
    uid = entry.get('id')
    size = entry.get('size')
    digest = entry.get('sha256')
    if not isinstance(uid, str) or not re.fullmatch('[a-f0-9]{32}\\.txt', uid) or type(size) is not int or (not 0 < size <= 25000) or (not isinstance(digest, str)) or (not re.fullmatch('[a-f0-9]{64}', digest)):
        raise ValueError('long_original_metadata')
    fd = os.open(directory / uid, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_mode & 63 or before.st_size != size:
            raise ValueError('long_original_unsafe')
        data = b''
        while len(data) < size:
            b = os.read(fd, min(32768, size - len(data)))
            if not b:
                break
            data += b
        after = os.fstat(fd)
        if len(data) != size or after.st_ino != before.st_ino or after.st_size != size or (after.st_mtime_ns != before.st_mtime_ns) or (hashlib.sha256(data).hexdigest() != digest):
            raise ValueError('long_original_modified')
    finally:
        os.close(fd)
    content = data.decode('utf-8')
    if not (1 <= len(content) <= 5000 and content.strip() and (not any((unicodedata.category(c).startswith('C') and c != '\n' or c in '\u2028\u2029' for c in content)))):
        raise ValueError('long_original_text_invalid')
    return content

def display_status(job):
    """Queue truth controls work locks, never the Telegram sidecar state."""
    record = job.get('delivery') or {}
    if job['state'] == 'answered':
        state = 'completed_no_text' if record.get('answer_source') == 'no_text' else 'completed'
    else:
        state = {'unknown': 'uncertain', 'queued': 'waiting', 'handed_off': 'running', 'seen': 'running', 'failed_unverified': 'uncertain', 'legacy_75_failed_unverified': 'uncertain'}.get(job['state'])
    projection = record.get('telegram')
    if not isinstance(projection, dict) or projection.get('state') not in ('waiting', 'delivered', 'partial', 'blocked_by_gate', 'uncertain'):
        confirmed = record.get('confirmed') is True
        projection = {'state': 'delivered' if confirmed and record.get('echo') else 'partial' if confirmed else 'uncertain' if job['state'] in ('answered', 'unknown') else 'waiting', 'parts': [], 'anomalies': []}
    return {'work_state': state, 'telegram': projection, 'webchat_isolated': job.get('webchat_isolated', False)}

def web_attachment(text, root):
    """Recognize only the exact generated final upload suffix, never arbitrary paths."""
    prefix = '\nCsatolt fájl (helyi útvonal): '
    body, sep, raw = text.rpartition(prefix)
    if not sep or not raw.startswith(str(root / 'uploads') + '/'):
        return (text, None)
    name = raw[len(str(root / 'uploads')) + 1:]
    if not re.fullmatch('[a-f0-9]{32}\\.(txt|md|csv|json|pdf|png|jpg|jpeg|webp)', name):
        return (text, None)
    path = root / 'uploads' / name
    try:
        st = path.lstat()
        if path.is_symlink() or not path.is_file() or path.resolve() != path or (st.st_size < 1) or (st.st_size > 30 * 1024 * 1024):
            return (body, {'name': name, 'available': False, 'image': False})
    except OSError:
        return (body, {'name': name, 'available': False, 'image': False})
    return (body, {'name': name, 'available': True, 'image': name.rsplit('.', 1)[1] in ('png', 'jpg', 'jpeg', 'webp')})

def batch_attachments(text, root):
    body, sep, raw = text.rpartition('\nCsatolt fajlok jegyzeke: ')
    if not sep:
        return (text, None)
    _, manifest = web_attachment('x\nCsatolt fájl (helyi útvonal): ' + raw, root)
    if not manifest or not manifest['available'] or (not manifest['name'].endswith('.json')):
        return (body, [])
    try:
        path = root / 'uploads' / manifest['name']
        if path.stat().st_size > 65536:
            return (body, [])
        data = json.loads(path.read_text())
        items = data['files']
        if data['version'] not in (1, 2) or not isinstance(items, list) or (not 1 <= len(items) <= 20):
            return (body, [])
        result = []
        seen = set()
        for item in items:
            name = item['id']
            expected = str(root / 'uploads' / name)
            if name in seen:
                return (body, [])
            if data['version'] == 1 and item['path'] != expected:
                return (body, [])
            if data['version'] == 2 and ('path' in item or type(item.get('size')) is not int or (not 0 < item['size'] <= 30 * 1024 * 1024) or (not isinstance(item.get('sha256'), str)) or (not re.fullmatch('[a-f0-9]{64}', item['sha256']))):
                return (body, [])
            _, a = web_attachment('x\nCsatolt fájl (helyi útvonal): ' + expected, root)
            if not a:
                return (body, [])
            seen.add(name)
            a['display_name'] = str(item['name'])[:150]
            result.append(a)
        return (body, result)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return (body, [])

def decorate_uploads(entries, root):
    gallery = []
    for e in entries:
        if e['role'] == 'assistant' and e['key'].startswith('t:') and e['text'].startswith('🌐 Webről írtad: '):
            body = re.sub('\\n\\n?Csatolt (?:fajlok jegyzeke|fájlok jegyzéke): ', '\nCsatolt fajlok jegyzeke: ', e['text'])
            clean, manifest = split_batch(body)
            if manifest and Path(manifest).parent == root / 'uploads':
                e['text'] = clean
        if e['role'] != 'user' or not e['key'].startswith('w:'):
            continue
        text, items = batch_attachments(e['text'], root)
        if items is not None:
            if text == LONG_MARKER:
                try:
                    e['text'] = verified_long_content(e['text'], root)
                except (OSError, ValueError, TypeError, KeyError, UnicodeError):
                    e['text'] = '[A hosszú kérés csatolt tartalma nem ellenőrizhető]'
            else:
                e['text'] = text
            e['uploads'] = items
            for a in items:
                if a['image'] and a['available']:
                    gallery.append({'key': e['key'] + ':' + a['name'], 'upload': a, 'at': e['at'], 'text': text})
            continue
        text, attachment = web_attachment(e['text'], root)
        if attachment:
            e['text'] = text
            e['upload'] = attachment
            if attachment['image']:
                gallery.append({'key': e['key'], 'upload': attachment, 'at': e['at'], 'text': text})
    return gallery

def merge_timeline(jobs, telegram):
    """Combine the owner-scoped ledger with web jobs. Never match by text alone.

    An exact, confirmed Telegram message ID AND exact content are required to
    collapse a delivery. If content differs, retain the Telegram projection.
    Ledger timestamps are ingestion times, not necessarily original send times.
    """
    modern = [j for j in jobs if (j.get('delivery') or {}).get('version') == 3]
    if modern:
        return merge_p58_timeline(modern, [j for j in jobs if j not in modern], telegram)
    entries = []
    by_id = {}
    for t in telegram:
        if t['direction'] == 'out' and t.get('message_id'):
            by_id.setdefault(str(t['message_id']), []).append(t)
    suppressed = set()
    for j in jobs:
        rid = j['request_id']
        created = j['created_at']
        updated = j['updated_at']
        delivery = j.get('delivery') or {}
        status = display_status(j)
        echo = delivery.get('echo') or {}
        ec = by_id.get(str(echo.get('message_id')), []) if echo.get('message_id') else []
        echo_linked = len(ec) == 1 and (not ec[0].get('truncated')) and (ec[0]['text'] == echo.get('text')) and (ec[0]['id'] not in suppressed)
        if echo_linked:
            suppressed.add(ec[0]['id'])
        entries.append({'key': 'w:' + rid + ':in', 'at': created, 'order': 0, 'seq': j['seq'], 'role': 'user', 'source': 'web+telegram' if echo_linked else 'web', 'text': j['request_text'], 'state': j['state'], 'failure_code': j.get('failure_code'), **status})
        if j['state'] == 'answered' and j.get('markdown'):
            delivery = j.get('delivery') or {}
            mid = delivery.get('message_id')
            candidates = by_id.get(str(mid), []) if delivery.get('confirmed') and mid else []
            match = candidates[0] if len(candidates) == 1 else None
            linked = bool(match and (not match.get('truncated')) and ((delivery.get('answer') or {}).get('text') == match['text'] if delivery.get('answer') else delivery.get('same_markdown') and match['text'] == j['markdown']) and (match['id'] not in suppressed))
            if linked:
                suppressed.add(match['id'])
            entries.append({'key': 'w:' + rid + ':out', 'at': max(created, updated), 'order': 2, 'seq': j['seq'], 'role': 'assistant', 'source': 'web+telegram' if linked else 'web', 'text': j['markdown'], 'markdown': delivery.get('answer_source') not in ('telegram', 'no_text'), 'telegram_format': delivery.get('answer_source') == 'telegram' and (delivery.get('answer') or {}).get('format') == 'markdownv2', 'answer_source': delivery.get('answer_source') or 'markdown', 'delivery': {'confirmed': delivery.get('confirmed') is True, 'same_markdown': delivery.get('same_markdown') is True, 'merged': linked}, **status})
        else:
            entries.append({'key': 'w:' + rid + ':status', 'at': max(created, updated), 'order': 2, 'seq': j['seq'], 'role': 'status', 'source': 'web', 'text': '', 'state': j['state'], 'failure_code': j.get('failure_code'), **status})
    for t in telegram:
        if t['id'] in suppressed:
            continue
        entries.append({'key': 't:' + str(t['id']), 'at': t['created_at'], 'order': 1, 'seq': t['id'], 'role': 'user' if t['direction'] == 'in' else 'assistant', 'source': 'telegram', 'text': t['text'], 'telegram_format': t['direction'] == 'out', 'attachment': t.get('attachment_kind'), 'image': t.get('image'), 'video': t.get('video'), 'video_state': t.get('video_state')})
    entries.sort(key=lambda e: (e['at'], e['order'], e['seq'], e['key']))
    return entries

def merge_p58_timeline(modern, legacy, telegram):
    used = set()
    parts = []
    counts = {}
    for t in telegram:
        if t['direction'] == 'out' and t.get('message_id'):
            counts[str(t['message_id'])] = counts.get(str(t['message_id']), 0) + 1
    for job in modern:
        status = display_status(job)
        record = job['delivery']
        emitted = set()
        shown = set()
        long_owner = split_batch(job['request_text'])[0] == LONG_MARKER
        long_echo_displayed = False
        for part in record.get('telegram', {}).get('parts', []):
            ids = part.get('message_ids', [])
            if part.get('state') != 'delivered' or len(ids) != 1:
                continue
            mid = str(ids[0])
            if not re.fullmatch('[1-9][0-9]*', mid) or mid in used:
                continue
            hits = [t for t in telegram if t['direction'] == 'out' and str(t.get('message_id')) == mid]
            if hits and (len(hits) != 1 or hits[0].get('truncated') or hits[0]['text'] != part.get('text')):
                continue
            used.add(mid)
            emitted.add(part['role'])
            shown.add(part['ordinal'])
            if part['role'] == 'photo':
                continue
            if part['role'] == 'echo' and long_owner:
                if long_echo_displayed:
                    continue
                long_echo_displayed = True
            parts.append({'key': 'w:' + job['request_id'] + ':' + str(part['ordinal']), 'at': hits[0]['created_at'] if hits else job['updated_at'], 'order': 1, 'seq': job['seq'], 'role': 'user' if part['role'] == 'echo' else 'assistant', 'source': 'web+telegram', 'text': job['request_text'] if part['role'] == 'echo' else part['text'], 'markdown': part['role'] == 'answer', 'telegram_format': False, 'telegram_message_id': mid, 'mirror_part': True, **status})
        projected = record.get('telegram', {}).get('parts', [])
        for part in projected:
            if part['ordinal'] in shown:
                continue
            if part['role'] == 'photo':
                continue
            if part['role'] == 'echo' and long_owner:
                if long_echo_displayed:
                    continue
                long_echo_displayed = True
            parts.append({'key': 'w:' + job['request_id'] + ':' + str(part['ordinal']), 'at': job['updated_at'], 'order': 1, 'seq': job['seq'], 'role': 'user' if part['role'] == 'echo' else 'assistant', 'source': 'web', 'text': job['request_text'] if part['role'] == 'echo' else part.get('text', ''), 'markdown': part['role'] == 'answer', 'telegram_format': False, 'pending_mirror': True, **status})
        if not projected or record.get('answer_source') == 'no_text':
            pending = dict(job, delivery={**record, 'version': 2})
            for entry in merge_timeline([pending], []):
                if projected and entry['role'] == 'user':
                    continue
                entry['pending_mirror'] = True
                parts.append(entry)
    rest = [t for t in telegram if not (t['direction'] == 'out' and str(t.get('message_id')) in used)]
    entries = merge_timeline(legacy, rest) + parts
    rest_ids = {'t:' + str(t['id']): str(t.get('message_id', '')) for t in rest}
    old_ids = {}
    for job in legacy:
        d = job.get('delivery') or {}
        if d.get('echo'):
            old_ids['w:' + job['request_id'] + ':in'] = d['echo'].get('message_id')
        if d.get('confirmed'):
            old_ids['w:' + job['request_id'] + ':out'] = d.get('message_id')
    for e in entries:
        mid = e.get('telegram_message_id') or rest_ids.get(e['key']) or (old_ids.get(e['key']) if e.get('source') == 'web+telegram' else None)
        if mid and re.fullmatch('[1-9][0-9]*', mid):
            e['_mid'] = int(mid)
    confirmed = sorted((e for e in entries if '_mid' in e), key=lambda e: e['_mid'])
    pending = sorted((e for e in entries if '_mid' not in e), key=lambda e: (e['at'], e['order'], e['seq'], e['key']))
    return confirmed + pending
