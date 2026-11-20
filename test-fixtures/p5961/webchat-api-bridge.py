#!/usr/bin/env python3
"""Authenticated local Node bridge invokes this script. Webchat queue ONLY."""
import json, sqlite3, sys, unicodedata, uuid
from pathlib import Path
import webchat_f0_queue as q
import webchat_isolation as iso

def valid_text(v):
    return isinstance(v,str) and 0<len(v)<=800 and bool(v.strip()) and all(
        (not unicodedata.category(ch).startswith('C') or ch=='\n') and ch not in '\u2028\u2029' for ch in v)

def handle(db,data):
    op=data.get('op');rid=data.get('request_id')
    try:
        if str(uuid.UUID(rid))!=rid:raise ValueError('Noncanonical UUID')
    except (ValueError,TypeError,AttributeError):return {'status':400,'body':{'error':'Invalid request ID'}}
    if op=='enqueue':
        value=data.get('text')
        if not valid_text(value):return {'status':400,'body':{'error':'Invalid preview text'}}
        try:
            with iso.transaction(db) as c:
                # Reject new work while the Claude dispatcher is paused. A UUID
                # already accepted remains queryable through the status route.
                flag=c.execute("SELECT value FROM webchat_isolation_meta WHERE key='enabled'").fetchone()
                if flag is None or flag[0]!='1':
                    return {'status':503,'body':{'error':'Webchat processing paused',
                                                  'code':'dispatch_paused'}}
                status,state=iso.enqueue(c,rid,value)
            return {'status':status,'body':{'request_id':rid,'state':state} if status==202 else {'error':state}}
        except sqlite3.Error:return {'status':503,'body':{'error':'Queue locked/unavailable'}}
    if op=='status':
        with sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True,timeout=2) as c:
            row=c.execute('SELECT request_id,state,markdown,telegram_state FROM f0_web_jobs WHERE request_id=?',(rid,)).fetchone()
            if row:return {'status':200,'body':dict(zip(('request_id','state','markdown','telegram_state'),row))}
            archived=c.execute('SELECT reason FROM webchat_tombstones WHERE request_id=?',(rid,)).fetchone()
            if archived:return {'status':200,'body':{'request_id':rid,'state':archived[0]}}
        return {'status':404,'body':{'error':'Not found'}}
    return {'status':400,'body':{'error':'Invalid operation'}}

def main():
    try:
        db=Path(sys.argv[1]);data=json.load(sys.stdin)
        out=handle(db,data)
    except Exception:
        out={'status':503,'body':{'error':'Queue unavailable'}}
    print(json.dumps(out,ensure_ascii=False))
if __name__=='__main__':main()
