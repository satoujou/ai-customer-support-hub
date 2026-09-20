import os
import io
import csv
import json
import bcrypt
from pathlib import Path
from database import (init_db, get_user, list_users, list_users_by_company, upsert_user, update_user_db, list_companies, ensure_company, update_company_db, add_audit, get_audits, company_usage_stats)
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

import streamlit as st
from openai import OpenAI
from dotenv import load_dotenv

from main import (
    analyze_inquiry,
    save_to_google_sheets,
    get_inquiry_history,
    update_inquiry_status,
    get_urgent_inquiries,
    connection_check,
)

from auth import (
    initialize_auth,
    login_screen,
    logout,
    is_authenticated,
    current_user,
    current_role,
    can,
)

load_dotenv()

# v3.2.1: auth.py と同じ権限名を app.py 側でも使用する
ROLE_ADMIN = "管理者"
ROLE_AGENT = "担当者"
ROLE_VIEWER = "閲覧者"

ASSIGNEES = [
    x.strip() for x in os.getenv("INQUIRY_ASSIGNEES", "佐藤,田中,鈴木").split(",")
    if x.strip()
]
CURRENT_USER = os.getenv(
    "INQUIRY_CURRENT_USER",
    ASSIGNEES[0] if ASSIGNEES else "佐藤"
)
COMPANY_NAME = os.getenv("COMPANY_NAME", "Demo Company")
DEFAULT_COMPANY_ID = os.getenv("COMPANY_ID", "company_001").strip() or "company_001"
APP_VERSION = "Portfolio Final 1.0"
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-luna")
JST = ZoneInfo("Asia/Tokyo")
def now_jst(): return datetime.now(JST)
def today_jst(): return now_jst().date()

def _reply_client():
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not key or key == "replace_me":
        raise RuntimeError("OPENAI_API_KEY が設定されていません。")
    return OpenAI(api_key=key)

st.set_page_config(
    page_title="AI問い合わせ管理",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# v5.0: authentication is PostgreSQL-backed
initialize_auth()

if not is_authenticated():
    login_screen()
    st.stop()

# 認証済みユーザーをアプリ全体の現在ユーザーとして使用
CURRENT_USER = current_user() or "未設定"
CURRENT_ROLE = current_role() or "未設定"
# CURRENT_COMPANY_ID は load_managed_users() 定義後に確定する
CURRENT_COMPANY_ID = DEFAULT_COMPANY_ID

st.markdown("""
<style>
.block-container {max-width: 1500px; padding-top: 2rem; padding-bottom: 3rem;}
div[data-testid="stMetric"] {
    border: 1px solid rgba(128,128,128,.22);
    border-radius: 12px;
    padding: 14px 16px;
}
div[data-testid="stExpander"] {border-radius: 12px;}
.stButton > button {border-radius: 10px;}
[data-testid="stMetricValue"] {font-variant-numeric: tabular-nums;}
[data-testid="stDataFrame"] {border-radius: 12px; overflow: hidden;}
h1, h2, h3 {letter-spacing: -0.02em;}
</style>
