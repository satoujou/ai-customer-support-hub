import bcrypt
import streamlit as st
from database import get_user, get_company, init_db
ROLE_ADMIN='管理者'; ROLE_AGENT='担当者'; ROLE_VIEWER='閲覧者'
ROLE_PERMISSIONS={
 ROLE_ADMIN:{'view':True,'create':True,'edit':True,'settings':True,'users':True,'audit':True},
 ROLE_AGENT:{'view':True,'create':True,'edit':True,'settings':False,'users':False,'audit':False},
 ROLE_VIEWER:{'view':True,'create':False,'edit':False,'settings':False,'users':False,'audit':False},
}
def initialize_auth():
 init_db()
 for k,v in {'authenticated':False,'username':None,'role':None,'company_id':None}.items():
  if k not in st.session_state: st.session_state[k]=v
def login(username,password):
 user=get_user((username or '').strip())
 if not user or not user.get('active',True): return False
 company=get_company(user.get('company_id'))
 if not company or not company.get('active',True): return False
 try: valid=bcrypt.checkpw((password or '').encode(),user['password_hash'].encode())
 except (ValueError,TypeError): return False
 if not valid:return False
 st.session_state.authenticated=True; st.session_state.username=user['username']; st.session_state.role=user['role']; st.session_state.company_id=user['company_id']; return True
def logout():
 st.session_state.authenticated=False; st.session_state.username=None; st.session_state.role=None; st.session_state.company_id=None
def is_authenticated(): return bool(st.session_state.get('authenticated',False))
def current_user(): return st.session_state.get('username')
def current_role(): return st.session_state.get('role')
def can(permission): return bool(ROLE_PERMISSIONS.get(current_role(),{}).get(permission,False))
def login_screen():
 st.markdown('# AIカスタマーサポートハブ'); st.caption('Authorized users only')
 with st.form('login_form'):
  username=st.text_input('ユーザーID'); password=st.text_input('パスワード',type='password')
  submitted=st.form_submit_button('ログイン',use_container_width=True)
 if submitted:
  if login(username,password): st.rerun()
  else: st.error('ユーザーIDまたはパスワードが正しくありません。')
