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
""", unsafe_allow_html=True)


def parse_due(value):
    try:
        return datetime.strptime((value or "").strip(), "%Y/%m/%d").date()
    except (ValueError, TypeError):
        return None


def status_value(item):
    return item.get("status") or "未対応"


def action_rank(item):
    if status_value(item) == "完了":
        return (9, 999999999)
    due = parse_due(item.get("due_date"))
    if due:
        days = (due - today_jst()).days
        if days < 0:
            return (0, days)
        if days == 0:
            return (1, 0)
        if days == 1:
            return (2, 1)
        if days <= 3:
            return (3, days)
    if (item.get("priority") or "中") == "高":
        return (4, 999999999)
    if status_value(item) == "対応中":
        return (5, 999999999)
    return (6, 999999999)



USER_STORE_FILE = Path(__file__).resolve().parent / "users.json"


def bootstrap_managed_users():
    init_db()

def load_managed_users(company_id=None):
    init_db()
    return list_users_by_company(company_id) if company_id else list_users()

_current_managed_user = get_user(CURRENT_USER)
if not _current_managed_user:
    st.error("ログインユーザー情報を取得できませんでした。再ログインしてください。")
    logout()
    st.stop()
CURRENT_COMPANY_ID = (_current_managed_user.get("company_id") or "").strip()
if not CURRENT_COMPANY_ID:
    st.error("ログインユーザーに企業IDが設定されていません。管理者へお問い合わせください。")
    logout()
    st.stop()


# v3.3.1: company/tenant isolation
def _belongs_to_current_company(item):
    if not isinstance(item, dict):
        return False
    company_id = (item.get("company_id") or DEFAULT_COMPANY_ID).strip()
    item["company_id"] = company_id
    return company_id == CURRENT_COMPANY_ID


def tenant_inquiry_history():
    """Return only inquiries belonging to the authenticated user's company."""
    return get_inquiry_history(company_id=CURRENT_COMPANY_ID)


def tenant_urgent_inquiries():
    return get_urgent_inquiries(company_id=CURRENT_COMPANY_ID)


def tenant_save_inquiry(
    inquiry, data, customer_name="", contact="", channel="Web", auto_due_date=True
):
    payload = dict(data or {})
    payload["company_id"] = CURRENT_COMPANY_ID
    return save_to_google_sheets(
        inquiry, payload,
        customer_name=customer_name, contact=contact, channel=channel,
        auto_due_date=auto_due_date, company_id=CURRENT_COMPANY_ID,
    )


def _current_company_inquiry(reception_number):
    return next(
        (
            x for x in tenant_inquiry_history()
            if (x.get("reception_number") or "") == reception_number
        ),
        None,
    )


def tenant_update_inquiry(
    reception_number, new_status, new_assignee, new_note, new_due,
    customer_name="", contact="", channel="Web", tags=""
):
    if not _current_company_inquiry(reception_number):
        raise PermissionError("この企業に属さない問い合わせは更新できません。")
    return update_inquiry_status(
        reception_number, new_status, new_assignee, new_note, new_due,
        customer_name=customer_name, contact=contact, channel=channel, tags=tags,
        company_id=CURRENT_COMPANY_ID,
    )


def tenant_assignees():
    """Selectable assignees are limited to the authenticated company."""
    names = [
        u.get("username")
        for u in load_managed_users(CURRENT_COMPANY_ID)
        if (u.get("company_id") or DEFAULT_COMPANY_ID) == CURRENT_COMPANY_ID
        and u.get("active", True)
        and u.get("role") in [ROLE_ADMIN, ROLE_AGENT]
        and u.get("username")
    ]
    # Preserve the configured legacy assignee list for the original company.
    if CURRENT_COMPANY_ID == DEFAULT_COMPANY_ID:
        names.extend(ASSIGNEES)
    return list(dict.fromkeys(names))


def save_managed_users(users):
    init_db()
    for u in users:
        upsert_user(u)

def create_managed_user(username, role, password, company_id=None):
    username=(username or "").strip(); company_id=CURRENT_COMPANY_ID
    if not username: return False,"ユーザーIDを入力してください。"
    if role not in [ROLE_ADMIN,ROLE_AGENT,ROLE_VIEWER]: return False,"権限が不正です。"
    if len(password or "")<10: return False,"パスワードは10文字以上にしてください。"
    if any(u.get("username")==username for u in load_managed_users()): return False,"同じユーザーIDが既に存在します。"
    upsert_user({"username":username,"company_id":company_id,"role":role,"password_hash":bcrypt.hashpw(password.encode(),bcrypt.gensalt()).decode(),"active":True})
    return True,"ユーザーを追加しました。"

def update_managed_user(username, role, active, new_password=None):
    if role not in [ROLE_ADMIN,ROLE_AGENT,ROLE_VIEWER]: return False,"権限が不正です。"
    ph=None
    if new_password:
        if len(new_password)<10: return False,"新しいパスワードは10文字以上にしてください。"
        ph=bcrypt.hashpw(new_password.encode(),bcrypt.gensalt()).decode()
    ok=update_user_db(username,CURRENT_COMPANY_ID,role,bool(active),ph)
    return (True,"ユーザー情報を更新しました。") if ok else (False,"ユーザーが見つかりません。")

AUDIT_LOG_FILE = Path(__file__).resolve().parent / "audit_log.jsonl"



COMPANY_STORE_FILE = Path(__file__).resolve().parent / "companies.json"
PLATFORM_ADMIN_USER = os.getenv("PLATFORM_ADMIN_USER", "admin").strip() or "admin"

def load_companies():
    init_db()
    return list_companies()

def save_companies(companies):
    init_db()
    for c in companies:
        ensure_company(c.get("company_id"),c.get("company_name"),c.get("active",True))

def current_company_name():
    c=next((x for x in load_companies() if x.get('company_id')==CURRENT_COMPANY_ID),None)
    return (c or {}).get('company_name') or CURRENT_COMPANY_ID

def is_platform_admin():
    return CURRENT_USER == PLATFORM_ADMIN_USER and CURRENT_ROLE == ROLE_ADMIN

def create_company(company_id, company_name, admin_username, password):
    company_id=(company_id or "").strip(); company_name=(company_name or "").strip(); admin_username=(admin_username or "").strip()
    if not re_match_company_id(company_id): return False,"企業IDは英数字・_・- の3〜40文字で入力してください。"
    if not company_name: return False,"企業名を入力してください。"
    if any(c.get("company_id")==company_id for c in load_companies()): return False,"同じ企業IDが既に存在します。"
    if any(u.get("username")==admin_username for u in load_managed_users()): return False,"同じユーザーIDが既に存在します。"
    if len(password or "")<10: return False,"初期パスワードは10文字以上にしてください。"
    ensure_company(company_id,company_name,True)
    upsert_user({"username":admin_username,"company_id":company_id,"role":ROLE_ADMIN,"password_hash":bcrypt.hashpw(password.encode(),bcrypt.gensalt()).decode(),"active":True})
    return True,"企業と初期管理者を作成しました。"

def re_match_company_id(value):
    import re
    return bool(re.fullmatch(r'[A-Za-z0-9_-]{3,40}', value or ''))

def company_usage_rows():
    return [
        {'企業ID':x['company_id'],'企業名':x['company_name'],
         '状態':'有効' if x['active'] else '停止',
         'ユーザー数':x['user_count'],'問い合わせ数':x['inquiry_count'],'未完了':x['open_count']}
        for x in company_usage_stats()
    ]

def update_company(company_id, company_name, active):
    company_name=(company_name or "").strip()
    if not company_name: return False,"企業名を入力してください。"
    ok=update_company_db(company_id,company_name,bool(active))
    return (True,"企業情報を更新しました。") if ok else (False,"企業が見つかりません。")

def current_company_is_active():
    company = next((c for c in load_companies() if c.get("company_id") == CURRENT_COMPANY_ID), None)
    return True if company is None else bool(company.get("active", True))


def write_audit_log(action, target="", details=None):
    try: add_audit(CURRENT_COMPANY_ID,CURRENT_USER,CURRENT_ROLE,str(action),str(target or ""),details or {})
    except Exception: pass

def read_audit_logs(limit=500):
    try: return get_audits(CURRENT_COMPANY_ID,limit)
    except Exception: return []

def generate_reply_draft(inquiry, summary, recommended_action):
    response = _reply_client().responses.create(
        model=OPENAI_MODEL,
        input=f"""
あなたは企業のカスタマーサポート担当者です。
以下の情報だけを根拠に、顧客へ送れる丁寧で簡潔な日本語の返信本文を作成してください。

【問い合わせ内容】
{inquiry}

【AI要約】
{summary}

【推奨対応】
{recommended_action}

条件:
- 元情報にない事実を作らない
- 確認できていない内容を断定しない
- 不要な個人情報を要求しない
- 必要な場合のみ適切なお詫びを含める
- 社内向け説明、件名、注釈、解説は書かない
"""
    )
    return response.output_text.strip()



# ログイン成功をセッションにつき1回だけ監査記録
_audit_login_key = f"{CURRENT_USER}|{CURRENT_ROLE}"
if st.session_state.get("_audit_login_recorded") != _audit_login_key:
    write_audit_log("LOGIN", details={"message": "ログイン成功"})
    st.session_state["_audit_login_recorded"] = _audit_login_key

# v4.1: 停止中企業の一般ユーザー利用を遮断（プラットフォーム管理者は除外）
if not is_platform_admin() and not current_company_is_active():
    st.error("この企業アカウントは現在停止中です。プラットフォーム管理者へお問い合わせください。")
    if st.button("ログアウト", use_container_width=True, key="inactive_company_logout"):
        write_audit_log("BLOCKED_INACTIVE_COMPANY", details={"message": "停止中企業へのログインを遮断"})
        logout()
        st.rerun()
    st.stop()

user_col, logout_col = st.columns([5, 1])
with user_col:
    st.caption(f"ログイン中：{CURRENT_USER} ｜ 権限：{CURRENT_ROLE} ｜ 企業ID：{CURRENT_COMPANY_ID}")
with logout_col:
    if st.button("ログアウト", use_container_width=True, key="logout_button"):
        write_audit_log("LOGOUT", details={"message": "ログアウト"})
        st.session_state.pop("_audit_login_recorded", None)
        logout()
        st.rerun()

st.title("AI Customer Support Hub")
st.caption(f"{current_company_name()} ｜ AI問い合わせ管理 v{APP_VERSION} ｜ マルチテナント・AI対応支援")

# 権限に応じて表示するメニューを切り替える
_visible_tabs = [("tab1", "🏠 ホーム")]

if can("create"):
    _visible_tabs.append(("tab2", "📝 新規問い合わせ"))

_visible_tabs.extend([
    ("tab3", "📋 問い合わせ"),
    ("tab4", "👥 顧客"),
    ("tab5", "📊 分析"),
])

if can("settings"):
    _visible_tabs.append(("tab6", "⚙️ 設定"))
if is_platform_admin():
    _visible_tabs.append(("tab7", "🏢 企業管理"))

_rendered_tabs = st.tabs([label for _, label in _visible_tabs])
_tab_map = dict(zip([key for key, _ in _visible_tabs], _rendered_tabs))

tab1 = _tab_map.get("tab1", st.container())
tab2 = _tab_map.get("tab2", st.container())
tab3 = _tab_map.get("tab3", st.container())
tab4 = _tab_map.get("tab4", st.container())
tab5 = _tab_map.get("tab5", st.container())
tab6 = _tab_map.get("tab6", st.container())
tab7 = _tab_map.get("tab7", st.container())

with tab1:
    st.subheader("ホームダッシュボード")
    try:
        history = tenant_inquiry_history()
        open_items = [x for x in history if status_value(x) != "完了"]

        total = len(history)
        unhandled = sum(status_value(x) == "未対応" for x in history)
        handling = sum(status_value(x) == "対応中" for x in history)
        completed = sum(status_value(x) == "完了" for x in history)

        overdue = due_today = due_3days = high_open = 0
        for x in open_items:
            if (x.get("priority") or "中") == "高":
                high_open += 1
            due = parse_due(x.get("due_date"))
            if due:
                days = (due - today_jst()).days
                overdue += days < 0
                due_today += days == 0
                due_3days += 0 <= days <= 3

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("総問い合わせ", total)
        c2.metric("未対応", unhandled)
        c3.metric("対応中", handling)
        c4.metric("完了", completed)

        a1, a2, a3, a4 = st.columns(4)
        a1.metric("🔴 期限超過", overdue)
        a2.metric("📅 本日期限", due_today)
        a3.metric("⏱ 3日以内", due_3days)
        a4.metric("🚨 高重要度・未完了", high_open)

        completion_rate = round((completed / total) * 100, 1) if total else 0.0
        assigned_open = sum((x.get("assignee") or "未設定") != "未設定" for x in open_items)
        unassigned_open = len(open_items) - assigned_open

        st.write("### 📈 運用KPI")
        k1, k2, k3 = st.columns(3)
        k1.metric("完了率", f"{completion_rate}%")
        k2.metric("担当者設定済み・未完了", assigned_open)
        k3.metric("担当者未設定・未完了", unassigned_open)

        st.write("### 🎯 優先対応")
        if open_items:
            for x in sorted(open_items, key=action_rank)[:5]:
                st.write(
                    f"**{x.get('reception_number') or '受付番号なし'}** ｜ "
                    f"{x.get('category') or 'その他'} ｜ "
                    f"重要度 {x.get('priority') or '中'} ｜ "
                    f"担当 {x.get('assignee') or '未設定'} ｜ "
                    f"期限 {x.get('due_date') or '未設定'}"
                )
        else:
            st.success("未完了案件はありません。")

        st.write("### 👤 担当者別・未完了")
        workload = {}
        for x in open_items:
            name = x.get("assignee") or "未設定"
            workload[name] = workload.get(name, 0) + 1
        if workload:
            cols = st.columns(min(4, len(workload)))
            for i, (name, count) in enumerate(
                sorted(workload.items(), key=lambda v: (-v[1], v[0]))
            ):
                cols[i % len(cols)].metric(name, count)

        st.write("### 🕘 最近の問い合わせ")
        for x in history[:5]:
            st.write(
                f"**{x.get('datetime') or ''}** ｜ "
                f"{x.get('reception_number') or '受付番号なし'} ｜ "
                f"{x.get('customer_name') or '顧客未登録'} ｜ "
                f"{x.get('category') or 'その他'} ｜ {status_value(x)}"
            )
    except Exception as e:
        st.error("ホーム情報の取得中にエラーが発生しました。")
        st.caption("詳細はサーバーログを確認してください。")

with tab2:
    if not can("create"):
        pass
    else:
        st.subheader("新規問い合わせ")
        st.write("問い合わせを入力するとAIが分析し、Google Sheetsへ記録します。")
    
        inquiry = st.text_area(
            "お問い合わせ内容",
            placeholder="例：ログイン時にエラーが表示され利用できません。明日の朝までに対応してください。",
            height=180,
        )
        m1, m2, m3 = st.columns(3)
        with m1:
            customer_name = st.text_input("顧客名（任意）", placeholder="例：山田 太郎")
        with m2:
            contact = st.text_input("連絡先（任意）", placeholder="メール・電話など")
        with m3:
            channel = st.selectbox("受付経路", ["Web", "メール", "電話", "チャット", "SNS", "その他"])
    
        auto_due_date = st.checkbox("重要度に応じて対応期限を自動設定", value=True)
    
        if st.button("AIで分析して登録", type="primary", use_container_width=True):
            if not inquiry.strip():
                st.warning("お問い合わせ内容を入力してください。")
            else:
                try:
                    with st.spinner("AI分析 → Google Sheets登録を実行しています..."):
                        data = analyze_inquiry(inquiry)
                        reception_number = tenant_save_inquiry(
                            inquiry, data,
                            customer_name=customer_name,
                            contact=contact,
                            channel=channel,
                            auto_due_date=auto_due_date,
                        )
                    write_audit_log(
                        "CREATE_INQUIRY",
                        target=reception_number,
                        details={
                            "category": data.get("category", "その他"),
                            "priority": data.get("priority", "中"),
                            "customer_name": customer_name,
                            "channel": channel,
                        },
                    )
                    st.success("登録しました。")
                    st.code(reception_number, language=None)
                    r1, r2 = st.columns(2)
                    r1.metric("カテゴリ", data.get("category", "その他"))
                    r2.metric("重要度", data.get("priority", "中"))
                    st.write("### AI要約")
                    st.write(data.get("summary", ""))
                    st.write("### 推奨対応")
                    st.write(data.get("recommended_action", ""))
                    if data.get("tags"):
                        st.caption(f"タグ：{data['tags']}")
                except Exception as e:
                    st.error("登録処理中にエラーが発生しました。")
                    st.caption("詳細はサーバーログを確認してください。")

with tab3:
    st.subheader("問い合わせ管理")
    try:
        history = tenant_inquiry_history()

        overdue_count = due_today_count = due_tomorrow_count = 0
        for x in history:
            if status_value(x) == "完了":
                continue
            due = parse_due(x.get("due_date"))
            if not due:
                continue
            days = (due - today_jst()).days
            overdue_count += days < 0
            due_today_count += days == 0
            due_tomorrow_count += days == 1

        urgent_count = len(tenant_urgent_inquiries())
        d1, d2, d3, d4 = st.columns(4)
        d1.metric("🔴 期限超過", overdue_count)
        d2.metric("📅 本日期限", due_today_count)
        d3.metric("🟠 明日期限", due_tomorrow_count)
        d4.metric("🚨 高重要度・未完了", urgent_count)

        st.write("### 🔎 検索・絞り込み")
        search_keyword = st.text_input(
            "キーワード検索",
            placeholder="受付番号・内容・カテゴリ・担当者・顧客名・連絡先・タグ",
            key="inquiry_search",
        )
        categories = sorted({x.get("category") or "その他" for x in history})
        assignees = sorted({x.get("assignee") or "未設定" for x in history})

        f1, f2, f3, f4 = st.columns(4)
        with f1:
            filter_priority = st.selectbox("重要度", ["すべて", "高", "中", "低"])
        with f2:
            filter_status = st.selectbox("対応状況", ["すべて", "未対応", "対応中", "完了"])
        with f3:
            filter_category = st.selectbox("カテゴリ", ["すべて"] + categories)
        with f4:
            filter_assignee = st.selectbox("担当者", ["すべて"] + assignees)

        q1, q2, q3 = st.columns(3)
        with q1:
            deadline_filter = st.selectbox(
                "期限", ["すべて", "期限超過", "本日期限", "明日期限", "3日以内", "期限未設定"]
            )
        with q2:
            hide_completed = st.checkbox("完了案件を隠す")
        with q3:
            my_open_only = st.checkbox(f"{CURRENT_USER}の未完了だけ表示")

        filtered = list(history)
        if filter_priority != "すべて":
            filtered = [x for x in filtered if (x.get("priority") or "中") == filter_priority]
        if filter_status != "すべて":
            filtered = [x for x in filtered if status_value(x) == filter_status]
        if filter_category != "すべて":
            filtered = [x for x in filtered if (x.get("category") or "その他") == filter_category]
        if filter_assignee != "すべて":
            filtered = [x for x in filtered if (x.get("assignee") or "未設定") == filter_assignee]
        if hide_completed:
            filtered = [x for x in filtered if status_value(x) != "完了"]
        if my_open_only:
            filtered = [
                x for x in filtered
                if (x.get("assignee") or "未設定") == CURRENT_USER
                and status_value(x) != "完了"
            ]

        if deadline_filter != "すべて":
            temp = []
            for x in filtered:
                due = parse_due(x.get("due_date"))
                if deadline_filter == "期限未設定":
                    if not due:
                        temp.append(x)
                    continue
                if status_value(x) == "完了" or not due:
                    continue
                days = (due - today_jst()).days
                if deadline_filter == "期限超過" and days < 0:
                    temp.append(x)
                elif deadline_filter == "本日期限" and days == 0:
                    temp.append(x)
                elif deadline_filter == "明日期限" and days == 1:
                    temp.append(x)
                elif deadline_filter == "3日以内" and 0 <= days <= 3:
                    temp.append(x)
            filtered = temp

        if search_keyword.strip():
            kw = search_keyword.strip().lower()
            fields = [
                "reception_number", "inquiry", "category", "summary", "assignee",
                "customer_name", "contact", "channel", "tags"
            ]
            filtered = [
                x for x in filtered
                if kw in " ".join(str(x.get(k, "")) for k in fields).lower()
            ]

        filtered = sorted(filtered, key=action_rank)
        st.caption(f"検索結果：{len(filtered)}件")

        with st.expander("⬇️ CSV出力"):
            buf = io.StringIO()
            buf.write("\ufeff")
            writer = csv.writer(buf)
            writer.writerow([
                "登録日時", "受付番号", "問い合わせ内容", "カテゴリ", "重要度",
                "AI要約", "AI推奨対応", "対応状況", "担当者", "対応期限",
                "社内メモ", "対応履歴", "顧客名", "連絡先", "受付経路", "タグ"
            ])
            for x in filtered:
                writer.writerow([
                    x.get("datetime", ""), x.get("reception_number", ""), x.get("inquiry", ""),
                    x.get("category", ""), x.get("priority", ""), x.get("summary", ""),
                    x.get("recommended_action", ""), status_value(x),
                    x.get("assignee", "") or "未設定", x.get("due_date", ""),
                    x.get("internal_note", ""), x.get("response_history", ""),
                    x.get("customer_name", ""), x.get("contact", ""), x.get("channel", ""),
                    x.get("tags", ""),
                ])
            st.download_button(
                "現在の検索結果をCSVで出力",
                data=buf.getvalue().encode("utf-8-sig"),
                file_name="inquiry_export_" + now_jst().strftime("%Y%m%d_%H%M") + ".csv",
                mime="text/csv",
                use_container_width=True,
            )

        st.write("### 📋 問い合わせ一覧")
        if not filtered:
            st.info("条件に一致する問い合わせはありません。")

        for index, item in enumerate(filtered):
            number = item.get("reception_number") or "受付番号なし"
            priority = item.get("priority") or "中"
            status = status_value(item)
            category = item.get("category") or "その他"
            picon = {"高": "🔴", "中": "🟡", "低": "🟢"}.get(priority, "🟡")
            sicon = {"未対応": "⚪", "対応中": "🔵", "完了": "✅"}.get(status, "⚪")

            badge = ""
            due = parse_due(item.get("due_date"))
            if status != "完了" and due:
                days = (due - today_jst()).days
                if days < 0:
                    badge = f" ｜ ⏰期限超過{-days}日"
                elif days == 0:
                    badge = " ｜ 📅本日期限"
                elif days == 1:
                    badge = " ｜ 🟠明日期限"
                elif days <= 3:
                    badge = f" ｜ 📅あと{days}日"

            with st.expander(f"{picon} {number} ｜ {category} ｜ {sicon} {status}{badge}"):
                current_assignee = item.get("assignee") or "未設定"
                current_due_text = (item.get("due_date") or "").strip()
                current_due = parse_due(current_due_text) or today_jst()

                e1, e2 = st.columns(2)
                with e1:
                    new_status = st.selectbox(
                        "対応状況", ["未対応", "対応中", "完了"],
                        index=["未対応", "対応中", "完了"].index(status),
                        key=f"status_{number}_{index}",
                        disabled=not can("edit"),
                    )
                with e2:
                    options = ["未設定"] + tenant_assignees()
                    if current_assignee not in options:
                        options.append(current_assignee)
                    new_assignee = st.selectbox(
                        "担当者", options, index=options.index(current_assignee),
                        key=f"assignee_{number}_{index}",
                        disabled=not can("edit"),
                    )

                new_note = st.text_area(
                    "社内メモ", value=item.get("internal_note", ""),
                    key=f"note_{number}_{index}", height=90,
                    disabled=not can("edit"),
                )

                e3, e4 = st.columns(2)
                with e3:
                    new_customer = st.text_input(
                        "顧客名", value=item.get("customer_name", ""),
                        key=f"customer_{number}_{index}",
                        disabled=not can("edit"),
                    )
                    channels = ["Web", "メール", "電話", "チャット", "SNS", "その他"]
                    current_channel = item.get("channel") or "Web"
                    new_channel = st.selectbox(
                        "受付経路", channels,
                        index=channels.index(current_channel) if current_channel in channels else 0,
                        key=f"channel_{number}_{index}",
                        disabled=not can("edit"),
                    )
                with e4:
                    new_contact = st.text_input(
                        "連絡先", value=item.get("contact", ""),
                        key=f"contact_{number}_{index}",
                        disabled=not can("edit"),
                    )
                    new_tags = st.text_input(
                        "タグ", value=item.get("tags", ""),
                        key=f"tags_{number}_{index}",
                        disabled=not can("edit"),
                    )

                enabled = st.checkbox(
                    "対応期限を設定する", value=bool(current_due_text),
                    key=f"due_enabled_{number}_{index}",
                    disabled=not can("edit"),
                )
                if enabled:
                    selected_due = st.date_input(
                        "対応期限", value=current_due,
                        key=f"due_{number}_{index}", format="YYYY/MM/DD",
                        disabled=not can("edit"),
                    )
                    new_due = selected_due.strftime("%Y/%m/%d")
                else:
                    new_due = ""

                if can("edit") and number != "受付番号なし" and st.button(
                    "💾 変更を保存", key=f"save_{number}_{index}",
                    type="primary", use_container_width=True
                ):
                    try:
                        tenant_update_inquiry(
                            number, new_status, new_assignee, new_note, new_due,
                            customer_name=new_customer, contact=new_contact,
                            channel=new_channel, tags=new_tags
                        )
                        write_audit_log(
                            "UPDATE_INQUIRY",
                            target=number,
                            details={
                                "status": {
                                    "before": item.get("status", ""),
                                    "after": new_status,
                                },
                                "assignee": {
                                    "before": item.get("assignee", ""),
                                    "after": new_assignee,
                                },
                                "due_date": {
                                    "before": item.get("due_date", ""),
                                    "after": new_due,
                                },
                                "customer_name_changed": (
                                    item.get("customer_name", "") != new_customer
                                ),
                                "contact_changed": (
                                    item.get("contact", "") != new_contact
                                ),
                                "channel_changed": (
                                    item.get("channel", "") != new_channel
                                ),
                                "tags_changed": (
                                    item.get("tags", "") != new_tags
                                ),
                                "internal_note_changed": (
                                    item.get("internal_note", "") != new_note
                                ),
                            },
                        )
                        st.success("更新しました。")
                        st.rerun()
                    except Exception as e:
                        st.error("更新中にエラーが発生しました。")
                        st.caption("詳細はサーバーログを確認してください。")

                st.divider()
                st.write(f"**登録日時：** {item.get('datetime', '')}")
                st.write(f"**問い合わせ内容：** {item.get('inquiry', '')}")
                st.write(f"**AI要約：** {item.get('summary', '')}")
                st.write(f"**AI推奨対応：** {item.get('recommended_action', '')}")

                history_text = (item.get("response_history") or "").replace("\\n", "\n").strip()
                with st.expander("🕘 対応履歴"):
                    if history_text:
                        for line in history_text.splitlines():
                            if line.strip():
                                st.write(f"・{line}")
                    else:
                        st.caption("まだ対応履歴はありません。")

                st.write("#### ✉️ AI返信案")
                state_key = f"reply_{number}_{index}"
                editor_key = f"reply_editor_{number}_{index}"
                if st.button(
                    "🤖 AI返信案を作成" if not st.session_state.get(state_key) else "🔄 返信案を再生成",
                    key=f"reply_btn_{number}_{index}",
                    use_container_width=True,
                    disabled=not can("edit"),
                ):
                    try:
                        with st.spinner("AIが返信案を作成しています..."):
                            draft = generate_reply_draft(
                                item.get("inquiry", ""),
                                item.get("summary", ""),
                                item.get("recommended_action", ""),
                            )
                        st.session_state[state_key] = draft
                        st.session_state[editor_key] = draft
                        write_audit_log(
                            "GENERATE_AI_REPLY",
                            target=number,
                            details={"message": "AI返信案を生成"},
                        )
                    except Exception as e:
                        st.error("AI返信案の作成中にエラーが発生しました。")
                        st.caption("詳細はサーバーログを確認してください。")

                if st.session_state.get(state_key):
                    if editor_key not in st.session_state:
                        st.session_state[editor_key] = st.session_state[state_key]
                    st.text_area("返信文", key=editor_key, height=220)
                    st.caption("送信前に担当者が内容を確認・編集してください。")

    except Exception as e:
        st.error("問い合わせ履歴の取得中にエラーが発生しました。")
        st.caption("詳細はサーバーログを確認してください。")

with tab4:
    st.subheader("顧客管理")
    try:
        rows = tenant_inquiry_history()
        q = st.text_input(
            "顧客検索", placeholder="顧客名・連絡先で検索",
            key="customer_search"
        ).strip().lower()
        customers = {}
        for x in rows:
            name = (x.get("customer_name") or "").strip()
            contact = (x.get("contact") or "").strip()
            if not name and not contact:
                continue
            key = name or contact
            if key not in customers:
                customers[key] = {
                    "contact": contact, "channels": set(), "count": 0,
                    "open": 0, "last": "", "items": []
                }
            c = customers[key]
            if contact:
                c["contact"] = contact
            if x.get("channel"):
                c["channels"].add(x["channel"])
            c["count"] += 1
            c["open"] += status_value(x) != "完了"
            if not c["last"]:
                c["last"] = x.get("datetime", "")
            c["items"].append(x)

        visible = [
            (n, c) for n, c in customers.items()
            if not q or q in f"{n} {c['contact']}".lower()
        ]
        st.caption(f"顧客数：{len(visible)}")
        if not visible:
            st.info("条件に一致する顧客情報はありません。")
        for name, c in sorted(visible, key=lambda v: (-v[1]["count"], v[0])):
            with st.expander(
                f"👤 {name} ｜ 問い合わせ {c['count']}件 ｜ 未完了 {c['open']}件"
            ):
                st.write(f"**連絡先：** {c['contact'] or '未登録'}")
                st.write(
                    f"**受付経路：** {', '.join(sorted(c['channels'])) if c['channels'] else '未登録'}"
                )
                st.write(f"**最新登録日時：** {c['last'] or '不明'}")
                st.write("**問い合わせ履歴**")
                for x in c["items"][:10]:
                    st.write(
                        f"・{x.get('datetime','')} ｜ {x.get('reception_number','')} ｜ "
                        f"{x.get('category','その他')} ｜ {status_value(x)}"
                    )
    except Exception as e:
        st.error("顧客情報の取得中にエラーが発生しました。")
        st.caption("詳細はサーバーログを確認してください。")

with tab5:
    st.subheader("分析レポート")
    try:
        rows = tenant_inquiry_history()

        def counts(field, default, only_open=False):
            result = {}
            for x in rows:
                if only_open and status_value(x) == "完了":
                    continue
                key = x.get(field) or default
                result[key] = result.get(key, 0) + 1
            return result

        status_counts = counts("status", "未対応")
        priority_counts = counts("priority", "中")
        category_counts = counts("category", "その他")
        channel_counts = counts("channel", "未登録")
        assignee_counts = counts("assignee", "未設定", only_open=True)

        c1, c2, c3 = st.columns(3)
        c1.metric("未対応", status_counts.get("未対応", 0))
        c2.metric("対応中", status_counts.get("対応中", 0))
        c3.metric("完了", status_counts.get("完了", 0))

        p1, p2, p3 = st.columns(3)
        p1.metric("重要度：高", priority_counts.get("高", 0))
        p2.metric("重要度：中", priority_counts.get("中", 0))
        p3.metric("重要度：低", priority_counts.get("低", 0))

        open_rows = [x for x in rows if status_value(x) != "完了"]
        overdue_rows = []
        due_today_rows = []
        for x in open_rows:
            due = parse_due(x.get("due_date"))
            if not due:
                continue
            days = (due - today_jst()).days
            if days < 0:
                overdue_rows.append(x)
            elif days == 0:
                due_today_rows.append(x)

        st.write("### SLA・期限管理")
        s1, s2, s3 = st.columns(3)
        s1.metric("未完了", len(open_rows))
        s2.metric("期限超過", len(overdue_rows))
        s3.metric("本日期限", len(due_today_rows))

        left, right = st.columns(2)
        with left:
            st.write("### カテゴリ別")
            if category_counts:
                st.bar_chart(category_counts, horizontal=True)
            st.write("### 受付経路別")
            if channel_counts:
                st.bar_chart(channel_counts, horizontal=True)
        with right:
            st.write("### 担当者別・未完了")
            if assignee_counts:
                st.bar_chart(assignee_counts, horizontal=True)
            st.write("### カテゴリ上位")
            for name, count in sorted(
                category_counts.items(), key=lambda v: (-v[1], v[0])
            )[:10]:
                st.write(f"・{name}：{count}件")
    except Exception as e:
        st.error("分析データの取得中にエラーが発生しました。")
        st.caption("詳細はサーバーログを確認してください。")

with tab6:
    if not can("settings"):
        pass
    else:
        st.subheader("設定・接続確認")
        st.write("### 👤 担当者")
        st.write(" / ".join(ASSIGNEES) if ASSIGNEES else "担当者が設定されていません。")
        st.caption(".env の INQUIRY_ASSIGNEES で変更できます。")
    
        st.write("### 🙋 現在の利用者")
        st.code(f"{CURRENT_USER} ｜ {CURRENT_ROLE} ｜ {CURRENT_COMPANY_ID}", language=None)
    
        st.write("### ⏱ 自動対応期限")
        s1, s2, s3 = st.columns(3)
        s1.metric("重要度：高", "当日")
        s2.metric("重要度：中", "翌日")
        s3.metric("重要度：低", "3日後")
    
        st.write("### 🔗 接続状態")
        if st.button("接続状態を確認"):
            result = connection_check()
            if result["database"]:
                st.success("PostgreSQL：接続OK")
            else:
                st.error("PostgreSQL：接続を確認してください")
            if result["openai_key"]:
                st.success("OpenAI APIキー：設定済み")
            else:
                st.error("OpenAI APIキー：未設定")
    
        if can("users"):
            st.write("### 👥 ユーザー管理")
            managed_users = load_managed_users(CURRENT_COMPANY_ID)

            if managed_users:
                st.dataframe(
                    [{
                        "ユーザーID": u.get("username", ""),
                        "企業ID": u.get("company_id", DEFAULT_COMPANY_ID),
                        "権限": u.get("role", ""),
                        "状態": "有効" if u.get("active", True) else "無効",
                        "作成日時": u.get("created_at", ""),
                        "更新日時": u.get("updated_at", ""),
                    } for u in managed_users],
                    use_container_width=True,
                    hide_index=True,
                )

            with st.expander("➕ ユーザーを追加"):
                with st.form("create_managed_user_form"):
                    new_username = st.text_input("ユーザーID")
                    st.text_input("企業ID", value=CURRENT_COMPANY_ID, disabled=True, key="new_company_id_display")
                    new_company_id = CURRENT_COMPANY_ID
                    new_role = st.selectbox("権限", [ROLE_AGENT, ROLE_VIEWER, ROLE_ADMIN])
                    new_password = st.text_input("初期パスワード（10文字以上）", type="password")
                    new_password2 = st.text_input("初期パスワード（確認）", type="password")
                    create_submit = st.form_submit_button("ユーザーを追加", use_container_width=True)
                if create_submit:
                    if new_password != new_password2:
                        st.error("確認用パスワードが一致しません。")
                    else:
                        ok, msg = create_managed_user(new_username, new_role, new_password, new_company_id)
                        if ok:
                            write_audit_log("CREATE_USER", target=new_username.strip(),
                                            details={"role": new_role, "company_id": new_company_id.strip() or DEFAULT_COMPANY_ID})
                            st.success(msg)
                            st.rerun()
                        else:
                            st.error(msg)

            if managed_users:
                with st.expander("✏️ ユーザーを変更"):
                    selected_username = st.selectbox(
                        "対象ユーザー",
                        [u.get("username", "") for u in managed_users],
                    )
                    selected_user = next(
                        u for u in managed_users if u.get("username") == selected_username
                    )
                    roles = [ROLE_ADMIN, ROLE_AGENT, ROLE_VIEWER]
                    selected_role = st.selectbox(
                        "権限",
                        roles,
                        index=roles.index(selected_user.get("role"))
                        if selected_user.get("role") in roles else 2,
                    )
                    selected_active = st.checkbox(
                        "有効", value=bool(selected_user.get("active", True))
                    )
                    reset_password = st.text_input(
                        "新しいパスワード（変更しない場合は空欄）", type="password"
                    )
                    reset_password2 = st.text_input(
                        "新しいパスワード（確認）", type="password"
                    )

                    if st.button("ユーザー情報を保存", use_container_width=True):
                        if selected_username == CURRENT_USER and not selected_active:
                            st.error("現在ログイン中の自分自身は無効化できません。")
                        elif selected_username == CURRENT_USER and selected_role != ROLE_ADMIN:
                            st.error("現在ログイン中の管理者自身から管理者権限を外せません。")
                        elif reset_password and reset_password != reset_password2:
                            st.error("確認用パスワードが一致しません。")
                        else:
                            before_role = selected_user.get("role")
                            before_active = bool(selected_user.get("active", True))
                            ok, msg = update_managed_user(
                                selected_username, selected_role,
                                selected_active, reset_password or None
                            )
                            if ok:
                                write_audit_log(
                                    "UPDATE_USER",
                                    target=selected_username,
                                    details={
                                        "role": {"before": before_role, "after": selected_role},
                                        "active": {"before": before_active, "after": selected_active},
                                        "password_changed": bool(reset_password),
                                    },
                                )
                                st.success(msg)
                                st.rerun()
                            else:
                                st.error(msg)

        st.write("### 📦 データエクスポート")
        try:
            export_rows = tenant_inquiry_history()
            if export_rows:
                import pandas as pd
                export_df = pd.DataFrame(export_rows)
                st.download_button("自社問い合わせCSVをダウンロード", export_df.to_csv(index=False).encode("utf-8-sig"),
                                   file_name=f"inquiries_{CURRENT_COMPANY_ID}_{today_jst().isoformat()}.csv", mime="text/csv")
            else:
                st.caption("エクスポート対象の問い合わせはありません。")
        except Exception as e:
            st.warning(f"エクスポート準備に失敗しました: {e}")

        if can("audit"):
            st.write("### 🧾 監査ログ")
            audit_rows = read_audit_logs(limit=500)

            if audit_rows:
                a1, a2, a3 = st.columns(3)
                with a1:
                    audit_user = st.selectbox(
                        "ユーザー",
                        ["すべて"] + sorted({str(x.get("user", "")) for x in audit_rows if x.get("user")}),
                        key="audit_user_filter",
                    )
                with a2:
                    audit_action = st.selectbox(
                        "操作",
                        ["すべて"] + sorted({str(x.get("action", "")) for x in audit_rows if x.get("action")}),
                        key="audit_action_filter",
                    )
                with a3:
                    audit_keyword = st.text_input(
                        "受付番号・キーワード",
                        key="audit_keyword_filter",
                    ).strip().lower()

                filtered_audit = list(audit_rows)
                if audit_user != "すべて":
                    filtered_audit = [x for x in filtered_audit if x.get("user") == audit_user]
                if audit_action != "すべて":
                    filtered_audit = [x for x in filtered_audit if x.get("action") == audit_action]
                if audit_keyword:
                    filtered_audit = [
                        x for x in filtered_audit
                        if audit_keyword in json.dumps(x, ensure_ascii=False, default=str).lower()
                    ]

                st.caption(f"表示：{len(filtered_audit)}件 / 最大500件")
                display_rows = []
                for x in filtered_audit:
                    details = x.get("details") or {}
                    display_rows.append({
                        "日時": x.get("timestamp", ""),
                        "ユーザー": x.get("user", ""),
                        "権限": x.get("role", ""),
                        "操作": x.get("action", ""),
                        "対象": x.get("target", ""),
                        "詳細": json.dumps(details, ensure_ascii=False, default=str),
                    })
                st.dataframe(display_rows, use_container_width=True, hide_index=True)

                output = io.StringIO()
                writer = csv.DictWriter(
                    output,
                    fieldnames=["日時", "ユーザー", "権限", "操作", "対象", "詳細"],
                )
                writer.writeheader()
                writer.writerows(display_rows)
                st.download_button(
                    "監査ログをCSV出力",
                    data=output.getvalue().encode("utf-8-sig"),
                    file_name=f"audit_log_{today_jst().strftime('%Y%m%d')}.csv",
                    mime="text/csv",
                    use_container_width=True,
                )
            else:
                st.caption("監査ログはまだありません。")

        st.write("### 🧩 システム情報")
        i1, i2, i3 = st.columns(3)
        i1.metric("バージョン", APP_VERSION)
        i2.metric("データストア", "PostgreSQL")
        i3.metric("AI", "OpenAI")
        st.caption(f"企業分離基盤：{CURRENT_COMPANY_ID}（v3.3.1：問い合わせ・顧客・分析・担当者・ユーザー・監査ログを企業単位で分離）")
    
        st.write("### 🛡 運用上の注意")
        st.info(
            "AI返信案は自動送信せず、人が確認・編集してから利用する設計です。"
            "v3.0では認証・基本権限管理を実装済みです。本番公開時は監査ログ・"
            "秘密情報は環境変数で管理し、PostgreSQLを永続データストアとして使用します。"
        )


with tab7:
    if is_platform_admin():
        st.subheader("企業管理（プラットフォーム管理者）")
        st.caption("契約企業の作成・利用状況確認を行います。各企業の問い合わせ本文はこの画面には表示しません。")
        try:
            st.dataframe(company_usage_rows(), use_container_width=True, hide_index=True)
        except Exception as e:
            st.error(f"企業利用状況を取得できませんでした: {e}")
        with st.expander("➕ 新しい企業を作成"):
            with st.form("create_company_form"):
                c1,c2=st.columns(2)
                with c1:
                    cid=st.text_input("企業ID", placeholder="company_003")
                    cname=st.text_input("企業名", placeholder="株式会社サンプル")
                with c2:
                    auser=st.text_input("初期管理者ユーザーID", placeholder="admin_sample")
                    apass=st.text_input("初期パスワード（10文字以上）", type="password")
                apass2=st.text_input("初期パスワード（確認）", type="password")
                submitted=st.form_submit_button("企業と初期管理者を作成", use_container_width=True)
            if submitted:
                if apass != apass2:
                    st.error("確認用パスワードが一致しません。")
                else:
                    ok,msg=create_company(cid,cname,auser,apass)
                    if ok:
                        write_audit_log('CREATE_COMPANY',target=cid,details={'company_name':cname,'admin':auser})
                        st.success(msg); st.rerun()
                    else: st.error(msg)

        companies_for_edit = load_companies()
        if companies_for_edit:
            with st.expander("✏️ 企業情報を変更・停止／再開"):
                selected_cid = st.selectbox(
                    "対象企業",
                    [c.get("company_id", "") for c in companies_for_edit],
                    key="platform_company_select",
                )
                selected_company = next(
                    c for c in companies_for_edit if c.get("company_id") == selected_cid
                )
                edited_name = st.text_input(
                    "企業名",
                    value=selected_company.get("company_name", selected_cid),
                    key="platform_company_name",
                )
                edited_active = st.checkbox(
                    "有効",
                    value=bool(selected_company.get("active", True)),
                    key="platform_company_active",
                )
                if st.button("企業情報を保存", use_container_width=True, key="platform_company_save"):
                    if selected_cid == CURRENT_COMPANY_ID and not edited_active:
                        st.error("現在ログイン中のプラットフォーム管理企業は停止できません。")
                    else:
                        before_name = selected_company.get("company_name", selected_cid)
                        before_active = bool(selected_company.get("active", True))
                        ok, msg = update_company(selected_cid, edited_name, edited_active)
                        if ok:
                            write_audit_log(
                                "UPDATE_COMPANY",
                                target=selected_cid,
                                details={
                                    "company_name": {"before": before_name, "after": edited_name},
                                    "active": {"before": before_active, "after": edited_active},
                                },
                            )
                            st.success(msg)
                            st.rerun()
                        else:
                            st.error(msg)

# Global operational footer
st.divider()
st.caption(f"AI Customer Support Hub v{APP_VERSION} ｜ tenant={CURRENT_COMPANY_ID} ｜ user={CURRENT_USER}")
