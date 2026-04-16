# app.py
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_from_directory
import os
from datetime import datetime, timedelta
import base64
import requests
from io import BytesIO
from PIL import Image
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
import urllib.parse
import re
import json
import math
import random
import time
import hmac as _hmac
import hashlib
from functools import wraps

try:
    import pytesseract
    HAS_OCR = True
except ImportError:
    HAS_OCR = False

try:
    from thai_food_data import THAI_FOOD_DB
except ImportError:
    THAI_FOOD_DB = {}

app = Flask(__name__)
app.secret_key = os.environ.get('SESSION_SECRET', 'super_secret_key_anti_aging_2024')
# Cookie-based sessions — stateless, works on Render (ephemeral filesystem)
app.config['SESSION_PERMANENT'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'None'
app.config['SESSION_COOKIE_SECURE'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = 86400

# ── Token-based auth (bypasses third-party cookie blocking in iframes) ──
_TOKEN_TTL = 86400  # 24 hours

def _make_token(user_id, role, name, email):
    payload = json.dumps({'uid': user_id, 'role': role, 'name': name,
                          'email': email, 'exp': int(time.time()) + _TOKEN_TTL})
    sig = _hmac.new(app.secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}|||{sig}".encode()).decode()

def _verify_token(token):
    try:
        decoded = base64.urlsafe_b64decode(token.encode()).decode()
        payload, sig = decoded.rsplit('|||', 1)
        expected = _hmac.new(app.secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not _hmac.compare_digest(sig, expected):
            return None
        data = json.loads(payload)
        if data['exp'] < time.time():
            return None
        return data
    except Exception:
        return None

app.config['UPLOAD_FOLDER'] = 'static/uploads'
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

ALLOWED_LOGO_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'svg', 'webp'}

def allowed_logo_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_LOGO_EXTENSIONS

app.jinja_env.filters['enumerate'] = enumerate
app.jinja_env.filters['abs'] = abs

# ====================================================
# Import Database Functions
# ====================================================
from database import (get_db, storage_url, upload_to_storage, delete_from_storage,
                       reload_from_sheets, setup_google_sheets, sheets_status)

def _redirect_with_token(endpoint, **kwargs):
    """Redirect preserving ?_t token from current request so session works in Replit iframe."""
    token = request.args.get('_t') or request.form.get('_t')
    if token:
        kwargs['_t'] = token
    return redirect(url_for(endpoint, **kwargs))

# ====================================================
# Biological Age Models
# ====================================================
try:
    from bio_age_models import s_anthropoage, anthropoage_full, phenoage
except ImportError:
    s_anthropoage = None
    anthropoage_full = None
    phenoage = None

# ====================================================
# Default Questionnaire Questions
# ====================================================
DIMENSION_META = {
    1: {"name": "โภชนาการ", "en": "Nutrition", "icon": "utensils", "color": "orange"},
    2: {"name": "การออกกำลังกาย", "en": "Exercise", "icon": "dumbbell", "color": "blue"},
    3: {"name": "การนอนหลับ", "en": "Sleep", "icon": "moon", "color": "violet"},
    4: {"name": "การจัดการความเครียด", "en": "Stress", "icon": "brain", "color": "rose"},
    5: {"name": "สิ่งแวดล้อมและวิถีชีวิต", "en": "Environment", "icon": "leaf", "color": "emerald"},
}

DEFAULT_QUESTIONS = [
    {"q_number": 1,  "dimension": 1, "q_text": "ท่านรับประทานอาหารที่เน้นการชะลอวัยและลดการอักเสบ เช่น ผักหลากสี ธัญพืช"},
    {"q_number": 2,  "dimension": 1, "q_text": "ท่านหลีกเลี่ยงการรับประทานอาหารที่มีน้ำตาลหรือความหวานเกินเกณฑ์"},
    {"q_number": 3,  "dimension": 1, "q_text": "ท่านหลีกเลี่ยงอาหารที่มีโซเดียม (เค็มจัด) หรืออาหารแปรรูป"},
    {"q_number": 4,  "dimension": 1, "q_text": "ท่านหลีกเลี่ยงการรับประทานอาหารที่มีไขมันสูง"},
    {"q_number": 5,  "dimension": 1, "q_text": "ท่านรับประทานอาหารเช้าเป็นประจำและเบาในมื้อเย็น"},
    {"q_number": 6,  "dimension": 1, "q_text": "ท่านดื่มน้ำสะอาดอย่างเพียงพอในแต่ละวัน"},
    {"q_number": 7,  "dimension": 2, "q_text": "ท่านออกกำลังกายแบบแอโรบิคอย่างสม่ำเสมอ"},
    {"q_number": 8,  "dimension": 2, "q_text": "ท่านออกกำลังกายเพื่อสร้างมวลกล้ามเนื้อและกระตุ้นระบบเผาผลาญ"},
    {"q_number": 9,  "dimension": 2, "q_text": "ท่านมีการเคลื่อนไหวร่างกายอย่างต่อเนื่องในชีวิตประจำวัน"},
    {"q_number": 10, "dimension": 2, "q_text": "ท่านออกกำลังกายด้วยความหนักและระยะเวลาที่เหมาะสมกับวัย"},
    {"q_number": 11, "dimension": 2, "q_text": "ท่านมีการบันทึกหรือติดตามกิจกรรมการออกกำลังกายของตนเอง"},
    {"q_number": 12, "dimension": 2, "q_text": "ท่านหลีกเลี่ยงพฤติกรรมเนือยนิ่ง เช่น การนั่งนิ่งๆ เป็นเวลานาน"},
    {"q_number": 13, "dimension": 3, "q_text": "ท่านเข้านอนและตื่นนอนตรงเวลาสม่ำเสมอ"},
    {"q_number": 14, "dimension": 3, "q_text": "ท่านนอนหลับพักผ่อนได้อย่างน้อย 7 ชั่วโมงต่อคืน"},
    {"q_number": 15, "dimension": 3, "q_text": "ท่านหลีกเลี่ยงการใช้หน้าจอสมาร์ทโฟนก่อนเข้านอนเพื่อคุณภาพการนอน"},
    {"q_number": 16, "dimension": 3, "q_text": "ท่านนอนหลับได้สนิทและไม่มีอาการตื่นบ่อยกลางดึก"},
    {"q_number": 17, "dimension": 3, "q_text": "ท่านรู้สึกสดชื่นและกระปรี้กระเปร่าเมื่อตื่นนอน"},
    {"q_number": 18, "dimension": 3, "q_text": "ท่านจัดสิ่งแวดล้อมให้เหมาะสมกับการพักผ่อน เช่น เงียบ สงบ อุณหภูมิพอดี"},
    {"q_number": 19, "dimension": 4, "q_text": "ท่านรู้วิธีจัดการกับอารมณ์และลดความเครียดที่เกิดขึ้นระหว่างวัน"},
    {"q_number": 20, "dimension": 4, "q_text": "ท่านมีการฝึกสมาธิหรือกำหนดลมหายใจเพื่อความผ่อนคลาย"},
    {"q_number": 21, "dimension": 4, "q_text": "ท่านทำกิจกรรมที่สร้างความสุขทางจิตใจเพื่อลดฮอร์โมนความเครียด"},
    {"q_number": 22, "dimension": 4, "q_text": "ท่านมีทัศนคติที่ดี มองโลกในแง่ดี และรู้จักปล่อยวาง"},
    {"q_number": 23, "dimension": 4, "q_text": "ท่านปรึกษาหรือพูดคุยกับผู้อื่นเมื่อมีความไม่สบายใจ"},
    {"q_number": 24, "dimension": 4, "q_text": "ท่านมีเวลาทำกิจกรรมสันทนาการที่ตนเองสนใจเพื่อคลายความกังวล"},
    {"q_number": 25, "dimension": 5, "q_text": "ท่านงดการสูบบุหรี่หรือหลีกเลี่ยงการรับควันบุหรี่"},
    {"q_number": 26, "dimension": 5, "q_text": "ท่านงดหรือจำกัดการดื่มแอลกอฮอล์ในปริมาณที่เหมาะสม"},
    {"q_number": 27, "dimension": 5, "q_text": "ท่านหลีกเลี่ยงการสัมผัสมลพิษ สารพิษ หรือฝุ่น PM2.5"},
    {"q_number": 28, "dimension": 5, "q_text": "ท่านหลีกเลี่ยงการใช้สารเคมีหรือปัจจัยเสี่ยงที่เร่งกระบวนการชรา"},
    {"q_number": 29, "dimension": 5, "q_text": "ท่านมีส่วนร่วมในกิจกรรมทางสังคมหรือการรวมกลุ่มกับผู้อื่น"},
    {"q_number": 30, "dimension": 5, "q_text": "ท่านติดตามข้อมูลข่าวสารเพื่อปรับเปลี่ยนวิถีชีวิตให้มีคุณภาพ"},
]

def get_questions_for_template(conn):
    rows = conn.execute("SELECT q_number, dimension, q_text FROM custom_questions ORDER BY q_number").fetchall()
    custom = {r['q_number']: r['q_text'] for r in rows}
    result = []
    for dq in DEFAULT_QUESTIONS:
        q = dict(dq)
        q['q_text'] = custom.get(dq['q_number'], dq['q_text'])
        q['dim_meta'] = DIMENSION_META[dq['dimension']]
        result.append(q)
    by_dim = {}
    for q in result:
        d = q['dimension']
        if d not in by_dim:
            by_dim[d] = {'meta': DIMENSION_META[d], 'questions': []}
        by_dim[d]['questions'].append(q)
    return result, by_dim

# ====================================================
# Context Processors
# ====================================================
@app.context_processor
def inject_user_stats():
    if session.get('role') != 'user' or 'user_id' not in session:
        return {}
    try:
        conn = get_db()
        uid = session['user_id']
        pts_row = conn.execute(
            "SELECT points FROM user_points WHERE user_id=%s", (uid,)
        ).fetchone()
        pts = pts_row['points'] if pts_row else 0
        rank_row = conn.execute(
            "SELECT COUNT(*)+1 AS r FROM user_points WHERE points > %s",
            (pts,)
        ).fetchone()
        rank = int(rank_row['r']) if rank_row and rank_row['r'] else 1
        conn.close()
        return {'points': pts, 'my_rank': rank}
    except Exception:
        return {'points': 0, 'my_rank': 1}

@app.context_processor
def inject_app_settings():
    try:
        conn = get_db()
        rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
        conn.close()
        settings = {row['key']: row['value'] for row in rows}
    except Exception:
        settings = {}
    logo = settings.get('logo_path', '')
    if logo and not logo.startswith('/') and not logo.startswith('http'):
        logo = '/' + logo
    return dict(app_settings={
        'app_name': settings.get('app_name', 'mHealth'),
        'logo_path': logo,
        'font_name': settings.get('font_name', 'Prompt'),
    })

# ====================================================
# AI & Environment APIs
# ====================================================
# ── OpenRouter free models สำหรับ สแกนภาพ (Vision) ──────────────────────────
_VISION_MODELS = [
    # ยืนยันรองรับภาพ (จาก API ล่าสุด)
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "nvidia/nemotron-nano-12b-v2-vl:free",
    "google/gemma-3-27b-it:free",
    "google/gemma-3-12b-it:free",
    "google/gemma-3-4b-it:free",
    "meta-llama/llama-guard-4-12b:free",
    # fallback vision เก่า (อาจยังใช้ได้)
    "meta-llama/llama-3.2-90b-vision-instruct:free",
    "meta-llama/llama-3.2-11b-vision-instruct:free",
    "meta-llama/llama-4-maverick:free",
    "meta-llama/llama-4-scout:free",
    "qwen/qwen2-vl-72b-instruct:free",
    "qwen/qwen2-vl-7b-instruct:free",
    "qwen/qwen-vl-plus:free",
    "mistralai/pixtral-12b:free",
    "mistralai/pixtral-large:free",
    "mistralai/mistral-small-3.1-24b-instruct:free",
    "microsoft/phi-4-multimodal-instruct:free",
    "microsoft/phi-3.5-vision-instruct:free",
    "google/gemini-flash-1.5:free",
    "google/gemini-pro-vision:free",
    "google/gemini-2.0-flash-exp:free",
    "google/gemini-1.5-flash:free",
    "google/gemini-exp-1206:free",
    "openai/gpt-4o-mini:free",
    "openai/gpt-4o:free",
    "openai/gpt-oss-120b:free",
    "openai/gpt-oss-20b:free",
    "anthropic/claude-3-haiku:free",
    "anthropic/claude-3.5-sonnet:free",
    "thudm/glm-4v-9b:free",
    "thudm/glm-z1-9b:free",
    "bytedance-research/ui-tars-72b:free",
    "moonshotai/moonlight-16b-a3b-instruct:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "nvidia/nemotron-3-nano-30b-a3b:free",
    "nvidia/nemotron-nano-9b-v2:free",
    "nvidia/llama-3.1-nemotron-70b-instruct:free",
    "nousresearch/hermes-3-llama-3.1-405b:free",
    "minimax/minimax-m2.5:free",
    "arcee-ai/trinity-large-preview:free",
    "z-ai/glm-4.5-air:free",
    "qwen/qwen3-next-80b-a3b-instruct:free",
    "qwen/qwen3-235b-a22b:free",
    "deepseek/deepseek-chat-v3-0324:free",
    "deepseek/deepseek-r1:free",
    "meta-llama/llama-3.3-70b-instruct:free",
]

# ── OpenRouter free models สำหรับ แนะนำเมนู/text ────────────────────────────
_TEXT_MODELS = [
    # ยืนยันฟรี (จาก API ล่าสุด)
    "openai/gpt-oss-120b:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "nousresearch/hermes-3-llama-3.1-405b:free",
    "minimax/minimax-m2.5:free",
    "qwen/qwen3-next-80b-a3b-instruct:free",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "google/gemma-3-27b-it:free",
    "arcee-ai/trinity-large-preview:free",
    "z-ai/glm-4.5-air:free",
    "openai/gpt-oss-20b:free",
    "nvidia/nemotron-3-nano-30b-a3b:free",
    "nvidia/nemotron-nano-9b-v2:free",
    "nvidia/nemotron-nano-12b-v2-vl:free",
    "google/gemma-3-12b-it:free",
    "google/gemma-3-4b-it:free",
    "google/gemma-3n-e4b-it:free",
    "google/gemma-3n-e2b-it:free",
    "meta-llama/llama-3.2-3b-instruct:free",
    "cognitivecomputations/dolphin-mistral-24b-venice-edition:free",
    "liquid/lfm-2.5-1.2b-instruct:free",
    "liquid/lfm-2.5-1.2b-thinking:free",
    "qwen/qwen3-coder:free",
    # fallback เก่า (อาจยังใช้ได้)
    "meta-llama/llama-4-maverick:free",
    "meta-llama/llama-4-scout:free",
    "qwen/qwen3-235b-a22b:free",
    "qwen/qwen3-30b-a3b:free",
    "qwen/qwen3-14b:free",
    "qwen/qwen-2.5-72b-instruct:free",
    "qwen/qwen-2.5-7b-instruct:free",
    "qwen/qwen-2.5-coder-32b-instruct:free",
    "deepseek/deepseek-chat-v3-0324:free",
    "deepseek/deepseek-r1:free",
    "deepseek/deepseek-chat:free",
    "mistralai/mistral-small-3.1-24b-instruct:free",
    "mistralai/mistral-7b-instruct:free",
    "mistralai/mixtral-8x7b-instruct:free",
    "nvidia/llama-3.3-nemotron-super-49b-v1:free",
    "nvidia/llama-3.1-nemotron-70b-instruct:free",
    "microsoft/phi-4-mini-instruct:free",
    "microsoft/phi-3-mini-128k-instruct:free",
    "tngtech/deepseek-r1t-chimera:free",
    "liquid/lfm-40b:free",
    "huggingfaceh4/zephyr-7b-beta:free",
    "openchat/openchat-7b:free",
    "gryphe/mythomax-l2-13b:free",
    "undi95/toppy-m-7b:free",
    "01-ai/yi-34b-chat:free",
]

def ask_ai(prompt, image_base64=None):
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return "Error: Missing API Key"
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    if image_base64:
        messages = [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}}
        ]}]
        models = _VISION_MODELS
    else:
        messages = [{"role": "user", "content": prompt}]
        models = _TEXT_MODELS
    for model in models:
        try:
            response = requests.post(url, headers=headers,
                                     json={"model": model, "messages": messages}, timeout=12)
            if response.status_code == 200:
                return response.json()['choices'][0]['message']['content']
        except Exception:
            continue
    return "ระบบ AI ขัดข้อง"

@app.route('/api/uv_index')
def get_uv_index():
    lat = request.args.get('lat')
    lon = request.args.get('lon')
    try:
        if lat and lon:
            r = requests.get(
                f'https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&daily=uv_index_max&forecast_days=1&timezone=auto',
                timeout=5
            )
            if r.status_code == 200:
                uv = r.json().get('daily', {}).get('uv_index_max', [5])[0]
                return jsonify({"success": True, "uv_index": round(uv, 1)})
    except Exception:
        pass
    return jsonify({"success": True, "uv_index": 5, "advice": "UV ปานกลาง ทาครีมกันแดดด้วยนะ"})

@app.route('/api/air_quality')
def air_quality():
    lat = request.args.get('lat')
    lon = request.args.get('lon')
    try:
        if lat and lon:
            aq_res = requests.get(
                f'https://air-quality-api.open-meteo.com/v1/air-quality?latitude={lat}&longitude={lon}&current=pm2_5&timezone=auto',
                timeout=5
            )
            temp_res = requests.get(
                f'https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m&timezone=auto',
                timeout=5
            )
            pm25 = 0
            temp = 0
            if aq_res.status_code == 200:
                pm25 = round(aq_res.json().get('current', {}).get('pm2_5', 0), 1)
            if temp_res.status_code == 200:
                temp = round(temp_res.json().get('current', {}).get('temperature_2m', 0), 1)
            return jsonify([{"pm25": pm25, "temp": temp, "dustboy_name": "Open-Meteo Live"}])
    except Exception:
        pass
    return jsonify([{"pm25": 32, "temp": 27, "dustboy_name": "Default"}])

def get_youtube_education_videos(query="อาหารสุขภาพ ชะลอวัย", max_results=4):
    yt_key = os.environ.get("YOUTUBE_API_KEY")
    if not yt_key: 
        return []
    try:
        res = requests.get(
            "https://www.googleapis.com/youtube/v3/search", 
            params={
                'part': 'snippet', 'q': query, 'key': yt_key, 
                'type': 'video', 'maxResults': max_results
            }
        )
        if res.status_code == 200: 
            return [
                {
                    'title': item['snippet']['title'], 
                    'video_id': item['id']['videoId']
                } 
                for item in res.json().get('items', [])
            ]
    except Exception: 
        pass
    return []

# ====================================================
# Database Setup
# ====================================================
def init_db():
    from database import init_db as db_init
    db_init()

@app.before_request
def auth_from_token():
    """Allow login via ?_t=TOKEN so sessions work even when cookies are blocked."""
    if 'user_id' in session:
        return
    token = (request.args.get('_t') or
             request.form.get('_t') or
             request.headers.get('X-Auth-Token') or
             request.cookies.get('auth_token'))
    if token:
        data = _verify_token(token)
        if data:
            session.permanent = True
            session['user_id'] = data['uid']
            session['role']    = data['role']
            session['name']    = data['name']
            session['email']   = data['email']

# ====================================================
# Auth Routes
# ====================================================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email=%s", (email,)).fetchone()
        authenticated = False
        if user:
            stored_pw = user['password'] or ''
            if stored_pw.startswith('pbkdf2:') or stored_pw.startswith('scrypt:'):
                authenticated = check_password_hash(stored_pw, password)
            else:
                authenticated = (stored_pw == password)
                if authenticated:
                    conn.execute("UPDATE users SET password=%s WHERE id=%s",
                                 (generate_password_hash(password), user['id']))
                    conn.commit()
        if user and authenticated:
            session.permanent = True
            session['user_id'] = user['id']
            session['name'] = user['name']
            session['role'] = user['role']
            session['email'] = user['email']
            login_token = _make_token(user['id'], user['role'], user['name'], user['email'])
            if user['role'] == 'user':
                pre_test = conn.execute("SELECT id FROM questionnaires WHERE user_id=%s AND type='pre'", (user['id'],)).fetchone()
                if not pre_test:
                    conn.close()
                    return redirect(url_for('questionnaire', type='pre', _t=login_token))
                # daily login bonus — once per calendar day
                _today = datetime.now().strftime('%Y-%m-%d')
                _has_login = conn.execute(
                    "SELECT id FROM challenges WHERE user_id=%s AND type='daily_login' AND date=%s",
                    (user['id'], _today)
                ).fetchone()
                if not _has_login:
                    conn.execute("INSERT INTO challenges (user_id, type, date) VALUES (%s, 'daily_login', %s)", (user['id'], _today))
                    conn.execute("UPDATE user_points SET points = points + 10 WHERE user_id=%s", (user['id'],))
                    conn.commit()
            conn.close()
            if user['role'] in ['admin', 'researcher']:
                return redirect(url_for('admin_panel', _t=login_token))
            return redirect(url_for('dashboard', _t=login_token))
        conn.close()
        return render_template('login.html', error="อีเมลหรือรหัสผ่านไม่ถูกต้อง")
    return render_template('login.html')

@app.route('/api/login', methods=['POST'])
def api_login():
    """JSON login endpoint — returns HMAC token so JS can store in localStorage."""
    data = request.get_json(silent=True) or {}
    email    = data.get('email', '').strip()
    password = data.get('password', '')
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email=%s", (email,)).fetchone()
    authenticated = False
    if user:
        stored_pw = user['password'] or ''
        if stored_pw.startswith('pbkdf2:') or stored_pw.startswith('scrypt:'):
            authenticated = check_password_hash(stored_pw, password)
        else:
            authenticated = (stored_pw == password)
            if authenticated:
                conn.execute("UPDATE users SET password=%s WHERE id=%s",
                             (generate_password_hash(password), user['id']))
                conn.commit()
    if not (user and authenticated):
        conn.close()
        return jsonify({'ok': False, 'error': 'อีเมลหรือรหัสผ่านไม่ถูกต้อง'}), 401
    redirect_url = '/admin' if user['role'] in ['admin', 'researcher'] else '/dashboard'
    if user['role'] == 'user':
        pre_test = conn.execute("SELECT id FROM questionnaires WHERE user_id=%s AND type='pre'",
                                (user['id'],)).fetchone()
        if not pre_test:
            redirect_url = '/questionnaire/pre'
        else:
            _today = datetime.now().strftime('%Y-%m-%d')
            _has_login = conn.execute(
                "SELECT id FROM challenges WHERE user_id=%s AND type='daily_login' AND date=%s",
                (user['id'], _today)
            ).fetchone()
            if not _has_login:
                conn.execute("INSERT INTO challenges (user_id, type, date) VALUES (%s, 'daily_login', %s)", (user['id'], _today))
                conn.execute("UPDATE user_points SET points = points + 10 WHERE user_id=%s", (user['id'],))
                conn.commit()
    conn.close()
    token = _make_token(user['id'], user['role'], user['name'], user['email'])
    return jsonify({'ok': True, 'token': token, 'redirect': redirect_url})

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    """Step 1: ยืนยันตัวตนด้วย username + นามสกุล + อายุ + อาชีพ  Step 2: ตั้งรหัสผ่านใหม่"""
    if request.method == 'GET':
        return render_template('forgot_password.html')

    step = request.form.get('step', '1')
    conn = get_db()

    if step == '1':
        username  = (request.form.get('username') or '').strip()
        last_name = (request.form.get('last_name') or '').strip()
        age_str   = (request.form.get('age') or '').strip()
        occupation= (request.form.get('occupation') or '').strip()

        if not username or not last_name or not age_str:
            conn.close()
            return render_template('forgot_password.html', error='กรุณากรอกชื่อผู้ใช้ นามสกุล และอายุ', step=1)

        user = conn.execute("SELECT * FROM users WHERE email=%s", (username,)).fetchone()
        if not user:
            conn.close()
            return render_template('forgot_password.html', error='ไม่พบบัญชีผู้ใช้นี้ในระบบ', step=1)

        # ตรวจสอบนามสกุล
        db_last = (user['last_name'] or '').strip().lower()
        if db_last != last_name.lower():
            conn.close()
            return render_template('forgot_password.html', error='ข้อมูลไม่ตรงกับที่ลงทะเบียนไว้', step=1)

        # ตรวจสอบอาชีพ (ข้ามถ้า DB ว่าง; ยืดหยุ่นสำหรับ "อื่นๆ")
        db_occ = (user['occupation'] or '').strip().lower()
        if db_occ:  # ตรวจเฉพาะเมื่อ DB มีข้อมูลอาชีพ
            inp_occ = occupation.strip().lower()
            _OTHER = {'อื่นๆ', 'อื่น', 'อื่น ๆ', 'other', 'others', 'อื่นๆ ', ' อื่นๆ'}
            occ_match = (db_occ == inp_occ) or (db_occ in _OTHER and inp_occ in _OTHER)
            if not occ_match:
                conn.close()
                return render_template('forgot_password.html', error='ข้อมูลไม่ตรงกับที่ลงทะเบียนไว้', step=1)

        # ตรวจสอบอายุจาก questionnaire (ยืดหยุ่น ±1 ปี)
        try:
            age_in = int(age_str)
        except ValueError:
            conn.close()
            return render_template('forgot_password.html', error='กรุณากรอกอายุเป็นตัวเลข', step=1)

        q_row = conn.execute(
            "SELECT age FROM questionnaires WHERE user_id=%s ORDER BY id DESC LIMIT 1",
            (user['id'],)
        ).fetchone()
        if not q_row or q_row['age'] is None:
            # ถ้ายังไม่เคยตอบแบบสอบถาม ให้ผ่านการตรวจอายุ (ไม่มีข้อมูลเปรียบเทียบ)
            age_ok = True
        else:
            age_ok = abs(int(q_row['age']) - age_in) <= 1

        if not age_ok:
            conn.close()
            return render_template('forgot_password.html', error='ข้อมูลไม่ตรงกับที่ลงทะเบียนไว้', step=1)

        conn.close()
        return render_template('forgot_password.html', step=2, verified_user=username)

    elif step == '2':
        username     = (request.form.get('verified_user') or '').strip()
        new_password = (request.form.get('new_password') or '').strip()
        confirm_pw   = (request.form.get('confirm_password') or '').strip()

        if not new_password or len(new_password) < 6:
            conn.close()
            return render_template('forgot_password.html', step=2, verified_user=username,
                                   error='รหัสผ่านต้องมีอย่างน้อย 6 ตัวอักษร')
        if new_password != confirm_pw:
            conn.close()
            return render_template('forgot_password.html', step=2, verified_user=username,
                                   error='รหัสผ่านทั้งสองช่องไม่ตรงกัน')

        conn.execute("UPDATE users SET password=%s WHERE email=%s",
                     (generate_password_hash(new_password), username))
        conn.commit()
        conn.close()
        return render_template('forgot_password.html', step='done')

    conn.close()
    return redirect(url_for('login'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        name = request.form.get('name')
        last_name = request.form.get('last_name', '').strip()
        occupation = request.form.get('occupation', '').strip()
        role = request.form.get('role', 'user')
        conn = get_db()
        if conn.execute("SELECT id FROM users WHERE email=%s", (email,)).fetchone():
            conn.close()
            return render_template('register.html', error="บัญชีนี้มีผู้ใช้งานแล้ว")

        cur = conn.execute("INSERT INTO users (email, password, name, last_name, occupation, role) VALUES (%s, %s, %s, %s, %s, %s)", 
                  (email, generate_password_hash(password), name, last_name or None, occupation or None, role))
        conn.commit()
        user_id = conn.execute("SELECT currval('users_id_seq')").fetchone()['currval']

        conn.execute("INSERT INTO user_points (user_id, points) VALUES (%s, 0)", (user_id,))
        conn.execute("INSERT INTO user_health_stats (user_id) VALUES (%s)", (user_id,))
        conn.execute("INSERT INTO post_test_status (user_id, is_unlocked) VALUES (%s, 0)", (user_id,))
        conn.commit()
        conn.close()

        session.permanent = True
        session['user_id'] = int(user_id)
        session['name'] = name
        session['email'] = email
        session['role'] = role
        token = _make_token(int(user_id), role, name, email)
        if role == 'user':
            return redirect(url_for('questionnaire', type='pre', _t=token))
        if role in ['admin', 'researcher']:
            return redirect(url_for('admin_panel', _t=token))
        return redirect(url_for('dashboard', _t=token))
    return render_template('register.html')

@app.route('/manifest.json')
def dynamic_manifest():
    conn = get_db()
    try:
        rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
        settings = {r['key']: r['value'] for r in rows}
    except Exception:
        settings = {}
    conn.close()
    logo = settings.get('logo_path', '')
    if logo and not logo.startswith('/'):
        logo = '/' + logo
    icons = []
    if logo:
        icons = [
            {"src": logo, "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": logo, "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}
        ]
    else:
        icons = [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"}
        ]
    manifest = {
        "name": settings.get('app_name', 'mHealth') + " Anti-Aging",
        "short_name": settings.get('app_name', 'mHealth'),
        "description": "Multidomain Anti-Aging Health Program",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#F5F3EE",
        "theme_color": "#064e3b",
        "orientation": "portrait-primary",
        "icons": icons,
        "categories": ["health", "fitness", "medical"]
    }
    return jsonify(manifest)

@app.route('/app-icon')
def app_icon():
    from flask import send_file as _send_file
    conn = get_db()
    try:
        rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
        settings = {r['key']: r['value'] for r in rows}
    except Exception:
        settings = {}
    conn.close()
    logo = settings.get('logo_path', '')
    if logo:
        if logo.startswith('http'):
            from flask import redirect as _redir
            return _redir(logo)
        local_path = logo.lstrip('/')
        if os.path.exists(local_path):
            return _send_file(local_path)
    return _send_file('static/icon-192.png')

@app.route('/debug-session')
def debug_session():
    if not (request.remote_addr in ('127.0.0.1', '::1') or os.environ.get('FLASK_DEBUG') == '1'):
        return jsonify({'error': 'Forbidden'}), 403
    return jsonify({
        'session': dict(session),
        'session_keys': list(session.keys()),
        'logged_in': 'user_id' in session
    })

@app.route('/logout')
def logout():
    session.clear()
    return '''<!DOCTYPE html><html><head><meta charset="UTF-8"></head><body>
<script>localStorage.removeItem('auth_token'); window.location.href = '/login';</script>
</body></html>'''

# ====================================================
# Dashboard
# ====================================================
@app.route('/')
@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session: 
        return redirect(url_for('login'))
    if session.get('role') in ['admin', 'researcher']:
        return _redirect_with_token('admin_panel')

    conn = get_db()
    users = []
    q_data = None
    pts = {'points': 0}
    health_stats = None
    post_test_ready = False
    my_rank = 0
    user_total_users = 0
    social_share = {'text': '', 'files': [], 'entries': []}

    today_str = datetime.now().strftime('%Y-%m-%d')
    today_exercise = today_steps = today_sleep = today_cal = 0
    chart_data = { 
        (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d'): {'exercise': 0, 'sleep': 0} 
        for i in range(6, -1, -1) 
    }

    if session.get('role') in ['admin', 'researcher']:
        users_raw = conn.execute('''
            SELECT u.id, u.name, u.last_name, u.occupation, u.email,
                   COALESCE(p.is_unlocked, 0) as is_unlocked,
                   (SELECT MAX(date) FROM daily_logs WHERE user_id = u.id) as last_log,
                   (SELECT MAX(date) FROM exercises WHERE user_id = u.id) as last_ex
            FROM users u LEFT JOIN post_test_status p ON u.id = p.user_id WHERE u.role='user'
        ''').fetchall()

        user_surveys = {}
        surveys_raw = conn.execute("SELECT user_id, answers_json FROM questionnaires ORDER BY id DESC").fetchall()
        for s in surveys_raw:
            if s['user_id'] not in user_surveys and s['answers_json']:
                try: 
                    user_surveys[s['user_id']] = json.loads(s['answers_json'])
                except: 
                    pass

        today = datetime.now().date()
        for r in users_raw:
            last_log = r['last_log'] or '2000-01-01'
            last_ex = r['last_ex'] or '2000-01-01'
            last_date_str = max(last_log, last_ex)

            if last_date_str == '2000-01-01': 
                days_absent = 999 
            else:
                try: 
                    days_absent = (today - datetime.strptime(last_date_str, '%Y-%m-%d').date()).days
                except: 
                    days_absent = 999

            users.append({
                'id': r['id'], 'name': r['name'] or 'ผู้ใช้ไม่ระบุชื่อ', 'email': r['email'] or 'ไม่ระบุอีเมล',
                'is_unlocked': r['is_unlocked'], 'days_absent': days_absent, 'latest_answers': user_surveys.get(r['id'], {})
            })

    if session.get('role') == 'user':
        q_data_raw = conn.execute("SELECT age, bmi, waist FROM questionnaires WHERE user_id=%s ORDER BY id DESC LIMIT 1", (session['user_id'],)).fetchone()
        if q_data_raw: 
            q_data = dict(q_data_raw)

        pts_raw = conn.execute("SELECT points FROM user_points WHERE user_id=%s", (session['user_id'],)).fetchone()
        if pts_raw: 
            pts = dict(pts_raw)

        user_rank_row = conn.execute('''
            SELECT COUNT(*) + 1 AS my_rank FROM user_points
            WHERE points > COALESCE((SELECT points FROM user_points WHERE user_id=%s), 0)
        ''', (session['user_id'],)).fetchone()

        _cnt = conn.execute("SELECT COUNT(*) FROM users WHERE role='user'").fetchone()
        user_total_users = (_cnt['count'] if _cnt else 0)
        my_rank = user_rank_row['my_rank'] if user_rank_row else 0

        health_stats_raw = conn.execute("SELECT * FROM user_health_stats WHERE user_id=%s", (session['user_id'],)).fetchone()
        if health_stats_raw: 
            health_stats = dict(health_stats_raw)
            # ถ้ายังไม่มีค่า pre ให้ fallback ไปใช้ค่ารวม
            if not health_stats.get('epigenetic_age_pre') and health_stats.get('epigenetic_age'):
                health_stats['epigenetic_age_pre'] = health_stats['epigenetic_age']
            if not health_stats.get('fitness_score_pre') and health_stats.get('fitness_score'):
                health_stats['fitness_score_pre'] = health_stats['fitness_score']
        else:
            health_stats = {'epigenetic_age': None, 'fitness_score': None, 'biological_age': None, 'age_acceleration': None,
                            'epigenetic_age_pre': None, 'epigenetic_age_post': None,
                            'fitness_score_pre': None, 'fitness_score_post': None}

        latest_epi_lab = conn.execute(
            "SELECT filename FROM lab_results WHERE user_id=%s AND lab_type='epigenetic' ORDER BY uploaded_at DESC LIMIT 1",
            (session['user_id'],)
        ).fetchone()
        latest_inbody_lab = conn.execute(
            "SELECT filename FROM lab_results WHERE user_id=%s AND lab_type='inbody' ORDER BY uploaded_at DESC LIMIT 1",
            (session['user_id'],)
        ).fetchone()
        if latest_epi_lab and latest_epi_lab["filename"]:
            health_stats['epigenetic_lab_url'] = storage_url('lab-results', latest_epi_lab["filename"])
        if latest_inbody_lab and latest_inbody_lab["filename"]:
            health_stats['inbody_lab_url'] = storage_url('lab-results', latest_inbody_lab["filename"])

        # Fetch per-period lab records for dashboard bottom sheet
        def _lab_rows_by_period(lab_type, period):
            rows = conn.execute(
                "SELECT id, filename, original_name, notes, uploaded_at FROM lab_results "
                "WHERE user_id=%s AND lab_type=%s AND period=%s ORDER BY uploaded_at DESC LIMIT 5",
                (session['user_id'], lab_type, period)
            ).fetchall()
            return [{
                'filename': r['filename'],
                'original_name': r['original_name'] or '',
                'notes': r['notes'] or '',
                'uploaded_at': str(r['uploaded_at'])[:10] if r['uploaded_at'] else '',
                'url': storage_url('lab-results', r['filename']) if r['filename'] else '',
                'has_file': bool(r['filename'])
            } for r in rows]

        import json as _json
        lab_periods_data = _json.dumps({
            'epigenetic': {
                'pre':  _lab_rows_by_period('epigenetic', 'pre'),
                'post': _lab_rows_by_period('epigenetic', 'post'),
            },
            'inbody': {
                'pre':  _lab_rows_by_period('inbody', 'pre'),
                'post': _lab_rows_by_period('inbody', 'post'),
            }
        }, ensure_ascii=False)

        status = conn.execute("SELECT is_unlocked FROM post_test_status WHERE user_id=%s", (session['user_id'],)).fetchone()
        done = conn.execute("SELECT id FROM questionnaires WHERE user_id=%s AND type='post'", (session['user_id'],)).fetchone()
        if status and status['is_unlocked'] == 1 and not done: 
            post_test_ready = True

        exercises = conn.execute("SELECT date, duration, steps FROM exercises WHERE user_id=%s ORDER BY date DESC LIMIT 30", (session['user_id'],)).fetchall()
        for ex in exercises:
            if ex['date'] == today_str:
                if ex['duration']: 
                    today_exercise += ex['duration']
                if ex['steps']: 
                    today_steps += ex['steps']
            if ex['date'] in chart_data and ex['duration']: 
                chart_data[ex['date']]['exercise'] += ex['duration']

        logs = conn.execute("SELECT date, sleep_hours FROM daily_logs WHERE user_id=%s ORDER BY date DESC LIMIT 30", (session['user_id'],)).fetchall()
        for log in logs:
            if log['date'] == today_str and log['sleep_hours']: 
                today_sleep = log['sleep_hours']
            if log['date'] in chart_data and log['sleep_hours']: 
                chart_data[log['date']]['sleep'] = log['sleep_hours']

        today_cal = int((today_steps * 0.04) + (today_exercise * 5))

        social_share = {
            'text': request.args.get('share_text', '').strip(),
            'files': [],
            'entries': conn.execute("""
                SELECT ss.id, ss.share_text, ss.file_name, ss.file_type, ss.created_at, COALESCE(u.name,'') AS sender_name
                FROM social_shares ss LEFT JOIN users u ON u.id = ss.user_id
                ORDER BY ss.created_at DESC LIMIT 20
            """).fetchall()
        }

    conn.close()

    return render_template('dashboard.html', 
                           name=session.get('name'), role=session.get('role'), users=users, 
                           has_questionnaire=bool(q_data), points=pts.get('points', 0),
                           social_share=social_share,
                           q_data=q_data, health_stats=health_stats, post_test_ready=post_test_ready,
                           today_exercise=today_exercise, today_sleep=today_sleep, 
                           today_steps=today_steps, today_cal=today_cal, 
                           chart_labels=[d[-5:] for d in chart_data.keys()], 
                           chart_exercise=[chart_data[d]['exercise'] for d in chart_data.keys()], 
                           chart_sleep=[chart_data[d]['sleep'] for d in chart_data.keys()],
                           lab_periods_data=lab_periods_data if 'lab_periods_data' in dir() else '{}')

# ====================================================
# AnthropoAge Route
# ====================================================
@app.route('/anthropoage', methods=['GET', 'POST'])
def anthropoage():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        try:
            model_type = request.form.get('model_type', 's_anthropoage')
            age = float(request.form.get('age', 0))

            if age < 18 or age > 100:
                return render_template('anthropoage.html', error="อายุต้องอยู่ระหว่าง 18–100 ปี")

            bio_age = None
            model_name = ""
            diff = 0
            bmi = None
            whtr = None
            sex = request.form.get('sex', 'Women')
            ethnicity = request.form.get('ethnicity', 'Other')

            if model_type == 's_anthropoage':
                if not s_anthropoage:
                    return render_template('anthropoage.html', error="โมเดล s_anthropoage ไม่พร้อมใช้งาน")

                height_cm = float(request.form.get('height_cm', 0))
                weight_kg = float(request.form.get('weight_kg', 0))
                waist_cm = float(request.form.get('waist_cm', 0))

                if height_cm <= 0 or weight_kg <= 0 or waist_cm <= 0:
                    return render_template('anthropoage.html', error="กรุณากรอกข้อมูลสัดส่วนร่างกายให้ครบถ้วน")

                height_m = height_cm / 100.0
                bmi = round(weight_kg / (height_m ** 2), 1)
                whtr = round(waist_cm / height_cm, 3)

                bio_age = s_anthropoage(age, sex, height_m, weight_kg, waist_cm, ethnicity)
                model_name = 'S-AnthropoAge'

            elif model_type == 'full_anthropoage':
                if not anthropoage_full:
                    return render_template('anthropoage.html', error="โมเดล full_anthropoage ไม่พร้อมใช้งาน")

                height_cm  = float(request.form.get('height_cm', 0))
                weight_kg  = float(request.form.get('weight_kg', 0))
                waist_cm   = float(request.form.get('waist_cm', 0))
                thigh_cm   = float(request.form.get('thigh', 0))
                arm_cm     = float(request.form.get('armc', 0))
                subs_mm    = float(request.form.get('subs', 0))
                tric_mm    = float(request.form.get('tric', 0))

                if height_cm <= 0 or weight_kg <= 0 or waist_cm <= 0 or thigh_cm <= 0:
                    return render_template('anthropoage.html', error="กรุณากรอกข้อมูลให้ครบถ้วน")

                if sex.upper() in ["MEN", "M", "MALE"]:
                    if arm_cm <= 0:
                        return render_template('anthropoage.html', error="สำหรับเพศชาย กรุณากรอกรอบแขน")
                else:
                    if subs_mm <= 0 or tric_mm <= 0:
                        return render_template('anthropoage.html', error="สำหรับเพศหญิง กรุณากรอกความหนาไขมันใต้ผิวหนัง")

                height_m = height_cm / 100.0
                bmi  = round(weight_kg / (height_m ** 2), 1)
                whtr = round(waist_cm / height_cm, 3)

                bio_age = anthropoage_full(age, sex, height_m, weight_kg, waist_cm, ethnicity,
                                           thigh_cm=thigh_cm, arm_cm=(arm_cm or 1),
                                           subs_mm=(subs_mm or 1), tric_mm=(tric_mm or 1))
                model_name = 'Full AnthropoAge'

            elif model_type == 'phenoage':
                if not phenoage:
                    return render_template('anthropoage.html', error="โมเดล phenoage ไม่พร้อมใช้งาน")

                crp = float(request.form.get('crp', 0))
                lymph = float(request.form.get('lymph', 0))
                wbc = float(request.form.get('wbc', 0))
                glu = float(request.form.get('glu', 0))
                rdw = float(request.form.get('rdw', 0))
                alb = float(request.form.get('alb', 0))
                cr = float(request.form.get('cr', 0))
                mcv = float(request.form.get('mcv', 0))
                ap = float(request.form.get('ap', 0))

                if any(v <= 0 for v in [crp, lymph, wbc, glu, rdw, alb, cr, mcv, ap]):
                     return render_template('anthropoage.html', error="กรุณากรอกค่าผลเลือดให้ครบ และต้องมากกว่า 0")

                bio_age = phenoage(age, crp, lymph, wbc, glu, rdw, alb, cr, mcv, ap)
                model_name = 'PhenoAge'

            if bio_age is None:
                return render_template('anthropoage.html', error=f"ไม่สามารถคำนวณ {model_name} ได้")

            diff = round(bio_age - age, 1)

            conn = get_db()
            conn.execute(
                "UPDATE user_health_stats SET biological_age=%s, age_acceleration=%s WHERE user_id=%s",
                (bio_age, diff, session['user_id'])
            )
            conn.commit()
            conn.close()

            result = {
                'model': model_name,
                'biological_age': bio_age,
                'chronological_age': int(age),
                'diff': diff,
                'bmi': bmi,
                'whtr': whtr,
                'sex': sex,
                'ethnicity': ethnicity,
            }
            return render_template('anthropoage.html', result=result)

        except (ValueError, TypeError):
            return render_template('anthropoage.html', error="ข้อมูลไม่ถูกต้อง กรุณากรอกเป็นตัวเลขเท่านั้น")

    conn = get_db()
    q_data = conn.execute("SELECT age, bmi, waist FROM questionnaires WHERE user_id=%s ORDER BY id DESC LIMIT 1", (session['user_id'],)).fetchone()
    conn.close()

    prefill = {'age': q_data['age']} if q_data else None
    return render_template('anthropoage.html', prefill=prefill)

# ====================================================
# Settings Route
# ====================================================
@app.route('/settings', methods=['GET', 'POST'])
def settings():
    if 'user_id' not in session: 
        return redirect(url_for('login'))
    conn = get_db()
    if request.method == 'POST':
        new_name     = (request.form.get('name') or '').strip()
        new_last     = (request.form.get('last_name') or '').strip()
        new_occ      = (request.form.get('occupation') or '').strip()
        new_password = (request.form.get('password') or '').strip()
        if new_name:
            if new_password:
                conn.execute(
                    "UPDATE users SET name=%s, last_name=%s, occupation=%s, password=%s WHERE id=%s",
                    (new_name, new_last or None, new_occ or None,
                     generate_password_hash(new_password), session['user_id']))
                msg = "อัปเดตโปรไฟล์และรหัสผ่านสำเร็จ"
            else:
                conn.execute(
                    "UPDATE users SET name=%s, last_name=%s, occupation=%s WHERE id=%s",
                    (new_name, new_last or None, new_occ or None, session['user_id']))
                msg = "อัปเดตข้อมูลส่วนตัวสำเร็จ"
            session['name'] = new_name
        else:
            msg = "กรุณากรอกชื่อจริง"
        conn.commit()
        user = conn.execute("SELECT * FROM users WHERE id=%s", (session['user_id'],)).fetchone()
        conn.close()
        return render_template('settings.html', user=user, success=msg)
    user = conn.execute("SELECT * FROM users WHERE id=%s", (session['user_id'],)).fetchone()
    conn.close()
    return render_template('settings.html', user=user, email=session.get('email'), name=session.get('name'))

# ====================================================
# Questionnaire Route
# ====================================================
@app.route('/questionnaire', methods=['GET', 'POST'])
def questionnaire():
    if 'user_id' not in session: 
        return redirect(url_for('login'))
    q_type = request.args.get('type', request.form.get('q_type', 'pre'))
    if request.method == 'POST':
        form_data = request.form.to_dict()
        age = form_data.pop('age', None)
        waist = form_data.pop('waist', None)
        height_cm = form_data.pop('height_cm', None)
        weight_kg = form_data.pop('weight_kg', None)
        bmi = round(float(weight_kg) / ((float(height_cm) / 100.0) ** 2), 1) if height_cm and weight_kg else None
        conn = get_db()
        conn.execute("""
            INSERT INTO questionnaires (user_id, type, age, bmi, waist, answers_json) 
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (session['user_id'], q_type, age, bmi, waist, json.dumps(form_data)))
        conn.commit()
        conn.close()
        return _redirect_with_token('dashboard')
    conn = get_db()
    questions, by_dim = get_questions_for_template(conn)
    conn.close()
    return render_template('questionnaire.html', q_type=q_type, questions=questions, by_dim=by_dim, dim_meta=DIMENSION_META)

# ====================================================
# Leaderboard Route
# ====================================================
@app.route('/leaderboard')
def leaderboard():
    if 'user_id' not in session: 
        return redirect(url_for('login'))
    conn = get_db()

    # Fetch each table separately to avoid JOIN issues
    user_rows = conn.execute("SELECT id, name, email FROM users WHERE role='user' ORDER BY id ASC").fetchall()
    pts_rows  = conn.execute("SELECT user_id, points FROM user_points").fetchall()
    pts_map   = {r['user_id']: r['points'] for r in pts_rows}

    # Build total-points leaderboard
    board = []
    for r in user_rows:
        board.append({
            'id':    r['id'],
            'name':  r['name'] or 'ผู้ใช้ไม่ระบุชื่อ',
            'email': r['email'] or '-',
            'points': pts_map.get(r['id'], 0),
            'is_me': r['id'] == session['user_id'],
        })
    board.sort(key=lambda x: (-x['points'], x['id']))
    ranked = [dict(r, rank=i+1) for i, r in enumerate(board)]

    # Weekly: นับตั้งแต่วันอาทิตย์ที่ผ่านมา (รีเซ็ตทุกอาทิตย์เที่ยงคืน)
    from datetime import datetime as _dt, timedelta as _td
    _today = _dt.now()
    _days_since_sun = (_today.weekday() + 1) % 7   # Mon=1, Tue=2, ... Sun=0
    _week_start = (_today - _td(days=_days_since_sun)).strftime('%Y-%m-%d')

    ex_rows = conn.execute(
        "SELECT user_id, COUNT(*) as cnt FROM exercises WHERE date >= %s GROUP BY user_id",
        (_week_start,)
    ).fetchall()
    dl_rows = conn.execute(
        "SELECT user_id, COUNT(*) as cnt FROM daily_logs WHERE date >= %s GROUP BY user_id",
        (_week_start,)
    ).fetchall()
    vw_rows = conn.execute(
        "SELECT user_id, COUNT(*) as cnt FROM video_watches WHERE watched_at >= %s GROUP BY user_id",
        (_week_start,)
    ).fetchall()
    ex_map = {r['user_id']: r['cnt'] for r in ex_rows}
    dl_map = {r['user_id']: r['cnt'] for r in dl_rows}
    vw_map = {r['user_id']: r['cnt'] for r in vw_rows}

    weekly_board = []
    for r in user_rows:
        wp = (ex_map.get(r['id'], 0) + dl_map.get(r['id'], 0) + vw_map.get(r['id'], 0)) * 10
        if wp > 0:
            weekly_board.append({
                'id':    r['id'],
                'name':  r['name'] or 'ผู้ใช้ไม่ระบุชื่อ',
                'email': r['email'] or '-',
                'points': wp,
                'is_me': r['id'] == session['user_id'],
            })
    weekly_board.sort(key=lambda x: (-x['points'], x['id']))
    weekly_ranked = [dict(r, rank=i+1) for i, r in enumerate(weekly_board)]

    my_rank   = next((r for r in ranked if r['is_me']), None)
    my_weekly = next((r for r in weekly_ranked if r['is_me']), None)
    conn.close()
    return render_template('leaderboard.html', ranked=ranked, weekly_ranked=weekly_ranked,
                           my_rank=my_rank, my_weekly=my_weekly, week_start=_week_start)

# ====================================================
# Video Watch API
# ====================================================
@app.route('/api/mark_video_watched', methods=['POST'])
def mark_video_watched():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'not logged in'}), 401
    user_id = session['user_id']
    material_id = request.json.get('material_id') if request.is_json else request.form.get('material_id')
    if not material_id:
        return jsonify({'success': False, 'error': 'missing material_id'}), 400
    conn = get_db()
    existing = conn.execute(
        'SELECT id FROM video_watches WHERE user_id=%s AND material_id=%s',
        (user_id, int(material_id))
    ).fetchone()
    if existing:
        conn.close()
        return jsonify({'success': False, 'already_watched': True})
    conn.execute(
        'INSERT INTO video_watches (user_id, material_id) VALUES (%s, %s)',
        (user_id, int(material_id))
    )
    conn.execute('UPDATE user_points SET points = points + 10 WHERE user_id = %s', (user_id,))
    conn.commit()
    new_total = conn.execute('SELECT points FROM user_points WHERE user_id=%s', (user_id,)).fetchone()
    conn.close()
    return jsonify({'success': True, 'awarded': 10, 'total': new_total['points'] if new_total else 0})

# ====================================================
# Social Share Route
# ====================================================
@app.route('/social/share', methods=['POST'])
def social_share():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    conn = get_db()
    share_text = request.form.get('share_text', '').strip()
    uploaded = request.files.getlist('share_files')
    saved = 0
    for f in uploaded:
        if not f or not f.filename:
            continue
        filename = secure_filename(f.filename)
        file_bytes = f.read()
        upload_to_storage('social-files', filename, file_bytes, f.mimetype or 'application/octet-stream')
        os.makedirs('static/uploads/social', exist_ok=True)
        with open(os.path.join('static/uploads/social', filename), 'wb') as fout:
            fout.write(file_bytes)
        conn.execute(
            "INSERT INTO social_shares (user_id, share_text, file_name, file_type) VALUES (%s, %s, %s, %s)",
            (session['user_id'], share_text if saved == 0 else '', filename, f.mimetype)
        )
        saved += 1
    if saved == 0 and share_text:
        conn.execute(
            "INSERT INTO social_shares (user_id, share_text, file_name, file_type) VALUES (%s, %s, NULL, NULL)",
            (session['user_id'], share_text)
        )
    conn.commit()
    conn.close()
    return _redirect_with_token('dashboard')

# ====================================================
# Chat / Community API Routes
# ====================================================
@app.route('/api/get_messages')
def api_get_messages():
    if 'user_id' not in session:
        return jsonify([]), 401
    conn = get_db()
    rows = conn.execute(
        "SELECT ss.id, ss.user_id, ss.share_text, ss.file_name, ss.file_type, ss.created_at, "
        "COALESCE(u.name,'') AS sender_name "
        "FROM social_shares ss LEFT JOIN users u ON u.id = ss.user_id "
        "ORDER BY ss.created_at ASC LIMIT 60"
    ).fetchall()
    conn.close()
    last_read_id = session.get('chat_last_read_id', 0)
    result = []
    for r in rows:
        fname = r['file_name'] or ''
        file_url = storage_url('social-files', fname) if fname else ''
        result.append({
            'id': r['id'],
            'user_id': r['user_id'],
            'text': r['share_text'] or '',
            'file_name': fname,
            'file_url': file_url,
            'file_type': r['file_type'] or '',
            'created_at': str(r['created_at'])[:16].replace('T', ' ') if r['created_at'] else '',
            'sender_name': r['sender_name'] or 'ไม่ระบุ',
        })
    return jsonify({'msgs': result, 'last_read_id': last_read_id})

@app.route('/api/send_message', methods=['POST'])
def api_send_message():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json(silent=True) or {}
    text = (data.get('text') or '').strip()
    if not text:
        return jsonify({'error': 'กรุณากรอกข้อความ'}), 400
    if len(text) > 500:
        return jsonify({'error': 'ข้อความยาวเกินไป (สูงสุด 500 ตัวอักษร)'}), 400
    conn = get_db()
    conn.execute(
        "INSERT INTO social_shares (user_id, share_text, file_name, file_type) VALUES (%s, %s, NULL, NULL)",
        (session['user_id'], text)
    )
    conn.commit()
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M')
    conn.close()
    return jsonify({
        'success': True,
        'id': None,
        'user_id': session['user_id'],
        'text': text,
        'sender_name': session.get('name', 'ไม่ระบุ'),
        'created_at': now_str,
        'file_name': '',
        'file_type': '',
    })

@app.route('/api/send_message_file', methods=['POST'])
def api_send_message_file():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    text = (request.form.get('text') or '').strip()
    file = request.files.get('file')
    if not text and not file:
        return jsonify({'error': 'กรุณากรอกข้อความหรือแนบไฟล์'}), 400
    if text and len(text) > 500:
        return jsonify({'error': 'ข้อความยาวเกินไป (สูงสุด 500 ตัวอักษร)'}), 400

    fname = None
    ftype = None
    if file and file.filename:
        import uuid as _uuid
        ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else 'bin'
        fname = f"{_uuid.uuid4().hex[:12]}.{ext}"
        ftype = file.mimetype or f'application/{ext}'
        file_bytes = file.read()
        upload_to_storage('social-files', fname, file_bytes, ftype)
        os.makedirs('static/uploads/social', exist_ok=True)
        with open(os.path.join('static/uploads/social', fname), 'wb') as fout:
            fout.write(file_bytes)

    conn = get_db()
    conn.execute(
        "INSERT INTO social_shares (user_id, share_text, file_name, file_type) VALUES (%s, %s, %s, %s)",
        (session['user_id'], text or None, fname, ftype)
    )
    conn.commit()
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M')
    conn.close()
    return jsonify({
        'success': True,
        'user_id': session['user_id'],
        'text': text,
        'sender_name': session.get('name', 'ไม่ระบุ'),
        'created_at': now_str,
        'file_name': fname or '',
        'file_url': storage_url('social-files', fname) if fname else '',
        'file_type': ftype or '',
    })

@app.route('/api/delete_message/<int:msg_id>', methods=['POST'])
def api_delete_message(msg_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    conn = get_db()
    if session.get('role') in ['admin', 'researcher']:
        conn.execute("DELETE FROM social_shares WHERE id=%s", (msg_id,))
    else:
        conn.execute("DELETE FROM social_shares WHERE id=%s AND user_id=%s", (msg_id, session['user_id']))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/mark_chat_read', methods=['POST'])
def api_mark_chat_read():
    if 'user_id' not in session:
        return jsonify({'ok': False}), 401
    data = request.get_json(silent=True) or {}
    max_id = int(data.get('max_id', 0))
    if max_id > session.get('chat_last_read_id', 0):
        session['chat_last_read_id'] = max_id
        session.modified = True
    return jsonify({'ok': True, 'last_read_id': session['chat_last_read_id']})

@app.route('/api/edit_message/<int:msg_id>', methods=['POST'])
def api_edit_message(msg_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    data = request.get_json(silent=True) or {}
    new_text = (data.get('text') or '').strip()
    if not new_text:
        return jsonify({'error': 'ข้อความว่างเปล่า'}), 400
    conn = get_db()
    conn.execute(
        "UPDATE social_shares SET share_text=%s WHERE id=%s AND user_id=%s",
        (new_text, msg_id, session['user_id'])
    )
    conn.commit()
    conn.close()
    return jsonify({'success': True})

# ====================================================
# Admin Routes - Questions
# ====================================================
@app.route('/admin/save_questions', methods=['POST'])
def admin_save_questions():
    if session.get('role') not in ['admin', 'researcher']: 
        return "Access Denied", 403
    conn = get_db()
    for dq in DEFAULT_QUESTIONS:
        n = dq['q_number']
        text = request.form.get(f'q_{n}', '').strip()
        if not text:
            text = dq['q_text']
        conn.execute('''
            INSERT INTO custom_questions (q_number, dimension, q_text, updated_at)
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT(q_number) DO UPDATE SET q_text=excluded.q_text, updated_at=CURRENT_TIMESTAMP
        ''', (n, dq['dimension'], text))
    conn.commit()
    conn.close()
    return _redirect_with_token('admin_panel', tab='questions', saved='1')

@app.route('/admin/reset_questions', methods=['POST'])
def admin_reset_questions():
    if session.get('role') not in ['admin', 'researcher']: 
        return "Access Denied", 403
    conn = get_db()
    conn.execute("DELETE FROM custom_questions")
    conn.commit()
    conn.close()
    return _redirect_with_token('admin_panel', tab='questions', saved='reset')

@app.route('/admin/update_user_stats', methods=['POST'])
def update_user_stats():
    if session.get('role') not in ['admin', 'researcher']: 
        return "Access Denied", 403
    user_id = request.form.get('user_id')
    conn = get_db()
    if request.form.get('epigenetic_age'): 
        conn.execute("UPDATE user_health_stats SET epigenetic_age=%s WHERE user_id=%s", 
                    (request.form.get('epigenetic_age'), user_id))
    if request.form.get('fitness_score'): 
        conn.execute("UPDATE user_health_stats SET fitness_score=%s WHERE user_id=%s", 
                    (request.form.get('fitness_score'), user_id))
    if request.form.get('biological_age'): 
        conn.execute("UPDATE user_health_stats SET biological_age=%s WHERE user_id=%s", 
                    (request.form.get('biological_age'), user_id))
    if request.form.get('age_acceleration'): 
        conn.execute("UPDATE user_health_stats SET age_acceleration=%s WHERE user_id=%s", 
                    (request.form.get('age_acceleration'), user_id))
    conn.commit()
    conn.close()
    return _redirect_with_token('admin_panel')

def _calc_dim_scores(answers):
    """Calculate per-dimension % scores from answers dict {q1:..q30:}."""
    DIM_RANGES = {1: range(1,7), 2: range(7,13), 3: range(13,19), 4: range(19,25), 5: range(25,31)}
    result = {}
    total_got = total_max = 0
    for dim, qs in DIM_RANGES.items():
        got = max_ = 0
        for n in qs:
            val = answers.get(f'q{n}')
            if val is not None:
                try:
                    got += int(val)
                    max_ += 5
                except (ValueError, TypeError):
                    pass
        result[dim] = {
            'score': got,
            'max': max_,
            'percent': round(got / max_ * 100) if max_ else 0
        }
        total_got += got
        total_max += max_
    result['total'] = {
        'score': total_got,
        'max': total_max,
        'percent': round(total_got / total_max * 100) if total_max else 0
    }
    return result

@app.route('/admin/view_user/<int:uid>')
def admin_view_user(uid):
    if session.get('role') not in ['admin', 'researcher']: 
        return "Access Denied", 403
    conn = get_db()
    target_user = conn.execute("SELECT name, email FROM users WHERE id=%s", (uid,)).fetchone()
    raw_surveys = conn.execute("SELECT * FROM questionnaires WHERE user_id=%s ORDER BY created_at ASC", (uid,)).fetchall()
    surveys = []
    pre_scores = post_scores = None
    for s in raw_surveys:
        answers = json.loads(s['answers_json']) if s['answers_json'] else {}
        clean = {k: v for k, v in answers.items() if k.startswith('q') and k[1:].isdigit()}
        dim_scores = _calc_dim_scores(clean)
        entry = dict(s, answers=answers, dim_scores=dim_scores)
        surveys.append(entry)
        if s['type'] == 'pre' and pre_scores is None:
            pre_scores = dim_scores
        elif s['type'] == 'post' and post_scores is None:
            post_scores = dim_scores
    surveys.reverse()
    exercises = conn.execute("SELECT * FROM exercises WHERE user_id=%s ORDER BY date DESC LIMIT 30", (uid,)).fetchall()
    logs = conn.execute("SELECT * FROM daily_logs WHERE user_id=%s ORDER BY date DESC LIMIT 30", (uid,)).fetchall()
    health_stats = conn.execute("SELECT * FROM user_health_stats WHERE user_id=%s", (uid,)).fetchone()
    challenges_raw = conn.execute(
        "SELECT type, date FROM challenges WHERE user_id=%s ORDER BY date DESC", (uid,)
    ).fetchall()
    points_row = conn.execute("SELECT points FROM user_points WHERE user_id=%s", (uid,)).fetchone()
    conn.close()
    alcohol_days = sorted(set(r['date'] for r in challenges_raw if r['type'] == 'alcohol'), reverse=True)
    challenge_summary = {}
    for r in challenges_raw:
        challenge_summary[r['type']] = challenge_summary.get(r['type'], 0) + 1
    dim_meta = DIMENSION_META
    return render_template('admin_user_detail.html', target_user=target_user, surveys=surveys,
                          exercises=exercises, logs=logs, health_stats=health_stats,
                          alcohol_days=alcohol_days, challenge_summary=challenge_summary,
                          total_points=points_row['points'] if points_row else 0,
                          pre_scores=pre_scores, post_scores=post_scores,
                          dim_meta=dim_meta)

@app.route('/admin/add_material', methods=['POST'])
def add_material():
    if session.get('role') not in ['admin', 'researcher']: 
        return "Access Denied", 403
    conn = get_db()
    conn.execute("INSERT INTO learning_materials (title, type, url, category) VALUES (%s, 'link', %s, %s)", 
                 (request.form.get('title'), request.form.get('url'), request.form.get('category', 'ทั่วไป')))
    conn.commit()
    conn.close()
    return _redirect_with_token('admin_panel')

@app.route('/admin/unlock_posttest', methods=['POST'])
def admin_unlock_posttest():
    if session.get('role') not in ['admin', 'researcher']: 
        return "Access Denied", 403
    user_id = request.form.get('user_id')
    conn = get_db()
    conn.execute("""
        INSERT INTO post_test_status (user_id, is_unlocked) VALUES (%s, 1)
        ON CONFLICT (user_id) DO UPDATE SET is_unlocked=1
    """, (user_id,))
    conn.commit()
    conn.close()
    return _redirect_with_token('admin_panel')

@app.route('/admin/toggle_posttest', methods=['POST'])
def admin_toggle_posttest():
    if session.get('role') not in ['admin', 'researcher']: 
        return jsonify({'success': False}), 403
    user_id = request.form.get('user_id')
    conn = get_db()
    cur = conn.execute("SELECT is_unlocked FROM post_test_status WHERE user_id=%s", (user_id,)).fetchone()
    new_val = 0 if (cur and cur['is_unlocked'] == 1) else 1
    conn.execute("""
        INSERT INTO post_test_status (user_id, is_unlocked) VALUES (%s, %s)
        ON CONFLICT (user_id) DO UPDATE SET is_unlocked=%s
    """, (user_id, new_val, new_val))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'is_unlocked': new_val})

@app.route('/api/admin/posttest_answers/<int:user_id>')
def admin_posttest_answers(user_id):
    if session.get('role') not in ['admin', 'researcher']: 
        return jsonify({'success': False}), 403
    conn = get_db()
    row = conn.execute(
        "SELECT answers_json, age, bmi, waist, created_at FROM questionnaires WHERE user_id=%s AND type='post' ORDER BY id DESC LIMIT 1",
        (user_id,)
    ).fetchone()
    pre_row = conn.execute(
        "SELECT answers_json, age, bmi, waist, created_at FROM questionnaires WHERE user_id=%s AND type='pre' ORDER BY id DESC LIMIT 1",
        (user_id,)
    ).fetchone()
    conn.close()
    result = {}
    if row:
        try:
            result['post'] = {'answers': json.loads(row['answers_json'] or '{}'), 'age': row['age'], 
                            'bmi': row['bmi'], 'waist': row['waist'], 'created_at': str(row['created_at'] or '')}
        except:
            result['post'] = {}
    if pre_row:
        try:
            result['pre'] = {'answers': json.loads(pre_row['answers_json'] or '{}'), 'age': pre_row['age'], 
                           'bmi': pre_row['bmi'], 'waist': pre_row['waist'], 'created_at': str(pre_row['created_at'] or '')}
        except:
            result['pre'] = {}
    return jsonify({'success': True, 'data': result})

@app.route('/admin/delete_user', methods=['POST'])
def admin_delete_user():
    if session.get('role') not in ['admin', 'researcher']: 
        return "Access Denied", 403
    user_id = request.form.get('user_id')
    conn = get_db()
    for table in ['questionnaires', 'exercises', 'daily_logs', 'challenges', 'notifications', 'user_points', 'user_health_stats', 'post_test_status']:
        conn.execute(f"DELETE FROM {table} WHERE user_id=%s", (user_id,))
    conn.execute("DELETE FROM users WHERE id=%s", (user_id,))
    conn.commit()
    conn.close()
    return _redirect_with_token('admin_panel')

# ====================================================
# Education & Health Routes
# ====================================================
@app.route('/education')
def education():
    if 'user_id' not in session: 
        return redirect(url_for('login'))
    conn = get_db()
    materials = conn.execute("SELECT * FROM learning_materials ORDER BY id DESC").fetchall()
    conn.close()
    return render_template('education.html', materials=materials, 
                          yt_videos=get_youtube_education_videos("สุขภาพ ชะลอวัย", 4), 
                          role=session.get('role'))

@app.route('/health')
def health():
    if 'user_id' not in session: 
        return redirect(url_for('login'))
    user_id = session['user_id']
    today = datetime.now().strftime('%Y-%m-%d')
    conn = get_db()
    checkins_rows = conn.execute("SELECT type FROM challenges WHERE user_id=%s AND date=%s", (user_id, today)).fetchall()
    checkins = {row['type'] for row in checkins_rows}
    points_row = conn.execute("SELECT points FROM user_points WHERE user_id=%s", (user_id,)).fetchone()
    points = points_row['points'] if points_row else 0
    log = conn.execute("SELECT sleep_hours FROM daily_logs WHERE user_id=%s AND date=%s", (user_id, today)).fetchone()
    ex = conn.execute("SELECT SUM(duration) as total FROM exercises WHERE user_id=%s AND date=%s", (user_id, today)).fetchone()
    conn.close()
    today_sleep = log['sleep_hours'] if log else 0
    today_exercise = ex['total'] if ex and ex['total'] else 0
    return render_template('health.html', checkins=checkins, points=points, 
                          today_sleep=today_sleep, today_exercise=today_exercise)

@app.route('/exercise')
def exercise():
    if 'user_id' not in session: 
        return redirect(url_for('login'))
    return render_template('exercise.html')

# ====================================================
# Exercise & Daily Log APIs
# ====================================================
@app.route('/api/save_exercise', methods=['POST'])
def api_save_exercise():
    if 'user_id' not in session: 
        return jsonify({"error": "Unauthorized"}), 401
    data = request.json
    user_id = session['user_id']
    today = datetime.now().strftime('%Y-%m-%d')
    ex_type = data.get('type', 'cardio')
    duration = int(data.get('duration', 0))
    steps = int(data.get('steps', 0))
    distance = float(data.get('distance', 0))
    calories = int(data.get('calories', 0))
    sleep_hours = float(data.get('sleep_hours', 0) or 0)
    heart_rate = int(data.get('heart_rate', 0) or 0)
    spo2 = int(data.get('spo2', 0) or 0)
    conn = get_db()
    conn.execute("""
        INSERT INTO exercises (user_id, type, duration, steps, distance, calories, date) 
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (user_id, ex_type, duration, steps, distance, calories, today))
    conn.execute("UPDATE user_points SET points = points + 10 WHERE user_id=%s", (user_id,))
    # บันทึกการนอน / heart_rate / spo2 จาก smartwatch ลง daily_logs
    if sleep_hours > 0 or heart_rate > 0 or spo2 > 0:
        existing_log = conn.execute(
            "SELECT id FROM daily_logs WHERE user_id=%s AND date=%s", (user_id, today)
        ).fetchone()
        if existing_log:
            if sleep_hours > 0:
                conn.execute(
                    "UPDATE daily_logs SET sleep_hours=%s WHERE user_id=%s AND date=%s",
                    (sleep_hours, user_id, today)
                )
        else:
            conn.execute(
                "INSERT INTO daily_logs (user_id, date, sleep_hours) VALUES (%s, %s, %s)",
                (user_id, today, sleep_hours if sleep_hours > 0 else None)
            )
        if sleep_hours >= 6:
            existing_checkin = conn.execute(
                "SELECT id FROM challenges WHERE user_id=%s AND type='sleep' AND date=%s",
                (user_id, today)
            ).fetchone()
            if not existing_checkin:
                conn.execute(
                    "INSERT INTO challenges (user_id, type, date) VALUES (%s, %s, %s)",
                    (user_id, 'sleep', today)
                )
                conn.execute("UPDATE user_points SET points = points + 10 WHERE user_id=%s", (user_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route('/api/exercise_history')
def api_exercise_history():
    if 'user_id' not in session: 
        return jsonify([])
    conn = get_db()
    rows = conn.execute("""
        SELECT type, duration, steps, distance, calories, date 
        FROM exercises WHERE user_id=%s ORDER BY date DESC LIMIT 30
    """, (session['user_id'],)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/checkin', methods=['POST'])
def api_checkin():
    if 'user_id' not in session: 
        return jsonify({"error": "Unauthorized"}), 401
    user_id = session['user_id']
    today = datetime.now().strftime('%Y-%m-%d')
    task_type = request.json.get('type')
    if not task_type: 
        return jsonify({"error": "Missing type"}), 400
    conn = get_db()
    existing = conn.execute("SELECT id FROM challenges WHERE user_id=%s AND type=%s AND date=%s", 
                           (user_id, task_type, today)).fetchone()
    if existing:
        conn.close()
        return jsonify({"error": "ทำภารกิจนี้ไปแล้วครับ!"}), 400
    conn.execute("INSERT INTO challenges (user_id, type, date) VALUES (%s, %s, %s)", (user_id, task_type, today))
    conn.execute("UPDATE user_points SET points = points + 10 WHERE user_id=%s", (user_id,))
    if task_type == 'water':
        log_exists = conn.execute("SELECT id FROM daily_logs WHERE user_id=%s AND date=%s", (user_id, today)).fetchone()
        if log_exists:
            conn.execute("UPDATE daily_logs SET water_glasses = COALESCE(water_glasses,0) + 1 WHERE user_id=%s AND date=%s", 
                        (user_id, today))
        else:
            conn.execute("INSERT INTO daily_logs (user_id, date, water_glasses) VALUES (%s, %s, 1)", (user_id, today))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route('/api/save_daily_log', methods=['POST'])
def api_save_daily_log():
    if 'user_id' not in session: 
        return jsonify({"error": "Unauthorized"}), 401
    user_id = session['user_id']
    today = datetime.now().strftime('%Y-%m-%d')
    data = request.json
    sleep_hours = data.get('sleep')
    conn = get_db()
    existing = conn.execute("SELECT id FROM daily_logs WHERE user_id=%s AND date=%s", (user_id, today)).fetchone()
    if existing:
        if sleep_hours is not None:
            conn.execute("UPDATE daily_logs SET sleep_hours=%s WHERE user_id=%s AND date=%s", 
                        (sleep_hours, user_id, today))
    else:
        conn.execute("INSERT INTO daily_logs (user_id, date, sleep_hours) VALUES (%s, %s, %s)", 
                    (user_id, today, sleep_hours))
    if sleep_hours:
        existing_checkin = conn.execute("SELECT id FROM challenges WHERE user_id=%s AND type='sleep' AND date=%s", 
                                       (user_id, today)).fetchone()
        if not existing_checkin:
            conn.execute("INSERT INTO challenges (user_id, type, date) VALUES (%s, %s, %s)", (user_id, 'sleep', today))
            conn.execute("UPDATE user_points SET points = points + 10 WHERE user_id=%s", (user_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route('/api/send_nudge', methods=['POST'])
def send_nudge():
    if session.get('role') not in ['admin', 'researcher']: 
        return jsonify({"error": "Unauthorized"}), 403
    user_id = request.json.get('user_id')
    message = request.json.get('message')
    conn = get_db()
    conn.execute("INSERT INTO notifications (user_id, message, type) VALUES (%s, %s, 'admin_nudge')", (user_id, message))
    conn.execute("UPDATE user_points SET points = points + 10 WHERE user_id=%s", (user_id,))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route('/api/get_notifications')
def get_notifications():
    if 'user_id' not in session: 
        return jsonify([])
    conn = get_db()
    noti = conn.execute("SELECT id, message, type FROM notifications WHERE user_id=%s AND is_read=0", 
                       (session['user_id'],)).fetchall()
    if noti:
        conn.execute("UPDATE notifications SET is_read=1 WHERE user_id=%s", (session['user_id'],))
        conn.commit()
    conn.close()
    return jsonify([dict(n) for n in noti])

# ====================================================
# Food Analysis APIs
# ====================================================
@app.route('/api/analyze_food', methods=['POST'])
def analyze_food():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'กรุณาเข้าสู่ระบบ'}), 401
    data = request.get_json(silent=True) or {}
    query = (data.get('query') or '').strip()
    if not query:
        return jsonify({'success': False, 'error': 'กรุณาพิมพ์ชื่ออาหารหรือวัตถุดิบ'}), 400

    if THAI_FOOD_DB:
        matched = None
        for key, val in THAI_FOOD_DB.items():
            if query.lower() in key.lower() or key.lower() in query.lower():
                matched = (key, val)
                break
        if matched:
            key, val = matched
            nut = val.get('nutrition', {})
            return jsonify({
                'success': True,
                'source': 'local_db',
                'query_th': query,
                'query_en': key,
                'items': [key],
                'nutrition': {
                    'calories':        round(nut.get('calories', 0), 1),
                    'protein_g':       round(nut.get('protein_g', 0), 1),
                    'carbohydrates_g': round(nut.get('carbohydrates_g', 0), 1),
                    'fat_g':           round(nut.get('fat_g', 0), 1),
                    'sodium_mg':       round(nut.get('sodium_mg', 0), 0),
                    'fiber_g':         round(nut.get('fiber_g', 0), 1),
                    'sugar_g':         round(nut.get('sugar_g', 0), 1),
                }
            })

    ninjas_key = os.environ.get('API_NINJAS_KEY', '')
    if ninjas_key:
        try:
            from deep_translator import GoogleTranslator
            eng_query = GoogleTranslator(source='th', target='en').translate(query)
        except Exception:
            eng_query = query
        try:
            r = requests.get(
                f'https://api.api-ninjas.com/v1/nutrition?query={eng_query}',
                headers={'X-Api-Key': ninjas_key},
                timeout=8
            )
            if r.status_code == 200:
                items = r.json()
                if items:
                    total_cal  = round(sum(i.get('calories', 0) for i in items), 1)
                    total_pro  = round(sum(i.get('protein_g', 0) for i in items), 1)
                    total_carb = round(sum(i.get('carbohydrates_total_g', 0) for i in items), 1)
                    total_fat  = round(sum(i.get('fat_total_g', 0) for i in items), 1)
                    total_sod  = round(sum(i.get('sodium_mg', 0) for i in items), 0)
                    total_fib  = round(sum(i.get('fiber_g', 0) for i in items), 1)
                    total_sug  = round(sum(i.get('sugar_g', 0) for i in items), 1)
                    return jsonify({
                        'success': True,
                        'source': 'api_ninjas',
                        'query_th': query,
                        'query_en': eng_query,
                        'items': [i.get('name', '') for i in items],
                        'nutrition': {
                            'calories': total_cal, 'protein_g': total_pro,
                            'carbohydrates_g': total_carb, 'fat_g': total_fat,
                            'sodium_mg': total_sod, 'fiber_g': total_fib,
                            'sugar_g': total_sug
                        }
                    })
        except Exception:
            pass

    openrouter_key = os.environ.get('OPENROUTER_API_KEY', '')
    if not openrouter_key:
        return jsonify({'success': False, 'error': 'ไม่พบข้อมูลโภชนาการในฐานข้อมูล กรุณาตั้งค่า OPENROUTER_API_KEY'}), 404

    ai_prompt = (
        f"คุณคือนักโภชนาการผู้เชี่ยวชาญ ประมาณคุณค่าทางโภชนาการของอาหารต่อไปนี้ (1 หน่วยบริโภคมาตรฐาน):\n"
        f"อาหาร: {query}\n\n"
        "ตอบเป็น JSON เท่านั้น ห้ามมีข้อความนอก JSON:\n"
        '{"food_name_th":"ชื่ออาหาร","food_name_en":"English name",'
        '"portion":"ปริมาณ 1 หน่วยบริโภค เช่น 1 ถ้วย (200 ก.)",'
        '"nutrition":{"calories":0,"protein_g":0,"carbohydrates_g":0,"fat_g":0,"fiber_g":0,"sugar_g":0,"sodium_mg":0},'
        '"tip":"ข้อมูลเพิ่มเติมสั้นๆ เกี่ยวกับคุณค่าทางโภชนาการ",'
        '"source":"AI estimated"}'
    )
    try:
        raw = ask_ai(ai_prompt)
        if not raw:
            return jsonify({'success': False, 'error': 'AI ไม่สามารถประมาณโภชนาการได้ กรุณาลองใหม่'}), 500

        json_match = re.search(r'\{.*\}', raw, re.DOTALL)
        if not json_match:
            return jsonify({'success': False, 'error': 'ไม่สามารถอ่านข้อมูลจาก AI ได้'}), 500
        ai_data = json.loads(json_match.group())
        nut = ai_data.get('nutrition', {})
        return jsonify({
            'success': True,
            'source': 'ai_estimated',
            'query_th': query,
            'query_en': ai_data.get('food_name_en', query),
            'portion': ai_data.get('portion', ''),
            'items': [ai_data.get('food_name_th', query)],
            'tip': ai_data.get('tip', ''),
            'nutrition': {
                'calories':        round(float(nut.get('calories', 0)), 1),
                'protein_g':       round(float(nut.get('protein_g', 0)), 1),
                'carbohydrates_g': round(float(nut.get('carbohydrates_g', 0)), 1),
                'fat_g':           round(float(nut.get('fat_g', 0)), 1),
                'sodium_mg':       round(float(nut.get('sodium_mg', 0)), 0),
                'fiber_g':         round(float(nut.get('fiber_g', 0)), 1),
                'sugar_g':         round(float(nut.get('sugar_g', 0)), 1),
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'error': f'AI error: {str(e)}'}), 500

@app.route('/api/scan_food_image', methods=['POST'])
def scan_food_image():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'กรุณาเข้าสู่ระบบ'}), 401

    data = request.get_json(silent=True) or {}
    image_b64 = data.get('image_b64', '').strip()
    if not image_b64:
        return jsonify({'success': False, 'error': 'ไม่พบข้อมูลภาพ'}), 400

    api_key = os.environ.get('OPENROUTER_API_KEY', '')
    if not api_key:
        return jsonify({'success': False, 'error': 'ยังไม่ได้ตั้งค่า OPENROUTER_API_KEY'}), 500

    vision_models = [
        ("google/gemma-4-31b-it:free",              800),
        ("google/gemma-3-27b-it:free",              800),
        ("nvidia/nemotron-nano-12b-v2-vl:free",     800),
        ("google/gemma-3-12b-it:free",              800),
        ("google/gemma-4-26b-a4b-it:free",          800),
    ]

    prompt = (
        "วิเคราะห์อาหารในภาพและตอบเป็นภาษาไทย ตอบด้วย JSON เท่านั้น ห้ามมีข้อความอื่น ห้ามใส่ ```json ```:\n"
        '{"food_name_th":"ชื่ออาหารภาษาไทย","food_name_en":"Food name in English",'
        '"portion":"ปริมาณโดยประมาณ (เช่น 1 จาน 300 ก.)",'
        '"nutrition":{"calories":0,"protein_g":0,"carbohydrates_g":0,"fat_g":0,"fiber_g":0,"sugar_g":0,"sodium_mg":0},'
        '"tip":"คำแนะนำสั้นๆ เกี่ยวกับคุณค่าทางโภชนาการ"}'
    )

    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
        'HTTP-Referer': 'https://mhealth-antaging.replit.app',
        'X-Title': 'mHealth Anti-Aging Food Scanner',
    }

    def clean_json(raw: str) -> dict:
        raw = raw.strip()
        if raw.startswith('```'):
            parts = raw.split('```')
            for part in parts:
                part = part.strip()
                if part.startswith('json'):
                    part = part[4:].strip()
                if part.startswith('{'):
                    raw = part
                    break
        start = raw.find('{')
        end = raw.rfind('}')
        if start != -1 and end != -1:
            raw = raw[start:end+1]
        return json.loads(raw)

    last_error = 'ไม่สามารถวิเคราะห์ภาพได้'
    for model, max_tok in vision_models:
        try:
            payload = {
                'model': model,
                'messages': [{
                    'role': 'user',
                    'content': [
                        {'type': 'text', 'text': prompt},
                        {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{image_b64}'}}
                    ]
                }],
                'max_tokens': max_tok,
            }
            r = requests.post(
                'https://openrouter.ai/api/v1/chat/completions',
                headers=headers,
                json=payload,
                timeout=45,
            )
            if r.status_code != 200:
                last_error = f'{model}: HTTP {r.status_code} — {r.text[:200]}'
                print(f'[SCAN] {last_error}')
                continue

            resp_json = r.json()
            content = resp_json['choices'][0]['message'].get('content')
            if not content:
                last_error = f'{model}: empty content'
                continue

            result = clean_json(content)
            return jsonify({'success': True, 'data': result, 'model': model})

        except Exception as e:
            last_error = f'{model}: {str(e)}'
            continue

    return jsonify({'success': False, 'error': last_error}), 500

@app.route('/food')
def food():
    if 'user_id' not in session: 
        return redirect(url_for('login'))
    return render_template('food.html')

@app.route('/history')
def history():
    if 'user_id' not in session: 
        return redirect(url_for('login'))
    conn = get_db()
    exercises = conn.execute("SELECT * FROM exercises WHERE user_id=%s ORDER BY date DESC LIMIT 30", 
                            (session['user_id'],)).fetchall()
    logs = conn.execute("SELECT * FROM daily_logs WHERE user_id=%s ORDER BY date DESC LIMIT 30", 
                       (session['user_id'],)).fetchall()
    conn.close()
    return render_template('history.html', exercises=exercises, logs=logs)

# ====================================================
# Admin Panel Main Route
# ====================================================
@app.route('/admin')
def admin_panel():
    if session.get('role') not in ['admin', 'researcher']:
        return redirect(url_for('login'))

    conn = get_db()
    today = datetime.now().date()
    seven_days_ago = (today - timedelta(days=7)).isoformat()

    def _count(row):
        return (row[0] if row else 0)

    # ── 1. Stats (combined into as few simple queries as possible) ──
    total_users = _count(conn.execute("SELECT COUNT(*) FROM users WHERE role='user'").fetchone())
    pre_test_count = _count(conn.execute("SELECT COUNT(DISTINCT user_id) FROM questionnaires WHERE type='pre'").fetchone())
    post_test_count = _count(conn.execute("SELECT COUNT(DISTINCT user_id) FROM questionnaires WHERE type='post'").fetchone())
    total_materials = _count(conn.execute("SELECT COUNT(*) FROM learning_materials").fetchone())

    # active users: pull distinct user_ids from both tables, merge in Python
    active_log_ids = set(r['user_id'] for r in conn.execute(
        "SELECT DISTINCT user_id FROM daily_logs WHERE date >= %s", (seven_days_ago,)).fetchall())
    active_ex_ids = set(r['user_id'] for r in conn.execute(
        "SELECT DISTINCT user_id FROM exercises WHERE date >= %s", (seven_days_ago,)).fetchall())
    active_users = len(active_log_ids | active_ex_ids)

    # ── 2. Users (simple queries, no JOIN, merge in Python) ──────────
    user_rows = conn.execute("SELECT id, name, email FROM users WHERE role='user' ORDER BY id DESC").fetchall()

    # post_test_status: {user_id: is_unlocked}
    pts_map = {}
    for r in conn.execute("SELECT user_id, is_unlocked FROM post_test_status").fetchall():
        pts_map[r['user_id']] = r['is_unlocked']

    # last log date per user: {user_id: date_str}
    last_log_map = {}
    for r in conn.execute("SELECT user_id, MAX(date) as ld FROM daily_logs GROUP BY user_id").fetchall():
        last_log_map[r['user_id']] = r['ld'] or '2000-01-01'

    # last exercise date per user: {user_id: date_str}
    last_ex_map = {}
    for r in conn.execute("SELECT user_id, MAX(date) as ld FROM exercises GROUP BY user_id").fetchall():
        last_ex_map[r['user_id']] = r['ld'] or '2000-01-01'

    # questionnaire answers
    user_surveys = {}
    for s in conn.execute("SELECT user_id, answers_json FROM questionnaires WHERE type='pre' ORDER BY id DESC").fetchall():
        if s['user_id'] not in user_surveys and s['answers_json']:
            try:
                user_surveys[s['user_id']] = json.loads(s['answers_json'])
            except:
                pass

    post_test_done_ids = set(r['user_id'] for r in conn.execute(
        "SELECT DISTINCT user_id FROM questionnaires WHERE type='post'").fetchall())

    # Build users list in Python (no JOIN needed)
    users = []
    for r in user_rows:
        uid = r['id']
        last_log = last_log_map.get(uid, '2000-01-01')
        last_ex = last_ex_map.get(uid, '2000-01-01')
        last_date_str = max(last_log, last_ex)
        if last_date_str == '2000-01-01':
            days_absent = 999
        else:
            try:
                days_absent = (today - datetime.strptime(last_date_str, '%Y-%m-%d').date()).days
            except:
                days_absent = 999
        users.append({
            'id': uid, 'name': r['name'] or 'ผู้ใช้ไม่ระบุชื่อ', 'email': r['email'] or '-',
            'is_unlocked': pts_map.get(uid, 0), 'days_absent': days_absent,
            'latest_answers': user_surveys.get(uid, {}),
            'post_test_done': uid in post_test_done_ids
        })

    # ── 3. Materials, settings, questions ────────────────────────────
    materials = conn.execute("SELECT * FROM learning_materials ORDER BY id DESC").fetchall()
    rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    settings = {row['key']: row['value'] for row in rows}
    questions, by_dim = get_questions_for_template(conn)

    # ── 4. Leaderboard (no JOIN, merge user_points in Python) ─────────
    points_map = {}
    for r in conn.execute("SELECT user_id, points FROM user_points").fetchall():
        points_map[r['user_id']] = r['points']
    leaderboard_unsorted = [
        {'id': u['id'], 'name': u['name'], 'email': u['email'],
         'points': points_map.get(u['id'], 0)} for u in users
    ]
    leaderboard = [{'rank': i+1, **u} for i, u in
                   enumerate(sorted(leaderboard_unsorted, key=lambda x: (-x['points'], x['id'])))]

    conn.close()

    saved = request.args.get('saved')
    active_tab = request.args.get('tab', 'overview')
    if saved == 'branding': 
        flash_message = 'บันทึกการตั้งค่าสำเร็จแล้ว'
    elif saved == '1': 
        flash_message = 'บันทึกคำถามสำเร็จแล้ว'
    elif saved == 'reset': 
        flash_message = 'รีเซ็ตคำถามเป็นค่าเริ่มต้นแล้ว'
    else: 
        flash_message = None

    return render_template('admin_panel.html',
        stats={'total_users': total_users, 'active_users': active_users,
               'pre_test_count': pre_test_count, 'post_test_count': post_test_count,
               'total_materials': total_materials},
        users=users,
        materials=materials,
        settings=settings,
        questions=questions,
        by_dim=by_dim,
        dim_meta=DIMENSION_META,
        leaderboard=leaderboard,
        session_name=session.get('name', 'Admin'),
        session_role=session.get('role', 'admin'),
        now=datetime.now().strftime('%A, %d %B %Y'),
        flash_message=flash_message,
        active_tab=active_tab
    )

@app.route('/admin/save_branding', methods=['POST'])
def admin_save_branding():
    if session.get('role') not in ['admin', 'researcher']:
        return "Access Denied", 403
    conn = get_db()
    app_name = request.form.get('app_name', '').strip()
    font_name = request.form.get('font_name', 'Prompt')
    if app_name:
        conn.execute("""
            INSERT INTO app_settings (key, value) VALUES ('app_name', %s)
            ON CONFLICT (key) DO UPDATE SET value=%s
        """, (app_name, app_name))
    conn.execute("""
        INSERT INTO app_settings (key, value) VALUES ('font_name', %s)
        ON CONFLICT (key) DO UPDATE SET value=%s
    """, (font_name, font_name))
    logo = request.files.get('logo')
    if logo and logo.filename and allowed_logo_file(logo.filename):
        filename = secure_filename(logo.filename)
        logo_bytes = logo.read()
        upload_to_storage('social-files', f'logo/{filename}', logo_bytes, logo.content_type or 'image/png')
        logo_url = storage_url('social-files', f'logo/{filename}')
        if not logo_url or not logo_url.startswith('http'):
            local_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            logo_url = '/' + local_path.replace('\\', '/')
        conn.execute("""
            INSERT INTO app_settings (key, value) VALUES ('logo_path', %s)
            ON CONFLICT (key) DO UPDATE SET value=%s
        """, (logo_url, logo_url))
    conn.commit()
    conn.close()
    return _redirect_with_token('admin_panel', saved='branding', tab='branding')

@app.route('/admin/delete_material', methods=['POST'])
def admin_delete_material():
    if session.get('role') not in ['admin', 'researcher']:
        return "Access Denied", 403
    material_id = request.form.get('material_id')
    conn = get_db()
    conn.execute("DELETE FROM learning_materials WHERE id=%s", (material_id,))
    conn.commit()
    conn.close()
    return _redirect_with_token('admin_panel')

# ====================================================
# Presence / Heartbeat APIs
# ====================================================
@app.route('/api/heartbeat', methods=['POST'])
def api_heartbeat():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'ok': False}), 401
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn = get_db()
    conn.execute("UPDATE users SET last_seen=%s WHERE id=%s", (now_str, user_id))
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'ts': now_str})

@app.route('/api/user_presence')
def api_user_presence():
    if session.get('role') not in ['admin', 'researcher']:
        return jsonify({'error': 'Access Denied'}), 403
    conn = get_db()
    users = conn.execute(
        "SELECT id, name, email, last_seen FROM users WHERE role='user' ORDER BY CASE WHEN last_seen IS NULL THEN 1 ELSE 0 END, last_seen DESC"
    ).fetchall()
    conn.close()
    now = datetime.now()
    result = []
    for u in users:
        last_seen_unix = None
        if u['last_seen']:
            try:
                raw = str(u['last_seen']).strip().replace('T', ' ').split('+')[0].split('.')[0]
                ls = datetime.strptime(raw, '%Y-%m-%d %H:%M:%S')
                diff_sec = (now - ls).total_seconds()
                last_seen_unix = int(ls.timestamp())
                if diff_sec < 45:
                    status = 'online'
                    label = 'ออนไลน์'
                elif diff_sec < 3600:
                    m = int(diff_sec // 60)
                    status = 'away' if diff_sec < 600 else 'idle'
                    label = f'{m} นาทีที่แล้ว' if m > 0 else 'เพิ่งออกไป'
                elif diff_sec < 86400:
                    status = 'offline'
                    label = f'{int(diff_sec//3600)} ชั่วโมงที่แล้ว'
                else:
                    status = 'offline'
                    label = f'{int(diff_sec//86400)} วันที่แล้ว'
            except:
                status = 'offline'
                label = 'ไม่ทราบ'
        else:
            status = 'offline'
            label = 'ยังไม่เคยเข้าใช้'
        result.append({
            'id': u['id'],
            'name': u['name'],
            'email': u['email'],
            'status': status,
            'label': label,
            'last_seen': u['last_seen'],
            'last_seen_unix': last_seen_unix
        })
    return jsonify(result)

# ====================================================
# Lab Results Routes
# ====================================================
LAB_FOLDER = 'static/uploads/lab_results'
os.makedirs(LAB_FOLDER, exist_ok=True)

ALLOWED_LAB_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'heic', 'webp'}

def allowed_lab_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_LAB_EXTENSIONS

@app.route('/admin/upload_lab', methods=['POST'])
def admin_upload_lab():
    if session.get('role') not in ['admin', 'researcher']:
        return jsonify({'success': False, 'error': 'Access Denied'}), 403
    user_id = request.form.get('user_id')
    notes = request.form.get('notes', '').strip()
    lab_type = request.form.get('lab_type', 'other')
    period = request.form.get('period', 'pre')
    if lab_type not in ('epigenetic', 'inbody', 'other'):
        lab_type = 'other'
    if period not in ('pre', 'post'):
        period = 'pre'
    file = request.files.get('lab_file')
    numeric_value = request.form.get('numeric_value', '').strip()
    has_file = file and file.filename and file.filename != ''

    if not user_id:
        return jsonify({'success': False, 'error': 'ข้อมูลไม่ครบ'}), 400
    if not has_file and not numeric_value:
        return jsonify({'success': False, 'error': 'กรุณากรอกค่าตัวเลข หรือแนบไฟล์อย่างน้อย 1 อย่าง'}), 400
    if has_file and not allowed_lab_file(file.filename):
        return jsonify({'success': False, 'error': 'ไฟล์ไม่รองรับ (PDF, PNG, JPG)'}), 400

    conn = get_db()
    safe_name = None

    if has_file:
        ext = file.filename.rsplit('.', 1)[1].lower()
        safe_name = f"lab_{user_id}_{int(datetime.now().timestamp())}.{ext}"
        file_bytes = file.read()
        upload_to_storage('lab-results', safe_name, file_bytes, file.content_type or 'application/octet-stream')
        conn.execute(
            "INSERT INTO lab_results (user_id, filename, original_name, notes, lab_type, period) VALUES (%s, %s, %s, %s, %s, %s)",
            (user_id, safe_name, secure_filename(file.filename), notes, lab_type, period)
        )
    else:
        conn.execute(
            "INSERT INTO lab_results (user_id, filename, original_name, notes, lab_type, period) VALUES (%s, %s, %s, %s, %s, %s)",
            (user_id, '', f'{lab_type}_value_only', notes, lab_type, period)
        )

    if numeric_value:
        try:
            val = float(numeric_value)
            existing = conn.execute("SELECT id FROM user_health_stats WHERE user_id=%s", (user_id,)).fetchone()
            if lab_type == 'epigenetic':
                if period == 'pre':
                    if existing:
                        conn.execute("UPDATE user_health_stats SET epigenetic_age=%s, epigenetic_age_pre=%s WHERE user_id=%s", (val, val, user_id))
                    else:
                        conn.execute("INSERT INTO user_health_stats (user_id, epigenetic_age, epigenetic_age_pre) VALUES (%s, %s, %s)", (user_id, val, val))
                else:
                    if existing:
                        conn.execute("UPDATE user_health_stats SET epigenetic_age=%s, epigenetic_age_post=%s WHERE user_id=%s", (val, val, user_id))
                    else:
                        conn.execute("INSERT INTO user_health_stats (user_id, epigenetic_age, epigenetic_age_post) VALUES (%s, %s, %s)", (user_id, val, val))
            elif lab_type == 'inbody':
                if period == 'pre':
                    if existing:
                        conn.execute("UPDATE user_health_stats SET fitness_score=%s, fitness_score_pre=%s WHERE user_id=%s", (val, val, user_id))
                    else:
                        conn.execute("INSERT INTO user_health_stats (user_id, fitness_score, fitness_score_pre) VALUES (%s, %s, %s)", (user_id, val, val))
                else:
                    if existing:
                        conn.execute("UPDATE user_health_stats SET fitness_score=%s, fitness_score_post=%s WHERE user_id=%s", (val, val, user_id))
                    else:
                        conn.execute("INSERT INTO user_health_stats (user_id, fitness_score, fitness_score_post) VALUES (%s, %s, %s)", (user_id, val, val))
        except ValueError:
            pass

    conn.commit()
    conn.close()
    url = storage_url('lab-results', safe_name) if safe_name else ''
    return jsonify({'success': True, 'filename': safe_name or '', 'url': url})

@app.route('/admin/delete_lab', methods=['POST'])
def admin_delete_lab():
    if session.get('role') not in ['admin', 'researcher']:
        return jsonify({'success': False}), 403
    lab_id = request.form.get('lab_id')
    conn = get_db()
    row = conn.execute("SELECT filename FROM lab_results WHERE id=%s", (lab_id,)).fetchone()
    if row:
        if row['filename']:
            delete_from_storage('lab-results', row['filename'])
            try:
                os.remove(os.path.join(LAB_FOLDER, row['filename']))
            except Exception:
                pass
        conn.execute("DELETE FROM lab_results WHERE id=%s", (lab_id,))
        conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/admin/get_labs/<int:user_id>')
def admin_get_labs(user_id):
    if session.get('role') not in ['admin', 'researcher']:
        return jsonify({'error': 'Access Denied'}), 403
    conn = get_db()
    labs = conn.execute(
        "SELECT id, filename, original_name, notes, lab_type, period, uploaded_at FROM lab_results WHERE user_id=%s ORDER BY uploaded_at DESC",
        (user_id,)
    ).fetchall()
    conn.close()
    return jsonify([{
        'id': r['id'],
        'filename': r['filename'],
        'original_name': r['original_name'],
        'notes': r['notes'],
        'lab_type': r['lab_type'] or 'other',
        'period': r['period'] or 'pre',
        'uploaded_at': r['uploaded_at'],
        'url': storage_url('lab-results', r['filename']) if r['filename'] else '',
        'value_only': not bool(r['filename'])
    } for r in labs])

# ====================================================
# Certificate Routes
# ====================================================
CERT_FOLDER = 'static/uploads/certificates'
os.makedirs(CERT_FOLDER, exist_ok=True)

ALLOWED_CERT_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'webp'}

def allowed_cert_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_CERT_EXTENSIONS

@app.route('/admin/upload_cert', methods=['POST'])
def admin_upload_cert():
    if session.get('role') not in ['admin', 'researcher']:
        return jsonify({'success': False, 'error': 'Access Denied'}), 403
    user_id = request.form.get('user_id')
    notes = request.form.get('notes', '').strip()
    file = request.files.get('cert_file')
    if not user_id or not file or not file.filename:
        return jsonify({'success': False, 'error': 'ข้อมูลไม่ครบ'}), 400
    if not allowed_cert_file(file.filename):
        return jsonify({'success': False, 'error': 'ไฟล์ไม่รองรับ (PDF, PNG, JPG)'}), 400

    ext = file.filename.rsplit('.', 1)[1].lower()
    safe_name = f"cert_{user_id}_{int(datetime.now().timestamp())}.{ext}"
    file_bytes = file.read()
    upload_to_storage('certificates', safe_name, file_bytes, file.content_type or 'application/octet-stream')
    file_path = os.path.join(CERT_FOLDER, safe_name)
    with open(file_path, 'wb') as fout:
        fout.write(file_bytes)

    conn = get_db()
    conn.execute(
        "INSERT INTO certificates (user_id, filename, original_name, notes) VALUES (%s, %s, %s, %s)",
        (user_id, safe_name, secure_filename(file.filename), notes)
    )
    note_text = f'📜 คุณได้รับเกียรติบัตรใหม่! {notes}' if notes else '📜 คุณได้รับเกียรติบัตรใหม่! แตะเพื่อดูรายละเอียด'
    conn.execute(
        "INSERT INTO notifications (user_id, message, type) VALUES (%s, %s, 'certificate')",
        (user_id, note_text)
    )
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'filename': safe_name, 'url': storage_url('certificates', safe_name)})

@app.route('/admin/delete_cert', methods=['POST'])
def admin_delete_cert():
    if session.get('role') not in ['admin', 'researcher']:
        return jsonify({'success': False}), 403
    cert_id = request.form.get('cert_id')
    conn = get_db()
    row = conn.execute("SELECT filename FROM certificates WHERE id=%s", (cert_id,)).fetchone()
    if row:
        if row['filename']:
            delete_from_storage('certificates', row['filename'])
            try:
                os.remove(os.path.join(CERT_FOLDER, row['filename']))
            except Exception:
                pass
        conn.execute("DELETE FROM certificates WHERE id=%s", (cert_id,))
        conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/admin/get_certs/<int:user_id>')
def admin_get_certs(user_id):
    if session.get('role') not in ['admin', 'researcher']:
        return jsonify({'error': 'Access Denied'}), 403
    conn = get_db()
    certs = conn.execute(
        "SELECT id, filename, original_name, notes, issued_at FROM certificates WHERE user_id=%s ORDER BY issued_at DESC",
        (user_id,)
    ).fetchall()
    conn.close()
    return jsonify([{
        'id': r['id'],
        'filename': r['filename'],
        'original_name': r['original_name'],
        'notes': r['notes'],
        'issued_at': r['issued_at'],
        'url': storage_url('certificates', r['filename'])
    } for r in certs])

@app.route('/my_labs')
def my_labs():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    conn = get_db()
    labs = conn.execute(
        "SELECT id, filename, original_name, notes, lab_type, period, uploaded_at FROM lab_results WHERE user_id=%s ORDER BY uploaded_at DESC",
        (session['user_id'],)
    ).fetchall()
    pts_row = conn.execute("SELECT points FROM user_points WHERE user_id=%s", (session['user_id'],)).fetchone()
    rank_row = conn.execute(
        "SELECT COUNT(*)+1 AS rank FROM user_points WHERE points > (SELECT COALESCE(points,0) FROM user_points WHERE user_id=%s)",
        (session['user_id'],)
    ).fetchone()
    conn.close()
    lab_list = [{
        'id': r['id'],
        'original_name': r['original_name'] or 'ไฟล์ผลแล็บ',
        'notes': r['notes'] or '',
        'lab_type': r['lab_type'] or 'other',
        'period': r['period'] or 'pre',
        'uploaded_at': r['uploaded_at'],
        'url': storage_url('lab-results', r['filename']) if r['filename'] else None,
        'has_file': bool(r['filename'])
    } for r in labs]
    points = pts_row['points'] if pts_row else 0
    my_rank = rank_row['rank'] if rank_row else None
    return render_template('my_labs.html', labs=lab_list, points=points, my_rank=my_rank)

@app.route('/api/my_certificates')
def api_my_certificates():
    if 'user_id' not in session:
        return jsonify([]), 401
    conn = get_db()
    certs = conn.execute(
        "SELECT id, filename, original_name, notes, issued_at FROM certificates WHERE user_id=%s ORDER BY issued_at DESC",
        (session['user_id'],)
    ).fetchall()
    conn.close()
    return jsonify([{
        'id': r['id'],
        'original_name': r['original_name'] or 'เกียรติบัตร',
        'notes': r['notes'] or '',
        'issued_at': str(r['issued_at'])[:10] if r['issued_at'] else '',
        'url': storage_url('certificates', r['filename'])
    } for r in certs])

@app.route('/my_certificates')
def my_certificates():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    conn = get_db()
    certs = conn.execute(
        "SELECT id, filename, original_name, notes, issued_at FROM certificates WHERE user_id=%s ORDER BY issued_at DESC",
        (session['user_id'],)
    ).fetchall()
    pts_row  = conn.execute("SELECT points FROM user_points WHERE user_id=%s", (session['user_id'],)).fetchone()
    rank_row = conn.execute(
        "SELECT COUNT(*)+1 AS rank FROM user_points WHERE points > (SELECT COALESCE(points,0) FROM user_points WHERE user_id=%s)",
        (session['user_id'],)
    ).fetchone()
    conn.close()
    cert_list = [{
        'id': r['id'],
        'original_name': r['original_name'] or 'เกียรติบัตร',
        'notes': r['notes'] or '',
        'issued_at': str(r['issued_at'])[:10] if r['issued_at'] else '',
        'url': storage_url('certificates', r['filename'])
    } for r in certs]
    points  = pts_row['points']  if pts_row  else 0
    my_rank = rank_row['rank']   if rank_row else None
    return render_template('my_certificates.html', certs=cert_list, points=points, my_rank=my_rank)

# ====================================================
# File Serving
# ====================================================
@app.route('/uploads/<path:filename>')
def serve_upload(filename):
    return send_from_directory('static/uploads/social_files', filename)

# ====================================================
# OCR Exercise Route
# ====================================================
@app.route('/api/ocr_exercise', methods=['POST'])
def api_ocr_exercise():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'กรุณาเข้าสู่ระบบ'}), 401

    api_key = os.environ.get('OPENROUTER_API_KEY', '')
    if not api_key:
        return jsonify({'success': False, 'error': 'ยังไม่ได้ตั้งค่า API Key'}), 500

    image_file = request.files.get('image')
    if not image_file:
        return jsonify({'success': False, 'error': 'ไม่พบภาพ'}), 400

    image_b64 = base64.b64encode(image_file.read()).decode('utf-8')

    vision_models = [
        "google/gemma-4-31b-it:free",
        "google/gemma-3-27b-it:free",
        "nvidia/nemotron-nano-12b-v2-vl:free",
    ]

    prompt = (
        "นี่คือภาพหน้าจอจากนาฬิกาอัจฉริยะหรือแอปสุขภาพ (เช่น Apple Watch, Garmin, Fitbit, Samsung Health, Google Fit) "
        "กรุณาอ่านข้อมูลทั้งหมดที่มองเห็นในภาพ แล้วตอบด้วย JSON เท่านั้น:\n"
        '{"duration":null,"steps":null,"distance":null,"calories":null,"sleep_hours":null,"heart_rate":null,"spo2":null}\n'
        "- duration = นาทีที่ออกกำลังกาย (integer)\n"
        "- steps = จำนวนก้าว (integer)\n"
        "- distance = ระยะทางเป็นกิโลเมตร (float)\n"
        "- calories = แคลอรี่ที่เผาผลาญ kcal (integer)\n"
        "- sleep_hours = ชั่วโมงการนอน เช่น '7h 30m' = 7.5 (float)\n"
        "- heart_rate = อัตราการเต้นของหัวใจ bpm (integer)\n"
        "- spo2 = ค่าออกซิเจนในเลือด % (integer)\n"
        "ใส่ null หากไม่พบข้อมูลนั้นในภาพ ตอบด้วย JSON เท่านั้น ห้ามมีข้อความอื่น"
    )

    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
        'HTTP-Referer': 'https://mhealth-antaging.replit.app',
        'X-Title': 'mHealth Anti-Aging OCR Exercise',
    }

    def clean_json(raw):
        raw = raw.strip()
        if '```' in raw:
            parts = raw.split('```')
            for p in parts:
                p = p.strip()
                if p.startswith('json'): 
                    p = p[4:].strip()
                if p.startswith('{'): 
                    raw = p
                    break
        s, e = raw.find('{'), raw.rfind('}')
        if s != -1 and e != -1: 
            raw = raw[s:e+1]
        return json.loads(raw)

    for model in vision_models:
        try:
            payload = {
                'model': model,
                'messages': [{'role': 'user', 'content': [
                    {'type': 'text', 'text': prompt},
                    {'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{image_b64}'}}
                ]}],
                'max_tokens': 300,
            }
            r = requests.post('https://openrouter.ai/api/v1/chat/completions',
                              headers=headers, json=payload, timeout=45)
            if r.status_code != 200: 
                continue
            content = r.json()['choices'][0]['message'].get('content', '')
            if not content: 
                continue
            result = clean_json(content)
            data = {
                'duration': int(result.get('duration') or 0),
                'steps': int(result.get('steps') or 0),
                'distance': float(result.get('distance') or 0),
                'calories': int(result.get('calories') or 0),
                'sleep_hours': float(result.get('sleep_hours') or 0),
                'heart_rate': int(result.get('heart_rate') or 0),
                'spo2': int(result.get('spo2') or 0),
            }
            return jsonify({'success': True, 'data': data, 'model': model})
        except Exception:
            continue

    return jsonify({'success': False, 'error': 'ไม่สามารถอ่านข้อมูลจากภาพได้'}), 500

# ====================================================
# AI Food Generation
# ====================================================
@app.route('/api/generate_food', methods=['POST'])
def api_generate_food():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    api_key = os.environ.get('OPENROUTER_API_KEY', '')
    user_id = session['user_id']

    conn = get_db()
    q = conn.execute("SELECT age, bmi FROM questionnaires WHERE user_id=%s ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
    conn.close()

    age = q['age'] if q and q['age'] else 'ไม่ทราบ'
    bmi = q['bmi'] if q and q['bmi'] else 'ไม่ทราบ'

    now_hour = datetime.now().hour
    if now_hour < 10: 
        meal_time = "มื้อเช้า"
    elif now_hour < 14: 
        meal_time = "มื้อกลางวัน"
    elif now_hour < 17: 
        meal_time = "มื้อว่างบ่าย"
    else: 
        meal_time = "มื้อเย็น"

    prompt = f"""You are an expert longevity nutritionist. Generate a science-backed anti-aging meal for:
- User: age {age}, BMI {bmi}, meal: {meal_time}
- Base on: Blue Zones, Mediterranean, MIND diet, Autophagy, mTOR inhibition, Polyphenols, Omega-3

Reply ONLY with valid JSON (no markdown, no extra text):
{{
  "menu": "ชื่อเมนูภาษาไทย",
  "tagline": "สรุปประโยชน์สั้น 1 บรรทัด เช่น Omega-3 สูง · ลด IL-6",
  "ingredients": ["วัตถุดิบ1", "วัตถุดิบ2", "วัตถุดิบ3", "วัตถุดิบ4"],
  "mechanism": "กลไกชะลอวัยระดับเซลล์ 1-2 ประโยค กระชับ",
  "steps": ["ขั้นที่ 1", "ขั้นที่ 2", "ขั้นที่ 3"],
  "side_dishes": ["เมนูเสริม1", "เมนูเสริม2"],
  "references": [
    "Author et al. Title. Journal Year;Vol:Pages",
    "Author et al. Title. Journal Year;Vol:Pages"
  ]
}}"""

    models = [
        "google/gemma-4-31b-it:free",
        "google/gemma-3-27b-it:free",
        "nvidia/nemotron-nano-12b-v2-vl:free",
    ]

    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
        'HTTP-Referer': 'https://mhealth-antaging.replit.app',
        'X-Title': 'mHealth Longevity Food',
    }

    if not api_key:
        return jsonify({'result': '⚠️ ยังไม่ได้ตั้งค่า API Key กรุณาแจ้งผู้ดูแลระบบ'})

    for model in models:
        try:
            r = requests.post('https://openrouter.ai/api/v1/chat/completions',
                headers=headers,
                json={'model': model, 'messages': [{'role': 'user', 'content': prompt}], 'max_tokens': 600},
                timeout=45)
            if r.status_code == 200:
                content = r.json()['choices'][0]['message'].get('content', '')
                if content:
                    try:
                        clean = content.strip()
                        if clean.startswith('```'):
                            clean = re.sub(r'^```[a-z]*\n?', '', clean)
                            clean = re.sub(r'\n?```$', '', clean)
                        parsed = json.loads(clean.strip())
                        return jsonify({'structured': True, 'data': parsed})
                    except Exception:
                        return jsonify({'structured': False, 'result': content})
        except Exception:
            continue

    return jsonify({'structured': False, 'result': '⚠️ ไม่สามารถสร้างเมนูได้ในขณะนี้ กรุณาลองใหม่อีกครั้ง'})

# ====================================================
# AI Greeting
# ====================================================
@app.route('/api/ai_greeting', methods=['POST'])
def api_ai_greeting():
    if 'user_id' not in session:
        return jsonify({'message': 'มาเริ่มวันใหม่ด้วยกันเถอะ! 💪'}), 401

    api_key = os.environ.get('OPENROUTER_API_KEY', '')
    if not api_key:
        return jsonify({'message': 'วันนี้คุณทำได้ดีมาก ✨'})

    data = request.get_json() or {}
    missions_done = data.get('missions_done', 0)
    total_missions = data.get('total_missions', 5)
    exercise_done = data.get('exercise_done', False)
    education_done = data.get('education_done', False)
    name = data.get('name', 'คุณ')

    all_done = missions_done >= total_missions and exercise_done and education_done
    partial = missions_done > 0 or exercise_done or education_done

    if all_done:
        status = f"ปฏิบัติภารกิจครบ {missions_done}/{total_missions} ภารกิจ ออกกำลังกายแล้ว และศึกษาจากคลังความรู้แล้ว"
        tone = "ชมเชยอย่างสุภาพและจริงใจ กระตุ้นให้รักษาความสม่ำเสมอ"
    elif partial:
        done_parts = []
        if missions_done > 0:
            done_parts.append(f"ปฏิบัติภารกิจ {missions_done}/{total_missions} ภารกิจ")
        if exercise_done:
            done_parts.append("ออกกำลังกายแล้ว")
        if education_done:
            done_parts.append("ศึกษาความรู้แล้ว")
        status = "ดำเนินการสำเร็จ: " + ", ".join(done_parts)
        tone = "ชมเชยสิ่งที่ปฏิบัติสำเร็จแล้ว และสนับสนุนให้ดำเนินการต่อด้วยถ้อยคำสุภาพ"
    else:
        status = "ยังไม่ได้เริ่มกิจกรรมในวันนี้"
        tone = "ให้กำลังใจและเชิญชวนด้วยถ้อยคำเชิงบวกสุภาพ ไม่ตำหนิ"

    prompt = f"""สถานะวันนี้: {status}
โทน: {tone}

แต่งข้อความภาษาไทย 1 ประโยคสั้นๆ ไม่เกิน 15 คำ ภาษาธรรมชาติ ไม่ทางการมาก ใช้ emoji 1 ตัวท้ายประโยค"""

    greeting_models = [
        "google/gemma-4-31b-it:free",
        "google/gemma-3-27b-it:free",
        "nvidia/nemotron-nano-12b-v2-vl:free",
    ]
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
        'HTTP-Referer': 'https://mhealth-antaging.replit.app',
        'X-Title': 'mHealth AI Greeting',
    }
    for model in greeting_models:
        try:
            r = requests.post('https://openrouter.ai/api/v1/chat/completions',
                headers=headers,
                json={
                    'model': model,
                    'messages': [{'role': 'user', 'content': prompt}],
                    'max_tokens': 80,
                },
                timeout=15)
            if r.status_code == 200:
                msg = r.json()['choices'][0]['message'].get('content', '').strip()
                if msg:
                    return jsonify({'message': msg})
        except Exception:
            continue

    fallback = 'ท่านปฏิบัติภารกิจสุขภาพได้อย่างดียิ่งในวันนี้ ✨' if partial else 'ขอเชิญท่านเริ่มต้นภารกิจสุขภาพประจำวันได้แล้ววันนี้ 🌱'
    return jsonify({'message': fallback})

# ====================================================
# AI Food Recommendation
# ====================================================
@app.route('/api/recommend_food', methods=['POST'])
def api_recommend_food():
    data = request.get_json() or {}
    food_name = data.get('food_name', '')
    calories  = data.get('calories', 0)
    protein   = data.get('protein', 0)
    fat       = data.get('fat', 0)
    fiber     = data.get('fiber', 0)

    api_key = os.environ.get('OPENROUTER_API_KEY', '')
    if not api_key:
        return jsonify({'success': False, 'error': 'No API key'})

    prompt = f"""คุณเป็นนักโภชนาการด้านการชะลอวัย (Anti-Aging Nutritionist) วิเคราะห์อาหารนี้:
อาหาร: {food_name}
แคลอรี่: {calories} kcal | โปรตีน: {protein}g | ไขมัน: {fat}g | ใยอาหาร: {fiber}g

ตอบเป็น JSON เท่านั้น ห้ามมีข้อความอื่น:
{{
  "score": 7,
  "verdict": "ดีมาก",
  "verdict_icon": "✅",
  "reason": "เหตุผล 1-2 ประโยค ว่าดีหรือไม่ดีต่อการชะลอวัยอย่างไร",
  "tip": "คำแนะนำสั้นๆ ว่าควรกินกับอะไร หรือกินตอนไหน",
  "alternatives": ["เมนูทดแทนที่ดีกว่า 1", "เมนูทดแทนที่ดีกว่า 2"]
}}
score คือ 1-10 (10 = ดีที่สุดสำหรับต้านวัย)
verdict และ verdict_icon ต้องสอดคล้องกัน: ดีมาก=✅ พอใช้=⚠️ ควรหลีกเลี่ยง=❌"""

    recommend_models = [
        "google/gemma-4-31b-it:free",
        "google/gemma-3-27b-it:free",
        "nvidia/nemotron-nano-12b-v2-vl:free",
    ]
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
        'HTTP-Referer': 'https://mhealth-antaging.replit.app',
        'X-Title': 'mHealth Food Recommend',
    }
    for model in recommend_models:
        try:
            r = requests.post('https://openrouter.ai/api/v1/chat/completions',
                headers=headers,
                json={'model': model, 'messages': [{'role': 'user', 'content': prompt}], 'max_tokens': 300},
                timeout=20)
            if r.status_code == 200:
                raw = r.json()['choices'][0]['message'].get('content', '').strip()
                raw = raw.replace('```json', '').replace('```', '').strip()
                result = json.loads(raw)
                return jsonify({'success': True, 'data': result})
        except Exception:
            continue

    return jsonify({'success': False, 'error': 'AI ไม่สามารถวิเคราะห์ได้ในขณะนี้'})

# ====================================================
# Admin Update User Name
# ====================================================
@app.route('/admin/update_user_name', methods=['POST'])
def admin_update_user_name():
    if session.get('role') not in ['admin', 'researcher']:
        return jsonify({'error': 'Unauthorized'}), 403
    data = request.get_json() or {}
    user_id = data.get('user_id')
    new_name = (data.get('name') or '').strip()
    new_last_name = (data.get('last_name') or '').strip()
    if not user_id or not new_name:
        return jsonify({'error': 'ข้อมูลไม่ครบ'}), 400
    conn = get_db()
    conn.execute("UPDATE users SET name=%s, last_name=%s WHERE id=%s",
                 (new_name, new_last_name or None, user_id))
    conn.commit()
    conn.close()
    full = f"{new_name} {new_last_name}".strip()
    return jsonify({'success': True, 'name': new_name, 'last_name': new_last_name, 'full': full})

# ====================================================
# Admin Reload from Sheets
# ====================================================
@app.route('/admin/reload_db', methods=['POST'])
def admin_reload_db():
    if session.get('role') not in ['admin', 'researcher']:
        return jsonify({'error': 'Unauthorized'}), 403
    try:
        reload_from_sheets()
        return jsonify({'success': True, 'message': 'โหลดข้อมูลจาก Google Sheets สำเร็จ'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ====================================================
# Teardown - Close DB Connection
# ====================================================
@app.teardown_appcontext
def teardown_db(exception):
    """Close database connection after each request"""
    from database import _db
    if _db is not None:
        try:
            _db.close()
        except:
            pass

# ====================================================
# Startup — runs for both `python app.py` and gunicorn
# ====================================================
try:
    with app.app_context():
        init_db()
except Exception as _e:
    print(f"⚠️ init_db warning: {_e}")

# ====================================================
# Main
# ====================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug, use_reloader=False)