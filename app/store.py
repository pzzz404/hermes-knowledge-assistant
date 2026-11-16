"""Durable admission, idempotency and minimal 24-hour session storage."""
import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

class AppError(Exception):
    def __init__(self, code, message, http_status=503):
        self.code, self.message, self.http_status = code, message, http_status

class Store:
    def __init__(self, settings):
        self.settings = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        settings.data_dir.chmod(0o700)
        self.path = settings.data_dir / 'sessions.sqlite3'
        with self.connect() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, hermes_id TEXT, expires REAL, history TEXT);
            CREATE TABLE IF NOT EXISTS requests(sid TEXT, rid TEXT, digest TEXT, created REAL,
                code INTEGER, result TEXT, PRIMARY KEY(sid,rid));
            CREATE TABLE IF NOT EXISTS rate_events(sid TEXT, ip TEXT, created REAL);
            CREATE TABLE IF NOT EXISTS budget(day TEXT PRIMARY KEY, used INTEGER);
            CREATE TABLE IF NOT EXISTS leases(id TEXT PRIMARY KEY, sid TEXT UNIQUE, expires REAL);
            CREATE TABLE IF NOT EXISTS journal(rid TEXT, sid TEXT, corpus TEXT, evidence TEXT,
                latency REAL, status TEXT, model TEXT, usage TEXT, created REAL);
            ''')
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def cleanup(self, db):
        now=time.time()
        expired=[r[0] for r in db.execute('SELECT id FROM sessions WHERE expires<?',(now,))]
        for sid in expired:
            db.execute('DELETE FROM requests WHERE sid=?',(sid,))
            db.execute('DELETE FROM rate_events WHERE sid=?',(sid,))
        db.execute('DELETE FROM sessions WHERE expires<?',(now,))
        db.execute('DELETE FROM rate_events WHERE created<?',(now-60,))
        db.execute('DELETE FROM leases WHERE expires<?',(now,))
        db.execute('DELETE FROM journal WHERE created<?',(now-86400,))
        # A restart must not replay work whose upstream outcome is unknown.
        db.execute('''UPDATE requests SET code=503,result=? WHERE code=0
            AND NOT EXISTS (SELECT 1 FROM leases WHERE leases.id=requests.sid || ':' || requests.rid)''',
            (json.dumps({'error':{'code':'outcome_unknown','message':'Предыдущий запрос не завершился и не будет автоматически повторён.'}}),))

    def session(self, sid=None, fresh=False):
        now=time.time()
        with self.connect() as db:
            self.cleanup(db)
            row=db.execute('SELECT * FROM sessions WHERE id=?',(sid or '',)).fetchone()
            if row and not fresh:
                return dict(row)
            if fresh and row:
                db.execute('DELETE FROM requests WHERE sid=?',(sid,))
                db.execute('DELETE FROM sessions WHERE id=?',(sid,))
            sid=uuid.uuid4().hex
            row={'id':sid,'hermes_id':'knowledge-'+uuid.uuid4().hex,'expires':now+self.settings.session_ttl_hours*3600,'history':'[]'}
            db.execute('INSERT INTO sessions VALUES(:id,:hermes_id,:expires,:history)',row)
            return row

    def admit(self, sid, rid, question, ip):
        digest=hashlib.sha256(question.encode()).hexdigest()
        now=time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self.cleanup(db)
            old=db.execute('SELECT * FROM requests WHERE sid=? AND rid=?',(sid,rid)).fetchone()
            if old:
                if old['digest'] != digest:
                    raise AppError('request_conflict','Этот идентификатор уже использован для другого вопроса.',409)
                if old['code']==0:
                    raise AppError('request_in_progress','Ответ на этот вопрос ещё готовится.',409)
                return old['code'],json.loads(old['result'])
            for column,value,limit in [('sid',sid,self.settings.session_per_minute),('ip',ip,self.settings.ip_per_minute)]:
                count=db.execute(f'SELECT count(*) FROM rate_events WHERE {column}=? AND created>?',(value,now-60)).fetchone()[0]
                if count>=limit:
                    raise AppError('rate_limit','Слишком много вопросов. Подождите минуту.',429)
            if db.execute('SELECT 1 FROM leases WHERE sid=?',(sid,)).fetchone():
                raise AppError('conversation_busy','Дождитесь текущего ответа.',409)
            if db.execute('SELECT count(*) FROM leases').fetchone()[0]>=self.settings.max_parallel:
                raise AppError('busy','Помощник занят. Повторите попытку немного позже.',429)
            db.execute('INSERT INTO requests VALUES(?,?,?,?,0,NULL)',(sid,rid,digest,now))
            db.execute('INSERT INTO rate_events VALUES(?,?,?)',(sid,ip,now))
            db.execute('INSERT INTO leases VALUES(?,?,?)',(sid+':'+rid,sid,now+self.settings.timeout_seconds+15))
        return None

    def generation_limit(self, now=None):
        now=now or datetime.now(timezone.utc)
        if (self.settings.generation_budget_override and
                now.astimezone(ZoneInfo('Europe/Moscow')).date()==self.settings.generation_budget_override_date):
            return self.settings.generation_budget_override
        return self.settings.daily_generations

    def reserve_generation(self):
        day=datetime.now(timezone.utc).date().isoformat()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR IGNORE INTO budget VALUES(?,0)',(day,))
            used=db.execute('SELECT used FROM budget WHERE day=?',(day,)).fetchone()[0]
            if used>=self.generation_limit():
                raise AppError('daily_limit','Дневной лимит ответов исчерпан. Вернитесь завтра.',429)
            db.execute('UPDATE budget SET used=used+1 WHERE day=?',(day,))

    def finish(self, sid, rid, code, result, question=None, metadata=None):
        with self.connect() as db:
            db.execute('UPDATE requests SET code=?,result=? WHERE sid=? AND rid=?',(code,json.dumps(result),sid,rid))
            db.execute('DELETE FROM leases WHERE id=?',(sid+':'+rid,))
            if question and code==200:
                row=db.execute('SELECT history FROM sessions WHERE id=?',(sid,)).fetchone()
                if row:
                    history=json.loads(row[0]);history.append({'question':question,'response':result})
                    db.execute('UPDATE sessions SET history=? WHERE id=?',(json.dumps(history[-6:]),sid))
            meta=metadata or {}
            db.execute('INSERT INTO journal VALUES(?,?,?,?,?,?,?,?,?)',
                (rid,sid,result.get('corpus_version'),json.dumps(meta.get('evidence_ids',[])),
                 meta.get('latency'),result.get('status') or result.get('error',{}).get('code'),
                 meta.get('model'),json.dumps(meta.get('usage')),time.time()))
