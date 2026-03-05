"""
NexaShop — Initialisation de la base de données SQLite
Crée toutes les tables et insère des données de démo.
"""
import sqlite3
import hashlib
import os
from datetime import datetime, timedelta
import random

DB_PATH = os.path.join(os.path.dirname(__file__), "nexashop.db")

SCHEMA = """
-- Utilisateurs (acheteurs & vendeurs)
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    email       TEXT    NOT NULL UNIQUE,
    password    TEXT    NOT NULL,        -- SHA-256 hash
    role        TEXT    NOT NULL CHECK(role IN ('buyer','seller','admin')),
    avatar      TEXT,
    created_at  TEXT    DEFAULT (datetime('now')),
    is_active   INTEGER DEFAULT 1
);

-- Boutiques (un vendeur = une boutique)
CREATE TABLE IF NOT EXISTS shops (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    seller_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name             TEXT    NOT NULL,
    description      TEXT,
    logo             TEXT,
    wave_number      TEXT,                  -- Numéro Wave du vendeur ex: +2250700000000
    rating           REAL    DEFAULT 0,
    total_sales      INTEGER DEFAULT 0,
    subscription_paid INTEGER DEFAULT 0,   -- 1 = abonnement 3000 FCFA payé
    subscription_date TEXT,                -- date du paiement
    created_at       TEXT    DEFAULT (datetime('now'))
);

-- Catégories
CREATE TABLE IF NOT EXISTS categories (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL UNIQUE,
    emoji TEXT,
    slug  TEXT NOT NULL UNIQUE
);

-- Produits
CREATE TABLE IF NOT EXISTS products (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    shop_id      INTEGER NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    category_id  INTEGER REFERENCES categories(id),
    name         TEXT    NOT NULL,
    description  TEXT,
    price        REAL    NOT NULL CHECK(price >= 0),
    old_price    REAL,
    stock        INTEGER DEFAULT 0,
    emoji        TEXT    DEFAULT '📦',
    badge        TEXT,                   -- 'Promo','New','Featured', NULL
    condition    TEXT    DEFAULT 'Neuf',
    rating       REAL    DEFAULT 0,
    review_count INTEGER DEFAULT 0,
    is_active    INTEGER DEFAULT 1,
    created_at   TEXT    DEFAULT (datetime('now'))
);

-- Commandes
CREATE TABLE IF NOT EXISTS orders (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    buyer_id     INTEGER NOT NULL REFERENCES users(id),
    total_amount REAL    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'processing'
                         CHECK(status IN ('pending','processing','shipped','delivered','cancelled')),
    payment_ref  TEXT,
    promo_code   TEXT,
    discount     REAL    DEFAULT 0,
    created_at   TEXT    DEFAULT (datetime('now')),
    updated_at   TEXT    DEFAULT (datetime('now'))
);

-- Lignes de commande
CREATE TABLE IF NOT EXISTS order_items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id   INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id),
    shop_id    INTEGER NOT NULL REFERENCES shops(id),
    quantity   INTEGER NOT NULL DEFAULT 1,
    unit_price REAL    NOT NULL
);

-- Avis / Notes
CREATE TABLE IF NOT EXISTS reviews (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    buyer_id   INTEGER NOT NULL REFERENCES users(id),
    rating     INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
    comment    TEXT,
    created_at TEXT    DEFAULT (datetime('now'))
);

-- Favoris
CREATE TABLE IF NOT EXISTS favorites (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, product_id)
);

-- Codes promo
CREATE TABLE IF NOT EXISTS promo_codes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code       TEXT    NOT NULL UNIQUE,
    discount   REAL    NOT NULL,        -- pourcentage ex: 10 = 10%
    max_uses   INTEGER DEFAULT 100,
    used_count INTEGER DEFAULT 0,
    expires_at TEXT,
    is_active  INTEGER DEFAULT 1
);

-- Index
CREATE INDEX IF NOT EXISTS idx_products_shop    ON products(shop_id);
CREATE INDEX IF NOT EXISTS idx_products_cat     ON products(category_id);
CREATE INDEX IF NOT EXISTS idx_orders_buyer     ON orders(buyer_id);
CREATE INDEX IF NOT EXISTS idx_order_items_ord  ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_reviews_product  ON reviews(product_id);
"""

def hash_password(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def seed(conn):
    cur = conn.cursor()

    # ---- Catégories ----
    cats = [
        ("Mode & Accessoires", "👗", "mode"),
        ("Tech & Électronique", "💻", "tech"),
        ("Maison & Déco",       "🏠", "maison"),
        ("Art & Collection",    "🎨", "art"),
        ("Bio & Santé",         "🌿", "bio"),
        ("Livres & Culture",    "📚", "livres"),
    ]
    cur.executemany("INSERT OR IGNORE INTO categories(name,emoji,slug) VALUES(?,?,?)", cats)

    # ---- Utilisateurs ----
    users = [
        ("Marie Dupont",    "marie@nexashop.fr",   hash_password("mdp123"), "seller"),
        ("Jean Martin",     "jean@example.fr",     hash_password("mdp123"), "buyer"),
        ("Sophie Leblanc",  "sophie@example.fr",   hash_password("mdp123"), "buyer"),
        ("Marc Renaud",     "marc@example.fr",     hash_password("mdp123"), "buyer"),
        ("Lucie Bernard",   "lucie@example.fr",    hash_password("mdp123"), "buyer"),
        ("TechPro Store",   "techpro@shop.fr",     hash_password("mdp123"), "seller"),
        ("Galerie Iris",    "iris@shop.fr",        hash_password("mdp123"), "seller"),
        ("Terre & Plantes", "bio@shop.fr",         hash_password("mdp123"), "seller"),
        ("Admin NexaShop",  "admin@nexashop.fr",   hash_password("admin"),  "admin"),
    ]
    for u in users:
        cur.execute(
            "INSERT OR IGNORE INTO users(name,email,password,role) VALUES(?,?,?,?)", u
        )

    # ---- Boutiques ----
    shops = [
        (1, "L'Atelier Mode",    "Mode & accessoires artisanaux",   "👗", "+2250701000001", 1),
        (6, "TechPro Store",     "High-tech reconditionné & neuf",  "💻", "+2250701000002", 1),
        (7, "Galerie Iris",      "Art contemporain & collection",   "🎨", "+2250701000003", 1),
        (8, "Terre & Plantes",   "Bio, naturel & bien-être",        "🌿", "+2250701000004", 1),
    ]
    for s in shops:
        cur.execute(
            "INSERT OR IGNORE INTO shops(seller_id,name,description,logo,wave_number,subscription_paid) VALUES(?,?,?,?,?,?)", s
        )

    # ---- Produits ----
    products = [
        # shop_id, cat_id, name, desc, price FCFA, old_price FCFA, stock, emoji, badge, rating, reviews
        (1, 1, "Veste en cuir vintage",       "Veste cuir véritable, coupe slim, taille M. État excellent.",    84900,  125000, 8,  "🧥", "Promo",    4.8, 42),
        (1, 1, "Sneakers édition limitée",    "Coloris exclusif, semelle renforcée, pointure 42.",             138000, 183000, 3,  "👟", "Featured", 4.8, 67),
        (1, 1, "Montre minimaliste dorée",    "Boîtier acier 36mm, bracelet cuir marron, mouvement quartz.",   115000, 157000, 12, "⌚", "Featured", 4.8, 93),
        (2, 2, "MacBook Air M2 reconditionné","Grade A+, 8Go RAM, 256Go SSD. Garantie 12 mois.",               589000, 851000, 5,  "💻", "Vérifié",  4.9, 88),
        (2, 2, "Casque audio sans fil",       "Réduction de bruit active, 30h autonomie, Bluetooth 5.2.",       97500, 144000, 15, "🎧", "Promo",    4.5, 201),
        (3, 4, "Peinture abstraite originale","Acrylique sur toile 60x80cm. Signée et certificat.",            210000,   None, 2,  "🎨", None,       5.0, 9),
        (3, 4, "Carnet artisanal cuir",       "Reliure main, papier recyclé 200 pages, format A5.",             29500,   None, 20, "📒", "New",      4.9, 34),
        (3, 4, "Roman illustré collector",    "Édition limitée numérotée 1/500, illustrations originales.",    25500,   None, 7,  "📚", None,       4.9, 72),
        (4, 5, "Huile de soin bio certifiée", "Argan + rose musquée, certification Ecocert, 50ml.",             22500,  27500, 50, "🌿", "Promo",    4.6, 156),
        (4, 5, "Plante succulente rare",      "Echeveria perle d'Azur, pot céramique inclus, hauteur 15cm.",   18500,   None, 10, "🪴", None,       4.7, 45),
        (1, 3, "Lampe artisanale en rotin",   "Tressage main, ampoule E27 incluse, câble tissu 2m.",           44500,   None, 6,  "💡", "New",      4.7, 23),
        (1, 3, "Miroir en bois flotté",       "Cadre bois flotté naturel, diamètre 60cm, crochet inclus.",     55500,  72000, 4,  "🪞", "Promo",    4.6, 18),
    ]
    cur.executemany("""
        INSERT OR IGNORE INTO products
        (shop_id,category_id,name,description,price,old_price,stock,emoji,badge,rating,review_count)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)
    """, products)

    # ---- Commandes de démo ----
    statuses = ["processing","shipped","delivered","delivered","cancelled"]
    buyer_ids = [2, 3, 4, 5]
    for i in range(12):
        buyer = random.choice(buyer_ids)
        status = statuses[i % len(statuses)]
        days_ago = i * 3
        created = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            "INSERT INTO orders(buyer_id,total_amount,status,created_at,updated_at) VALUES(?,?,?,?,?)",
            (buyer, round(random.uniform(30, 500), 2), status, created, created)
        )
        order_id = cur.lastrowid
        # 1-3 lignes par commande
        for _ in range(random.randint(1, 3)):
            prod_id = random.randint(1, 12)
            qty = random.randint(1, 2)
            cur.execute("SELECT price, shop_id FROM products WHERE id=?", (prod_id,))
            row = cur.fetchone()
            if row:
                cur.execute(
                    "INSERT INTO order_items(order_id,product_id,shop_id,quantity,unit_price) VALUES(?,?,?,?,?)",
                    (order_id, prod_id, row[1], qty, row[0])
                )

    # ---- Avis ----
    reviews = [
        (1,2,5,"Qualité exceptionnelle, livraison rapide !"),
        (1,3,4,"Très belle veste, taille légèrement grande."),
        (4,2,5,"Parfait état, comme neuf. Je recommande !"),
        (5,4,4,"Son excellent, confortable. Micro correct."),
        (9,3,5,"Huile merveilleuse, peau transformée en 2 semaines."),
        (6,5,5,"Œuvre magnifique, livraison soignée."),
    ]
    cur.executemany(
        "INSERT OR IGNORE INTO reviews(product_id,buyer_id,rating,comment) VALUES(?,?,?,?)",
        reviews
    )

    # ---- Codes promo ----
    promos = [
        ("NEXA10", 10, 200, "2026-12-31"),
        ("BIENVENUE", 15, 500, "2026-06-30"),
        ("SUMMER25", 25, 100, "2026-09-01"),
    ]
    cur.executemany(
        "INSERT OR IGNORE INTO promo_codes(code,discount,max_uses,expires_at) VALUES(?,?,?,?)",
        promos
    )

    # Mettre à jour total_sales boutiques
    cur.execute("""
        UPDATE shops SET total_sales = (
            SELECT COUNT(*) FROM order_items oi
            JOIN orders o ON o.id = oi.order_id
            WHERE oi.shop_id = shops.id AND o.status = 'delivered'
        )
    """)

    conn.commit()
    print("✅ Base de données NexaShop initialisée avec succès !")
    print(f"   📁 Fichier : {DB_PATH}")

    # Résumé
    for table in ["users","shops","categories","products","orders","order_items","reviews","promo_codes"]:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        print(f"   📊 {table}: {cur.fetchone()[0]} enregistrements")


if __name__ == "__main__":
    # Supprimer l'ancienne BDD si elle existe
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
        print("🗑️  Ancienne BDD supprimée")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    seed(conn)
    conn.close()
