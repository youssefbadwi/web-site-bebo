import os
import json
import sqlite3
import html
import re
import time
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, abort
import uuid
from werkzeug.security import generate_password_hash, check_password_hash
import supabase_db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, 'templates'),
    static_folder=os.path.join(BASE_DIR, 'static')
)
# High entropy secret key for secure admin sessions
app.secret_key = os.environ.get('SECRET_KEY', 'kholy_luxury_abayas_super_secret_shield_2026_x89!')
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # Max 16MB to comfortably accept modern phone camera photos

UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads', 'products')
try:
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
except Exception:
    pass
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'avif', 'heic'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

DB_PATH = os.path.join(BASE_DIR, 'fashion_store.db')

# Admin Credentials Hashed with Secure Salt (No plain-text passwords & removed weak default accounts)
ADMIN_USERS_HASHED = {
    'hareer': generate_password_hash('hareer2026'),
    'kholy': generate_password_hash('kholy2026'),
    'youssef': generate_password_hash('youssef2026'),
    'bibo': generate_password_hash('bibo2026')
}

PARTNER_NAMES = {
    'youssef': 'يوسف شعبان',
    'bibo': 'بيبو',
    'hareer': 'يوسف شعبان وبيبو',
    'kholy': 'يوسف شعبان وبيبو'
}

# In-memory Rate Limiters for Brute Force & Spam Prevention
LOGIN_ATTEMPTS = {}     # ip -> {'count': int, 'last_attempt': float, 'blocked_until': float}
ORDER_ATTEMPTS = {}     # ip -> list of timestamps

def get_client_ip():
    if request.headers.getlist("X-Forwarded-For"):
        return request.headers.getlist("X-Forwarded-For")[0].split(',')[0].strip()
    return request.remote_addr or '127.0.0.1'

def is_login_blocked(ip):
    rec = LOGIN_ATTEMPTS.get(ip)
    if not rec:
        return False, 0
    now = time.time()
    if rec.get('blocked_until', 0) > now:
        remaining_minutes = max(1, int((rec['blocked_until'] - now) / 60) + 1)
        return True, remaining_minutes
    return False, 0

def record_failed_login(ip):
    now = time.time()
    rec = LOGIN_ATTEMPTS.setdefault(ip, {'count': 0, 'last_attempt': now, 'blocked_until': 0})
    if now - rec['last_attempt'] > 900:  # Reset count if last attempt was > 15m ago
        rec['count'] = 0
    rec['count'] += 1
    rec['last_attempt'] = now
    if rec['count'] >= 5:
        rec['blocked_until'] = now + 900  # Lock out for 15 minutes after 5 failed tries

def record_successful_login(ip):
    LOGIN_ATTEMPTS.pop(ip, None)

def is_order_rate_limited(ip):
    now = time.time()
    window = 600  # 10 minutes
    max_orders = 5  # Max 5 orders per 10 minutes per IP
    timestamps = ORDER_ATTEMPTS.get(ip, [])
    recent = [t for t in timestamps if now - t < window]
    ORDER_ATTEMPTS[ip] = recent
    if len(recent) >= max_orders:
        return True
    return False

def record_order_placed(ip):
    ORDER_ATTEMPTS.setdefault(ip, []).append(time.time())

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            if request.path.startswith('/api/admin/'):
                return jsonify({'error': 'غير مصرح بالدخول، يرجى تسجيل الدخول أولاً'}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # Products table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        category TEXT NOT NULL,
        price REAL NOT NULL,
        old_price REAL,
        image TEXT NOT NULL,
        description TEXT,
        rating REAL DEFAULT 4.9,
        reviews_count INTEGER DEFAULT 24,
        sizes TEXT DEFAULT '52,54,56,58,60',
        colors TEXT DEFAULT 'أسود ملكي,كحلي,بترولي',
        in_stock INTEGER DEFAULT 1,
        featured INTEGER DEFAULT 0,
        badge TEXT DEFAULT '',
        cost_price REAL DEFAULT 0.0
    )
    ''')

    # Migration for existing DB without cost_price
    try:
        cursor.execute("ALTER TABLE products ADD COLUMN cost_price REAL DEFAULT 0.0")
    except Exception:
        pass

    # Categories table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        slug TEXT NOT NULL UNIQUE,
        icon TEXT DEFAULT 'sparkles'
    )
    ''')

    # Orders table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_number TEXT UNIQUE NOT NULL,
        customer_name TEXT NOT NULL,
        customer_phone TEXT NOT NULL,
        customer_email TEXT,
        city TEXT NOT NULL,
        address TEXT NOT NULL,
        payment_method TEXT NOT NULL,
        subtotal REAL NOT NULL,
        discount REAL DEFAULT 0,
        shipping REAL DEFAULT 0,
        total_amount REAL NOT NULL,
        items_json TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        val TEXT
    )
    ''')

    # Seed categories if empty
    cursor.execute('SELECT COUNT(*) FROM categories')
    cat_count = cursor.fetchone()[0]
    if cat_count == 0:
        categories = [
            ('عبايات خروج وكاجوال', 'casual', 'shirt'),
            ('عبايات سواريه ومناسبات', 'soiree', 'sparkles'),
            ('عبايات خليجي وبشت', 'khaleeji', 'crown'),
            ('إسدالات واستقبال', 'prayer', 'heart'),
            ('عبايات كتان وصيفي', 'linen', 'feather')
        ]
        cursor.executemany('INSERT OR IGNORE INTO categories (name, slug, icon) VALUES (?, ?, ?)', categories)

    # Check if demo products were already seeded once
    cursor.execute("SELECT val FROM settings WHERE key = 'seeded'")
    seeded_flag = cursor.fetchone()
    cursor.execute('SELECT COUNT(*) FROM products')
    prod_count = cursor.fetchone()[0]

    if not seeded_flag and prod_count == 0:

        abaya_products = [
            (
                'عباية مخملية سوداء مطرزة بتطريز كمبيوتر ملكي',
                'عبايات سواريه ومناسبات',
                1450.0,
                1850.0,
                'https://images.unsplash.com/photo-1583391733956-3750e0ff4e8b?auto=format&fit=crop&w=800&q=80',
                'عباية سواريه راقية جداً مصنوعة من قماش المخمل الفاخر مع كريب ملكي، مزينة بتطريز هندسي أنيق على الصدر والأكمام بلون أسود لامع، خيارك الأول لأرقى المناسبات.',
                5.0,
                48,
                '52,54,56,58,60',
                'أسود سواد فاحم',
                1,
                1,
                'الأكثر طلباً'
            ),
            (
                'عباية بشت خليجي ملكي كريب كوري كلوش واسع',
                'عبايات خليجي وبشت',
                1250.0,
                1600.0,
                'https://images.unsplash.com/photo-1595777457583-95e059d581b8?auto=format&fit=crop&w=800&q=80',
                'قصة بشت خليجي فخمة جداً وانسيابية، مصممة من أجود أقمشة الكريب الكوري البارد غير الشفاف مع تقفيل أنيق بالكباسين المخفية وطرحة ليزر هدية.',
                4.9,
                62,
                '52,54,56,58,60',
                'أسود ملكي,كحلي داكن,زيتي فاخر',
                1,
                1,
                'تشكيلة 2026'
            ),
            (
                'عباية كاجوال يومية قماش كريب صالونا مريح',
                'عبايات خروج وكاجوال',
                850.0,
                1100.0,
                'https://images.unsplash.com/photo-1515886657613-9f3515b0c78f?auto=format&fit=crop&w=800&q=80',
                'عباية خروج عملية وعصرية بقصة نص كلوش مريحة، نسيج كريب صالونا عالي التحمل ومقاوم للتجعد، مناسبة للدوام والجامعة والمشاوير اليومية.',
                4.8,
                35,
                '52,54,56,58',
                'أسود,كشمير هادئ,رمادي رمزي',
                1,
                1,
                'عرض خاص'
            ),
            (
                'عباية لف حرير مغسول بأكمام دانتيل فرنسي',
                'عبايات سواريه ومناسبات',
                1680.0,
                2100.0,
                'https://images.unsplash.com/photo-1539109136881-3be0616acf4b?auto=format&fit=crop&w=800&q=80',
                'قطعة فنية فريدة من الحرير المغسول الفخم مع أكمام مطعمة بالدانتيل الفرنسي الأصلي والشك اليدوي الناعم لإطلالة ساحرة ومميزة في السهرات.',
                4.9,
                29,
                '54,56,58',
                'أسود داكن,زيتي مائل للذهبي',
                1,
                1,
                'إصدار محدود'
            ),
            (
                'عباية كلوش كريب ندى بتطريز ورد دقيق',
                'عبايات خروج وكاجوال',
                920.0,
                1200.0,
                'https://images.unsplash.com/photo-1572804013309-59a88b7e92f1?auto=format&fit=crop&w=800&q=80',
                'عباية كلوش أنيقة من قماش كريب ندى الأصلي البارد، تتزين بنقشات ورود مطرزة بخيوط الحرير على الأساور وياقة العباية بلمسة غاية في الرقة.',
                4.8,
                41,
                '52,54,56,58,60',
                'أسود فاحم,بترولي',
                1,
                0,
                ''
            ),
            (
                'إسدال صلاة فاخر قطن بيور مع طرحة متصلة',
                'إسدالات واستقبال',
                480.0,
                650.0,
                'https://images.unsplash.com/photo-1584917865442-de89df76afd3?auto=format&fit=crop&w=800&q=80',
                'إسدال صلاة واسع وساتر ومريح جداً، مصنوع من القطن الطبيعي 100% المبرد ذو ملمس ناعم جداً، مع طرحة متصلة عريضة وأساور مطاطية مريحة.',
                5.0,
                78,
                'مقاس حر One Size',
                'كحلي كلاسيكي,موف باستيل,زيتي ناعم,أسود',
                1,
                1,
                'الأعلى مبيعاً'
            ),
            (
                'جلابية استقبال مغربية مطرزة بالسفيفة والصقلي',
                'إسدالات واستقبال',
                990.0,
                1350.0,
                'https://images.unsplash.com/photo-1518611012118-696072aa579a?auto=format&fit=crop&w=800&q=80',
                'قفطان استقبال بيتي راقي جداً للعرائس والضيافة، مطرز بحرفية بخيوط الصقلي اللامعة وأزرار العقدة التقليدية لإطلالة ملوكية في بيتك.',
                4.9,
                33,
                'M,L,XL,XXL',
                'زمردي أخضر,عنابي ملكي,أوف وايت',
                1,
                0,
                'تشكيلة العيد'
            ),
            (
                'عباية رأس إسلامية كريب سعودي فضفاضة ساترة',
                'عبايات خروج وكاجوال',
                790.0,
                980.0,
                'https://images.unsplash.com/photo-1509631179647-0177331693ae?auto=format&fit=crop&w=800&q=80',
                'عباية رأس شرعية بتفصيل متقن وقماش كريب سعودي سوبر ثقيل لا يشف ولا يلتصق بالجسم، ومزودة بأكمام مطاطية مخفية لراحة تامة.',
                5.0,
                54,
                '54,56,58,60',
                'أسود غاطس',
                1,
                0,
                ''
            ),
            (
                'عباية سواريه فخمة بتطريز سيلفر يدوي وأكتاف ملكية',
                'عبايات سواريه ومناسبات',
                1890.0,
                2400.0,
                'https://images.unsplash.com/photo-1566174053879-31528523f8ae?auto=format&fit=crop&w=800&q=80',
                'تصميم حصري من دار حرير للعبايات، قماش كريب كافيار مرصع بخرز وفصوص كريستالية لامعة عاكسة للضوء على الأكتاف والظهر لحضور ملفت في حفلات الزفاف.',
                4.9,
                22,
                '54,56,58',
                'أسود سواريه',
                1,
                1,
                'VIP كولكشن'
            ),
            (
                'عباية كتان فاخرة مطرزة بخيوط الحرير الذهبي',
                'عبايات كتان وصيفي',
                1150.0,
                1450.0,
                'https://images.unsplash.com/photo-1572804013309-59a88b7e92f1?auto=format&fit=crop&w=800&q=80',
                'عباية كتان طبيعي باردة وخفيفة جداً للصيف والمشاوير، بتطريز تراثي أنيق بخيوط ذهبية ناعمة على الصدر والأكمام.',
                4.9,
                45,
                '52,54,56,58,60',
                'بيج طبيعي,أسود,زيتي',
                1,
                1,
                'تشكيلة الصيف'
            ),
            (
                'عباية فسكوز استقبال بكم مروحة واسع',
                'إسدالات واستقبال',
                890.0,
                1150.0,
                'https://images.unsplash.com/photo-1622290291468-a28f7a7dc6a8?auto=format&fit=crop&w=800&q=80',
                'عباية وجلابية استقبال فسكوز ناعمة ومريحة جداً بقصة أكمام مروحة أنيقة وتطريز صدر جذاب ومناسب لضيافة البيت.',
                5.0,
                58,
                'M,L,XL,XXL',
                'بني مطرز,كحلي,عنابي',
                1,
                0,
                'الأكثر طلباً'
            ),
            (
                'عباية بشت إماراتي مفتوحة مع فستان داخلي منفصل',
                'عبايات خليجي وبشت',
                1590.0,
                1950.0,
                'https://images.unsplash.com/photo-1490481651871-ab68de25d43d?auto=format&fit=crop&w=800&q=80',
                'طقم عباية بشت إماراتي قطعتين (عباية خارجية فاخرة + اندر دريس داخلي ناعم)، تمنحك إطلالة راقية متعددة الاستخدامات.',
                5.0,
                38,
                '52,54,56,58',
                'أسود مع بيج,كحلي مع أوف وايت',
                1,
                1,
                'طقم كامل قطعتين'
            )
        ]

        cursor.executemany('''
        INSERT INTO products 
        (name, category, price, old_price, image, description, rating, reviews_count, sizes, colors, in_stock, featured, badge)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', abaya_products)

        # Sample initial order
        sample_items = [
            {
                "id": 1,
                "name": "عباية مخملية سوداء مطرزة بتطريز كمبيوتر ملكي",
                "price": 1450.0,
                "quantity": 1,
                "size": "56",
                "color": "أسود سواد فاحم",
                "image": "https://images.unsplash.com/photo-1583391733956-3750e0ff4e8b?auto=format&fit=crop&w=800&q=80"
            }
        ]
        cursor.execute('''
        INSERT OR IGNORE INTO orders 
        (order_number, customer_name, customer_phone, customer_email, city, address, payment_method, subtotal, discount, shipping, total_amount, items_json, status, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            'ORD-2026-001',
            'مدام مروة الشاذلي',
            '01012345678',
            'marwa@example.com',
            'القاهرة (التجمع الخامس)',
            'شارع التسعين، كمبوند النرجس، فيلا 12',
            'الدفع عند الاستلام',
            1450.0,
            0.0,
            0.0,
            1450.0,
            json.dumps(sample_items, ensure_ascii=False),
            'pending',
            'يرجى التوصيل بعد الساعة 4 عصراً'
        ))

        cursor.execute("INSERT OR REPLACE INTO settings (key, val) VALUES ('seeded', '1')")
    else:
        cursor.execute("INSERT OR REPLACE INTO settings (key, val) VALUES ('seeded', '1')")

    # Seed / Update realistic wholesale cost prices
    cost_defaults = {
        1: 850.0,
        2: 700.0,
        3: 450.0,
        4: 980.0,
        5: 520.0,
        6: 260.0,
        7: 550.0,
        8: 420.0,
        9: 1100.0,
        10: 650.0,
        11: 480.0,
        12: 900.0
    }
    for pid, cprice in cost_defaults.items():
        cursor.execute("UPDATE products SET cost_price = ? WHERE id = ? AND (cost_price IS NULL OR cost_price = 0)", (cprice, pid))
    
    # Generic fallback
    cursor.execute("UPDATE products SET cost_price = ROUND((price * 0.58) / 10) * 10 WHERE cost_price IS NULL OR cost_price = 0")

    conn.commit()
    conn.close()

# ----------------- PUBLIC STORE ROUTES -----------------

@app.route('/')
def index():
    return render_template('index.html')

# ----------------- AUTHENTICATION ROUTES -----------------

@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('admin_logged_in'):
        return redirect(url_for('admin'))

    error = None
    client_ip = get_client_ip()

    if request.method == 'POST':
        blocked, remaining_mins = is_login_blocked(client_ip)
        if blocked:
            error = f"تم حظر تسجيل الدخول مؤقتاً بسبب تكرار المحاولات الخاطئة لحماية الحساب. يرجى الانتظار {remaining_mins} دقيقة والمحاولة مجدداً."
            return render_template('login.html', error=error), 429

        username = request.form.get('username', '').strip().lower()
        password = request.form.get('password', '').strip()

        if username in ADMIN_USERS_HASHED and check_password_hash(ADMIN_USERS_HASHED[username], password):
            record_successful_login(client_ip)
            session.clear()
            session['admin_logged_in'] = True
            session['admin_username'] = username
            session.permanent = True
            return redirect(url_for('admin'))
        else:
            record_failed_login(client_ip)
            error = 'اسم المستخدم أو كلمة المرور غير صحيحة، يرجى المحاولة مجدداً.'

    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.pop('admin_logged_in', None)
    session.pop('admin_username', None)
    return redirect(url_for('login'))

@app.route('/admin')
@admin_required
def admin():
    u = session.get('admin_username', 'kholy')
    display_name = PARTNER_NAMES.get(u, 'يوسف شعبان وبيبو')
    return render_template('admin.html', username=display_name)

# ----------------- PUBLIC API ENDPOINTS -----------------

def _parse_sqlite_product_metadata(p, is_admin=False):
    desc = p.get('description') or ''
    merchant = p.get('merchant_name') or ''
    m_merch = re.search(r'<!--merchant:(.*?)-->', desc)
    if m_merch:
        merchant = m_merch.group(1).strip()
    p['merchant_name'] = merchant

    images = []
    m_imgs = re.search(r'<!--images:(.*?)-->', desc)
    if m_imgs:
        try:
            images = json.loads(m_imgs.group(1))
        except Exception:
            pass
    if not images and p.get('image'):
        images = [p['image']]
    elif p.get('image') and p['image'] not in images:
        images.insert(0, p['image'])
    p['images'] = images

    clean_desc = re.sub(r'<!--images:.*?-->', '', desc).strip()
    clean_desc = re.sub(r'<!--merchant:.*?-->', '', clean_desc).strip()
    if not is_admin:
        p.pop('cost_price', None)
    p['description'] = clean_desc
    return p

@app.route('/api/products', methods=['GET'])
def get_products():
    category = request.args.get('category')
    search = request.args.get('search')
    sort_by = request.args.get('sort', 'newest')
    featured_only = request.args.get('featured')
    is_admin = session.get('admin_logged_in')

    if supabase_db.is_supabase_enabled():
        return jsonify(supabase_db.get_products(category, search, sort_by, featured_only, is_admin))

    conn = get_db()
    cursor = conn.cursor()

    query = 'SELECT * FROM products WHERE in_stock = 1'
    params = []

    if category and category != 'all':
        query += ' AND category = ?'
        params.append(category)

    if featured_only == '1':
        query += ' AND featured = 1'

    if search:
        query += ' AND (name LIKE ? OR description LIKE ?)'
        wildcard = f'%{search}%'
        params.extend([wildcard, wildcard])

    if sort_by == 'price_low':
        query += ' ORDER BY price ASC'
    elif sort_by == 'price_high':
        query += ' ORDER BY price DESC'
    elif sort_by == 'rating':
        query += ' ORDER BY rating DESC'
    else:
        query += ' ORDER BY id DESC'

    cursor.execute(query, params)
    rows = cursor.fetchall()
    products = [_parse_sqlite_product_metadata(dict(row), is_admin=is_admin) for row in rows]
    conn.close()

    return jsonify(products)

@app.route('/api/products/<int:product_id>', methods=['GET'])
def get_product(product_id):
    is_admin = session.get('admin_logged_in')
    if supabase_db.is_supabase_enabled():
        p = supabase_db.get_product(product_id, is_admin)
        if p:
            return jsonify(p)
        return jsonify({'error': 'Product not found'}), 404

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM products WHERE id = ?', (product_id,))
    row = cursor.fetchone()
    conn.close()

    if row:
        return jsonify(_parse_sqlite_product_metadata(dict(row), is_admin=is_admin))
    return jsonify({'error': 'Product not found'}), 404

@app.route('/api/categories', methods=['GET'])
def get_categories():
    if supabase_db.is_supabase_enabled():
        return jsonify(supabase_db.get_categories())

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM categories ORDER BY id ASC')
    rows = cursor.fetchall()
    categories = [dict(row) for row in rows]
    conn.close()
    return jsonify(categories)

@app.route('/api/admin/categories', methods=['POST'])
@admin_required
def add_admin_category():
    data = request.json or {}
    name = html.escape(str(data.get('name', '')).strip()[:100])
    icon = html.escape(str(data.get('icon', 'tag')).strip()[:50])
    if not name:
        return jsonify({'error': 'اسم القسم مطلوب'}), 400

    if supabase_db.is_supabase_enabled():
        res = supabase_db.add_category(name, icon=icon)
        return jsonify({'success': True, 'category': res, 'message': 'تم إضافة القسم بنجاح'}), 201

    conn = get_db()
    cursor = conn.cursor()
    slug_val = f"cat_{uuid.uuid4().hex[:6]}"
    cursor.execute('INSERT INTO categories (name, slug, icon) VALUES (?, ?, ?)', (name, slug_val, icon))
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return jsonify({'success': True, 'category': {'id': new_id, 'name': name, 'slug': slug_val, 'icon': icon}, 'message': 'تم إضافة القسم بنجاح'}), 201

@app.route('/api/admin/categories/<int:cat_id>', methods=['DELETE'])
@admin_required
def delete_admin_category(cat_id):
    if supabase_db.is_supabase_enabled():
        supabase_db.delete_category(cat_id)
        return jsonify({'success': True, 'message': 'تم حذف القسم بنجاح'})

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM categories WHERE id = ?', (cat_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': 'تم حذف القسم بنجاح'})

# ----------------- PUBLIC SETTINGS, DISCOUNTS & REVIEWS -----------------

@app.route('/api/settings', methods=['GET'])
def get_public_settings():
    st = supabase_db.get_store_settings()
    discounts = supabase_db.get_discounts()
    active_banner = None
    for d in discounts:
        if d.get('is_active', True) and d.get('show_banner', True):
            max_uses = int(d.get('max_uses', 0))
            used = int(d.get('used_count', 0))
            rem = max(0, max_uses - used) if max_uses > 0 else 999
            if rem > 0:
                active_banner = {
                    'code': d.get('code'),
                    'type': d.get('type', 'percent'),
                    'value': d.get('value', 20),
                    'remaining_uses': rem,
                    'max_uses': max_uses,
                    'description': d.get('description', '')
                }
                break

    wa = st.get('whatsapp_number') or st.get('whatsapp') or '01132647659'
    ph = st.get('contact_phone') or st.get('phone') or '01132647658'
    fb = st.get('facebook_url') or st.get('facebook') or 'https://www.facebook.com/share/1KEkt1NZbg/'
    tt = st.get('tiktok_url') or st.get('tiktok') or 'https://www.tiktok.com/@youssef.shas?_r=1&_t=ZS-9AAMIOqltSL'
    f_thresh = float(st.get('free_shipping_threshold', 1000))
    f_active = bool(st.get('free_shipping_active', st.get('free_shipping_enabled', True)))
    ann_text = st.get('announcement_text') or st.get('free_shipping_text') or 'دار حرير للعبايات الفاخرة • شحن وتوصيل لكافة المحافظات ✨'

    if active_banner:
        active_banner['discount_display'] = f"{active_banner['value']}%" if active_banner.get('type') == 'percent' else f"{active_banner['value']} ج.م"

    return jsonify({
        'whatsapp': wa,
        'whatsapp_number': wa,
        'phone': ph,
        'contact_phone': ph,
        'facebook': fb,
        'facebook_url': fb,
        'tiktok': tt,
        'tiktok_url': tt,
        'free_shipping_enabled': f_active,
        'free_shipping_active': f_active,
        'free_shipping_threshold': f_thresh,
        'free_shipping_text': ann_text,
        'announcement_text': ann_text,
        'active_discount_banner': active_banner
    })

@app.route('/api/discounts/validate', methods=['POST'])
def validate_discount():
    data = request.json or {}
    code = data.get('code', '')
    subtotal = float(data.get('subtotal', 0))
    res = supabase_db.validate_discount_code(code, subtotal)
    return jsonify(res)

@app.route('/api/reviews', methods=['GET'])
def get_public_reviews():
    reviews = supabase_db.get_reviews()
    return jsonify(reviews)

@app.route('/api/reviews', methods=['POST'])
def submit_public_review():
    data = request.json or {}
    name = html.escape(str(data.get('name', 'عميلة دار حرير')).strip()[:100])
    comment = html.escape(str(data.get('comment', '')).strip()[:500])
    city = html.escape(str(data.get('city', 'مصر')).strip()[:50])
    try:
        rating = int(data.get('rating', 5))
        rating = max(1, min(5, rating))
    except Exception:
        rating = 5

    if not comment:
        return jsonify({'error': 'يرجى كتابة رأيك وتقييمك'}), 400

    new_rev = {
        'id': f"rev_{uuid.uuid4().hex[:6]}",
        'name': name,
        'rating': rating,
        'comment': comment,
        'city': city,
        'created_at': datetime.now().strftime('%Y-%m-%d')
    }
    supabase_db.add_review(new_rev)
    return jsonify({'success': True, 'message': 'شكراً لتقييمك الرائع لدار حرير ❤️', 'review': new_rev}), 201

# ----------------- ADMIN SETTINGS & DISCOUNTS -----------------

@app.route('/api/admin/settings', methods=['GET'])
@admin_required
def get_admin_settings():
    st = supabase_db.get_store_settings()
    discounts = supabase_db.get_discounts()
    for d in discounts:
        if 'discount_type' not in d:
            d['discount_type'] = d.get('type', 'percent')
        if 'discount_value' not in d:
            d['discount_value'] = d.get('value', 20)
    return jsonify({
        'settings': st,
        'store_settings': st,
        'discounts': discounts,
        'reviews': supabase_db.get_reviews()
    })

@app.route('/api/admin/settings', methods=['POST'])
@admin_required
def update_admin_settings():
    data = request.json or {}
    saved = supabase_db.save_store_settings(data)
    return jsonify({'success': True, 'message': 'تم حفظ إعدادات المتجر بنجاح', 'settings': saved})

@app.route('/api/admin/discounts', methods=['POST'])
@admin_required
def save_admin_discount():
    data = request.json or {}
    code = str(data.get('code', '')).strip().upper()
    if not code:
        return jsonify({'error': 'كود الخصم مطلوب'}), 400
    try:
        val = float(data.get('discount_value', data.get('value', 10)))
        max_uses = int(data.get('max_uses', 20))
    except Exception:
        return jsonify({'error': 'القيمة والحد الأقصى يجب أن يكونا أرقاماً'}), 400

    disc_type = data.get('discount_type', data.get('type', 'percent'))
    discounts = supabase_db.get_discounts()
    found = False
    for d in discounts:
        if d.get('code', '').upper() == code:
            d['value'] = val
            d['discount_value'] = val
            d['type'] = disc_type
            d['discount_type'] = disc_type
            d['max_uses'] = max_uses
            d['is_active'] = bool(data.get('is_active', True))
            d['show_banner'] = bool(data.get('show_banner', True))
            d['description'] = data.get('description', f'خصم {val}{"%" if disc_type=="percent" else " ج.م"}')
            found = True
            break
    if not found:
        discounts.insert(0, {
            'code': code,
            'type': disc_type,
            'discount_type': disc_type,
            'value': val,
            'discount_value': val,
            'max_uses': max_uses,
            'used_count': 0,
            'is_active': bool(data.get('is_active', True)),
            'show_banner': bool(data.get('show_banner', True)),
            'description': data.get('description', f'خصم {val}{"%" if disc_type=="percent" else " ج.م"}')
        })
    supabase_db.save_discounts(discounts)
    return jsonify({'success': True, 'message': f'تم حفظ كود الخصم {code} بنجاح'})

@app.route('/api/admin/discounts/<string:code>', methods=['DELETE'])
@admin_required
def delete_admin_discount(code):
    code_clean = code.strip().upper()
    discounts = supabase_db.get_discounts()
    discounts = [d for d in discounts if d.get('code', '').upper() != code_clean]
    supabase_db.save_discounts(discounts)
    return jsonify({'success': True, 'message': 'تم حذف كود الخصم بنجاح'})

@app.route('/api/admin/reviews/<string:rev_id>', methods=['DELETE'])
@admin_required
def delete_admin_review(rev_id):
    supabase_db.delete_review(rev_id)
    return jsonify({'success': True, 'message': 'تم حذف التقييم بنجاح'})

@app.route('/api/orders', methods=['POST'])
def create_order():
    client_ip = get_client_ip()
    if is_order_rate_limited(client_ip):
        return jsonify({
            'error': 'تم تجاوز الحد المسموح به من الطلبات مؤقتاً لحماية أمن المتجر. يرجى الانتظار 10 دقائق والمحاولة مجدداً.'
        }), 429

    data = request.json or {}
    
    # 1. Required fields presence
    customer_name = str(data.get('customer_name', '')).strip()
    customer_phone = str(data.get('customer_phone', '')).strip()
    city = str(data.get('city', '')).strip()
    address = str(data.get('address', '')).strip()
    notes = str(data.get('notes', '')).strip()
    items = data.get('items', [])

    if not customer_name or not customer_phone or not city or not address:
        return jsonify({'error': 'جميع البيانات الأساسية (الاسم، الهاتف، المحافظة، العنوان) مطلوبة'}), 400

    # 2. XSS & Length Sanitization
    if len(customer_name) < 2 or len(customer_name) > 80:
        return jsonify({'error': 'اسم العميل غير صالح (يجب أن يكون بين 2 و 80 حرفاً)'}), 400
    if len(address) < 5 or len(address) > 250:
        return jsonify({'error': 'عنوان التوصيل غير صالح (يجب أن يكون بين 5 و 250 حرفاً)'}), 400
    if len(city) < 2 or len(city) > 50:
        return jsonify({'error': 'اسم المحافظة غير صالح'}), 400
    if len(notes) > 300:
        notes = notes[:300]

    # Clean HTML entities to prevent Stored XSS completely
    clean_name = html.escape(customer_name)
    clean_city = html.escape(city)
    clean_address = html.escape(address)
    clean_notes = html.escape(notes)

    # 3. Egyptian Mobile Phone Validation (Strict regex)
    phone_clean = re.sub(r'[\s\-\+]', '', customer_phone)
    if phone_clean.startswith('20') and len(phone_clean) == 12:
        phone_clean = '0' + phone_clean[2:]
    
    if not re.match(r'^01[0125][0-9]{8}$', phone_clean):
        return jsonify({'error': 'رقم الهاتف غير صحيح، يرجى كتابة رقم محمول مصري صحيح مكون من 11 رقماً (مثال: 01132647659)'}), 400

    # 4. Items & Price Tampering Verification (Verify against real DB products)
    if not isinstance(items, list) or len(items) == 0:
        return jsonify({'error': 'سلة المشتريات فارغة'}), 400
    if len(items) > 15:
        return jsonify({'error': 'الحد الأقصى للطلب الواحد هو 15 عباية'}), 400

    use_sb = supabase_db.is_supabase_enabled()
    conn = None if use_sb else get_db()
    cursor = None if use_sb else conn.cursor()

    verified_items = []
    calculated_subtotal = 0.0

    for item in items:
        try:
            prod_id = int(item.get('id', 0))
            qty = int(item.get('quantity', 1))
        except (ValueError, TypeError):
            if conn: conn.close()
            return jsonify({'error': 'بيانات الكميات أو المنتجات غير صالحة'}), 400

        if qty < 1 or qty > 10:
            if conn: conn.close()
            return jsonify({'error': 'الكمية لكل عباية يجب أن تكون بين 1 و 10'}), 400

        if use_sb:
            prod = supabase_db.get_product(prod_id)
        else:
            cursor.execute('SELECT id, name, price, image, in_stock FROM products WHERE id = ?', (prod_id,))
            prod = cursor.fetchone()

        if not prod or not prod.get('in_stock', 1):
            if conn: conn.close()
            return jsonify({'error': 'أحد الموديلات المطلوبة غير متوفر حالياً في المخزن'}), 400

        real_price = float(prod['price'])
        item_total = real_price * qty
        calculated_subtotal += item_total

        # Sanitize size and color
        size_clean = html.escape(str(item.get('size', 'حر One Size'))[:20])
        color_clean = html.escape(str(item.get('color', 'أسود ملكي'))[:30])

        verified_items.append({
            'id': prod['id'],
            'name': prod['name'],
            'price': real_price,
            'quantity': qty,
            'size': size_clean,
            'color': color_clean,
            'image': prod['image']
        })

    discount_code = str(data.get('discount_code') or '').strip().upper()
    calculated_discount = 0.0
    if discount_code:
        disc_val = supabase_db.validate_discount_code(discount_code, calculated_subtotal)
        if disc_val.get('valid'):
            calculated_discount = disc_val.get('discount_amount', 0.0)
            supabase_db.record_discount_usage(discount_code)
            clean_notes = f"كود خصم: {discount_code} (خصم {calculated_discount} ج.م) | {clean_notes}".strip(' |')

    st_settings = supabase_db.get_store_settings()
    is_free_shipping = bool(st_settings.get('free_shipping_enabled', True)) and (calculated_subtotal >= float(st_settings.get('free_shipping_threshold', 1000)))
    calculated_shipping = 0.0
    final_total = max(0.0, round(calculated_subtotal - calculated_discount, 2))

    order_num = f"HAREER-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    items_json = json.dumps(verified_items, ensure_ascii=False)

    if use_sb:
        order_res = supabase_db.create_order({
            'order_number': order_num,
            'customer_name': clean_name,
            'customer_phone': phone_clean,
            'customer_email': '',
            'city': clean_city,
            'address': clean_address,
            'payment_method': 'الدفع عند الاستلام والتأكد من المقاس',
            'subtotal': calculated_subtotal,
            'discount': calculated_discount,
            'shipping': calculated_shipping,
            'total_amount': final_total,
            'items_json': items_json,
            'status': 'pending',
            'notes': clean_notes
        })
        order_id = order_res.get('id', 1)
    else:
        cursor.execute('''
        INSERT INTO orders 
        (order_number, customer_name, customer_phone, customer_email, city, address, payment_method, subtotal, discount, shipping, total_amount, items_json, status, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            order_num,
            clean_name,
            phone_clean,
            '',
            clean_city,
            clean_address,
            'الدفع عند الاستلام والتأكد من المقاس',
            calculated_subtotal,
            calculated_discount,
            calculated_shipping,
            final_total,
            items_json,
            'pending',
            clean_notes
        ))
        conn.commit()
        order_id = cursor.lastrowid
        conn.close()

    record_order_placed(client_ip)

    return jsonify({
        'success': True,
        'order_id': order_id,
        'order_number': order_num,
        'subtotal': calculated_subtotal,
        'discount': calculated_discount,
        'discount_code': discount_code,
        'is_free_shipping': is_free_shipping,
        'shipping': calculated_shipping,
        'total_amount': final_total,
        'message': 'تم استلام طلبك بنجاح وسيتواصل معك فريق خدمة عملاء دار حرير للعبايات قريباً لتأكيد الشحن!'
    }), 201

# ----------------- PROTECTED ADMIN API -----------------

@app.route('/api/admin/orders', methods=['GET'])
@admin_required
def get_admin_orders():
    if supabase_db.is_supabase_enabled():
        return jsonify(supabase_db.get_admin_orders())

    conn = get_db()
    cursor = conn.cursor()
    
    # Cost lookup map
    cursor.execute('SELECT id, cost_price, price FROM products')
    costs_map = {row['id']: float(row['cost_price'] or 0.0) for row in cursor.fetchall()}

    cursor.execute('SELECT * FROM orders ORDER BY id DESC')
    rows = cursor.fetchall()
    orders = []
    for row in rows:
        order_dict = dict(row)
        try:
            items = json.loads(order_dict['items_json'])
        except Exception:
            items = []
        
        order_cost = 0.0
        for item in items:
            pid = item.get('id')
            qty = float(item.get('quantity', 1))
            c_price = costs_map.get(pid, float(item.get('cost_price', 0.0)))
            item['cost_price'] = c_price
            item['item_cost_total'] = round(c_price * qty, 2)
            item['item_profit_total'] = round((float(item.get('price', 0)) - c_price) * qty, 2)
            order_cost += item['item_cost_total']

        order_dict['items'] = items
        order_dict['total_cost'] = round(order_cost, 2)
        total_amt = float(order_dict.get('total_amount', 0.0))
        order_profit = max(0.0, round(total_amt - order_cost, 2))
        share = round(order_profit / 2.0, 2)
        order_dict['net_profit'] = order_profit
        order_dict['partner_share'] = share
        order_dict['youssef_share'] = share
        order_dict['bibo_share'] = share
        orders.append(order_dict)
    conn.close()
    return jsonify(orders)

@app.route('/api/admin/orders/<int:order_id>/status', methods=['PUT'])
@admin_required
def update_order_status(order_id):
    data = request.json or {}
    new_status = data.get('status')
    if not new_status:
        return jsonify({'error': 'الحالة مطلوبة'}), 400

    if supabase_db.is_supabase_enabled():
        supabase_db.update_order_status(order_id, new_status)
        return jsonify({'success': True, 'message': 'تم تحديث حالة الطلب بنجاح'})

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('UPDATE orders SET status = ? WHERE id = ?', (new_status, order_id))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': 'تم تحديث حالة الطلب بنجاح'})

@app.route('/api/admin/products', methods=['GET'])
@admin_required
def get_admin_products():
    if supabase_db.is_supabase_enabled():
        return jsonify(supabase_db.get_admin_products())

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM products ORDER BY id ASC')
    rows = cursor.fetchall()
    products = []
    for row in rows:
        p = _parse_sqlite_product_metadata(dict(row), is_admin=True)
        cost = float(p.get('cost_price') or 0.0)
        price = float(p.get('price') or 0.0)
        profit = round(price - cost, 2)
        margin_pct = round((profit / price * 100), 1) if price > 0 else 0.0
        share = round(profit / 2.0, 2)
        p['profit'] = profit
        p['margin_pct'] = margin_pct
        p['partner_share'] = share
        p['youssef_share'] = share
        p['bibo_share'] = share
        products.append(p)
    conn.close()
    return jsonify(products)

@app.route('/api/admin/products/<int:product_id>/cost', methods=['PUT'])
@admin_required
def update_product_cost(product_id):
    data = request.json or {}
    cost_price = data.get('cost_price')
    if cost_price is None:
        return jsonify({'error': 'سعر الشراء / التكلفة مطلوب'}), 400
    try:
        cost_price = float(cost_price)
    except (ValueError, TypeError):
        return jsonify({'error': 'سعر التكلفة يجب أن يكون رقماً صحيحاً'}), 400

    if supabase_db.is_supabase_enabled():
        supabase_db.update_product_cost(product_id, cost_price)
        return jsonify({'success': True, 'message': 'تم تحديث سعر الشراء بنجاح', 'cost_price': cost_price})

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('UPDATE products SET cost_price = ? WHERE id = ?', (cost_price, product_id))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': 'تم تحديث سعر الشراء بنجاح', 'cost_price': cost_price})

@app.route('/api/admin/upload-image', methods=['POST'])
@admin_required
def upload_image():
    files_list = []
    if 'images' in request.files:
        files_list = request.files.getlist('images')
    elif 'image' in request.files:
        files_list = request.files.getlist('image')

    if not files_list:
        return jsonify({'error': 'لم يتم اختيار أي ملف صورة'}), 400

    results = []
    for file in files_list:
        if not file or file.filename == '':
            continue
        if allowed_file(file.filename):
            if supabase_db.is_supabase_enabled():
                try:
                    public_url, unique_name = supabase_db.upload_image(file, file.filename)
                    results.append({'url': public_url, 'filename': unique_name})
                except Exception as e:
                    print("Supabase storage upload error:", e)
                    return jsonify({'error': f'فشل رفع الصورة إلى التخزين السحابي: {str(e)}'}), 500
            else:
                try:
                    ext = file.filename.rsplit('.', 1)[1].lower()
                    unique_name = f"abaya_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.{ext}"
                    filepath = os.path.join(UPLOAD_FOLDER, unique_name)
                    file.save(filepath)
                    url = f"/static/uploads/products/{unique_name}"
                    results.append({'url': url, 'filename': unique_name})
                except Exception as e:
                    return jsonify({'error': f'تعذر حفظ الصورة: {str(e)}'}), 500

    if not results:
        return jsonify({'error': 'صيغة الصورة غير مدعومة. يرجى اختيار صورة بصيغة JPG أو PNG أو WEBP'}), 400

    return jsonify({
        'success': True,
        'url': results[0]['url'],
        'filename': results[0]['filename'],
        'urls': [r['url'] for r in results],
        'items': results
    })

@app.route('/api/admin/products', methods=['POST'])
@admin_required
def add_product():
    data = request.json or {}
    name = data.get('name')
    price = data.get('price')
    category = data.get('category')
    image = data.get('image')
    images_raw = data.get('images') or []
    cost_price = data.get('cost_price')

    if not name or not price or not category or (not image and not images_raw):
        return jsonify({'error': 'الاسم، السعر، القسم، وصورة العباية حقول مطلوبة'}), 400

    try:
        price_val = float(price)
        cost_val = float(cost_price) if cost_price is not None and str(cost_price).strip() != '' else round(price_val * 0.6, 2)
    except (ValueError, TypeError):
        return jsonify({'error': 'السعر والتكلفة يجب أن يكونا أرقاماً صالحة'}), 400

    if price_val <= 0 or cost_val < 0:
        return jsonify({'error': 'الأسعار يجب أن تكون أرقاماً موجبة'}), 400

    images_list = []
    if isinstance(images_raw, list):
        images_list = [str(u).strip() for u in images_raw if str(u).strip().startswith(('http://', 'https://', '/static/'))]
    elif isinstance(images_raw, str) and images_raw.strip():
        images_list = [u.strip() for u in images_raw.split(',') if u.strip().startswith(('http://', 'https://', '/static/'))]

    image_str = str(image).strip() if image else ''
    if image_str and image_str not in images_list:
        images_list.insert(0, image_str)

    if not images_list:
        return jsonify({'error': 'صورة العباية غير صالحة. يرجى رفع صورة من جهازك'}), 400

    primary_image = images_list[0]

    if supabase_db.is_supabase_enabled():
        new_prod = {
            'name': html.escape(str(name).strip()[:100]),
            'category': html.escape(str(category).strip()[:50]),
            'price': price_val,
            'old_price': float(data.get('old_price', 0)) if data.get('old_price') else None,
            'image': primary_image[:500],
            'images': images_list,
            'description': html.escape(str(data.get('description', '')).strip()[:1000]),
            'sizes': html.escape(str(data.get('sizes', '52,54,56,58,60')).strip()[:100]),
            'colors': html.escape(str(data.get('colors', 'أسود ملكي,كحلي,بترولي')).strip()[:100]),
            'in_stock': int(data.get('in_stock', 1)),
            'featured': 1 if data.get('featured') else 0,
            'badge': html.escape(str(data.get('badge', '')).strip()[:50]),
            'cost_price': cost_val,
            'merchant_name': html.escape(str(data.get('merchant_name', '')).strip()[:100])
        }
        res = supabase_db.add_product(new_prod)
        return jsonify({'success': True, 'id': res.get('id', 0), 'message': 'تم إضافة الموديل بنجاح'}), 201

    conn = get_db()
    cursor = conn.cursor()
    desc_val = html.escape(str(data.get('description', '')).strip()[:1000])
    if len(images_list) > 1:
        desc_val = f"{desc_val}\n<!--images:{json.dumps(images_list, ensure_ascii=False)}-->".strip()
    merchant = html.escape(str(data.get('merchant_name', '')).strip()[:100])
    if merchant:
        desc_val = f"{desc_val}\n<!--merchant:{merchant}-->".strip()

    cursor.execute('''
    INSERT INTO products (name, category, price, old_price, image, description, sizes, colors, in_stock, featured, badge, cost_price)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        html.escape(str(name).strip()[:100]),
        html.escape(str(category).strip()[:50]),
        price_val,
        float(data.get('old_price', 0)) if data.get('old_price') else None,
        primary_image[:500],
        desc_val,
        html.escape(str(data.get('sizes', '52,54,56,58,60')).strip()[:100]),
        html.escape(str(data.get('colors', 'أسود ملكي,كحلي,بترولي')).strip()[:100]),
        int(data.get('in_stock', 1)),
        1 if data.get('featured') else 0,
        html.escape(str(data.get('badge', '')).strip()[:50]),
        cost_val
    ))
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return jsonify({'success': True, 'id': new_id, 'message': 'تم إضافة الموديل بنجاح'}), 201

@app.route('/api/admin/products/<int:product_id>', methods=['DELETE'])
@admin_required
def delete_product(product_id):
    if supabase_db.is_supabase_enabled():
        supabase_db.delete_product(product_id)
        return jsonify({'success': True, 'message': 'تم حذف الموديل بنجاح'})

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM products WHERE id = ?', (product_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': 'تم حذف الموديل بنجاح'})

@app.route('/api/admin/stats', methods=['GET'])
@admin_required
def get_stats():
    if supabase_db.is_supabase_enabled():
        return jsonify(supabase_db.get_stats())

    conn = get_db()
    cursor = conn.cursor()

    cursor.execute('SELECT id, cost_price, price FROM products')
    prod_costs = {p['id']: float(p['cost_price'] or 0.0) for p in cursor.fetchall()}

    cursor.execute('SELECT COUNT(*) as total_orders FROM orders')
    total_orders = cursor.fetchone()[0]

    cursor.execute('SELECT COUNT(*) FROM orders WHERE status IN ("pending", "قيد الانتظار")')
    pending_orders = cursor.fetchone()[0]

    cursor.execute('SELECT items_json, total_amount FROM orders WHERE status IN ("delivered", "تم التوصيل")')
    delivered_orders = cursor.fetchall()
    delivered_count = len(delivered_orders)

    total_sales = sum(float(ord_row['total_amount'] or 0.0) for ord_row in delivered_orders)
    total_cost = 0.0
    for ord_row in delivered_orders:
        try:
            items = json.loads(ord_row['items_json'])
            for itm in items:
                pid = itm.get('id')
                qty = float(itm.get('quantity', 1))
                unit_cost = prod_costs.get(pid, 0.0)
                total_cost += unit_cost * qty
        except Exception:
            pass

    cursor.execute('SELECT COUNT(*) FROM products')
    total_products = cursor.fetchone()[0]

    conn.close()

    total_sales = round(total_sales, 2)
    net_profit = max(0.0, round(total_sales - total_cost, 2))
    partner_share = round(net_profit / 2.0, 2)
    profit_margin_pct = round((net_profit / total_sales * 100), 1) if total_sales > 0 else 0.0

    return jsonify({
        'total_orders': total_orders,
        'delivered_orders': delivered_count,
        'total_sales': total_sales,
        'total_cost': round(total_cost, 2),
        'net_profit': net_profit,
        'partner_share': partner_share,
        'youssef_share': partner_share,
        'bibo_share': partner_share,
        'partner_1_name': 'يوسف شعبان',
        'partner_2_name': 'بيبو',
        'profit_margin_pct': profit_margin_pct,
        'total_products': total_products,
        'pending_orders': pending_orders
    })

# ----------------- SECURITY HEADERS & ERROR HANDLERS -----------------

@app.after_request
def apply_security_headers(response):
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'microphone=(), geolocation=()'
    response.headers['Server'] = 'DAR-HAREER-SecureServer'
    return response

@app.route('/api/index')
@app.route('/api/index.py')
def vercel_index():
    return render_template('index.html')

@app.errorhandler(404)
def not_found(e):
    if request.path in ('/api/index', '/api/index.py', '/api/index/'):
        return render_template('index.html')
    if request.path.startswith('/api/') and not request.path.startswith('/api/index'):
        return jsonify({'error': 'المورد المطلوب غير موجود'}), 404
    return redirect(url_for('index'))

@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({'error': 'تم تجاوز عدد الطلبات المسموح به مؤقتاً لحماية أمن النظام'}), 429

@app.errorhandler(500)
def server_error(e):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'حدث خطأ غير متوقع في الخادم، تم تسجيل الحادثة'}), 500
    return "عذراً، حدث خطأ مؤقت في الخادم. يرجى المحاولة بعد قليل.", 500

if __name__ == '__main__':
    init_db()
    print("Dar Hareer Abayas Store server running on: http://127.0.0.1:5000")
    app.run(host='0.0.0.0', debug=False, port=5000)
