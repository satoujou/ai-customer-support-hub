import os,json
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from openai import OpenAI
from dotenv import load_dotenv
from database import conn, init_db, get_inquiries_by_company, get_urgent_inquiries_by_company
load_dotenv()
JST=ZoneInfo('Asia/Tokyo')
def now_jst(): return datetime.now(JST)
def _normalize_priority(value):
 value=str(value or '中').strip()
 if value in ['高い','高','緊急','至急']: return '高'
 if value in ['低い','低']: return '低'
 return '中'
def suggest_due_date(priority):
 days={'高':0,'中':1,'低':3}.get(_normalize_priority(priority),1); return (now_jst().date()+timedelta(days=days)).strftime('%Y/%m/%d')
def _openai_client():
 key=(os.getenv('OPENAI_API_KEY') or '').strip()
 if not key or key == 'replace_me':
  raise RuntimeError('OPENAI_API_KEY が設定されていません。')
 return OpenAI(api_key=key)
def analyze_inquiry(inquiry):
 response=_openai_client().responses.create(model=os.getenv('OPENAI_MODEL','gpt-5.6-luna'),input=f'''あなたは企業の問い合わせ対応を支援するAIです。以下を分析してください。\n問い合わせ:\n{inquiry}\nJSON形式だけで回答してください。{{"category":"問い合わせのカテゴリ","priority":"低・中・高のいずれか","summary":"簡潔な要約","recommended_action":"具体的な対応","tags":"最大3個、カンマ区切り"}}''')
 text=response.output_text.strip().replace('```json','').replace('```','').strip(); return json.loads(text)
def save_to_google_sheets(inquiry,data,customer_name='',contact='',channel='Web',auto_due_date=True,company_id=None):
 if not company_id: raise ValueError('company_id は必須です。')
 init_db(); now=now_jst(); number=now.strftime('INQ-%Y%m%d-%H%M%S-%f')[:-3]; priority=_normalize_priority(data.get('priority','中')); due=suggest_due_date(priority) if auto_due_date else ''
 with conn() as c:
  c.execute('''INSERT INTO inquiries(company_id,datetime,inquiry,category,priority,summary,recommended_action,reception_number,status,assignee,response_history,internal_note,due_date,customer_name,contact,channel,tags) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'未対応','未設定','','',%s,%s,%s,%s,%s)''',(company_id,now.strftime('%Y/%m/%d %H:%M'),inquiry,data.get('category','その他'),priority,data.get('summary',''),data.get('recommended_action',''),number,due,customer_name.strip(),contact.strip(),channel.strip() or 'Web',data.get('tags',''))); c.commit()
 return number
def get_inquiry_history(company_id=None):
 init_db()
 if not company_id: raise ValueError('company_id は必須です。')
 rows=get_inquiries_by_company(company_id)
 keys=['datetime','inquiry','category','priority','summary','recommended_action','reception_number','status','assignee','response_history','internal_note','due_date','customer_name','contact','channel','tags','company_id']; return [{k:r.get(k) or '' for k in keys} for r in rows]
def update_inquiry_status(reception_number,status,assignee,internal_note='',due_date='',customer_name=None,contact=None,channel=None,tags=None,company_id=None):
 init_db()
 with conn() as c:
  if not company_id: raise ValueError('company_id は必須です。')
  r=c.execute('SELECT * FROM inquiries WHERE reception_number=%s AND company_id=%s',(reception_number,company_id)).fetchone()
  if not r: raise ValueError(f'受付番号 {reception_number} が見つかりません。')
  customer_name=r['customer_name'] if customer_name is None else customer_name; contact=r['contact'] if contact is None else contact; channel=r['channel'] if channel is None else channel; tags=r['tags'] if tags is None else tags
  changes=[]
  for label,old,new in [('対応状況',r['status'],status),('担当者',r['assignee'],assignee),('対応期限',r['due_date'],due_date)]:
   if (old or '')!=(new or ''): changes.append(f'{label}: {old or "未設定"} → {new or "未設定"}')
  if (r['internal_note'] or '')!=(internal_note or ''): changes.append('社内メモを更新')
  for label,old,new in [('顧客名',r['customer_name'],customer_name),('連絡先',r['contact'],contact),('受付経路',r['channel'],channel),('タグ',r['tags'],tags)]:
   if (old or '')!=(new or ''): changes.append(f'{label}を更新')
  oldh=(r['response_history'] or '').replace('\\n','\n'); line=f"{now_jst().strftime('%Y/%m/%d %H:%M')}｜"+'｜'.join(changes) if changes else ''; newh=f'{oldh}\n{line}'.strip() if oldh and line else (line or oldh)
  c.execute('''UPDATE inquiries SET status=%s,assignee=%s,response_history=%s,internal_note=%s,due_date=%s,customer_name=%s,contact=%s,channel=%s,tags=%s,updated_at=now() WHERE reception_number=%s AND company_id=%s''',(status,assignee,newh,internal_note,due_date,customer_name,contact,channel,tags,reception_number,company_id)); c.commit(); return True
def get_urgent_inquiries(company_id=None):
 if not company_id: raise ValueError('company_id は必須です。')
 return get_urgent_inquiries_by_company(company_id)
def connection_check():
 out={'database':False,'openai_key':bool(os.getenv('OPENAI_API_KEY'))}
 try:
  init_db()
  with conn() as c: c.execute('SELECT 1').fetchone()
  out['database']=True
 except Exception: pass
 return out
