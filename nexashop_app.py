"""
NexaShop - API REST Flask + PostgreSQL
"""
import hashlib
import os
import json
import base64
import urllib.request
import urllib.parse
import urllib.error
import time
import struct
import hmac
import hashlib as hl
from datetime import datetime
from functools import wraps
import psycopg2
import psycopg2.extras
from flask import Flask, request, jsonify, g, send_from_directory

# ==============================================================================
# CONFIG
# ==============================================================================
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
STATIC       = os.path.join(BASE_DIR, "static")
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:JaXlExHdvvLFEoKxfDWbVTPrhPjbPamc@shortline.proxy.rlwy.net:21560/railway"
)

app = Flask(__name__, static_folder=STATIC, static_url_path="")
app.secret_key = "nexashop_secret_2026"

_db_initialized = False

@app.before_request
def ensure_db():
    global _db_initialized
    if not _db_initialized:
        init_db()
        _db_initialized = True

# ==============================================================================
# TWILIO SMS
# ==============================================================================
TWILIO_SID    = os.environ.get("TWILIO_SID",    "AC5dd3e34db0ca71f9edd2280e64828020")
TWILIO_TOKEN  = os.environ.get("TWILIO_TOKEN",  "d69c28bab445001b6261ddbe0c075d0d")
TWILIO_NUMBER = os.environ.get("TWILIO_NUMBER", "+17405737973")

def send_sms(to_number, message):
    if not to_number or not to_number.startswith("+"):
        print(f"[SMS] Numero invalide : {to_number}")
        return False
    try:
        url  = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json"
        data = urllib.parse.urlencode({
            "From": TWILIO_NUMBER,
            "To":   to_number,
            "Body": message,
        }).encode("utf-8")
        credentials = base64.b64encode(f"{TWILIO_SID}:{TWILIO_TOKEN}".encode()).decode()
        req = urllib.request.Request(url, data=data, headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type":  "application/x-www-form-urlencoded",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
            print(f"[SMS] Envoye a {to_number} - SID: {result.get('sid')}")
            return True
    except Exception as e:
        print(f"[SMS] Erreur : {e}")
        return False

# ==============================================================================
# WAVE CONFIG
# ==============================================================================
NEXASHOP_WAVE_NUMBER = os.environ.get("NEXASHOP_WAVE_NUMBER", "+2250700000000")
SUBSCRIPTION_FEE     = 3000
DELIVERY_FEE         = 2500
FRONTEND_URL         = os.environ.get("FRONTEND_URL", "https://stupendous-axolotl-b342fa.netlify.app")

def make_wave_link(phone_number, amount, description=""):
    clean = phone_number.replace(" ", "").replace("-", "")
    desc  = urllib.parse.quote(description)
    return f"https://pay.wave.com/m/{clean}?currency=XOF&amount={int(amount)}&note={desc}"

# ==============================================================================
# WEB PUSH — Notifications navigateur
# ==============================================================================
# Clés VAPID — générées une seule fois (gardez-les secrètes en production)
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_PUBLIC_KEY  = os.environ.get(
    "VAPID_PUBLIC_KEY",
    "BNexaShopVAPIDPublicKeyPlaceholder_ReplaceWithRealKey"
)
VAPID_SUBJECT     = os.environ.get("VAPID_SUBJECT", "mailto:admin@nexashop.ci")


def send_web_push(subscription_info, payload):
    """
    Envoie une Web Push notification via l'API pywebpush.
    subscription_info: dict avec endpoint, keys.p256dh, keys.auth
    payload: dict {title, body, icon, url, tag}
    """
    try:
        from pywebpush import webpush, WebPushException
        webpush(
            subscription_info=subscription_info,
            data=json.dumps(payload),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims={"sub": VAPID_SUBJECT},
        )
        return True
    except Exception as e:
        print(f"[PUSH] Erreur : {e}")
        return False


def push_to_user(user_id, title, body, url="/", tag="nexashop", icon="/icon-192.png"):
    """Envoie une push notification à toutes les souscriptions d'un utilisateur."""
    subs = q(
        "SELECT * FROM push_subscriptions WHERE user_id=%s AND is_active=1",
        (user_id,)
    )
    payload = {"title": title, "body": body, "url": url, "tag": tag, "icon": icon}
    for sub in subs:
        info = {
            "endpoint": sub["endpoint"],
            "keys": {"p256dh": sub["p256dh"], "auth": sub["auth_key"]}
        }
        ok = send_web_push(info, payload)
        if not ok:
            # Désactiver les souscriptions expirées
            run("UPDATE push_subscriptions SET is_active=0 WHERE id=%s", (sub["id"],))


def push_to_role(role, title, body, url="/", tag="nexashop"):
    """Envoie une push notification à tous les utilisateurs d'un rôle."""
    users = q(
        "SELECT DISTINCT ps.user_id FROM push_subscriptions ps JOIN users u ON u.id=ps.user_id WHERE u.role=%s AND ps.is_active=1",
        (role,)
    )
    for u in users:
        push_to_user(u["user_id"], title, body, url, tag)

# ==============================================================================
# CORS
# ==============================================================================
@app.after_request
def add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"]  = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,DELETE,OPTIONS"
    return resp

@app.route("/api/<path:p>", methods=["OPTIONS"])
def options_handler(p):
    return jsonify({}), 200

# ==============================================================================
# DB HELPERS - PostgreSQL
# ==============================================================================
def get_db():
    if "db" not in g:
        g.db = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        g.db.autocommit = False
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db:
        db.close()

def q(sql, params=(), one=False):
    db  = get_db()
    cur = db.cursor()
    cur.execute(sql, params)
    return cur.fetchone() if one else cur.fetchall()

def run(sql, params=()):
    db  = get_db()
    cur = db.cursor()
    cur.execute(sql, params)
    db.commit()
    return cur

def run_returning(sql, params=()):
    db  = get_db()
    cur = db.cursor()
    cur.execute(sql, params)
    db.commit()
    row = cur.fetchone()
    return row["id"] if row else None

def rows_to_list(rows):
    if not rows:
        return []
    result = []
    for r in rows:
        row = {}
        for k, v in dict(r).items():
            if isinstance(v, datetime):
                row[k] = v.isoformat()
            else:
                row[k] = v
        result.append(row)
    return result

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

# ==============================================================================
# AUTH
# ==============================================================================
def make_token(user_id):
    return base64.b64encode(f"nexashop:{user_id}".encode()).decode()

def decode_token(token):
    try:
        decoded = base64.b64decode(token.encode()).decode()
        _, uid  = decoded.split(":")
        return int(uid)
    except Exception:
        return None

def auth_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        uid   = decode_token(token)
        if not uid:
            return jsonify({"error": "Non autorise"}), 401
        user = q("SELECT * FROM users WHERE id=%s AND is_active=1", (uid,), one=True)
        if not user:
            return jsonify({"error": "Utilisateur introuvable"}), 401
        g.current_user = dict(user)
        return f(*args, **kwargs)
    return wrapper

def seller_required(f):
    @wraps(f)
    @auth_required
    def wrapper(*args, **kwargs):
        if g.current_user["role"] not in ("seller", "admin"):
            return jsonify({"error": "Reserve aux vendeurs"}), 403
        return f(*args, **kwargs)
    return wrapper

# ==============================================================================
# INIT DB - Cree les tables PostgreSQL au demarrage
# ==============================================================================
def init_db():
    db  = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    cur = db.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id         SERIAL PRIMARY KEY,
            name       TEXT NOT NULL,
            email      TEXT NOT NULL UNIQUE,
            password   TEXT NOT NULL,
            role       TEXT NOT NULL CHECK(role IN ('buyer','seller','admin')),
            phone      TEXT,
            avatar     TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            is_active  INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS shops (
            id                SERIAL PRIMARY KEY,
            seller_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name              TEXT NOT NULL,
            description       TEXT,
            logo              TEXT,
            wave_number       TEXT,
            rating            REAL DEFAULT 0,
            total_sales       INTEGER DEFAULT 0,
            subscription_paid INTEGER DEFAULT 0,
            subscription_date TIMESTAMP,
            created_at        TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS categories (
            id    SERIAL PRIMARY KEY,
            name  TEXT NOT NULL UNIQUE,
            emoji TEXT,
            slug  TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS products (
            id           SERIAL PRIMARY KEY,
            shop_id      INTEGER NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
            category_id  INTEGER REFERENCES categories(id),
            name         TEXT NOT NULL,
            description  TEXT,
            price        REAL NOT NULL CHECK(price >= 0),
            old_price    REAL,
            stock        INTEGER DEFAULT 0,
            emoji        TEXT DEFAULT '📦',
            badge        TEXT,
            condition    TEXT DEFAULT 'Neuf',
            rating       REAL DEFAULT 0,
            review_count INTEGER DEFAULT 0,
            is_active    INTEGER DEFAULT 1,
            created_at   TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS orders (
            id           SERIAL PRIMARY KEY,
            buyer_id     INTEGER NOT NULL REFERENCES users(id),
            total_amount REAL NOT NULL,
            status       TEXT NOT NULL DEFAULT 'processing'
                         CHECK(status IN ('pending','processing','shipped','delivered','cancelled')),
            payment_ref  TEXT,
            promo_code   TEXT,
            discount     REAL DEFAULT 0,
            created_at   TIMESTAMP DEFAULT NOW(),
            updated_at   TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS order_items (
            id         SERIAL PRIMARY KEY,
            order_id   INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
            product_id INTEGER NOT NULL REFERENCES products(id),
            shop_id    INTEGER NOT NULL REFERENCES shops(id),
            quantity   INTEGER NOT NULL DEFAULT 1,
            unit_price REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reviews (
            id         SERIAL PRIMARY KEY,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            buyer_id   INTEGER NOT NULL REFERENCES users(id),
            rating     INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
            comment    TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS favorites (
            user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            PRIMARY KEY (user_id, product_id)
        );
        CREATE TABLE IF NOT EXISTS promo_codes (
            id         SERIAL PRIMARY KEY,
            code       TEXT NOT NULL UNIQUE,
            discount   REAL NOT NULL,
            max_uses   INTEGER DEFAULT 100,
            used_count INTEGER DEFAULT 0,
            expires_at TIMESTAMP,
            is_active  INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id         SERIAL PRIMARY KEY,
            user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            endpoint   TEXT NOT NULL UNIQUE,
            p256dh     TEXT NOT NULL,
            auth_key   TEXT NOT NULL,
            is_active  INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)

    # Donnees initiales si vide
    cur.execute("SELECT COUNT(*) as c FROM categories")
    if cur.fetchone()["c"] == 0:
        print("Insertion des donnees initiales...")
        cats = [
            ("Mode & Accessoires","👗","mode"),
            ("Tech & Electronique","💻","tech"),
            ("Maison & Deco","🏠","maison"),
            ("Art & Collection","🎨","art"),
            ("Bio & Sante","🌿","bio"),
            ("Livres & Culture","📚","livres"),
        ]
        cur.executemany(
            "INSERT INTO categories(name,emoji,slug) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING", cats
        )

        # Admin
        admin_pw = hashlib.sha256("admin2026".encode()).hexdigest()
        cur.execute("""
            INSERT INTO users(name,email,password,role)
            VALUES('Admin NexaShop','admin@nexashop.ci',%s,'admin')
            ON CONFLICT DO NOTHING
        """, (admin_pw,))

        # Codes promo
        promos = [("NEXA10",10,200),("BIENVENUE",15,500),("SUMMER25",25,100)]
        cur.executemany(
            "INSERT INTO promo_codes(code,discount,max_uses) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
            promos
        )

        # Vendeurs demo
        seller_pw = hashlib.sha256("mdp123".encode()).hexdigest()
        sellers = [
            ("Marie Dupont","marie@nexashop.fr", seller_pw, "seller", "+2250701000001"),
            ("Tech Pro","tech@nexashop.fr",      seller_pw, "seller", "+2250701000002"),
            ("Galerie Iris","iris@nexashop.fr",  seller_pw, "seller", "+2250701000003"),
            ("Bio Nature","bio@nexashop.fr",     seller_pw, "seller", "+2250701000004"),
        ]
        for s in sellers:
            cur.execute(
                "INSERT INTO users(name,email,password,role,phone) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                s
            )

        # Boutiques demo
        cur.execute("SELECT id FROM users WHERE email='marie@nexashop.fr'")
        u1 = cur.fetchone()
        cur.execute("SELECT id FROM users WHERE email='tech@nexashop.fr'")
        u2 = cur.fetchone()
        cur.execute("SELECT id FROM users WHERE email='iris@nexashop.fr'")
        u3 = cur.fetchone()
        cur.execute("SELECT id FROM users WHERE email='bio@nexashop.fr'")
        u4 = cur.fetchone()

        if u1:
            shops_data = [
                (u1["id"], "L'Atelier Mode",  "Mode & accessoires", "👗", "+2250701000001", 1),
                (u2["id"], "TechPro Store",   "High-tech reconditione", "💻", "+2250701000002", 1),
                (u3["id"], "Galerie Iris",    "Art contemporain", "🎨", "+2250701000003", 1),
                (u4["id"], "Terre & Plantes", "Bio et bien-etre", "🌿", "+2250701000004", 1),
            ]
            shop_ids = []
            for sd in shops_data:
                cur.execute(
                    "INSERT INTO shops(seller_id,name,description,logo,wave_number,subscription_paid) VALUES(%s,%s,%s,%s,%s,%s) RETURNING id",
                    sd
                )
                shop_ids.append(cur.fetchone()["id"])

            # Produits demo
            cur.execute("SELECT id FROM categories WHERE slug='mode'")
            cat_mode = cur.fetchone()["id"]
            cur.execute("SELECT id FROM categories WHERE slug='tech'")
            cat_tech = cur.fetchone()["id"]
            cur.execute("SELECT id FROM categories WHERE slug='art'")
            cat_art = cur.fetchone()["id"]
            cur.execute("SELECT id FROM categories WHERE slug='bio'")
            cat_bio = cur.fetchone()["id"]
            cur.execute("SELECT id FROM categories WHERE slug='maison'")
            cat_maison = cur.fetchone()["id"]

            products = [
                (shop_ids[0], cat_mode,  "Veste en cuir vintage",       "Veste cuir veritable, coupe slim.", 84900,  125000, 8,  "🧥", "Promo",    4.8, 42),
                (shop_ids[0], cat_mode,  "Sneakers edition limitee",     "Coloris exclusif, pointure 42.",  138000, 183000, 3,  "👟", "Featured", 4.8, 67),
                (shop_ids[0], cat_mode,  "Montre minimaliste doree",     "Boitier acier 36mm, quartz.",     115000, 157000, 12, "⌚", "Featured", 4.8, 93),
                (shop_ids[1], cat_tech,  "MacBook Air M2 reconditionne", "Grade A+, 8Go RAM, 256Go SSD.",   589000, 851000, 5,  "💻", "Verifie",  4.9, 88),
                (shop_ids[1], cat_tech,  "Casque audio sans fil",        "ANC, 30h autonomie, BT 5.2.",      97500, 144000, 15, "🎧", "Promo",    4.5, 201),
                (shop_ids[2], cat_art,   "Peinture abstraite originale", "Acrylique 60x80cm. Signee.",      210000,   None, 2,  "🎨", None,       5.0, 9),
                (shop_ids[2], cat_art,   "Carnet artisanal cuir",        "Reliure main, 200 pages, A5.",     29500,   None, 20, "📒", "New",      4.9, 34),
                (shop_ids[2], cat_art,   "Roman illustre collector",     "Edition limitee 1/500.",           25500,   None, 7,  "📚", None,       4.9, 72),
                (shop_ids[3], cat_bio,   "Huile de soin bio certifiee",  "Argan + rose musquee, 50ml.",      22500,  27500, 50, "🌿", "Promo",    4.6, 156),
                (shop_ids[3], cat_bio,   "Plante succulente rare",       "Echeveria, pot ceramique.",        18500,   None, 10, "🪴", None,       4.7, 45),
                (shop_ids[0], cat_maison,"Lampe artisanale en rotin",    "Tressage main, ampoule E27.",      44500,   None, 6,  "💡", "New",      4.7, 23),
                (shop_ids[0], cat_maison,"Miroir en bois flotte",        "Cadre naturel, diametre 60cm.",    55500,  72000, 4,  "🪞", "Promo",    4.6, 18),
            ]
            cur.executemany("""
                INSERT INTO products(shop_id,category_id,name,description,price,old_price,stock,emoji,badge,rating,review_count)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, products)

    db.commit()
    cur.close()
    db.close()
    print("PostgreSQL - Base de donnees prete!")

# ==============================================================================
# CATEGORIES
# ==============================================================================
@app.route("/api/categories", methods=["GET"])
def get_categories():
    cats = q("""
        SELECT c.*,
               (SELECT COUNT(*) FROM products WHERE category_id=c.id AND is_active=1) as product_count
        FROM categories c
    """)
    return jsonify(rows_to_list(cats))

# ==============================================================================
# PRODUITS
# ==============================================================================
@app.route("/api/products", methods=["GET"])
def get_products():
    cat    = request.args.get("category")
    search = request.args.get("search", "")
    min_p  = request.args.get("min_price", type=float)
    max_p  = request.args.get("max_price", type=float)
    sort   = request.args.get("sort", "newest")
    page   = request.args.get("page", 1, type=int)
    limit  = request.args.get("limit", 12, type=int)
    offset = (page - 1) * limit

    sql    = """
        SELECT p.*, s.name as shop_name, c.name as category_name, c.emoji as cat_emoji
        FROM products p
        JOIN shops s ON s.id = p.shop_id
        LEFT JOIN categories c ON c.id = p.category_id
        WHERE p.is_active = 1
    """
    params = []

    if cat:
        sql += " AND c.slug = %s"
        params.append(cat)
    if search:
        sql += " AND (p.name ILIKE %s OR p.description ILIKE %s OR s.name ILIKE %s)"
        params += [f"%{search}%"] * 3
    if min_p is not None:
        sql += " AND p.price >= %s"
        params.append(min_p)
    if max_p is not None:
        sql += " AND p.price <= %s"
        params.append(max_p)

    sort_map = {
        "newest":     "p.created_at DESC",
        "price_asc":  "p.price ASC",
        "price_desc": "p.price DESC",
        "rating":     "p.rating DESC",
    }
    sql += f" ORDER BY {sort_map.get(sort, 'p.created_at DESC')}"
    sql += " LIMIT %s OFFSET %s"
    params += [limit, offset]

    products = rows_to_list(q(sql, params))
    total    = q("""
        SELECT COUNT(*) as c FROM products p
        JOIN shops s ON s.id=p.shop_id
        LEFT JOIN categories c ON c.id=p.category_id
        WHERE p.is_active=1
    """, (), one=True)["c"]

    return jsonify({"products": products, "total": total, "page": page, "limit": limit})


@app.route("/api/products/<int:pid>", methods=["GET"])
def get_product(pid):
    p = q("""
        SELECT p.*, s.name as shop_name, c.name as category_name, c.emoji as cat_emoji
        FROM products p
        JOIN shops s ON s.id=p.shop_id
        LEFT JOIN categories c ON c.id=p.category_id
        WHERE p.id=%s AND p.is_active=1
    """, (pid,), one=True)
    if not p:
        return jsonify({"error": "Produit introuvable"}), 404
    reviews = rows_to_list(q("""
        SELECT r.*, u.name as buyer_name FROM reviews r
        JOIN users u ON u.id=r.buyer_id
        WHERE r.product_id=%s ORDER BY r.created_at DESC LIMIT 10
    """, (pid,)))
    return jsonify({"product": dict(p), "reviews": reviews})


@app.route("/api/products", methods=["POST"])
@seller_required
def create_product():
    d    = request.json or {}
    shop = q("SELECT id FROM shops WHERE seller_id=%s", (g.current_user["id"],), one=True)
    if not shop:
        return jsonify({"error": "Boutique introuvable"}), 404
    if not all(d.get(k) for k in ["name","price","stock"]):
        return jsonify({"error": "Champs requis: name, price, stock"}), 400
    pid = run_returning("""
        INSERT INTO products(shop_id,category_id,name,description,price,old_price,stock,emoji,badge,condition)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
    """, (
        shop["id"], d.get("category_id"), d["name"], d.get("description",""),
        float(d["price"]), float(d["old_price"]) if d.get("old_price") else None,
        int(d["stock"]), d.get("emoji","📦"), d.get("badge"), d.get("condition","Neuf")
    ))
    return jsonify({"id": pid, "message": "Produit cree"}), 201


@app.route("/api/products/<int:pid>", methods=["PUT"])
@seller_required
def update_product(pid):
    d    = request.json or {}
    shop = q("SELECT id FROM shops WHERE seller_id=%s", (g.current_user["id"],), one=True)
    prod = q("SELECT * FROM products WHERE id=%s AND shop_id=%s", (pid, shop["id"]), one=True)
    if not prod:
        return jsonify({"error": "Produit introuvable"}), 404
    allowed    = ("name","description","price","old_price","stock","emoji","badge","condition","is_active")
    fields     = {k: d[k] for k in allowed if k in d}
    if not fields:
        return jsonify({"error": "Aucun champ a modifier"}), 400
    set_clause = ", ".join(f"{k}=%s" for k in fields)
    run(f"UPDATE products SET {set_clause} WHERE id=%s", (*fields.values(), pid))
    return jsonify({"message": "Produit mis a jour"})


@app.route("/api/products/<int:pid>", methods=["DELETE"])
@seller_required
def delete_product(pid):
    shop = q("SELECT id FROM shops WHERE seller_id=%s", (g.current_user["id"],), one=True)
    run("UPDATE products SET is_active=0 WHERE id=%s AND shop_id=%s", (pid, shop["id"]))
    return jsonify({"message": "Produit supprime"})

# ==============================================================================
# AUTH ENDPOINTS
# ==============================================================================
@app.route("/api/auth/register", methods=["POST"])
def register():
    d     = request.json or {}
    name  = d.get("name","").strip()
    email = d.get("email","").strip().lower()
    pw    = d.get("password","")
    role  = d.get("role","buyer")
    phone = d.get("phone","").strip()

    if not all([name, email, pw]):
        return jsonify({"error": "Champs requis manquants"}), 400
    if role not in ("buyer","seller"):
        return jsonify({"error": "Role invalide"}), 400
    if q("SELECT id FROM users WHERE email=%s", (email,), one=True):
        return jsonify({"error": "Email deja utilise"}), 409

    uid = run_returning(
        "INSERT INTO users(name,email,password,role,phone) VALUES(%s,%s,%s,%s,%s) RETURNING id",
        (name, email, hash_pw(pw), role, phone or None)
    )
    if role == "seller":
        run("INSERT INTO shops(seller_id,name,description) VALUES(%s,%s,%s)",
            (uid, f"Boutique de {name}", "Ma nouvelle boutique NexaShop"))

    user = dict(q("SELECT id,name,email,role,phone,created_at FROM users WHERE id=%s", (uid,), one=True))
    user["created_at"] = str(user.get("created_at",""))
    return jsonify({"token": make_token(uid), "user": user}), 201


@app.route("/api/auth/login", methods=["POST"])
def login():
    d     = request.json or {}
    email = d.get("email","").strip().lower()
    pw    = d.get("password","")
    user  = q("SELECT * FROM users WHERE email=%s AND is_active=1", (email,), one=True)
    if not user or user["password"] != hash_pw(pw):
        return jsonify({"error": "Email ou mot de passe incorrect"}), 401
    u = {k: str(user[k]) if isinstance(user[k], datetime) else user[k]
         for k in ("id","name","email","role","created_at")}
    return jsonify({"token": make_token(user["id"]), "user": u})


@app.route("/api/auth/me", methods=["GET"])
@auth_required
def me():
    u    = g.current_user
    shop = None
    if u["role"] == "seller":
        shop = q("SELECT * FROM shops WHERE seller_id=%s", (u["id"],), one=True)
        shop = rows_to_list([shop])[0] if shop else None
    return jsonify({"user": {k: u[k] for k in ("id","name","email","role")}, "shop": shop})

# ==============================================================================
# COMMANDES
# ==============================================================================
@app.route("/api/orders", methods=["GET"])
@auth_required
def get_orders():
    u = g.current_user
    if u["role"] == "buyer":
        orders = q("""
            SELECT o.*, COUNT(oi.id) as item_count
            FROM orders o LEFT JOIN order_items oi ON oi.order_id=o.id
            WHERE o.buyer_id=%s GROUP BY o.id ORDER BY o.created_at DESC
        """, (u["id"],))
    elif u["role"] == "seller":
        shop = q("SELECT id FROM shops WHERE seller_id=%s", (u["id"],), one=True)
        orders = q("""
            SELECT DISTINCT o.*, u.name as buyer_name,
                   STRING_AGG(p.name, ', ') as product_names,
                   SUM(oi.quantity * oi.unit_price) as subtotal
            FROM orders o
            JOIN order_items oi ON oi.order_id=o.id
            JOIN products p ON p.id=oi.product_id
            JOIN users u ON u.id=o.buyer_id
            WHERE oi.shop_id=%s
            GROUP BY o.id, u.name ORDER BY o.created_at DESC
        """, (shop["id"],))
    else:
        orders = q("SELECT * FROM orders ORDER BY created_at DESC LIMIT 100")
    return jsonify(rows_to_list(orders))


@app.route("/api/orders/<int:oid>/status", methods=["PUT"])
@seller_required
def update_order_status(oid):
    status = (request.json or {}).get("status")
    if status not in ("processing","shipped","delivered","cancelled"):
        return jsonify({"error": "Statut invalide"}), 400
    run("UPDATE orders SET status=%s, updated_at=NOW() WHERE id=%s", (status, oid))
    # Push au client selon le statut
    order = q("SELECT * FROM orders WHERE id=%s", (oid,), one=True)
    if order:
        if status == "shipped":
            push_to_user(order["buyer_id"],
                "🚚 Commande expédiée !",
                f"Votre commande #{oid} est en route !",
                url="/", tag=f"shipped-{oid}"
            )
        elif status == "delivered":
            push_to_user(order["buyer_id"],
                "📬 Commande livrée !",
                f"Votre commande #{oid} a été livrée. Merci !",
                url="/", tag=f"delivered-{oid}"
            )
        elif status == "cancelled":
            push_to_user(order["buyer_id"],
                "❌ Commande annulée",
                f"Votre commande #{oid} a été annulée.",
                url="/", tag=f"cancelled-{oid}"
            )
    return jsonify({"message": "Statut mis a jour"})

# ==============================================================================
# AVIS
# ==============================================================================
@app.route("/api/products/<int:pid>/reviews", methods=["POST"])
@auth_required
def add_review(pid):
    d      = request.json or {}
    rating = d.get("rating")
    if not rating or not (1 <= int(rating) <= 5):
        return jsonify({"error": "Note entre 1 et 5 requise"}), 400
    if q("SELECT id FROM reviews WHERE product_id=%s AND buyer_id=%s", (pid, g.current_user["id"]), one=True):
        return jsonify({"error": "Vous avez deja note ce produit"}), 409
    run("INSERT INTO reviews(product_id,buyer_id,rating,comment) VALUES(%s,%s,%s,%s)",
        (pid, g.current_user["id"], int(rating), d.get("comment","")))
    avg = q("SELECT AVG(rating) as avg, COUNT(*) as cnt FROM reviews WHERE product_id=%s", (pid,), one=True)
    run("UPDATE products SET rating=%s, review_count=%s WHERE id=%s",
        (round(float(avg["avg"]),1), avg["cnt"], pid))
    return jsonify({"message": "Avis ajoute"}), 201

# ==============================================================================
# FAVORIS
# ==============================================================================
@app.route("/api/favorites", methods=["GET"])
@auth_required
def get_favorites():
    favs = q("""
        SELECT p.*, s.name as shop_name FROM favorites f
        JOIN products p ON p.id=f.product_id
        JOIN shops s ON s.id=p.shop_id
        WHERE f.user_id=%s
    """, (g.current_user["id"],))
    return jsonify(rows_to_list(favs))


@app.route("/api/favorites/<int:pid>", methods=["POST"])
@auth_required
def toggle_favorite(pid):
    uid = g.current_user["id"]
    if q("SELECT 1 FROM favorites WHERE user_id=%s AND product_id=%s", (uid, pid), one=True):
        run("DELETE FROM favorites WHERE user_id=%s AND product_id=%s", (uid, pid))
        return jsonify({"liked": False})
    run("INSERT INTO favorites(user_id,product_id) VALUES(%s,%s) ON CONFLICT DO NOTHING", (uid, pid))
    return jsonify({"liked": True})

# ==============================================================================
# DASHBOARD VENDEUR
# ==============================================================================
@app.route("/api/dashboard", methods=["GET"])
@seller_required
def dashboard():
    uid  = g.current_user["id"]
    shop = q("SELECT * FROM shops WHERE seller_id=%s", (uid,), one=True)
    if not shop:
        return jsonify({"error": "Boutique introuvable"}), 404
    sid  = shop["id"]

    revenue = float(q("""
        SELECT COALESCE(SUM(oi.quantity * oi.unit_price), 0) as rev
        FROM order_items oi JOIN orders o ON o.id=oi.order_id
        WHERE oi.shop_id=%s AND o.status != 'cancelled'
        AND DATE_TRUNC('month', o.created_at) = DATE_TRUNC('month', NOW())
    """, (sid,), one=True)["rev"])

    prev_revenue = float(q("""
        SELECT COALESCE(SUM(oi.quantity * oi.unit_price), 0) as rev
        FROM order_items oi JOIN orders o ON o.id=oi.order_id
        WHERE oi.shop_id=%s AND o.status != 'cancelled'
        AND DATE_TRUNC('month', o.created_at) = DATE_TRUNC('month', NOW() - INTERVAL '1 month')
    """, (sid,), one=True)["rev"])

    orders_count    = q("""
        SELECT COUNT(DISTINCT o.id) as cnt FROM orders o
        JOIN order_items oi ON oi.order_id=o.id
        WHERE oi.shop_id=%s
        AND DATE_TRUNC('month', o.created_at) = DATE_TRUNC('month', NOW())
    """, (sid,), one=True)["cnt"]

    active_products = q("SELECT COUNT(*) as cnt FROM products WHERE shop_id=%s AND is_active=1", (sid,), one=True)["cnt"]
    low_stock       = q("SELECT COUNT(*) as cnt FROM products WHERE shop_id=%s AND stock <= 3 AND is_active=1", (sid,), one=True)["cnt"]
    avg_rating      = q("""
        SELECT COALESCE(AVG(r.rating),0) as avg, COUNT(r.id) as cnt
        FROM reviews r JOIN products p ON p.id=r.product_id WHERE p.shop_id=%s
    """, (sid,), one=True)

    daily = rows_to_list(q("""
        SELECT DATE(o.created_at) as day,
               COALESCE(SUM(oi.quantity * oi.unit_price), 0) as revenue,
               COUNT(DISTINCT o.id) as orders
        FROM orders o JOIN order_items oi ON oi.order_id=o.id
        WHERE oi.shop_id=%s AND o.created_at >= NOW() - INTERVAL '7 days'
        GROUP BY DATE(o.created_at) ORDER BY day
    """, (sid,)))

    return jsonify({
        "shop": rows_to_list([shop])[0],
        "kpi": {
            "revenue_month":   round(revenue, 2),
            "revenue_prev":    round(prev_revenue, 2),
            "orders_month":    orders_count,
            "active_products": active_products,
            "low_stock":       low_stock,
            "avg_rating":      round(float(avg_rating["avg"]), 1),
            "review_count":    avg_rating["cnt"],
            "total_sales":     shop["total_sales"],
        },
        "daily_stats": daily,
    })


@app.route("/api/dashboard/products", methods=["GET"])
@seller_required
def dashboard_products():
    shop  = q("SELECT id FROM shops WHERE seller_id=%s", (g.current_user["id"],), one=True)
    prods = rows_to_list(q("""
        SELECT p.*, c.name as category_name,
               COALESCE(SUM(oi.quantity),0) as total_sold
        FROM products p
        LEFT JOIN categories c ON c.id=p.category_id
        LEFT JOIN order_items oi ON oi.product_id=p.id
        WHERE p.shop_id=%s
        GROUP BY p.id, c.name ORDER BY p.created_at DESC
    """, (shop["id"],)))
    return jsonify(prods)

# ==============================================================================
# PROMO
# ==============================================================================
@app.route("/api/promo/check", methods=["POST"])
def check_promo():
    code = (request.json or {}).get("code","").upper()
    pc   = q("SELECT * FROM promo_codes WHERE code=%s AND is_active=1", (code,), one=True)
    if not pc:
        return jsonify({"valid": False, "error": "Code invalide"})
    if pc["expires_at"] and pc["expires_at"] < datetime.now():
        return jsonify({"valid": False, "error": "Code expire"})
    if pc["used_count"] >= pc["max_uses"]:
        return jsonify({"valid": False, "error": "Code epuise"})
    return jsonify({"valid": True, "discount": pc["discount"], "code": code})

# ==============================================================================
# WAVE - Paiement direct client vers vendeur
# ==============================================================================
@app.route("/api/payment/wave/checkout", methods=["POST"])
@auth_required
def wave_checkout():
    d     = request.json or {}
    items = d.get("items", [])
    promo = d.get("promo_code")
    if not items:
        return jsonify({"error": "Panier vide"}), 400

    by_shop   = {}
    total_all = 0

    for item in items:
        prod = q("""
            SELECT p.*, s.name as shop_name, s.wave_number, s.subscription_paid
            FROM products p JOIN shops s ON s.id=p.shop_id
            WHERE p.id=%s AND p.is_active=1
        """, (item["product_id"],), one=True)

        if not prod:
            return jsonify({"error": f"Produit {item['product_id']} introuvable"}), 404
        if prod["stock"] < item["quantity"]:
            return jsonify({"error": f"Stock insuffisant pour {prod['name']}"}), 400
        if not prod["subscription_paid"]:
            return jsonify({"error": f"La boutique {prod['shop_name']} n'est pas active"}), 403
        if not prod["wave_number"]:
            return jsonify({"error": f"La boutique {prod['shop_name']} n'a pas configure son Wave"}), 400

        sid      = prod["shop_id"]
        subtotal = prod["price"] * item["quantity"]
        total_all += subtotal

        if sid not in by_shop:
            by_shop[sid] = {"shop_id": sid, "shop_name": prod["shop_name"],
                            "wave_number": prod["wave_number"], "items": [], "subtotal": 0}
        by_shop[sid]["items"].append({**dict(prod), "quantity": item["quantity"]})
        by_shop[sid]["subtotal"] += subtotal

    discount = 0
    if promo:
        pc = q("SELECT * FROM promo_codes WHERE code=%s AND is_active=1", (promo.upper(),), one=True)
        if pc:
            discount  = round(total_all * pc["discount"] / 100)
            total_all -= discount

    total_all = int(round(total_all)) + DELIVERY_FEE
    order_id  = run_returning(
        "INSERT INTO orders(buyer_id,total_amount,discount,promo_code,status) VALUES(%s,%s,%s,%s,'pending') RETURNING id",
        (g.current_user["id"], total_all, discount, promo)
    )
    for sid, sd in by_shop.items():
        for item in sd["items"]:
            run("INSERT INTO order_items(order_id,product_id,shop_id,quantity,unit_price) VALUES(%s,%s,%s,%s,%s)",
                (order_id, item["id"], sid, item["quantity"], item["price"]))

    wave_links = []
    for sid, sd in by_shop.items():
        desc     = f"NexaShop commande #{order_id} - {sd['shop_name']}"
        wave_url = make_wave_link(sd["wave_number"], sd["subtotal"], desc)
        wave_links.append({
            "shop_name":   sd["shop_name"],
            "wave_number": sd["wave_number"],
            "amount":      sd["subtotal"],
            "wave_url":    wave_url,
            "items_count": sum(i["quantity"] for i in sd["items"]),
        })

    return jsonify({"order_id": order_id, "total": total_all, "discount": discount,
                    "delivery": DELIVERY_FEE, "wave_links": wave_links})


@app.route("/api/payment/wave/confirm/<int:order_id>", methods=["POST"])
@auth_required
def wave_confirm(order_id):
    order = q("SELECT * FROM orders WHERE id=%s AND buyer_id=%s",
              (order_id, g.current_user["id"]), one=True)
    if not order:
        return jsonify({"error": "Commande introuvable"}), 404
    if order["status"] != "pending":
        return jsonify({"error": "Commande deja traitee"}), 400

    run("UPDATE orders SET status='processing', updated_at=NOW() WHERE id=%s", (order_id,))
    items = q("SELECT * FROM order_items WHERE order_id=%s", (order_id,))
    for item in items:
        run("UPDATE products SET stock=stock-%s WHERE id=%s", (item["quantity"], item["product_id"]))
        run("UPDATE shops SET total_sales=total_sales+%s WHERE id=%s", (item["quantity"], item["shop_id"]))

    if order["promo_code"]:
        run("UPDATE promo_codes SET used_count=used_count+1 WHERE code=%s", (order["promo_code"],))

    shops_in_order = q("""
        SELECT DISTINCT s.id, s.name, s.wave_number, s.seller_id
        FROM order_items oi JOIN shops s ON s.id=oi.shop_id
        WHERE oi.order_id=%s
    """, (order_id,))

    for shop in shops_in_order:
        shop_total = q("""
            SELECT COALESCE(SUM(oi.quantity * oi.unit_price),0) as tot
            FROM order_items oi WHERE oi.order_id=%s AND oi.shop_id=%s
        """, (order_id, shop["id"]), one=True)["tot"]

        # SMS vendeur
        if shop["wave_number"] and shop["wave_number"].startswith("+"):
            send_sms(shop["wave_number"],
                f"NexaShop - Nouvelle commande!\n"
                f"Commande #{order_id}\n"
                f"Client: {g.current_user['name']}\n"
                f"Montant: {int(shop_total)} FCFA\n"
                f"Paiement Wave recu. Preparez la livraison!"
            )
        # Push navigateur vendeur
        push_to_user(
            shop["seller_id"],
            "🛍️ Nouvelle commande !",
            f"Commande #{order_id} \u2014 {int(shop_total):,} FCFA de {g.current_user['name']}".replace(",", " "),
            url="/?page=dashboard",
            tag=f"order-{order_id}"
        )

    # Push admin
    push_to_role("admin",
        "📦 Nouvelle commande sur NexaShop",
        f"Commande #{order_id} \u2014 {int(order['total_amount']):,} FCFA".replace(",", " "),
        url="/?page=admin",
        tag=f"admin-order-{order_id}"
    )

    # SMS + Push client
    buyer = q("SELECT phone FROM users WHERE id=%s", (g.current_user["id"],), one=True)
    if buyer and buyer["phone"]:
        send_sms(buyer["phone"],
            f"NexaShop - Commande confirmee!\n"
            f"Commande #{order_id}\n"
            f"Total: {int(order['total_amount'])} FCFA\n"
            f"Merci pour votre achat!"
        )
    push_to_user(
        g.current_user["id"],
        "✅ Commande confirmée !",
        f"Votre commande #{order_id} a bien été reçue.",
        url="/", tag=f"order-confirm-{order_id}"
    )

    return jsonify({"message": f"Commande #{order_id} confirmee!", "order_id": order_id})


@app.route("/api/payment/wave/subscription", methods=["GET"])
@auth_required
def get_subscription_wave_link():
    if g.current_user["role"] != "seller":
        return jsonify({"error": "Reserve aux vendeurs"}), 403
    shop = q("SELECT * FROM shops WHERE seller_id=%s", (g.current_user["id"],), one=True)
    if not shop:
        return jsonify({"error": "Boutique introuvable"}), 404
    if shop["subscription_paid"]:
        return jsonify({"already_paid": True, "message": "Abonnement deja actif"})
    desc     = f"Abonnement NexaShop - {g.current_user['name']}"
    wave_url = make_wave_link(NEXASHOP_WAVE_NUMBER, SUBSCRIPTION_FEE, desc)
    return jsonify({"wave_url": wave_url, "amount": SUBSCRIPTION_FEE, "wave_number": NEXASHOP_WAVE_NUMBER})


@app.route("/api/payment/wave/subscription/confirm", methods=["POST"])
@auth_required
def confirm_subscription():
    if g.current_user["role"] != "seller":
        return jsonify({"error": "Reserve aux vendeurs"}), 403
    shop = q("SELECT * FROM shops WHERE seller_id=%s", (g.current_user["id"],), one=True)
    run("UPDATE shops SET subscription_paid=1, subscription_date=NOW() WHERE seller_id=%s",
        (g.current_user["id"],))
    # SMS vendeur
    if shop and shop["wave_number"] and shop["wave_number"].startswith("+"):
        send_sms(shop["wave_number"],
            f"NexaShop - Boutique activee!\n"
            f"Bonjour {g.current_user['name']},\n"
            f"Votre boutique est maintenant active.\n"
            f"Publiez vos produits sur NexaShop!"
        )
    # Push vendeur
    push_to_user(g.current_user["id"],
        "🏪 Boutique activée !",
        "Votre boutique est maintenant visible. Publiez vos premiers produits !",
        url="/", tag="subscription"
    )
    # Push admin — nouveau vendeur actif
    push_to_role("admin",
        "🎉 Nouveau vendeur actif !",
        f"{g.current_user['name']} vient d'activer sa boutique \"{shop['name']}\".",
        url="/", tag="new-vendor"
    )
    return jsonify({"message": "Abonnement active!", "shop_id": shop["id"]})


@app.route("/api/shops/<int:shop_id>/wave", methods=["PUT"])
@seller_required
def update_wave_number(shop_id):
    wave_number = (request.json or {}).get("wave_number","").strip()
    if not wave_number:
        return jsonify({"error": "Numero Wave requis"}), 400
    run("UPDATE shops SET wave_number=%s WHERE id=%s AND seller_id=%s",
        (wave_number, shop_id, g.current_user["id"]))
    return jsonify({"message": "Numero Wave mis a jour"})

# ==============================================================================
# PUSH SUBSCRIPTIONS — Enregistrement et gestion
# ==============================================================================
@app.route("/api/push/vapid-public-key", methods=["GET"])
def get_vapid_public_key():
    return jsonify({"public_key": VAPID_PUBLIC_KEY})


@app.route("/api/push/subscribe", methods=["POST"])
@auth_required
def push_subscribe():
    d        = request.json or {}
    endpoint = d.get("endpoint")
    p256dh   = d.get("keys", {}).get("p256dh")
    auth_key = d.get("keys", {}).get("auth")
    if not all([endpoint, p256dh, auth_key]):
        return jsonify({"error": "Donnees de souscription incompletes"}), 400
    existing = q("SELECT id FROM push_subscriptions WHERE endpoint=%s", (endpoint,), one=True)
    if existing:
        run("UPDATE push_subscriptions SET user_id=%s, p256dh=%s, auth_key=%s, is_active=1 WHERE endpoint=%s",
            (g.current_user["id"], p256dh, auth_key, endpoint))
    else:
        run("INSERT INTO push_subscriptions(user_id,endpoint,p256dh,auth_key) VALUES(%s,%s,%s,%s)",
            (g.current_user["id"], endpoint, p256dh, auth_key))
    return jsonify({"message": "Souscription enregistree"})


@app.route("/api/push/unsubscribe", methods=["POST"])
@auth_required
def push_unsubscribe():
    endpoint = (request.json or {}).get("endpoint")
    if endpoint:
        run("UPDATE push_subscriptions SET is_active=0 WHERE endpoint=%s AND user_id=%s",
            (endpoint, g.current_user["id"]))
    return jsonify({"message": "Souscription supprimee"})

# ==============================================================================
# ADMIN — Gestion complète vendeurs, commandes, utilisateurs, produits
# ==============================================================================
@app.route("/api/admin/stats", methods=["GET"])
@auth_required
def admin_stats():
    if g.current_user["role"] != "admin":
        return jsonify({"error": "Reserve a l'admin"}), 403
    return jsonify({
        "users":    q("SELECT COUNT(*) as c FROM users", (), one=True)["c"],
        "sellers":  q("SELECT COUNT(*) as c FROM users WHERE role='seller'", (), one=True)["c"],
        "buyers":   q("SELECT COUNT(*) as c FROM users WHERE role='buyer'", (), one=True)["c"],
        "products": q("SELECT COUNT(*) as c FROM products WHERE is_active=1", (), one=True)["c"],
        "orders":   q("SELECT COUNT(*) as c FROM orders", (), one=True)["c"],
        "revenue":  round(float(q("SELECT COALESCE(SUM(total_amount),0) as s FROM orders WHERE status!='cancelled'",(),one=True)["s"]),2),
    })


@app.route("/api/admin/vendors", methods=["GET"])
@auth_required
def admin_vendors():
    if g.current_user["role"] != "admin":
        return jsonify({"error": "Reserve a l'admin"}), 403
    vendors = rows_to_list(q("""
        SELECT u.id as user_id, u.name, u.email, u.phone, u.is_active,
               s.id as shop_id, s.name as shop_name, s.wave_number,
               s.subscription_paid, s.subscription_date, s.total_sales,
               (SELECT COUNT(*) FROM products WHERE shop_id=s.id AND is_active=1) as product_count
        FROM users u
        LEFT JOIN shops s ON s.seller_id=u.id
        WHERE u.role='seller'
        ORDER BY u.created_at DESC
    """))
    return jsonify({"vendors": vendors})


@app.route("/api/admin/orders", methods=["GET"])
@auth_required
def admin_orders():
    if g.current_user["role"] != "admin":
        return jsonify({"error": "Reserve a l'admin"}), 403
    orders = rows_to_list(q("""
        SELECT o.*, u.name as buyer_name, u.email as buyer_email
        FROM orders o JOIN users u ON u.id=o.buyer_id
        ORDER BY o.created_at DESC
        LIMIT 200
    """))
    return jsonify({"orders": orders})


@app.route("/api/admin/users", methods=["GET"])
@auth_required
def admin_users():
    if g.current_user["role"] != "admin":
        return jsonify({"error": "Reserve a l'admin"}), 403
    users = rows_to_list(q("""
        SELECT id, name, email, phone, role, is_active, created_at
        FROM users ORDER BY created_at DESC
    """))
    return jsonify({"users": users})


@app.route("/api/admin/products", methods=["GET"])
@auth_required
def admin_products():
    if g.current_user["role"] != "admin":
        return jsonify({"error": "Reserve a l'admin"}), 403
    products = rows_to_list(q("""
        SELECT p.*, s.name as shop_name, c.name as category_name
        FROM products p
        JOIN shops s ON s.id=p.shop_id
        LEFT JOIN categories c ON c.id=p.category_id
        ORDER BY p.created_at DESC
    """))
    return jsonify({"products": products})


@app.route("/api/admin/shops/<int:shop_id>/activate", methods=["PUT"])
@auth_required
def admin_activate_shop(shop_id):
    if g.current_user["role"] != "admin":
        return jsonify({"error": "Reserve a l'admin"}), 403
    run("UPDATE shops SET subscription_paid=1, subscription_date=NOW() WHERE id=%s", (shop_id,))
    # SMS au vendeur
    shop = q("SELECT s.*, u.name as seller_name FROM shops s JOIN users u ON u.id=s.seller_id WHERE s.id=%s", (shop_id,), one=True)
    if shop and shop["wave_number"] and shop["wave_number"].startswith("+"):
        send_sms(shop["wave_number"],
            f"NexaShop - Boutique activee par l'admin!\n"
            f"Bonjour {shop['seller_name']},\n"
            f"Votre boutique \"{shop['name']}\" est maintenant active.\n"
            f"Vous pouvez publier vos produits!"
        )
    return jsonify({"message": "Boutique activee"})


@app.route("/api/admin/shops/<int:shop_id>/deactivate", methods=["PUT"])
@auth_required
def admin_deactivate_shop(shop_id):
    if g.current_user["role"] != "admin":
        return jsonify({"error": "Reserve a l'admin"}), 403
    run("UPDATE shops SET subscription_paid=0 WHERE id=%s", (shop_id,))
    return jsonify({"message": "Boutique desactivee"})


@app.route("/api/admin/users/<int:user_id>/toggle", methods=["PUT"])
@auth_required
def admin_toggle_user(user_id):
    if g.current_user["role"] != "admin":
        return jsonify({"error": "Reserve a l'admin"}), 403
    user = q("SELECT is_active FROM users WHERE id=%s", (user_id,), one=True)
    if not user:
        return jsonify({"error": "Utilisateur introuvable"}), 404
    new_status = 0 if user["is_active"] else 1
    run("UPDATE users SET is_active=%s WHERE id=%s", (new_status, user_id))
    return jsonify({"message": "Statut mis a jour", "is_active": new_status})


@app.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
@auth_required
def admin_delete_user(user_id):
    if g.current_user["role"] != "admin":
        return jsonify({"error": "Reserve a l'admin"}), 403
    if user_id == g.current_user["id"]:
        return jsonify({"error": "Impossible de supprimer votre propre compte"}), 400
    run("DELETE FROM users WHERE id=%s", (user_id,))
    return jsonify({"message": "Utilisateur supprime"})


@app.route("/api/admin/products/<int:product_id>/toggle", methods=["PUT"])
@auth_required
def admin_toggle_product(product_id):
    if g.current_user["role"] != "admin":
        return jsonify({"error": "Reserve a l'admin"}), 403
    prod = q("SELECT is_active FROM products WHERE id=%s", (product_id,), one=True)
    if not prod:
        return jsonify({"error": "Produit introuvable"}), 404
    new_status = 0 if prod["is_active"] else 1
    run("UPDATE products SET is_active=%s WHERE id=%s", (new_status, product_id))
    return jsonify({"message": "Statut mis a jour", "is_active": new_status})

# ==============================================================================
# FRONTEND + HEALTH
# ==============================================================================
@app.route("/")
def index():
    return send_from_directory(STATIC, "index.html")

@app.route("/health")
def health():
    return jsonify({"status": "ok", "db": "postgresql", "time": datetime.now().isoformat()})

# ==============================================================================
# DEMARRAGE
# ==============================================================================
if __name__ == "__main__":
    os.makedirs(STATIC, exist_ok=True)
    init_db()
    port  = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV") != "production"
    print(f"NexaShop API (PostgreSQL) sur http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=debug)
