"""
NexaShop — API REST Flask + SQLite
Endpoints couvrant : auth, produits, commandes, panier, avis, favoris, dashboard vendeur
"""
import sqlite3
import hashlib
import os
import json
from datetime import datetime
from functools import wraps
from flask import Flask, request, jsonify, g, send_from_directory

# --- Config -------------------------------------------------------------------
BASE_DIR = os.path.dirname(__file__)
DB_PATH  = os.path.join(BASE_DIR, "nexashop.db")
STATIC   = os.path.join(BASE_DIR, "static")

app = Flask(__name__, static_folder=STATIC, static_url_path="")
app.secret_key = "nexashop_secret_2026"

# --- CORS manuel (pas besoin de flask-cors) -----------------------------------
@app.after_request
def add_cors(resp):
    resp.headers["Access-Control-Allow-Origin"]  = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,PUT,DELETE,OPTIONS"
    return resp

@app.route("/api/<path:p>", methods=["OPTIONS"])
def options_handler(p):
    return jsonify({}), 200

# --- DB helpers ---------------------------------------------------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db: db.close()

def q(sql, params=(), one=False):
    cur = get_db().execute(sql, params)
    return cur.fetchone() if one else cur.fetchall()

def run(sql, params=()):
    db = get_db()
    cur = db.execute(sql, params)
    db.commit()
    return cur

def rows_to_list(rows):
    return [dict(r) for r in rows]

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

# --- Auth simple par token (user_id encodé en base64 pour la démo) ------------
import base64

def make_token(user_id):
    return base64.b64encode(f"nexashop:{user_id}".encode()).decode()

def decode_token(token):
    try:
        decoded = base64.b64decode(token.encode()).decode()
        _, uid = decoded.split(":")
        return int(uid)
    except Exception:
        return None

def auth_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        uid = decode_token(token)
        if not uid:
            return jsonify({"error": "Non autorisé"}), 401
        user = q("SELECT * FROM users WHERE id=? AND is_active=1", (uid,), one=True)
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
            return jsonify({"error": "Réservé aux vendeurs"}), 403
        return f(*args, **kwargs)
    return wrapper

# ==============================================================================
# AUTH
# ==============================================================================

@app.route("/api/auth/register", methods=["POST"])
def register():
    d = request.json or {}
    name  = d.get("name", "").strip()
    email = d.get("email", "").strip().lower()
    pw    = d.get("password", "")
    role  = d.get("role", "buyer")

    if not all([name, email, pw]):
        return jsonify({"error": "Champs requis manquants"}), 400
    if role not in ("buyer", "seller"):
        return jsonify({"error": "Rôle invalide"}), 400

    existing = q("SELECT id FROM users WHERE email=?", (email,), one=True)
    if existing:
        return jsonify({"error": "Email déjà utilisé"}), 409

    cur = run("INSERT INTO users(name,email,password,role) VALUES(?,?,?,?)",
              (name, email, hash_pw(pw), role))
    uid = cur.lastrowid

    if role == "seller":
        run("INSERT INTO shops(seller_id,name,description) VALUES(?,?,?)",
            (uid, f"Boutique de {name}", "Ma nouvelle boutique NexaShop"))

    user = dict(q("SELECT id,name,email,role,created_at FROM users WHERE id=?", (uid,), one=True))
    return jsonify({"token": make_token(uid), "user": user}), 201


@app.route("/api/auth/login", methods=["POST"])
def login():
    d     = request.json or {}
    email = d.get("email", "").strip().lower()
    pw    = d.get("password", "")
    user  = q("SELECT * FROM users WHERE email=? AND is_active=1", (email,), one=True)

    if not user or user["password"] != hash_pw(pw):
        return jsonify({"error": "Email ou mot de passe incorrect"}), 401

    u = {k: user[k] for k in ("id","name","email","role","created_at")}
    return jsonify({"token": make_token(user["id"]), "user": u})


@app.route("/api/auth/me", methods=["GET"])
@auth_required
def me():
    u = g.current_user
    shop = None
    if u["role"] == "seller":
        shop = q("SELECT * FROM shops WHERE seller_id=?", (u["id"],), one=True)
        shop = dict(shop) if shop else None
    return jsonify({"user": {k: u[k] for k in ("id","name","email","role")}, "shop": shop})

# ==============================================================================
# CATÉGORIES
# ==============================================================================

@app.route("/api/categories", methods=["GET"])
def get_categories():
    cats = q("SELECT *, (SELECT COUNT(*) FROM products WHERE category_id=categories.id AND is_active=1) as product_count FROM categories")
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

    sql = """
        SELECT p.*, s.name as shop_name, c.name as category_name, c.emoji as cat_emoji
        FROM products p
        JOIN shops s ON s.id = p.shop_id
        LEFT JOIN categories c ON c.id = p.category_id
        WHERE p.is_active = 1
    """
    params = []

    if cat:
        sql += " AND c.slug = ?"
        params.append(cat)
    if search:
        sql += " AND (p.name LIKE ? OR p.description LIKE ? OR s.name LIKE ?)"
        params += [f"%{search}%"] * 3
    if min_p is not None:
        sql += " AND p.price >= ?"
        params.append(min_p)
    if max_p is not None:
        sql += " AND p.price <= ?"
        params.append(max_p)

    sort_map = {
        "newest":     "p.created_at DESC",
        "price_asc":  "p.price ASC",
        "price_desc": "p.price DESC",
        "rating":     "p.rating DESC",
    }
    sql += f" ORDER BY {sort_map.get(sort, 'p.created_at DESC')}"
    sql += " LIMIT ? OFFSET ?"
    params += [limit, offset]

    products = rows_to_list(q(sql, params))

    # Total count
    count_sql = "SELECT COUNT(*) FROM products p JOIN shops s ON s.id=p.shop_id LEFT JOIN categories c ON c.id=p.category_id WHERE p.is_active=1"
    total = q(count_sql, [])[0][0]

    return jsonify({"products": products, "total": total, "page": page, "limit": limit})


@app.route("/api/products/<int:pid>", methods=["GET"])
def get_product(pid):
    p = q("""
        SELECT p.*, s.name as shop_name, s.id as shop_id,
               c.name as category_name, c.emoji as cat_emoji
        FROM products p
        JOIN shops s ON s.id=p.shop_id
        LEFT JOIN categories c ON c.id=p.category_id
        WHERE p.id=? AND p.is_active=1
    """, (pid,), one=True)
    if not p:
        return jsonify({"error": "Produit introuvable"}), 404

    reviews = rows_to_list(q("""
        SELECT r.*, u.name as buyer_name
        FROM reviews r JOIN users u ON u.id=r.buyer_id
        WHERE r.product_id=? ORDER BY r.created_at DESC LIMIT 10
    """, (pid,)))

    return jsonify({"product": dict(p), "reviews": reviews})


@app.route("/api/products", methods=["POST"])
@seller_required
def create_product():
    d = request.json or {}
    shop = q("SELECT id FROM shops WHERE seller_id=?", (g.current_user["id"],), one=True)
    if not shop:
        return jsonify({"error": "Aucune boutique trouvée"}), 404

    required = ["name", "price", "stock"]
    if not all(d.get(k) for k in required):
        return jsonify({"error": "Champs requis: name, price, stock"}), 400

    cur = run("""
        INSERT INTO products(shop_id,category_id,name,description,price,old_price,stock,emoji,badge,condition)
        VALUES(?,?,?,?,?,?,?,?,?,?)
    """, (
        shop["id"],
        d.get("category_id"),
        d["name"],
        d.get("description", ""),
        float(d["price"]),
        float(d["old_price"]) if d.get("old_price") else None,
        int(d["stock"]),
        d.get("emoji", "📦"),
        d.get("badge"),
        d.get("condition", "Neuf"),
    ))
    return jsonify({"id": cur.lastrowid, "message": "Produit créé"}), 201


@app.route("/api/products/<int:pid>", methods=["PUT"])
@seller_required
def update_product(pid):
    d    = request.json or {}
    shop = q("SELECT id FROM shops WHERE seller_id=?", (g.current_user["id"],), one=True)
    prod = q("SELECT * FROM products WHERE id=? AND shop_id=?", (pid, shop["id"]), one=True)
    if not prod:
        return jsonify({"error": "Produit introuvable ou non autorisé"}), 404

    fields = {k: d[k] for k in ("name","description","price","old_price","stock","emoji","badge","condition","is_active") if k in d}
    if not fields:
        return jsonify({"error": "Aucun champ à modifier"}), 400

    set_clause = ", ".join(f"{k}=?" for k in fields)
    run(f"UPDATE products SET {set_clause} WHERE id=?", (*fields.values(), pid))
    return jsonify({"message": "Produit mis à jour"})


@app.route("/api/products/<int:pid>", methods=["DELETE"])
@seller_required
def delete_product(pid):
    shop = q("SELECT id FROM shops WHERE seller_id=?", (g.current_user["id"],), one=True)
    run("UPDATE products SET is_active=0 WHERE id=? AND shop_id=?", (pid, shop["id"]))
    return jsonify({"message": "Produit supprimé"})

# ==============================================================================
# COMMANDES
# ==============================================================================

@app.route("/api/orders", methods=["POST"])
@auth_required
def create_order():
    d     = request.json or {}
    items = d.get("items", [])   # [{product_id, quantity}]
    promo = d.get("promo_code")

    if not items:
        return jsonify({"error": "Panier vide"}), 400

    # Vérifier stock et calculer total
    total = 0
    enriched = []
    for item in items:
        prod = q("SELECT * FROM products WHERE id=? AND is_active=1", (item["product_id"],), one=True)
        if not prod:
            return jsonify({"error": f"Produit {item['product_id']} introuvable"}), 404
        if prod["stock"] < item["quantity"]:
            return jsonify({"error": f"Stock insuffisant pour {prod['name']}"}), 400
        enriched.append({**dict(prod), "quantity": item["quantity"]})
        total += prod["price"] * item["quantity"]

    total += 4.9  # frais de livraison
    discount = 0

    # Code promo
    if promo:
        pc = q("SELECT * FROM promo_codes WHERE code=? AND is_active=1", (promo.upper(),), one=True)
        if pc and (not pc["expires_at"] or pc["expires_at"] > datetime.now().isoformat()):
            discount = round(total * pc["discount"] / 100, 2)
            total = round(total - discount, 2)
            run("UPDATE promo_codes SET used_count=used_count+1 WHERE id=?", (pc["id"],))

    # Créer la commande
    cur = run("INSERT INTO orders(buyer_id,total_amount,discount,promo_code) VALUES(?,?,?,?)",
              (g.current_user["id"], total, discount, promo))
    order_id = cur.lastrowid

    # Insérer les lignes + décrémenter stock
    for item in enriched:
        run("INSERT INTO order_items(order_id,product_id,shop_id,quantity,unit_price) VALUES(?,?,?,?,?)",
            (order_id, item["id"], item["shop_id"], item["quantity"], item["price"]))
        run("UPDATE products SET stock=stock-? WHERE id=?", (item["quantity"], item["id"]))
        run("UPDATE shops SET total_sales=total_sales+? WHERE id=?", (item["quantity"], item["shop_id"]))

    return jsonify({"order_id": order_id, "total": total, "discount": discount, "message": "Commande créée !"}), 201


@app.route("/api/orders", methods=["GET"])
@auth_required
def get_orders():
    u = g.current_user
    if u["role"] == "buyer":
        orders = q("""
            SELECT o.*, COUNT(oi.id) as item_count
            FROM orders o LEFT JOIN order_items oi ON oi.order_id=o.id
            WHERE o.buyer_id=?
            GROUP BY o.id ORDER BY o.created_at DESC
        """, (u["id"],))
    elif u["role"] == "seller":
        shop = q("SELECT id FROM shops WHERE seller_id=?", (u["id"],), one=True)
        orders = q("""
            SELECT DISTINCT o.*, u.name as buyer_name,
                   GROUP_CONCAT(p.name, ', ') as product_names,
                   SUM(oi.quantity * oi.unit_price) as subtotal
            FROM orders o
            JOIN order_items oi ON oi.order_id=o.id
            JOIN products p ON p.id=oi.product_id
            JOIN users u ON u.id=o.buyer_id
            WHERE oi.shop_id=?
            GROUP BY o.id ORDER BY o.created_at DESC
        """, (shop["id"],))
    else:
        orders = q("SELECT * FROM orders ORDER BY created_at DESC LIMIT 100")

    return jsonify(rows_to_list(orders))


@app.route("/api/orders/<int:oid>", methods=["GET"])
@auth_required
def get_order(oid):
    order = q("SELECT * FROM orders WHERE id=?", (oid,), one=True)
    if not order:
        return jsonify({"error": "Commande introuvable"}), 404

    items = rows_to_list(q("""
        SELECT oi.*, p.name as product_name, p.emoji, s.name as shop_name
        FROM order_items oi
        JOIN products p ON p.id=oi.product_id
        JOIN shops s ON s.id=oi.shop_id
        WHERE oi.order_id=?
    """, (oid,)))

    return jsonify({"order": dict(order), "items": items})


@app.route("/api/orders/<int:oid>/status", methods=["PUT"])
@seller_required
def update_order_status(oid):
    status = (request.json or {}).get("status")
    if status not in ("processing","shipped","delivered","cancelled"):
        return jsonify({"error": "Statut invalide"}), 400
    run("UPDATE orders SET status=?, updated_at=datetime('now') WHERE id=?", (status, oid))
    return jsonify({"message": "Statut mis à jour"})

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

    existing = q("SELECT id FROM reviews WHERE product_id=? AND buyer_id=?",
                 (pid, g.current_user["id"]), one=True)
    if existing:
        return jsonify({"error": "Vous avez déjà noté ce produit"}), 409

    run("INSERT INTO reviews(product_id,buyer_id,rating,comment) VALUES(?,?,?,?)",
        (pid, g.current_user["id"], int(rating), d.get("comment","")))

    # Recalculer la moyenne
    avg = q("SELECT AVG(rating), COUNT(*) FROM reviews WHERE product_id=?", (pid,), one=True)
    run("UPDATE products SET rating=?, review_count=? WHERE id=?",
        (round(avg[0], 1), avg[1], pid))

    return jsonify({"message": "Avis ajouté"}), 201

# ==============================================================================
# FAVORIS
# ==============================================================================

@app.route("/api/favorites", methods=["GET"])
@auth_required
def get_favorites():
    favs = q("""
        SELECT p.*, s.name as shop_name
        FROM favorites f
        JOIN products p ON p.id=f.product_id
        JOIN shops s ON s.id=p.shop_id
        WHERE f.user_id=?
    """, (g.current_user["id"],))
    return jsonify(rows_to_list(favs))


@app.route("/api/favorites/<int:pid>", methods=["POST"])
@auth_required
def toggle_favorite(pid):
    uid = g.current_user["id"]
    existing = q("SELECT 1 FROM favorites WHERE user_id=? AND product_id=?", (uid, pid), one=True)
    if existing:
        run("DELETE FROM favorites WHERE user_id=? AND product_id=?", (uid, pid))
        return jsonify({"liked": False})
    else:
        run("INSERT OR IGNORE INTO favorites(user_id,product_id) VALUES(?,?)", (uid, pid))
        return jsonify({"liked": True})

# ==============================================================================
# DASHBOARD VENDEUR
# ==============================================================================

@app.route("/api/dashboard", methods=["GET"])
@seller_required
def dashboard():
    uid  = g.current_user["id"]
    shop = q("SELECT * FROM shops WHERE seller_id=?", (uid,), one=True)
    if not shop:
        return jsonify({"error": "Boutique introuvable"}), 404
    sid = shop["id"]

    # Revenus du mois
    revenue = q("""
        SELECT COALESCE(SUM(oi.quantity * oi.unit_price), 0)
        FROM order_items oi JOIN orders o ON o.id=oi.order_id
        WHERE oi.shop_id=? AND o.status != 'cancelled'
        AND strftime('%Y-%m', o.created_at) = strftime('%Y-%m', 'now')
    """, (sid,), one=True)[0]

    # Revenus mois précédent
    prev_revenue = q("""
        SELECT COALESCE(SUM(oi.quantity * oi.unit_price), 0)
        FROM order_items oi JOIN orders o ON o.id=oi.order_id
        WHERE oi.shop_id=? AND o.status != 'cancelled'
        AND strftime('%Y-%m', o.created_at) = strftime('%Y-%m', datetime('now','-1 month'))
    """, (sid,), one=True)[0]

    # Nombre commandes du mois
    orders_count = q("""
        SELECT COUNT(DISTINCT o.id)
        FROM orders o JOIN order_items oi ON oi.order_id=o.id
        WHERE oi.shop_id=? AND strftime('%Y-%m', o.created_at) = strftime('%Y-%m', 'now')
    """, (sid,), one=True)[0]

    # Produits actifs
    active_products = q("SELECT COUNT(*) FROM products WHERE shop_id=? AND is_active=1", (sid,), one=True)[0]
    low_stock       = q("SELECT COUNT(*) FROM products WHERE shop_id=? AND stock <= 3 AND is_active=1", (sid,), one=True)[0]

    # Note moyenne boutique
    avg_rating = q("""
        SELECT COALESCE(AVG(r.rating), 0), COUNT(r.id)
        FROM reviews r JOIN products p ON p.id=r.product_id
        WHERE p.shop_id=?
    """, (sid,), one=True)

    # Évolution revenus (7 derniers jours)
    daily = rows_to_list(q("""
        SELECT DATE(o.created_at) as day,
               COALESCE(SUM(oi.quantity * oi.unit_price), 0) as revenue,
               COUNT(DISTINCT o.id) as orders
        FROM orders o JOIN order_items oi ON oi.order_id=o.id
        WHERE oi.shop_id=? AND o.created_at >= datetime('now','-7 days')
        GROUP BY DATE(o.created_at)
        ORDER BY day
    """, (sid,)))

    return jsonify({
        "shop": dict(shop),
        "kpi": {
            "revenue_month":    round(revenue, 2),
            "revenue_prev":     round(prev_revenue, 2),
            "orders_month":     orders_count,
            "active_products":  active_products,
            "low_stock":        low_stock,
            "avg_rating":       round(avg_rating[0], 1),
            "review_count":     avg_rating[1],
            "total_sales":      shop["total_sales"],
        },
        "daily_stats": daily,
    })


@app.route("/api/dashboard/products", methods=["GET"])
@seller_required
def dashboard_products():
    shop = q("SELECT id FROM shops WHERE seller_id=?", (g.current_user["id"],), one=True)
    prods = rows_to_list(q("""
        SELECT p.*, c.name as category_name,
               COALESCE(SUM(oi.quantity),0) as total_sold
        FROM products p
        LEFT JOIN categories c ON c.id=p.category_id
        LEFT JOIN order_items oi ON oi.product_id=p.id
        WHERE p.shop_id=?
        GROUP BY p.id
        ORDER BY p.created_at DESC
    """, (shop["id"],)))
    return jsonify(prods)


# ==============================================================================
# CODES PROMO
# ==============================================================================

@app.route("/api/promo/check", methods=["POST"])
def check_promo():
    code = (request.json or {}).get("code", "").upper()
    pc = q("SELECT * FROM promo_codes WHERE code=? AND is_active=1", (code,), one=True)
    if not pc:
        return jsonify({"valid": False, "error": "Code invalide"})
    if pc["expires_at"] and pc["expires_at"] < datetime.now().isoformat():
        return jsonify({"valid": False, "error": "Code expiré"})
    if pc["used_count"] >= pc["max_uses"]:
        return jsonify({"valid": False, "error": "Code épuisé"})
    return jsonify({"valid": True, "discount": pc["discount"], "code": code})


# ==============================================================================
# WAVE — Paiement direct client vers vendeur
# ==============================================================================

NEXASHOP_WAVE_NUMBER  = os.environ.get("NEXASHOP_WAVE_NUMBER", "+2250700000000")  # Votre numéro Wave
SUBSCRIPTION_FEE      = 3000   # FCFA — abonnement vendeur
DELIVERY_FEE          = 2500   # FCFA — frais de livraison


def make_wave_link(phone_number, amount, description=""):
    """
    Génère un lien Wave universel qui ouvre l'app Wave avec
    le numéro et le montant pré-remplis.
    Format : https://pay.wave.com/m/NUMERO?amount=MONTANT
    """
    # Nettoyer le numéro : garder uniquement les chiffres + +
    clean = phone_number.replace(" ", "").replace("-", "")
    # Encoder la description
    import urllib.parse
    desc_enc = urllib.parse.quote(description)
    return f"https://pay.wave.com/m/{clean}?currency=XOF&amount={int(amount)}&note={desc_enc}"


@app.route("/api/payment/wave/checkout", methods=["POST"])
@auth_required
def wave_checkout():
    """
    Prépare le paiement Wave pour un panier.
    Si le panier contient des produits de plusieurs vendeurs,
    retourne un lien Wave par vendeur.
    Body: { items: [{product_id, quantity}], promo_code? }
    """
    d     = request.json or {}
    items = d.get("items", [])
    promo = d.get("promo_code")

    if not items:
        return jsonify({"error": "Panier vide"}), 400

    # Vérifier stock et regrouper par vendeur
    by_shop   = {}   # shop_id -> {shop, items, subtotal}
    total_all = 0

    for item in items:
        prod = q("""
            SELECT p.*, s.name as shop_name, s.wave_number, s.subscription_paid
            FROM products p JOIN shops s ON s.id=p.shop_id
            WHERE p.id=? AND p.is_active=1
        """, (item["product_id"],), one=True)

        if not prod:
            return jsonify({"error": f"Produit {item['product_id']} introuvable"}), 404
        if prod["stock"] < item["quantity"]:
            return jsonify({"error": f"Stock insuffisant pour {prod['name']}"}), 400
        if not prod["subscription_paid"]:
            return jsonify({"error": f"La boutique {prod['shop_name']} n'est pas active"}), 403
        if not prod["wave_number"]:
            return jsonify({"error": f"La boutique {prod['shop_name']} n'a pas configuré son Wave"}), 400

        sid      = prod["shop_id"]
        subtotal = prod["price"] * item["quantity"]
        total_all += subtotal

        if sid not in by_shop:
            by_shop[sid] = {
                "shop_id":     sid,
                "shop_name":   prod["shop_name"],
                "wave_number": prod["wave_number"],
                "items":       [],
                "subtotal":    0,
            }
        by_shop[sid]["items"].append({**dict(prod), "quantity": item["quantity"]})
        by_shop[sid]["subtotal"] += subtotal

    # Appliquer code promo sur le total
    discount = 0
    if promo:
        pc = q("SELECT * FROM promo_codes WHERE code=? AND is_active=1", (promo.upper(),), one=True)
        if pc and (not pc["expires_at"] or pc["expires_at"] > datetime.now().isoformat()):
            discount = round(total_all * pc["discount"] / 100)
            total_all -= discount

    total_all = int(round(total_all)) + DELIVERY_FEE

    # Créer la commande globale en BDD (statut pending_wave)
    cur = run(
        "INSERT INTO orders(buyer_id,total_amount,discount,promo_code,status) VALUES(?,?,?,?,'pending')",
        (g.current_user["id"], total_all, discount, promo)
    )
    order_id = cur.lastrowid

    for sid, shop_data in by_shop.items():
        for item in shop_data["items"]:
            run(
                "INSERT INTO order_items(order_id,product_id,shop_id,quantity,unit_price) VALUES(?,?,?,?,?)",
                (order_id, item["id"], sid, item["quantity"], item["price"])
            )

    # Générer les liens Wave (un par boutique)
    wave_links = []
    for sid, shop_data in by_shop.items():
        desc      = f"NexaShop commande #{order_id} - {shop_data['shop_name']}"
        wave_url  = make_wave_link(shop_data["wave_number"], shop_data["subtotal"], desc)
        wave_links.append({
            "shop_name":   shop_data["shop_name"],
            "wave_number": shop_data["wave_number"],
            "amount":      shop_data["subtotal"],
            "wave_url":    wave_url,
            "items_count": sum(i["quantity"] for i in shop_data["items"]),
        })

    return jsonify({
        "order_id":    order_id,
        "total":       total_all,
        "discount":    discount,
        "delivery":    DELIVERY_FEE,
        "wave_links":  wave_links,
        "message":     "Ouvrez Wave pour payer chaque vendeur",
    })


@app.route("/api/payment/wave/confirm/<int:order_id>", methods=["POST"])
@auth_required
def wave_confirm(order_id):
    """
    Le client confirme avoir effectué le paiement Wave.
    Met à jour la commande et décrémente les stocks.
    """
    order = q("SELECT * FROM orders WHERE id=? AND buyer_id=?",
              (order_id, g.current_user["id"]), one=True)
    if not order:
        return jsonify({"error": "Commande introuvable"}), 404
    if order["status"] != "pending":
        return jsonify({"error": "Commande déjà traitée"}), 400

    # Confirmer et décrémenter stocks
    run("UPDATE orders SET status='processing', updated_at=datetime('now') WHERE id=?", (order_id,))
    items = q("SELECT * FROM order_items WHERE order_id=?", (order_id,))
    for item in items:
        run("UPDATE products SET stock=stock-? WHERE id=?", (item["quantity"], item["product_id"]))
        run("UPDATE shops SET total_sales=total_sales+? WHERE id=?", (item["quantity"], item["shop_id"]))

    if order["promo_code"]:
        run("UPDATE promo_codes SET used_count=used_count+1 WHERE code=?", (order["promo_code"],))

    return jsonify({"message": f"Commande #{order_id} confirmée !", "order_id": order_id})


# --- Abonnement vendeur (3 000 FCFA via Wave) ----------------------------------

@app.route("/api/payment/wave/subscription", methods=["GET"])
@auth_required
def get_subscription_wave_link():
    """
    Retourne le lien Wave pour payer l'abonnement vendeur (3 000 FCFA).
    """
    if g.current_user["role"] != "seller":
        return jsonify({"error": "Réservé aux vendeurs"}), 403

    shop = q("SELECT * FROM shops WHERE seller_id=?", (g.current_user["id"],), one=True)
    if not shop:
        return jsonify({"error": "Boutique introuvable"}), 404
    if shop["subscription_paid"]:
        return jsonify({"already_paid": True, "message": "Abonnement déjà actif"})

    desc     = f"Abonnement NexaShop - {g.current_user['name']}"
    wave_url = make_wave_link(NEXASHOP_WAVE_NUMBER, SUBSCRIPTION_FEE, desc)

    return jsonify({
        "wave_url":    wave_url,
        "amount":      SUBSCRIPTION_FEE,
        "wave_number": NEXASHOP_WAVE_NUMBER,
        "message":     f"Payez {SUBSCRIPTION_FEE:,} FCFA pour activer votre boutique".replace(",", " "),
    })


@app.route("/api/payment/wave/subscription/confirm", methods=["POST"])
@auth_required
def confirm_subscription():
    """
    Le vendeur confirme avoir payé l'abonnement.
    En production, vous validez manuellement via votre app Wave.
    """
    if g.current_user["role"] != "seller":
        return jsonify({"error": "Réservé aux vendeurs"}), 403

    wave_ref = (request.json or {}).get("wave_ref", "")  # référence transaction Wave
    shop     = q("SELECT * FROM shops WHERE seller_id=?", (g.current_user["id"],), one=True)

    run("""
        UPDATE shops
        SET subscription_paid=1,
            subscription_date=datetime('now')
        WHERE seller_id=?
    """, (g.current_user["id"],))

    return jsonify({"message": "Abonnement activé ! Votre boutique est maintenant visible.", "shop_id": shop["id"]})


@app.route("/api/shops/<int:shop_id>/wave", methods=["PUT"])
@seller_required
def update_wave_number(shop_id):
    """Permet au vendeur de renseigner son numéro Wave."""
    wave_number = (request.json or {}).get("wave_number", "").strip()
    if not wave_number:
        return jsonify({"error": "Numéro Wave requis"}), 400
    run("UPDATE shops SET wave_number=? WHERE id=? AND seller_id=?",
        (wave_number, shop_id, g.current_user["id"]))
    return jsonify({"message": "Numéro Wave mis à jour"})



import urllib.request

CINETPAY_API_KEY  = "19604404846840a3b008c627.80740525"
CINETPAY_SITE_ID  = "105897295"
CINETPAY_BASE_URL = "https://api-checkout.cinetpay.com/v2"
FRONTEND_URL      = os.environ.get("FRONTEND_URL", "https://stupendous-axolotl-b342fa.netlify.app")


@app.route("/api/payment/initiate", methods=["POST"])
@auth_required
def initiate_payment():
    """
    Initie un paiement CinetPay pour les articles du panier.
    Body: { items: [{product_id, quantity}], promo_code? }
    Retourne: { payment_url } — l'URL vers laquelle rediriger le client
    """
    d     = request.json or {}
    items = d.get("items", [])
    promo = d.get("promo_code")

    if not items:
        return jsonify({"error": "Panier vide"}), 400

    # Calculer le total
    total = 0
    enriched = []
    for item in items:
        prod = q("SELECT * FROM products WHERE id=? AND is_active=1", (item["product_id"],), one=True)
        if not prod:
            return jsonify({"error": f"Produit {item['product_id']} introuvable"}), 404
        if prod["stock"] < item["quantity"]:
            return jsonify({"error": f"Stock insuffisant pour {prod['name']}"}), 400
        enriched.append({**dict(prod), "quantity": item["quantity"]})
        total += prod["price"] * item["quantity"]

    total += 2500  # frais de livraison FCFA
    discount = 0

    # Code promo
    if promo:
        pc = q("SELECT * FROM promo_codes WHERE code=? AND is_active=1", (promo.upper(),), one=True)
        if pc and (not pc["expires_at"] or pc["expires_at"] > datetime.now().isoformat()):
            discount = round(total * pc["discount"] / 100)
            total    = total - discount

    total = int(round(total))

    # Créer la commande en BDD avec statut "pending"
    cur = run(
        "INSERT INTO orders(buyer_id, total_amount, discount, promo_code, status) VALUES(?,?,?,?,'pending')",
        (g.current_user["id"], total, discount, promo)
    )
    order_id = cur.lastrowid

    # Enregistrer les lignes de commande
    for item in enriched:
        run(
            "INSERT INTO order_items(order_id, product_id, shop_id, quantity, unit_price) VALUES(?,?,?,?,?)",
            (order_id, item["id"], item["shop_id"], item["quantity"], item["price"])
        )

    # Identifiant de transaction unique
    transaction_id = f"NEXA-{order_id}-{int(datetime.now().timestamp())}"

    # Appel API CinetPay
    payload = json.dumps({
        "apikey":         CINETPAY_API_KEY,
        "site_id":        CINETPAY_SITE_ID,
        "transaction_id": transaction_id,
        "amount":         total,
        "currency":       "XOF",
        "description":    f"Commande NexaShop #{order_id}",
        "notify_url":     f"https://nexashop-production.up.railway.app/api/payment/notify",
        "return_url":     f"{FRONTEND_URL}?order={order_id}&status=success",
        "channels":       "ALL",
        "lang":           "fr",
        "customer_name":  g.current_user["name"],
        "customer_email": g.current_user["email"],
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            f"{CINETPAY_BASE_URL}/payment",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        if result.get("code") == "201":
            payment_url = result["data"]["payment_url"]
            # Sauvegarder le transaction_id dans la commande
            run("UPDATE orders SET payment_ref=? WHERE id=?", (transaction_id, order_id))
            return jsonify({
                "payment_url":    payment_url,
                "order_id":       order_id,
                "transaction_id": transaction_id,
                "total":          total,
            })
        else:
            run("UPDATE orders SET status='cancelled' WHERE id=?", (order_id,))
            return jsonify({"error": result.get("message", "Erreur CinetPay")}), 502

    except Exception as e:
        run("UPDATE orders SET status='cancelled' WHERE id=?", (order_id,))
        return jsonify({"error": f"Impossible de contacter CinetPay : {str(e)}"}), 502


@app.route("/api/payment/notify", methods=["POST"])
def payment_notify():
    """
    Webhook appelé par CinetPay après un paiement.
    Met à jour le statut de la commande et décrémente les stocks.
    """
    d              = request.json or {}
    transaction_id = d.get("cpm_trans_id") or d.get("transaction_id", "")
    status         = d.get("cpm_result") or d.get("payment_status", "")

    order = q("SELECT * FROM orders WHERE payment_ref=?", (transaction_id,), one=True)
    if not order:
        return jsonify({"error": "Commande introuvable"}), 404

    if status == "00":
        # Paiement accepté
        run("UPDATE orders SET status='processing', updated_at=datetime('now') WHERE id=?", (order["id"],))
        # Décrémenter les stocks
        items = q("SELECT * FROM order_items WHERE order_id=?", (order["id"],))
        for item in items:
            run("UPDATE products SET stock=stock-? WHERE id=?", (item["quantity"], item["product_id"]))
            run("UPDATE shops SET total_sales=total_sales+? WHERE id=?", (item["quantity"], item["shop_id"]))
        # Utiliser le code promo si présent
        if order["promo_code"]:
            run("UPDATE promo_codes SET used_count=used_count+1 WHERE code=?", (order["promo_code"],))
    else:
        run("UPDATE orders SET status='cancelled', updated_at=datetime('now') WHERE id=?", (order["id"],))

    return jsonify({"message": "OK"}), 200


@app.route("/api/payment/verify/<transaction_id>", methods=["GET"])
@auth_required
def verify_payment(transaction_id):
    """Vérifie le statut d'un paiement auprès de CinetPay."""
    payload = json.dumps({
        "apikey":         CINETPAY_API_KEY,
        "site_id":        CINETPAY_SITE_ID,
        "transaction_id": transaction_id,
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            f"{CINETPAY_BASE_URL}/payment/check",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        order = q("SELECT * FROM orders WHERE payment_ref=?", (transaction_id,), one=True)
        return jsonify({
            "cinetpay": result,
            "order":    dict(order) if order else None,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 502


# ==============================================================================
# ADMIN — stats globales
# ==============================================================================

@app.route("/api/admin/stats", methods=["GET"])
@auth_required
def admin_stats():
    if g.current_user["role"] != "admin":
        return jsonify({"error": "Réservé à l'admin"}), 403
    return jsonify({
        "users":    q("SELECT COUNT(*) FROM users", ())[0][0],
        "sellers":  q("SELECT COUNT(*) FROM users WHERE role='seller'", ())[0][0],
        "buyers":   q("SELECT COUNT(*) FROM users WHERE role='buyer'", ())[0][0],
        "products": q("SELECT COUNT(*) FROM products WHERE is_active=1", ())[0][0],
        "orders":   q("SELECT COUNT(*) FROM orders", ())[0][0],
        "revenue":  round(q("SELECT COALESCE(SUM(total_amount),0) FROM orders WHERE status!='cancelled'",())[0][0], 2),
    })


# ==============================================================================
# SERVE FRONTEND
# ==============================================================================

@app.route("/")
def index():
    return send_from_directory(STATIC, "index.html")

@app.route("/health")
def health():
    return jsonify({"status": "ok", "db": DB_PATH, "time": datetime.now().isoformat()})

if __name__ == "__main__":
    os.makedirs(STATIC, exist_ok=True)
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV") != "production"
    print(f"NexaShop API démarrée sur http://localhost:{port}")
    print("   Endpoints disponibles :")
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
        if rule.rule.startswith("/api"):
            print(f"   {', '.join(rule.methods - {'HEAD','OPTIONS'}):20s} {rule.rule}")
    app.run(host="0.0.0.0", port=port, debug=debug)
