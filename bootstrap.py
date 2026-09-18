"""Create the first tenant and administrator in a fresh v5.1 database.
Safe to run repeatedly: it refuses to overwrite an existing user.
"""
import os
import sys
import bcrypt
from dotenv import load_dotenv
from database import init_db, ensure_company, get_user, upsert_user

load_dotenv()

def required(name):
    value=(os.getenv(name) or '').strip()
    if not value:
        raise SystemExit(f'{name} が設定されていません。')
    return value

def main():
    company_id=(os.getenv('BOOTSTRAP_COMPANY_ID') or os.getenv('COMPANY_ID') or 'company_001').strip()
    company_name=(os.getenv('BOOTSTRAP_COMPANY_NAME') or os.getenv('COMPANY_NAME') or 'Demo Company').strip()
    username=(os.getenv('BOOTSTRAP_ADMIN_USER') or 'admin').strip()
    password=required('BOOTSTRAP_ADMIN_PASSWORD')
    if len(password) < 12:
        raise SystemExit('BOOTSTRAP_ADMIN_PASSWORD は12文字以上にしてください。')
    init_db()
    if get_user(username):
        print(f'SKIP: user {username!r} already exists. Nothing changed.')
        return
    ensure_company(company_id, company_name, True)
    password_hash=bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    upsert_user({'username':username,'company_id':company_id,'role':'管理者','password_hash':password_hash,'active':True})
    print(f'CREATED: company={company_id}, admin={username}')
    print('Security: remove BOOTSTRAP_ADMIN_PASSWORD from .env after the first successful login.')

if __name__=='__main__':
    main()
