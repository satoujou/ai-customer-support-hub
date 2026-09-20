import os, json
from contextlib import contextmanager
from datetime import datetime
import psycopg
from psycopg.rows import dict_row
from zoneinfo import ZoneInfo

DATABASE_URL=os.getenv('DATABASE_URL','').strip()

def _url():
    if not DATABASE_URL:
        raise RuntimeError('DATABASE_URL が設定されていません。')
    return DATABASE_URL

@contextmanager
def conn():
    with psycopg.connect(_url(), row_factory=dict_row) as c:
        yield c

def init_db():
    with conn() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS companies(
          company_id text PRIMARY KEY, company_name text NOT NULL, active boolean NOT NULL DEFAULT true,
          created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now())''')
        c.execute('''CREATE TABLE IF NOT EXISTS users(
          username text PRIMARY KEY, company_id text NOT NULL REFERENCES companies(company_id), role text NOT NULL,
          password_hash text NOT NULL, active boolean NOT NULL DEFAULT true,
          created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now())''')
        c.execute('''CREATE TABLE IF NOT EXISTS inquiries(
          id bigserial PRIMARY KEY, company_id text NOT NULL REFERENCES companies(company_id),
          datetime text NOT NULL, inquiry text NOT NULL, category text, priority text, summary text,
          recommended_action text, reception_number text NOT NULL UNIQUE, status text, assignee text,
          response_history text, internal_note text, due_date text, customer_name text, contact text,
          channel text, tags text, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now())''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_inquiries_company ON inquiries(company_id)')
        c.execute('CREATE INDEX IF NOT EXISTS idx_inquiries_company_status ON inquiries(company_id,status)')
        c.execute('''CREATE TABLE IF NOT EXISTS audit_logs(
          id bigserial PRIMARY KEY, timestamp timestamptz NOT NULL DEFAULT now(), user_name text, role text,
          company_id text NOT NULL, action text NOT NULL, target text, details jsonb NOT NULL DEFAULT '{}'::jsonb)''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_audit_company_time ON audit_logs(company_id,timestamp DESC)')
        c.commit()

def ensure_company(company_id, company_name=None, active=True):
    with conn() as c:
        c.execute('''INSERT INTO companies(company_id,company_name,active) VALUES(%s,%s,%s)
          ON CONFLICT(company_id) DO UPDATE SET company_name=EXCLUDED.company_name, active=EXCLUDED.active, updated_at=now()''',
          (company_id, company_name or company_id, active)); c.commit()


def get_company(company_id):
    with conn() as c:
        r=c.execute('SELECT * FROM companies WHERE company_id=%s',(company_id,)).fetchone()
        return dict(r) if r else None

def list_companies():
    with conn() as c: return [dict(x) for x in c.execute('SELECT * FROM companies ORDER BY company_id').fetchall()]

def update_company_db(company_id,name,active):
    with conn() as c:
        r=c.execute('UPDATE companies SET company_name=%s,active=%s,updated_at=now() WHERE company_id=%s RETURNING company_id',(name,active,company_id)).fetchone(); c.commit(); return bool(r)

def list_users():
    with conn() as c: return [dict(x) for x in c.execute('SELECT * FROM users ORDER BY username').fetchall()]

def get_user(username):
    with conn() as c:
        r=c.execute('SELECT * FROM users WHERE username=%s',(username,)).fetchone(); return dict(r) if r else None

def upsert_user(u):
    with conn() as c:
        c.execute('''INSERT INTO users(username,company_id,role,password_hash,active) VALUES(%s,%s,%s,%s,%s)
          ON CONFLICT(username) DO UPDATE SET company_id=EXCLUDED.company_id,role=EXCLUDED.role,password_hash=EXCLUDED.password_hash,active=EXCLUDED.active,updated_at=now()''',
          (u['username'],u['company_id'],u['role'],u['password_hash'],u.get('active',True))); c.commit()

def update_user_db(username, company_id, role, active, password_hash=None):
    with conn() as c:
        if password_hash:
            r=c.execute('UPDATE users SET role=%s,active=%s,password_hash=%s,updated_at=now() WHERE username=%s AND company_id=%s RETURNING username',(role,active,password_hash,username,company_id)).fetchone()
        else:
            r=c.execute('UPDATE users SET role=%s,active=%s,updated_at=now() WHERE username=%s AND company_id=%s RETURNING username',(role,active,username,company_id)).fetchone()
        c.commit(); return bool(r)

def add_audit(company_id,user,role,action,target='',details=None):
    with conn() as c:
        c.execute('INSERT INTO audit_logs(company_id,user_name,role,action,target,details) VALUES(%s,%s,%s,%s,%s,%s::jsonb)',(company_id,user,role,action,target,json.dumps(details or {},ensure_ascii=False,default=str))); c.commit()

def get_audits(company_id,limit=500):
    with conn() as c:
        rows=c.execute('SELECT timestamp,user_name AS user,role,company_id,action,target,details FROM audit_logs WHERE company_id=%s ORDER BY timestamp DESC LIMIT %s',(company_id,limit)).fetchall()
        out=[]
        for r in rows:
            d = dict(r)
            d["timestamp"] = d["timestamp"].astimezone(ZoneInfo("Asia/Tokyo")).isoformat(timespec="seconds")
            out.append(d)

        return out
# v5.1: tenant-scoped data access. Application code should prefer these functions.
def list_users_by_company(company_id):
    with conn() as c:
        return [dict(x) for x in c.execute(
            'SELECT * FROM users WHERE company_id=%s ORDER BY username', (company_id,)
        ).fetchall()]

def get_inquiries_by_company(company_id):
    with conn() as c:
        return [dict(x) for x in c.execute(
            'SELECT * FROM inquiries WHERE company_id=%s ORDER BY id DESC', (company_id,)
        ).fetchall()]

def get_urgent_inquiries_by_company(company_id):
    with conn() as c:
        return [dict(x) for x in c.execute(
            "SELECT * FROM inquiries WHERE company_id=%s AND priority='高' AND COALESCE(status,'未対応')='未対応' ORDER BY id DESC",
            (company_id,)
        ).fetchall()]

def get_inquiry_by_company(company_id, reception_number):
    with conn() as c:
        r=c.execute(
            'SELECT * FROM inquiries WHERE company_id=%s AND reception_number=%s',
            (company_id,reception_number)
        ).fetchone()
        return dict(r) if r else None

def company_usage_stats():
    with conn() as c:
        rows=c.execute("""
            SELECT co.company_id, co.company_name, co.active,
                   COUNT(DISTINCT u.username) AS user_count,
                   COUNT(DISTINCT i.id) AS inquiry_count,
                   COUNT(DISTINCT i.id) FILTER (WHERE COALESCE(i.status,'未対応') <> '完了') AS open_count
            FROM companies co
            LEFT JOIN users u ON u.company_id=co.company_id
            LEFT JOIN inquiries i ON i.company_id=co.company_id
            GROUP BY co.company_id,co.company_name,co.active
            ORDER BY co.company_id
        """).fetchall()
        return [dict(x) for x in rows]
