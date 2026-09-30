import os
import json
import uuid
from datetime import datetime

SUPABASE_URL = os.environ.get('SUPABASE_URL', 'https://ouzxjghpplbplacycxrd.supabase.co')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im91enhqZ2hwcGxicGxhY3ljeHJkIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTAyNTcyMjgsImV4cCI6MjEwNTgzMzIyOH0.KrbxWa2WuTrI-m_U3VFgU7XNwQ9PuVkY5lCIyE1OoKA')

_client = None

def get_client():
    global _client
    if _client is not None:
        return _client
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            from supabase import create_client
            _client = create_client(SUPABASE_URL, SUPABASE_KEY)
            return _client
        except Exception as e:
            print("Failed to initialize Supabase client:", e)
            return None
    return None

def is_supabase_enabled():
    return get_client() is not None

# ----------------- CATEGORIES -----------------
def get_categories():
    client = get_client()
    res = client.table('categories').select('*').order('id', desc=False).execute()
    return res.data or []

def add_category(name, slug=None, icon='tag'):
    client = get_client()
    slug_val = slug or f"cat_{uuid.uuid4().hex[:6]}"
    res = client.table('categories').insert({'name': name, 'slug': slug_val, 'icon': icon}).execute()
    return res.data[0] if res.data else {}

def delete_category(category_id):
    client = get_client()
    client.table('categories').delete().eq('id', category_id).execute()
    return True

# ----------------- PRODUCTS -----------------
def get_products(category=None, search=None, sort_by='newest', featured_only=None, is_admin=False):
    import re
    client = get_client()
    query = client.table('products').select('*').eq('in_stock', 1)
    
    if category and category != 'all':
        query = query.eq('category', category)
    if featured_only == '1':
        query = query.eq('featured', 1)
    if search:
        query = query.ilike('name', f"%{search}%")

    if sort_by == 'price_low':
        query = query.order('price', desc=False)
    elif sort_by == 'price_high':
        query = query.order('price', desc=True)
    elif sort_by == 'rating':
        query = query.order('rating', desc=True)
    else:
        query = query.order('id', desc=True)

    rows = query.execute().data or []
    products = []
    for r in rows:
        p = dict(r)
        if not is_admin:
            p.pop('cost_price', None)
            if p.get('description'):
                p['description'] = re.sub(r'<!--merchant:.*?-->', '', p['description']).strip()
        products.append(p)
    return products

def get_product(product_id, is_admin=False):
    import re
    client = get_client()
    res = client.table('products').select('*').eq('id', product_id).execute()
    if res.data:
        p = dict(res.data[0])
        if not is_admin:
            p.pop('cost_price', None)
            if p.get('description'):
                p['description'] = re.sub(r'<!--merchant:.*?-->', '', p['description']).strip()
        return p
    return None

def add_product(data):
    client = get_client()
    merchant = data.pop('merchant_name', None) or data.pop('merchant', None)
    if merchant:
        desc = data.get('description') or ''
        data['description'] = f"{desc}\n<!--merchant:{merchant}-->".strip()
    res = client.table('products').insert(data).execute()
    return res.data[0] if res.data else {}

def delete_product(product_id):
    client = get_client()
    client.table('products').delete().eq('id', product_id).execute()
    return True

def update_product_cost(product_id, cost_price):
    client = get_client()
    client.table('products').update({'cost_price': cost_price}).eq('id', product_id).execute()
    return True

def get_admin_products():
    import re
    client = get_client()
    rows = client.table('products').select('*').order('id', desc=False).execute().data or []
    products = []
    for row in rows:
        p = dict(row)
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

        merchant = p.get('merchant_name') or ''
        if not merchant and p.get('description'):
            m = re.search(r'<!--merchant:(.*?)-->', p['description'])
            if m:
                merchant = m.group(1).strip()
                p['description'] = re.sub(r'<!--merchant:.*?-->', '', p['description']).strip()
        p['merchant_name'] = merchant

        products.append(p)
    return products

# ----------------- ORDERS -----------------
def create_order(order_data):
    client = get_client()
    res = client.table('orders').insert(order_data).execute()
    return res.data[0] if res.data else {}

def get_admin_orders():
    client = get_client()
    prods = client.table('products').select('id, cost_price, price').execute().data or []
    costs_map = {p['id']: float(p.get('cost_price') or 0.0) for p in prods}
    
    rows = client.table('orders').select('*').order('id', desc=True).execute().data or []
    orders = []
    for row in rows:
        order_dict = dict(row)
        try:
            items = json.loads(order_dict['items_json']) if isinstance(order_dict['items_json'], str) else (order_dict['items_json'] or [])
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
    return orders

def update_order_status(order_id, status):
    client = get_client()
    client.table('orders').update({'status': status}).eq('id', order_id).execute()
    return True

# ----------------- STATS -----------------
def get_stats():
    client = get_client()
    prods = client.table('products').select('id, cost_price, price').execute().data or []
    prod_costs = {p['id']: float(p.get('cost_price') or 0.0) for p in prods}

    orders = client.table('orders').select('*').execute().data or []
    total_orders = len(orders)
    total_sales = sum(float(o.get('total_amount') or 0.0) for o in orders)
    total_cost = 0.0
    pending_orders = 0

    for o in orders:
        if o.get('status') == 'pending':
            pending_orders += 1
        try:
            items = json.loads(o['items_json']) if isinstance(o['items_json'], str) else (o['items_json'] or [])
            for itm in items:
                pid = itm.get('id')
                qty = float(itm.get('quantity', 1))
                unit_cost = prod_costs.get(pid, 0.0)
                total_cost += unit_cost * qty
        except Exception:
            pass

    net_profit = max(0.0, round(total_sales - total_cost, 2))
    partner_share = round(net_profit / 2.0, 2)
    profit_margin_pct = round((net_profit / total_sales * 100), 1) if total_sales > 0 else 0.0

    return {
        'total_orders': total_orders,
        'total_sales': round(total_sales, 2),
        'total_cost': round(total_cost, 2),
        'net_profit': net_profit,
        'partner_share': partner_share,
        'youssef_share': partner_share,
        'bibo_share': partner_share,
        'partner_1_name': 'يوسف شعبان',
        'partner_2_name': 'بيبو',
        'profit_margin_pct': profit_margin_pct,
        'total_products': len(prods),
        'pending_orders': pending_orders
    }

# ----------------- STORAGE UPLOAD -----------------
def upload_image(file_obj, filename):
    client = get_client()
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else 'jpg'
    unique_name = f"abaya_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.{ext}"
    
    mime_map = {
        'jpg': 'image/jpeg',
        'jpeg': 'image/jpeg',
        'png': 'image/png',
        'webp': 'image/webp',
        'avif': 'image/avif',
        'heic': 'image/heic'
    }
    content_type = mime_map.get(ext, 'image/jpeg')
    file_obj.seek(0)
    file_bytes = file_obj.read()
    client.storage.from_('products').upload(unique_name, file_bytes, file_options={'content-type': content_type})
    public_url = client.storage.from_('products').get_public_url(unique_name)
    return public_url, unique_name

# ----------------- SETTINGS, DISCOUNTS & REVIEWS -----------------
DEFAULT_STORE_SETTINGS = {
    'whatsapp': '01132647659',
    'phone': '01132647658',
    'facebook': 'https://www.facebook.com/share/1KEkt1NZbg/',
    'tiktok': 'https://www.tiktok.com/@youssef.shas?_r=1&_t=ZS-9AAMIOqltSL',
    'free_shipping_enabled': True,
    'free_shipping_threshold': 1000,
    'free_shipping_text': 'شحن مجاني لكافة المحافظات للطلبات فوق 1,000 ج.م 🚚✨'
}

def get_setting(key, default=None):
    client = get_client()
    try:
        res = client.table('settings').select('val').eq('key', key).execute()
        if res.data and len(res.data) > 0:
            val = res.data[0]['val']
            try:
                return json.loads(val)
            except Exception:
                return val
    except Exception as e:
        print(f"Error reading setting {key}:", e)
    return default

def set_setting(key, val):
    client = get_client()
    val_str = json.dumps(val, ensure_ascii=False) if not isinstance(val, str) else val
    client.table('settings').upsert({'key': key, 'val': val_str}).execute()
    return True

def get_store_settings():
    settings = get_setting('store_settings', DEFAULT_STORE_SETTINGS)
    if not isinstance(settings, dict):
        settings = DEFAULT_STORE_SETTINGS
    merged = dict(DEFAULT_STORE_SETTINGS)
    merged.update(settings)
    return merged

def save_store_settings(new_settings):
    current = get_store_settings()
    current.update(new_settings)
    set_setting('store_settings', current)
    return current

def get_discounts():
    discounts = get_setting('discounts', [])
    if not isinstance(discounts, list):
        discounts = []
    return discounts

def save_discounts(discounts_list):
    set_setting('discounts', discounts_list)
    return True

def validate_discount_code(code_str, subtotal):
    if not code_str:
        return {'valid': False, 'message': 'يرجى إدخال كود الخصم'}
    code_clean = str(code_str).strip().upper()
    discounts = get_discounts()
    match = None
    for d in discounts:
        if d.get('code', '').upper() == code_clean:
            match = d
            break
    if not match:
        return {'valid': False, 'message': 'كود الخصم غير موجود أو غير صالح'}
    if not match.get('is_active', True):
        return {'valid': False, 'message': 'عذراً، تم إيقاف هذا الكود حالياً'}
    
    max_uses = int(match.get('max_uses', 0))
    used_count = int(match.get('used_count', 0))
    if max_uses > 0 and used_count >= max_uses:
        return {'valid': False, 'message': 'عذراً، هذا الكود وصل للحد الأقصى من الاستخدام (انتهى العرض)'}

    val = float(match.get('value', 0))
    dtype = match.get('type', 'percent')
    if dtype == 'percent':
        discount_amount = round(subtotal * (val / 100.0), 2)
    else:
        discount_amount = min(subtotal, val)

    remaining_uses = max(0, max_uses - used_count) if max_uses > 0 else 999
    return {
        'valid': True,
        'code': match.get('code'),
        'type': dtype,
        'value': val,
        'discount_amount': discount_amount,
        'new_total': max(0.0, round(subtotal - discount_amount, 2)),
        'remaining_uses': remaining_uses,
        'max_uses': max_uses,
        'message': f'تم تفعيل كود الخصم بنجاح! خصم {val}{"%" if dtype == "percent" else " ج.م"}'
    }

def record_discount_usage(code_str):
    if not code_str:
        return
    code_clean = str(code_str).strip().upper()
    discounts = get_discounts()
    changed = False
    for d in discounts:
        if d.get('code', '').upper() == code_clean:
            d['used_count'] = int(d.get('used_count', 0)) + 1
            changed = True
            break
    if changed:
        save_discounts(discounts)

def get_reviews():
    reviews = get_setting('reviews', [])
    if not isinstance(reviews, list):
        reviews = []
    for r in reviews:
        if 'customer_name' not in r or not r['customer_name']:
            r['customer_name'] = r.get('name', 'عميلة دار حرير')
        if 'name' not in r or not r['name']:
            r['name'] = r.get('customer_name', 'عميلة دار حرير')
    return reviews

def add_review(review_dict):
    reviews = get_reviews()
    reviews.insert(0, review_dict)
    set_setting('reviews', reviews)
    return review_dict

def delete_review(review_id):
    reviews = get_reviews()
    reviews = [r for r in reviews if str(r.get('id')) != str(review_id)]
    set_setting('reviews', reviews)
    return True
