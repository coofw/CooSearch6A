import sys
import subprocess
import asyncio
import random
import string
import urllib.request
import sqlite3
import os
import time
import re
import hashlib
import logging
import shutil
from datetime import datetime, timedelta

# ========== НАСТРОЙКА ЛОГГЕРА ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def install_reqs():
    reqs = [
        ("aiogram", "aiogram"),
        ("telethon", "telethon"),
        ("requests", "requests"),
        ("psycopg2", "psycopg2-binary")
    ]
    for import_name, package_name in reqs:
        try:
            __import__(import_name)
        except ImportError:
            print(f"⏳ Установка {package_name}...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", package_name])

install_reqs()

import requests
import psycopg2
from psycopg2.extras import execute_values
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from telethon import TelegramClient
from telethon.errors import FloodWaitError, UsernameNotOccupiedError, UsernameInvalidError, AuthKeyDuplicatedError
from telethon.tl.functions.account import CheckUsernameRequest
from telethon.tl.functions.contacts import ResolveUsernameRequest

# ========== КОНФИГ ==========
API_ID = 31799872
API_HASH = "d37c2d4db3e5a86fc01d5b8353635502"
BOT_TOKEN = "8793286826:AAHSMKyNp9UW9Cg17FBjaPK4Gr7kpOEEvyc"
CRYPTOBOT_TOKEN = "562001:AA93Uyx3t5L4S9Vxl0rhKM16eLdbDhK5fcQ"
ADMIN_IDS = [8484944484]
SESSIONS_DIR = "sessions"
BACKUP_DIR = "backups"
MIN_DONATE_USDT = 0.01
SEARCH_PRICE_STARS = 1.5
MAX_SEARCH_ATTEMPTS = 1750
HTTP_CACHE_TTL = 180
HTTP_CACHE_MAX_SIZE = 10000
os.makedirs(SESSIONS_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

WELCOME_TEXT = (
    "🚀 Приветствуем в CooSearch!\n"
    "Лучший бот для сбора юзернеймов.\n"
    "🔍 Лимиты: обычные 3 поиска/день, Premium 10 поисков/день\n"
    "👑 Купить Premium: @coofw\n"
    "📢 Наш канал: @CooSearch"
)

# ========== АВТОМАТИЧЕСКАЯ БАЗА ДАННЫХ ==========
class DatabaseManager:
    def __init__(self, db_name="userhunt.db"):
        self.db_name = db_name
        self.db_type = None
        self.conn = None
        self.cursor = None
        self.auto_detect_and_migrate()
        self.connect()
        self.optimize()
        self.setup()
        self.create_indexes()
        logger.info(f"БД инициализирована: {self.db_type.upper()}")

    def auto_detect_and_migrate(self):
        pg_host = os.environ.get("PG_HOST")
        pg_db = os.environ.get("PG_DATABASE")
        pg_user = os.environ.get("PG_USER")
        pg_pass = os.environ.get("PG_PASSWORD")
        
        if pg_host and pg_db and pg_user and pg_pass:
            self.db_type = "postgres"
            logger.info("🐘 Используется PostgreSQL")
            if os.path.exists(self.db_name) and os.path.getsize(self.db_name) > 0:
                logger.info("🔄 Обнаружена SQLite БД. Авто-миграция...")
                self.auto_migrate_sqlite_to_postgres()
        else:
            self.db_type = "sqlite"
            logger.info("📁 Используется SQLite")
    
    def auto_migrate_sqlite_to_postgres(self):
        sqlite_conn = sqlite3.connect(self.db_name)
        sqlite_conn.row_factory = sqlite3.Row
        sqlite_cursor = sqlite_conn.cursor()
        
        pg_conn = psycopg2.connect(
            host=os.environ.get("PG_HOST"),
            database=os.environ.get("PG_DATABASE"),
            user=os.environ.get("PG_USER"),
            password=os.environ.get("PG_PASSWORD")
        )
        pg_conn.autocommit = False
        pg_cursor = pg_conn.cursor()
        
        try:
            backup_name = os.path.join(BACKUP_DIR, f"pre_migration_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db")
            shutil.copy2(self.db_name, backup_name)
            logger.info(f"💾 Бэкап: {backup_name}")
            
            self._create_postgres_tables(pg_cursor)
            
            tables = [
                "users", "traps", "global_stats", "market_lots", "market_orders",
                "reviews", "disputes", "promocodes", "user_promocodes",
                "blacklist", "crypto_invoices", "donations", "referrals"
            ]
            
            total = 0
            for table in tables:
                try:
                    sqlite_cursor.execute(f"SELECT * FROM {table}")
                    rows = sqlite_cursor.fetchall()
                    if not rows:
                        continue
                    columns = [desc[0] for desc in sqlite_cursor.description]
                    placeholders = ','.join(['%s'] * len(columns))
                    data = [tuple(row) for row in rows]
                    for i in range(0, len(data), 1000):
                        batch = data[i:i+1000]
                        execute_values(pg_cursor, f"INSERT INTO {table} ({','.join(columns)}) VALUES %s ON CONFLICT DO NOTHING", batch)
                    pg_conn.commit()
                    logger.info(f"  ✅ {table}: {len(data)} записей")
                    total += len(data)
                except Exception as e:
                    logger.warning(f"  ⚠️ {table}: {e}")
            
            logger.info(f"✅ Миграция завершена! Перенесено {total} записей")
            os.rename(self.db_name, f"{self.db_name}.migrated_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        except Exception as e:
            pg_conn.rollback()
            logger.error(f"❌ Ошибка миграции: {e}")
            self.db_type = "sqlite"
        finally:
            sqlite_conn.close()
            pg_conn.close()
    
    def _create_postgres_tables(self, cursor):
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                today_searches INTEGER DEFAULT 0,
                last_search_date DATE,
                total_searches INTEGER DEFAULT 0,
                found_nicks INTEGER DEFAULT 0,
                join_date TIMESTAMP,
                is_premium BOOLEAN DEFAULT FALSE,
                premium_until TIMESTAMP,
                mirror_searches INTEGER DEFAULT 0,
                stars INTEGER DEFAULT 0,
                searches_balance INTEGER DEFAULT 0,
                referrer_id BIGINT,
                ref_code TEXT
            )
        """)
        cursor.execute("CREATE TABLE IF NOT EXISTS traps (id SERIAL PRIMARY KEY, user_id INTEGER, target_username TEXT, status TEXT DEFAULT 'active')")
        cursor.execute("CREATE TABLE IF NOT EXISTS global_stats (key TEXT PRIMARY KEY, value INTEGER DEFAULT 0)")
        cursor.execute("INSERT INTO global_stats (key, value) VALUES ('found_nicks', 0) ON CONFLICT DO NOTHING")
        cursor.execute("INSERT INTO global_stats (key, value) VALUES ('total_mirror_searches', 0) ON CONFLICT DO NOTHING")
        cursor.execute("INSERT INTO global_stats (key, value) VALUES ('total_donations_usdt', 0) ON CONFLICT DO NOTHING")
        cursor.execute("CREATE TABLE IF NOT EXISTS market_lots (id SERIAL PRIMARY KEY, seller_id INTEGER, username TEXT, price INTEGER, description TEXT, created_at TEXT, status TEXT DEFAULT 'active')")
        cursor.execute("CREATE TABLE IF NOT EXISTS market_orders (id SERIAL PRIMARY KEY, lot_id INTEGER, buyer_id INTEGER, seller_id INTEGER, status TEXT DEFAULT 'pending', created_at TEXT, confirmed_at TEXT)")
        cursor.execute("CREATE TABLE IF NOT EXISTS reviews (id SERIAL PRIMARY KEY, seller_id INTEGER, buyer_id INTEGER, rating INTEGER, text TEXT, created_at TEXT)")
        cursor.execute("CREATE TABLE IF NOT EXISTS disputes (id SERIAL PRIMARY KEY, order_id INTEGER, buyer_id INTEGER, seller_id INTEGER, reason TEXT, status TEXT DEFAULT 'open', resolved_by INTEGER, resolution TEXT, created_at TEXT, opener_id INTEGER)")
        cursor.execute("CREATE TABLE IF NOT EXISTS promocodes (code TEXT PRIMARY KEY, promo_type TEXT, reward TEXT, max_uses INTEGER, used INTEGER DEFAULT 0)")
        cursor.execute("CREATE TABLE IF NOT EXISTS user_promocodes (user_id INTEGER, code TEXT, activated_at TEXT, PRIMARY KEY (user_id, code))")
        cursor.execute("CREATE TABLE IF NOT EXISTS blacklist (user_id INTEGER PRIMARY KEY, reason TEXT, banned_at TEXT, banned_until TEXT, banned_by INTEGER)")
        cursor.execute("CREATE TABLE IF NOT EXISTS crypto_invoices (invoice_id TEXT PRIMARY KEY, user_id INTEGER, stars INTEGER, searches INTEGER DEFAULT 0, amount_usdt REAL, status TEXT DEFAULT 'pending', created_at TEXT, invoice_type TEXT DEFAULT 'topup')")
        cursor.execute("CREATE TABLE IF NOT EXISTS donations (id SERIAL PRIMARY KEY, user_id INTEGER, username TEXT, amount_usdt REAL, invoice_id TEXT, created_at TEXT)")
        cursor.execute("CREATE TABLE IF NOT EXISTS referrals (referrer_id INTEGER, referred_id INTEGER, date TEXT)")

    def connect(self):
        if self.db_type == "postgres":
            self.conn = psycopg2.connect(
                host=os.environ.get("PG_HOST"),
                database=os.environ.get("PG_DATABASE"),
                user=os.environ.get("PG_USER"),
                password=os.environ.get("PG_PASSWORD")
            )
            self.cursor = self.conn.cursor()
        else:
            self.conn = sqlite3.connect(self.db_name, check_same_thread=False)
            self.cursor = self.conn.cursor()
    
    def optimize(self):
        if self.db_type == "sqlite":
            self.cursor.execute("PRAGMA journal_mode=WAL")
            self.cursor.execute("PRAGMA synchronous=NORMAL")
            self.cursor.execute("PRAGMA cache_size=-50000")
            self.cursor.execute("PRAGMA temp_store=MEMORY")
            self.cursor.execute("PRAGMA mmap_size=268435456")
            self.conn.commit()
        else:
            self.cursor.execute("SET synchronous_commit = OFF")
            self.cursor.execute("SET work_mem = '16MB'")
            self.conn.commit()
    
    def create_indexes(self):
        if self.db_type == "sqlite":
            self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_premium ON users(premium_until)")
            self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_market_lots_seller ON market_lots(seller_id)")
            self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_traps_user ON traps(user_id)")
        else:
            self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_premium ON users(premium_until)")
            self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_market_lots_seller ON market_lots(seller_id)")
            self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_traps_user ON traps(user_id)")
        self.conn.commit()

    def setup(self):
        if self.db_type == "sqlite":
            self._create_sqlite_tables()
        else:
            self._create_postgres_tables(self.cursor)
        self.conn.commit()
    
    def _create_sqlite_tables(self):
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            today_searches INTEGER DEFAULT 0,
            last_search_date TEXT,
            total_searches INTEGER DEFAULT 0,
            found_nicks INTEGER DEFAULT 0,
            join_date TEXT,
            is_premium INTEGER DEFAULT 0,
            premium_until TEXT,
            mirror_searches INTEGER DEFAULT 0,
            stars INTEGER DEFAULT 0,
            searches_balance INTEGER DEFAULT 0,
            referrer_id INTEGER,
            ref_code TEXT
        )''')
        for col, col_type in [('stars', 'INTEGER DEFAULT 0'), ('searches_balance', 'INTEGER DEFAULT 0'), ('referrer_id', 'INTEGER'), ('ref_code', 'TEXT')]:
            try:
                self.cursor.execute(f"ALTER TABLE users ADD COLUMN {col} {col_type}")
            except:
                pass
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS traps (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, target_username TEXT, status TEXT DEFAULT 'active')''')
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS global_stats (key TEXT PRIMARY KEY, value INTEGER DEFAULT 0)''')
        self.cursor.execute("INSERT OR IGNORE INTO global_stats VALUES ('found_nicks', 0)")
        self.cursor.execute("INSERT OR IGNORE INTO global_stats VALUES ('total_mirror_searches', 0)")
        self.cursor.execute("INSERT OR IGNORE INTO global_stats VALUES ('total_donations_usdt', 0)")
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS market_lots (id INTEGER PRIMARY KEY AUTOINCREMENT, seller_id INTEGER, username TEXT, price INTEGER, description TEXT, created_at TEXT, status TEXT DEFAULT 'active')''')
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS market_orders (id INTEGER PRIMARY KEY AUTOINCREMENT, lot_id INTEGER, buyer_id INTEGER, seller_id INTEGER, status TEXT DEFAULT 'pending', created_at TEXT, confirmed_at TEXT)''')
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS reviews (id INTEGER PRIMARY KEY AUTOINCREMENT, seller_id INTEGER, buyer_id INTEGER, rating INTEGER, text TEXT, created_at TEXT)''')
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS disputes (id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER, buyer_id INTEGER, seller_id INTEGER, reason TEXT, status TEXT DEFAULT 'open', resolved_by INTEGER, resolution TEXT, created_at TEXT, opener_id INTEGER)''')
        try:
            self.cursor.execute("ALTER TABLE disputes ADD COLUMN opener_id INTEGER")
        except:
            pass
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS promocodes (code TEXT PRIMARY KEY, promo_type TEXT, reward TEXT, max_uses INTEGER, used INTEGER DEFAULT 0)''')
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS user_promocodes (user_id INTEGER, code TEXT, activated_at TEXT, PRIMARY KEY (user_id, code))''')
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS blacklist (user_id INTEGER PRIMARY KEY, reason TEXT, banned_at TEXT, banned_until TEXT, banned_by INTEGER)''')
        try:
            self.cursor.execute("ALTER TABLE blacklist ADD COLUMN banned_until TEXT")
        except:
            pass
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS crypto_invoices (invoice_id TEXT PRIMARY KEY, user_id INTEGER, stars INTEGER, searches INTEGER DEFAULT 0, amount_usdt REAL, status TEXT DEFAULT 'pending', created_at TEXT, invoice_type TEXT DEFAULT 'topup')''')
        try:
            self.cursor.execute("ALTER TABLE crypto_invoices ADD COLUMN searches INTEGER DEFAULT 0")
        except:
            pass
        try:
            self.cursor.execute("ALTER TABLE crypto_invoices ADD COLUMN invoice_type TEXT DEFAULT 'topup'")
        except:
            pass
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS donations (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, amount_usdt REAL, invoice_id TEXT, created_at TEXT)''')
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS referrals (referrer_id INTEGER, referred_id INTEGER, date TEXT)''')

    # ========== МЕТОДЫ РАБОТЫ С ДАННЫМИ ==========
    def add_stars(self, user_id, amount):
        self.cursor.execute("UPDATE users SET stars = stars + ? WHERE user_id = ?", (amount, user_id))
        self.conn.commit()

    def get_stars(self, user_id):
        self.cursor.execute("SELECT stars FROM users WHERE user_id = ?", (user_id,))
        row = self.cursor.fetchone()
        return row[0] if row else 0

    def remove_stars(self, user_id, amount):
        self.cursor.execute("SELECT stars FROM users WHERE user_id = ?", (user_id,))
        row = self.cursor.fetchone()
        if row and row[0] >= amount:
            self.cursor.execute("UPDATE users SET stars = stars - ? WHERE user_id = ?", (amount, user_id))
            self.conn.commit()
            return True
        return False

    def add_searches_balance(self, user_id, amount):
        self.cursor.execute("UPDATE users SET searches_balance = searches_balance + ? WHERE user_id = ?", (amount, user_id))
        self.conn.commit()

    def get_searches_balance(self, user_id):
        self.cursor.execute("SELECT searches_balance FROM users WHERE user_id = ?", (user_id,))
        row = self.cursor.fetchone()
        return row[0] if row else 0

    def use_search(self, user_id):
        self.cursor.execute("UPDATE users SET searches_balance = searches_balance - 1 WHERE user_id = ? AND searches_balance > 0", (user_id,))
        self.conn.commit()

    def add_user(self, user_id, username, referrer_id=None):
        self.cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
        if not self.cursor.fetchone():
            ref_code = hashlib.md5(f"{user_id}{random.random()}".encode()).hexdigest()[:8]
            self.cursor.execute(
                "INSERT INTO users (user_id, username, join_date, ref_code, stars, searches_balance) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, username, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), ref_code, 0, 0)
            )
            self.conn.commit()
            self.add_stars(user_id, 15)
            if referrer_id:
                self.cursor.execute("INSERT INTO referrals (referrer_id, referred_id, date) VALUES (?, ?, ?)",
                                    (referrer_id, user_id, datetime.now().isoformat()))
                self.add_stars(referrer_id, 25)
                self.conn.commit()
            return True
        return False

    def get_profile(self, user_id):
        self.cursor.execute("SELECT username, today_searches, total_searches, found_nicks, join_date, is_premium, premium_until, mirror_searches, stars, searches_balance FROM users WHERE user_id = ?", (user_id,))
        user_data = self.cursor.fetchone()
        if user_data:
            self.cursor.execute("SELECT COUNT(*) FROM traps WHERE user_id = ? AND status = 'active'", (user_id,))
            active_traps = self.cursor.fetchone()[0]
            self.cursor.execute("SELECT COUNT(*) FROM traps WHERE user_id = ? AND status = 'caught'", (user_id,))
            caught_traps = self.cursor.fetchone()[0]
            return user_data, active_traps, caught_traps
        return None, 0, 0

    def get_ref_code(self, user_id):
        self.cursor.execute("SELECT ref_code FROM users WHERE user_id = ?", (user_id,))
        row = self.cursor.fetchone()
        if row and row[0]:
            return row[0]
        code = hashlib.md5(f"{user_id}{random.random()}".encode()).hexdigest()[:8]
        self.cursor.execute("UPDATE users SET ref_code = ? WHERE user_id = ?", (code, user_id))
        self.conn.commit()
        return code

    def get_referral_count(self, user_id):
        self.cursor.execute("SELECT COUNT(*) FROM referrals WHERE referrer_id = ?", (user_id,))
        return self.cursor.fetchone()[0]

    def get_mirror_searches(self, user_id):
        self.cursor.execute("SELECT mirror_searches FROM users WHERE user_id = ?", (user_id,))
        row = self.cursor.fetchone()
        return row[0] if row else 0

    def add_mirror_searches(self, user_id, amount):
        self.cursor.execute("UPDATE users SET mirror_searches = mirror_searches + ? WHERE user_id = ?", (amount, user_id))
        self.conn.commit()

    def use_mirror_search(self, user_id):
        self.cursor.execute("UPDATE users SET mirror_searches = mirror_searches - 1 WHERE user_id = ? AND mirror_searches > 0", (user_id,))
        self.cursor.execute("UPDATE global_stats SET value = value + 1 WHERE key = 'total_mirror_searches'")
        self.conn.commit()

    def add_trap(self, user_id, target):
        self.cursor.execute("DELETE FROM traps WHERE user_id = ? AND target_username = ? AND status = 'active'", (user_id, target))
        self.cursor.execute("INSERT INTO traps (user_id, target_username) VALUES (?, ?)", (user_id, target))
        self.conn.commit()

    def get_user_active_traps(self, user_id):
        self.cursor.execute("SELECT target_username FROM traps WHERE user_id = ? AND status = 'active'", (user_id,))
        return [row[0] for row in self.cursor.fetchall()]

    def cancel_trap(self, user_id, target):
        self.cursor.execute("DELETE FROM traps WHERE user_id = ? AND target_username = ? AND status = 'active'", (user_id, target))
        self.conn.commit()

    def get_all_active_traps(self):
        self.cursor.execute("SELECT user_id, target_username FROM traps WHERE status = 'active'")
        return self.cursor.fetchall()

    def mark_trap_caught(self, user_id, target):
        self.cursor.execute("UPDATE traps SET status = 'caught' WHERE user_id = ? AND target_username = ?", (user_id, target))
        self.conn.commit()

    def inc_found_nicks(self):
        self.cursor.execute("UPDATE global_stats SET value = value + 1 WHERE key = 'found_nicks'")
        self.conn.commit()

    def get_daily_limit(self, user_id):
        if user_id in ADMIN_IDS:
            return float('inf')
        profile_data = self.get_profile(user_id)
        user_data = profile_data[0] if profile_data else None
        if not user_data:
            return 3
        premium_until = user_data[6]
        is_premium = False
        if premium_until:
            try:
                if isinstance(premium_until, str):
                    is_premium = datetime.strptime(premium_until, "%Y-%m-%d %H:%M:%S") > datetime.now()
                else:
                    is_premium = premium_until > datetime.now()
            except:
                pass
        return 10 if is_premium else 3

    def add_search(self, user_id):
        today = datetime.now().strftime("%Y-%m-%d")
        self.cursor.execute("SELECT last_search_date, today_searches FROM users WHERE user_id = ?", (user_id,))
        row = self.cursor.fetchone()
        if row and row[0] != today:
            self.cursor.execute("UPDATE users SET today_searches = 1, total_searches = total_searches + 1, last_search_date = ? WHERE user_id = ?", (today, user_id))
        elif row:
            self.cursor.execute("UPDATE users SET today_searches = today_searches + 1, total_searches = total_searches + 1 WHERE user_id = ?", (user_id,))
        self.conn.commit()

    def add_found_nick(self, user_id):
        self.cursor.execute("UPDATE users SET found_nicks = found_nicks + 1 WHERE user_id = ?", (user_id,))
        self.conn.commit()

    def get_stats(self):
        self.cursor.execute("SELECT COUNT(*) FROM users")
        total_users = self.cursor.fetchone()[0]
        self.cursor.execute("SELECT value FROM global_stats WHERE key = 'found_nicks'")
        row = self.cursor.fetchone()
        found_nicks = row[0] if row else 0
        self.cursor.execute("SELECT COUNT(*) FROM traps WHERE status = 'active'")
        active_traps = self.cursor.fetchone()[0]
        return total_users, found_nicks, active_traps

    def get_admin_info_stats(self):
        today = datetime.now().strftime("%Y-%m-%d")
        self.cursor.execute("SELECT COUNT(*) FROM users WHERE join_date LIKE ?", (today + "%",))
        new_users_today = self.cursor.fetchone()[0]
        self.cursor.execute("SELECT SUM(today_searches) FROM users WHERE last_search_date = ?", (today,))
        row = self.cursor.fetchone()
        searches_today = row[0] if row and row[0] else 0
        self.cursor.execute("SELECT SUM(total_searches) FROM users")
        row = self.cursor.fetchone()
        total_searches = row[0] if row and row[0] else 0
        return new_users_today, searches_today, total_searches

    def get_all_premium_users(self):
        self.cursor.execute("SELECT user_id, username, premium_until FROM users WHERE premium_until IS NOT NULL")
        return self.cursor.fetchall()

    def get_all_user_ids(self):
        self.cursor.execute("SELECT user_id FROM users")
        return [row[0] for row in self.cursor.fetchall()]

    def take_premium(self, user_id):
        self.cursor.execute("UPDATE users SET premium_until = NULL, is_premium = 0 WHERE user_id = ?", (user_id,))
        self.conn.commit()

    def add_premium_time(self, user_id, delta):
        self.cursor.execute("SELECT premium_until FROM users WHERE user_id = ?", (user_id,))
        row = self.cursor.fetchone()
        current = row[0] if row else None
        now = datetime.now()
        if current:
            try:
                if isinstance(current, str):
                    current_dt = datetime.strptime(current, "%Y-%m-%d %H:%M:%S")
                else:
                    current_dt = current
                if current_dt < now:
                    current_dt = now
            except:
                current_dt = now
        else:
            current_dt = now
        new_dt = current_dt + delta
        if isinstance(new_dt, datetime):
            new_str = new_dt.strftime("%Y-%m-%d %H:%M:%S") if self.db_type == "sqlite" else new_dt
        else:
            new_str = new_dt
        self.cursor.execute("UPDATE users SET premium_until = ?, is_premium = 1 WHERE user_id = ?", (new_str, user_id))
        self.conn.commit()
        return new_str

    # ---------- Маркет ----------
    def is_market_banned(self, user_id):
        now = datetime.now().isoformat()
        self.cursor.execute("SELECT banned_until FROM blacklist WHERE user_id = ?", (user_id,))
        row = self.cursor.fetchone()
        if not row:
            return False
        if row[0] and row[0] < now:
            self.remove_from_blacklist(user_id)
            return False
        return True

    def add_market_lot(self, seller_id, username, price, description):
        if self.is_market_banned(seller_id):
            return None
        self.cursor.execute("INSERT INTO market_lots (seller_id, username, price, description, created_at, status) VALUES (?, ?, ?, ?, ?, 'active')",
                            (seller_id, username, price, description, datetime.now().isoformat()))
        self.conn.commit()
        return self.cursor.lastrowid

    def get_market_lots(self, offset=0, limit=7):
        self.cursor.execute("SELECT id, seller_id, username, price, description, created_at FROM market_lots WHERE status='active' ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))
        return self.cursor.fetchall()

    def get_market_lot(self, lot_id):
        self.cursor.execute("SELECT id, seller_id, username, price, description, created_at FROM market_lots WHERE id=? AND status='active'", (lot_id,))
        return self.cursor.fetchone()

    def delete_market_lot(self, lot_id, user_id):
        if self.is_market_banned(user_id):
            return False
        self.cursor.execute("SELECT seller_id FROM market_lots WHERE id=? AND status='active'", (lot_id,))
        row = self.cursor.fetchone()
        if row and row[0] == user_id:
            self.cursor.execute("DELETE FROM market_lots WHERE id=?", (lot_id,))
            self.conn.commit()
            return True
        return False

    def get_user_market_lots(self, user_id):
        if self.is_market_banned(user_id):
            return []
        self.cursor.execute("SELECT id, username, price, description, created_at FROM market_lots WHERE seller_id=? AND status='active'", (user_id,))
        return self.cursor.fetchall()

    def create_order(self, lot_id, buyer_id, seller_id):
        if self.is_market_banned(buyer_id) or self.is_market_banned(seller_id):
            return None
        self.cursor.execute("INSERT INTO market_orders (lot_id, buyer_id, seller_id, created_at, status) VALUES (?, ?, ?, ?, 'pending')",
                            (lot_id, buyer_id, seller_id, datetime.now().isoformat()))
        self.conn.commit()
        return self.cursor.lastrowid

    def get_order(self, order_id):
        self.cursor.execute("SELECT * FROM market_orders WHERE id=?", (order_id,))
        return self.cursor.fetchone()

    def confirm_order(self, order_id):
        self.cursor.execute("UPDATE market_orders SET status='confirmed', confirmed_at=? WHERE id=?", (datetime.now().isoformat(), order_id))
        self.conn.commit()
        self.cursor.execute("SELECT lot_id FROM market_orders WHERE id=?", (order_id,))
        row = self.cursor.fetchone()
        if row:
            self.cursor.execute("DELETE FROM market_lots WHERE id=?", (row[0],))
            self.conn.commit()

    def add_review(self, seller_id, buyer_id, rating, text):
        self.cursor.execute("INSERT INTO reviews (seller_id, buyer_id, rating, text, created_at) VALUES (?, ?, ?, ?, ?)",
                            (seller_id, buyer_id, rating, text, datetime.now().isoformat()))
        self.conn.commit()

    def get_seller_reviews(self, seller_id):
        self.cursor.execute("SELECT rating, text, buyer_id, created_at FROM reviews WHERE seller_id=? ORDER BY created_at DESC", (seller_id,))
        return self.cursor.fetchall()

    def get_seller_avg_rating(self, seller_id):
        self.cursor.execute("SELECT AVG(rating) FROM reviews WHERE seller_id=?", (seller_id,))
        row = self.cursor.fetchone()
        return round(row[0], 1) if row and row[0] else 0

    def add_dispute(self, order_id, buyer_id, seller_id, reason, opener_id):
        self.cursor.execute("INSERT INTO disputes (order_id, buyer_id, seller_id, reason, created_at, status, opener_id) VALUES (?, ?, ?, ?, ?, 'open', ?)",
                            (order_id, buyer_id, seller_id, reason, datetime.now().isoformat(), opener_id))
        self.conn.commit()
        return self.cursor.lastrowid

    def get_open_disputes(self):
        self.cursor.execute("SELECT * FROM disputes WHERE status='open'")
        return self.cursor.fetchall()

    def resolve_dispute(self, dispute_id, admin_id, resolution):
        self.cursor.execute("UPDATE disputes SET status='resolved', resolved_by=?, resolution=? WHERE id=?", (admin_id, resolution, dispute_id))
        self.conn.commit()

    def add_to_blacklist(self, user_id, reason, admin_id, until=None):
        until_str = until.isoformat() if until else None
        self.cursor.execute("INSERT OR REPLACE INTO blacklist (user_id, reason, banned_at, banned_until, banned_by) VALUES (?, ?, ?, ?, ?)",
                            (user_id, reason, datetime.now().isoformat(), until_str, admin_id))
        self.conn.commit()

    def remove_from_blacklist(self, user_id):
        self.cursor.execute("DELETE FROM blacklist WHERE user_id = ?", (user_id,))
        self.conn.commit()

    def get_blacklist(self):
        now = datetime.now().isoformat()
        self.cursor.execute("SELECT user_id, reason, banned_at, banned_until, banned_by FROM blacklist")
        rows = self.cursor.fetchall()
        result = []
        for row in rows:
            if row[3] and row[3] < now:
                self.remove_from_blacklist(row[0])
                continue
            result.append(row)
        return result

    def create_promocode(self, code, promo_type, reward, max_uses):
        try:
            self.cursor.execute("INSERT INTO promocodes (code, promo_type, reward, max_uses) VALUES (?, ?, ?, ?)", (code, promo_type, reward, max_uses))
            self.conn.commit()
            return True
        except:
            return False

    def get_promocode(self, code):
        self.cursor.execute("SELECT code, promo_type, reward, max_uses, used FROM promocodes WHERE code = ?", (code,))
        return self.cursor.fetchone()

    def is_promocode_activated_by_user(self, user_id, code):
        self.cursor.execute("SELECT 1 FROM user_promocodes WHERE user_id = ? AND code = ?", (user_id, code))
        return self.cursor.fetchone() is not None

    def add_user_promocode(self, user_id, code):
        self.cursor.execute("INSERT INTO user_promocodes (user_id, code, activated_at) VALUES (?, ?, ?)",
                            (user_id, code, datetime.now().isoformat()))
        self.conn.commit()

    def use_promocode(self, code, user_id):
        promo = self.get_promocode(code)
        if not promo:
            return False, "Промокод не найден", None
        code, promo_type, reward, max_uses, used = promo
        if used >= max_uses:
            return False, "Промокод использован максимальное число раз", None
        if self.is_promocode_activated_by_user(user_id, code):
            return False, "Вы уже активировали этот промокод", None
        self.cursor.execute("UPDATE promocodes SET used = used + 1 WHERE code = ?", (code,))
        self.conn.commit()
        self.add_user_promocode(user_id, code)
        if promo_type == "mirror":
            amount = int(reward)
            self.add_mirror_searches(user_id, amount)
            return True, f"Вы получили {amount} зеркальных запросов!", {"type": "mirror", "amount": amount}
        elif promo_type == "premium":
            delta = self._parse_delta(reward)
            if delta:
                new_until = self.add_premium_time(user_id, delta)
                return True, f"Premium активирован до {new_until}!", {"type": "premium", "until": new_until}
            return False, "Ошибка формата", None
        elif promo_type == "stars":
            amount = int(reward)
            self.add_stars(user_id, amount)
            return True, f"Вы получили {amount} звёзд!", {"type": "stars", "amount": amount}
        elif promo_type == "searches":
            amount = int(reward)
            self.add_searches_balance(user_id, amount)
            return True, f"Вы получили {amount} обычных поисков!", {"type": "searches", "amount": amount}
        return False, "Неизвестный тип", None

    def _parse_delta(self, time_str):
        match = re.match(r'^(\d+)([hdmy])$', time_str.lower())
        if match:
            val = int(match.group(1))
            unit = match.group(2)
            if unit == 'h': return timedelta(hours=val)
            elif unit == 'd': return timedelta(days=val)
            elif unit == 'm': return timedelta(days=val*30)
            elif unit == 'y': return timedelta(days=val*365)
        return None

    def get_all_promocodes(self):
        self.cursor.execute("SELECT code, promo_type, reward, max_uses, used FROM promocodes")
        return self.cursor.fetchall()

    def delete_promocode(self, code):
        self.cursor.execute("DELETE FROM promocodes WHERE code = ?", (code,))
        self.conn.commit()
        return self.cursor.rowcount > 0

    def add_invoice(self, invoice_id, user_id, stars, searches, amount_usdt, invoice_type="topup"):
        self.cursor.execute("INSERT INTO crypto_invoices (invoice_id, user_id, stars, searches, amount_usdt, created_at, invoice_type) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (invoice_id, user_id, stars, searches, amount_usdt, datetime.now().isoformat(), invoice_type))
        self.conn.commit()

    def get_invoice(self, invoice_id):
        self.cursor.execute("SELECT * FROM crypto_invoices WHERE invoice_id = ?", (invoice_id,))
        return self.cursor.fetchone()

    def update_invoice_status(self, invoice_id, status):
        self.cursor.execute("UPDATE crypto_invoices SET status = ? WHERE invoice_id = ?", (status, invoice_id))
        self.conn.commit()

    def add_donation(self, user_id, username, amount_usdt, invoice_id):
        self.cursor.execute("INSERT INTO donations (user_id, username, amount_usdt, invoice_id, created_at) VALUES (?, ?, ?, ?, ?)",
                            (user_id, username, amount_usdt, invoice_id, datetime.now().isoformat()))
        self.conn.commit()
        self.cursor.execute("UPDATE global_stats SET value = value + ? WHERE key = 'total_donations_usdt'", (amount_usdt,))
        self.conn.commit()

    def get_donations_history(self, limit=50):
        self.cursor.execute("SELECT id, user_id, username, amount_usdt, created_at FROM donations ORDER BY created_at DESC LIMIT ?", (limit,))
        return self.cursor.fetchall()

    def get_total_donations(self):
        self.cursor.execute("SELECT value FROM global_stats WHERE key = 'total_donations_usdt'")
        row = self.cursor.fetchone()
        return row[0] if row else 0

db = DatabaseManager()

# ========== КЭШ ДЛЯ HTTP ПРОВЕРОК ==========
http_cache = {}

def clean_http_cache():
    global http_cache
    now = time.time()
    to_delete = [k for k, (_, ts) in http_cache.items() if now - ts > HTTP_CACHE_TTL]
    for k in to_delete:
        del http_cache[k]
    if len(http_cache) > HTTP_CACHE_MAX_SIZE:
        sorted_items = sorted(http_cache.items(), key=lambda x: x[1][1])
        to_delete = [k for k, _ in sorted_items[:len(http_cache) - HTTP_CACHE_MAX_SIZE]]
        for k in to_delete:
            del http_cache[k]

def get_cached_http_result(username):
    clean_http_cache()
    if username in http_cache:
        result, timestamp = http_cache[username]
        if time.time() - timestamp < HTTP_CACHE_TTL:
            return result
        else:
            del http_cache[username]
    return None

def set_cached_http_result(username, is_free):
    http_cache[username] = (is_free, time.time())

# ========== ОЦЕНКА ЮЗЕРНЕЙМОВ ==========
class EvaluatorEngine:
    def __init__(self):
        self.dictionary = set()
        self.dict_loaded = False
        self.mats_ru = ['хуй', 'пизда', 'бля', 'ебать', 'залупа', 'мудак', 'говно', 'срать', 'пидор', 'гомик']
        self.mats_en = ['fuck', 'shit', 'damn', 'bitch', 'cunt', 'dick', 'asshole', 'pussy', 'cock', 'whore', 'penis', 'vagina']
        self.tg_words = [
            'durov', 'pavel', 'nikolai', 'usmanov',
            'channel', 'group', 'bot', 'sticker', 'gif', 'voice', 'video', 'call', 'poll', 'quiz',
            'premium', 'stars', 'gifts', 'collectible', 'stories', 'boost',
            'tgram', 'tgweb', 'tdesktop', 'android', 'ios', 'macos', 'windows', 'linux',
            'wallet', 'cryptobot', 'donate', 'sbd', 'vcoin', 'notcoin',
            'tg', 'tme', 'teleg', 'gram', 'mtproto', 'tdlib',
            'sendmessage', 'editmessage', 'getupdates', 'webhook', 'inline'
        ]
        
    def load_dict(self):
        if self.dict_loaded:
            return
        try:
            req = urllib.request.Request("https://raw.githubusercontent.com/charlesreid1/five-letter-words/master/sgb-words.txt", headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as response:
                words = response.read().decode('utf-8').splitlines()
                for w in words:
                    if w.isalpha():
                        self.dictionary.add(w.lower())
            self.dict_loaded = True
        except:
            pass

    def is_pronounceable(self, word):
        vowels = 'aeiouy'
        vowel_count = sum(1 for c in word if c in vowels)
        consonant_count = len(word) - vowel_count
        return vowel_count >= 1 and consonant_count >= 1

    def is_beautiful_pattern(self, word):
        if word == word[::-1]:
            return True
        if len(set(word)) <= 2:
            return True
        if len(word) >= 6 and word[:3] == word[:3][::-1] and word[3:] == word[3:][::-1]:
            return True
        return False

    def evaluate(self, username: str):
        username = username.lower()
        length = len(username)
        self.load_dict()
        
        base_score = 3.0
        
        if length <= 4: base_score += 3.0
        elif length == 5: base_score += 1.0
        elif length == 6: base_score -= 1.0
        elif length >= 7: base_score -= 2.0
        
        unique_chars = len(set(username))
        if unique_chars == 1:
            base_score = 10.0
        elif unique_chars <= 3:
            base_score += 1.0
        
        if username == username[::-1]:
            base_score += 2.0
        
        max_streak = 1
        current_streak = 1
        for i in range(1, len(username)):
            if username[i] == username[i-1]:
                current_streak += 1
                max_streak = max(max_streak, current_streak)
            else:
                current_streak = 1
        if max_streak >= 4: base_score += 2.0
        elif max_streak == 3: base_score += 1.0
        
        if username in self.dictionary and not any(c.isdigit() for c in username):
            base_score += 4.0
        
        if username in self.mats_ru or username in self.mats_en:
            base_score += 2.0
        
        if username in self.tg_words:
            base_score += 2.0
        
        if self.is_beautiful_pattern(username):
            base_score += 2.0
        
        if self.is_pronounceable(username):
            base_score += 1.5
        
        has_digits = any(c.isdigit() for c in username)
        if has_digits and base_score > 0:
            base_score /= 1.5
        
        base_score = max(1.0, min(10.0, base_score))
        final_score = round(base_score, 1)
        if final_score.is_integer():
            final_score = int(final_score)
        
        if final_score >= 9.5: verdict = "💎 ЭКСКЛЮЗИВ"
        elif final_score >= 8.0: verdict = "👑 ЭЛИТА"
        elif final_score >= 6.0: verdict = "👍 ХОРОШИЙ"
        elif final_score >= 4.0: verdict = "🔹 СРЕДНИЙ"
        elif final_score >= 2.5: verdict = "🟢 БАЗОВЫЙ"
        else: verdict = "💩 МУСОР"
        
        return final_score, verdict

    def generate_random(self, length=5, filter_type="🟢 Дефолт", use_digits=False):
        charset = string.ascii_lowercase
        if use_digits:
            charset += string.digits
        
        if filter_type == "✨ Красивые":
            return self.generate_beautiful(length)
        elif filter_type == "📖 Со смыслом":
            return self.generate_meaningful(length)
        elif filter_type == "🔤 Любое слово":
            return self.generate_anyword(length)
        elif filter_type == "🤬 Матерные":
            return self.generate_maternye(length)
        elif filter_type == "📱 Telegram":
            return self.generate_telegram(length)
        elif filter_type == "🪞 Зеркальный 🔒":
            p1 = random.choice(charset)
            p2 = random.choice(charset)
            p3 = random.choice(charset)
            return p1 + p2 + p3 + p2 + p1
        
        return ''.join(random.choices(charset, k=length))

    def generate_beautiful(self, length=5):
        patterns = [
            lambda: random.choice(string.ascii_lowercase) * length,
            lambda: random.choice(string.ascii_lowercase) + random.choice(string.ascii_lowercase) * (length-2) + random.choice(string.ascii_lowercase),
            lambda: ''.join([random.choice(string.ascii_lowercase) for _ in range(length//2)] + [random.choice(string.ascii_lowercase) for _ in range(length - length//2)][::-1]),
            lambda: (random.choice(['ab', 'bc', 'cd', 'de', 'ef', 'fg', 'gh', 'hi', 'ij', 'jk', 'kl', 'lm', 'mn', 'no', 'op', 'pq', 'qr', 'rs', 'st', 'tu', 'uv', 'vw', 'wx', 'xy', 'yz']) * (length//2 + 1))[:length],
            lambda: ''.join(random.choices('aeiou', k=length//2) + random.choices('bcdfghjklmnpqrstvwxz', k=length - length//2)),
            lambda: ''.join(random.choices('qwertyuiopasdfghjklzxcvbnm', k=length))
        ]
        return random.choice(patterns)()

    def generate_meaningful(self, length=5):
        self.load_dict()
        if self.dictionary:
            words = [w for w in self.dictionary if len(w) == length]
            if words:
                return random.choice(words)
        return self.generate_beautiful(length)

    def generate_anyword(self, length=5):
        vowels = 'aeiouy'
        consonants = 'bcdfghjklmnpqrstvwxz'
        result = []
        for i in range(length):
            if random.choice([True, False]):
                result.append(random.choice(vowels))
            else:
                result.append(random.choice(consonants))
        return ''.join(result)

    def generate_maternye(self, length=5):
        all_mats = self.mats_ru + self.mats_en
        suitable = [m for m in all_mats if len(m) == length]
        if suitable:
            return random.choice(suitable)
        return self.generate_anyword(length)

    def generate_telegram(self, length=5):
        suitable = [w for w in self.tg_words if len(w) == length]
        if suitable:
            return random.choice(suitable)
        result = []
        for i in range(length):
            source = random.choice(self.tg_words)
            if i < len(source):
                result.append(source[i])
            else:
                result.append(random.choice(string.ascii_lowercase))
        return ''.join(result)

    def generate_by_word(self, word, length, position='any'):
        if len(word) > length:
            word = word[:length]
        
        if position == 'start':
            return word + self.generate_random(length - len(word), "🟢 Дефолт", False)
        elif position == 'end':
            return self.generate_random(length - len(word), "🟢 Дефолт", False) + word
        else:
            prefix_len = random.randint(0, length - len(word))
            suffix_len = length - len(word) - prefix_len
            prefix = ''.join(random.choices(string.ascii_lowercase, k=prefix_len))
            suffix = ''.join(random.choices(string.ascii_lowercase, k=suffix_len))
            return prefix + word + suffix

    def generate_by_mask(self, mask, length):
        vowels = 'aeiouy'
        consonants = 'bcdfghjklmnpqrstvwxz'
        result = []
        mask = mask[:length]
        for i, ch in enumerate(mask):
            if ch == '!':
                result.append(random.choice(consonants))
            elif ch == '?':
                result.append(random.choice(vowels))
            elif ch.isalpha():
                result.append(ch.lower())
            else:
                result.append(random.choice(string.ascii_lowercase))
        return ''.join(result)

engine = EvaluatorEngine()

# ========== TELEGRAM USER КЛИЕНТЫ ==========
user_clients = []
flood_waits = {}
current_client_index = 0
requests_on_current_client = 0
http_last_request = 0
session_requests_count = {}

async def load_user_sessions():
    global user_clients
    print("Загрузка сессий...")
    for file in os.listdir(SESSIONS_DIR):
        if file.endswith(".session"):
            session_name = file.replace(".session", "")
            cl = TelegramClient(os.path.join(SESSIONS_DIR, session_name), API_ID, API_HASH)
            await cl.connect()
            if await cl.is_user_authorized():
                user_clients.append(cl)
                session_requests_count[session_name] = 0
                print(f"Подключен: {session_name}")
    if not user_clients:
        print("⚠️ Нет сессий!")

def get_session_status(session_name):
    now = time.time()
    if session_name in flood_waits:
        if now < flood_waits[session_name]:
            remaining = flood_waits[session_name] - now
            if remaining > 30:
                return "🟠", f"блок ({int(remaining)} сек)"
            else:
                return "🔴", f"блок ({int(remaining)} сек)"
    requests = session_requests_count.get(session_name, 0)
    if requests >= 9:
        return "🟠", f"критично ({requests}/10)"
    elif requests >= 7:
        return "🟡", f"скоро блок ({requests}/10)"
    else:
        return "🟢", f"активна ({requests}/10)"

def get_sessions_status_text():
    lines = []
    color_counts = {"🟢": 0, "🟡": 0, "🟠": 0, "🔴": 0}
    for cl in user_clients:
        session_name = os.path.basename(cl.session.filename).replace('.session', '')
        color, status_text = get_session_status(session_name)
        color_counts[color] += 1
        lines.append(f"• `{session_name}`: {color} {status_text}")
    if not lines:
        return "Нет активных сессий"
    summary = f"📊 **СЕССИИ:**\n\n" + "\n".join(lines)
    summary += f"\n\n**Итого:** {color_counts['🟢']}🟢 {color_counts['🟡']}🟡 {color_counts['🟠']}🟠 {color_counts['🔴']}🔴"
    summary += f"\n\n💾 HTTP кэш: {len(http_cache)} записей"
    return summary

def check_tme_http(username):
    try:
        req = urllib.request.Request(f"https://t.me/{username}", headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=3) as response:
            html = response.read().decode('utf-8', errors='ignore')
            if 'class="tgme_page_title"' in html or 'class="tgme_page_extra"' in html or 'tgme_page_description' in html:
                return False
            if response.status == 200:
                return False
            return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return True
        return False
    except:
        return True

async def check_username_via_session(username):
    global current_client_index, requests_on_current_client
    if not user_clients:
        return None
    now = time.time()
    available_indices = []
    for i, cl in enumerate(user_clients):
        session_name = os.path.basename(cl.session.filename).replace('.session', '')
        if session_name in flood_waits:
            if now < flood_waits[session_name]:
                continue
            else:
                del flood_waits[session_name]
                session_requests_count[session_name] = 0
        available_indices.append(i)
    if not available_indices:
        return None
    while available_indices:
        if current_client_index not in available_indices:
            next_indices = [idx for idx in available_indices if idx >= current_client_index]
            current_client_index = next_indices[0] if next_indices else available_indices[0]
            requests_on_current_client = 0
        cl = user_clients[current_client_index]
        session_name = os.path.basename(cl.session.filename).replace('.session', '')
        try:
            await cl(ResolveUsernameRequest(username=username))
            session_requests_count[session_name] = session_requests_count.get(session_name, 0) + 1
            return False
        except UsernameNotOccupiedError:
            try:
                is_available = await cl(CheckUsernameRequest(username=username))
                session_requests_count[session_name] = session_requests_count.get(session_name, 0) + 1
                return is_available
            except FloodWaitError as e:
                flood_waits[session_name] = time.time() + e.seconds
                session_requests_count[session_name] = 0
                available_indices.remove(current_client_index)
                requests_on_current_client = 0
                continue
            except:
                return False
        except FloodWaitError as e:
            flood_waits[session_name] = time.time() + e.seconds
            session_requests_count[session_name] = 0
            available_indices.remove(current_client_index)
            requests_on_current_client = 0
            continue
        except AuthKeyDuplicatedError:
            await cl.disconnect()
            session_path = os.path.join(SESSIONS_DIR, session_name)
            for ext in ['.session', '.session-journal']:
                if os.path.exists(session_path + ext):
                    os.remove(session_path + ext)
            new_cl = TelegramClient(session_path, API_ID, API_HASH)
            await new_cl.connect()
            if await new_cl.is_user_authorized():
                idx = user_clients.index(cl) if cl in user_clients else -1
                if idx != -1:
                    user_clients[idx] = new_cl
                else:
                    user_clients.append(new_cl)
                available_indices.append(current_client_index)
                continue
            return False
        except:
            return False
    else:
        if len(flood_waits) >= len(user_clients):
            return None
        return False

async def check_username_hybrid(username):
    global http_last_request
    cached = get_cached_http_result(username)
    if cached is not None:
        return cached, "cached"
    now = time.time()
    if now - http_last_request < 2:
        await asyncio.sleep(2 - (now - http_last_request))
    http_last_request = time.time()
    http_result = await asyncio.get_event_loop().run_in_executor(None, check_tme_http, username)
    set_cached_http_result(username, http_result)
    if http_result:
        session_result = await check_username_via_session(username)
        if session_result is not None:
            set_cached_http_result(username, session_result)
            return session_result, "reliable"
        else:
            return http_result, "unreliable"
    return http_result, "reliable"

# ========== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ==========
bot_username = None
donation_cooldowns = {}

def can_donate(user_id: int) -> bool:
    last_donate = donation_cooldowns.get(user_id)
    if last_donate is None:
        return True
    return time.time() - last_donate >= 60

def set_donate_cooldown(user_id: int):
    donation_cooldowns[user_id] = time.time()

async def get_bot_username():
    global bot_username
    if not bot_username:
        bot_username = (await bot.get_me()).username
    return bot_username

# ========== КЛАВИАТУРЫ ==========
def get_main_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="🔎 Поиск (5 букв)"), KeyboardButton(text="🔎 Поиск (6 букв)"))
    builder.row(KeyboardButton(text="⚙️ Фильтры"), KeyboardButton(text="⭐️ Оценить юзернейм"))
    builder.row(KeyboardButton(text="🎯 Поставить ловушку"), KeyboardButton(text="📊 Статистика"))
    builder.row(KeyboardButton(text="👤 Профиль"), KeyboardButton(text="🛒 Маркет"))
    builder.row(KeyboardButton(text="💎 Премиум"), KeyboardButton(text="🔗 Реферальная ссылка"))
    builder.row(KeyboardButton(text="❤️ Поддержать бота"), KeyboardButton(text="🎫 Активировать промокод"))
    builder.row(KeyboardButton(text="🔍 Купить поиски"), KeyboardButton(text="✨ Премиум-фильтры"))
    return builder.as_markup(resize_keyboard=True)

def get_buy_searches_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🔍 10 поисков (15⭐)", callback_data="buy_searches_10")
    builder.button(text="🔍 50 поисков (75⭐)", callback_data="buy_searches_50")
    builder.button(text="🔍 100 поисков (150⭐)", callback_data="buy_searches_100")
    builder.button(text="💰 Купить за USDT", callback_data="buy_searches_usdt")
    builder.button(text="🔙 Назад", callback_data="back_to_main")
    builder.adjust(1)
    return builder.as_markup()

def get_premium_filters_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="🔍 Поиск по слову"), KeyboardButton(text="🎭 Поиск по маске"))
    builder.row(KeyboardButton(text="🔙 Назад"))
    return builder.as_markup(resize_keyboard=True)

def get_filters_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="🟢 Дефолт"), KeyboardButton(text="✨ Красивые"))
    builder.row(KeyboardButton(text="📖 Со смыслом"), KeyboardButton(text="🔤 Любое слово"))
    builder.row(KeyboardButton(text="🤬 Матерные"), KeyboardButton(text="📱 Telegram"))
    builder.row(KeyboardButton(text="🪞 Зеркальный 🔒"), KeyboardButton(text="🔢 Включить цифры"))
    builder.row(KeyboardButton(text="🔠 Выключить цифры"), KeyboardButton(text="🔙 Назад"))
    return builder.as_markup(resize_keyboard=True)

def get_profile_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="⭐️ Пополнить баланс"), KeyboardButton(text="💎 Купить премиум"))
    builder.row(KeyboardButton(text="🔙 Назад"))
    return builder.as_markup(resize_keyboard=True)

def get_premium_prices_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="1 день (15⭐)"), KeyboardButton(text="3 дня (40⭐)"))
    builder.row(KeyboardButton(text="7 дней (75⭐)"), KeyboardButton(text="14 дней (125⭐)"))
    builder.row(KeyboardButton(text="30 дней (200⭐)"))
    builder.row(KeyboardButton(text="🔙 Назад"))
    return builder.as_markup(resize_keyboard=True)

def get_admin_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="ℹ️ Информация"), KeyboardButton(text="👥 Список премиум"))
    builder.row(KeyboardButton(text="💎 Выдать премиум"), KeyboardButton(text="❌ Забрать премиум"))
    builder.row(KeyboardButton(text="🪞 Выдать зеркала"), KeyboardButton(text="⭐️ Выдать звёзды"))
    builder.row(KeyboardButton(text="⭐️ Забрать звёзды"), KeyboardButton(text="🔍 Выдать поиски"))
    builder.row(KeyboardButton(text="📢 Рассылка"), KeyboardButton(text="🎫 Промокоды"))
    builder.row(KeyboardButton(text="📊 Статус сессий"), KeyboardButton(text="⚖️ Споры"))
    builder.row(KeyboardButton(text="🚫 Чёрный список"), KeyboardButton(text="📊 Донаты"))
    builder.row(KeyboardButton(text="💾 Бэкап БД"), KeyboardButton(text="🔙 Выйти в меню"))
    return builder.as_markup(resize_keyboard=True)

def get_blacklist_admin_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="➕ Добавить в ЧС"))
    builder.row(KeyboardButton(text="➖ Убрать из ЧС"))
    builder.row(KeyboardButton(text="📋 Список ЧС"))
    builder.row(KeyboardButton(text="🔙 Назад"))
    return builder.as_markup(resize_keyboard=True)

def get_promocode_admin_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="➕ Создать промокод"))
    builder.row(KeyboardButton(text="📋 Список промокодов"))
    builder.row(KeyboardButton(text="🗑 Удалить промокод"))
    builder.row(KeyboardButton(text="🔙 Назад"))
    return builder.as_markup(resize_keyboard=True)

def get_promocode_type_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="🪞 Зеркальные поиски"))
    builder.row(KeyboardButton(text="Премиум"))
    builder.row(KeyboardButton(text="⭐️ Звёзды"))
    builder.row(KeyboardButton(text="🔍 Обычные поиски"))
    builder.row(KeyboardButton(text="🔙 Назад"))
    return builder.as_markup(resize_keyboard=True)

def get_trap_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="❌ Отменить ловушку"))
    builder.row(KeyboardButton(text="🔙 Назад"))
    return builder.as_markup(resize_keyboard=True)

def get_cancel_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="🔙 Отмена"))
    return builder.as_markup(resize_keyboard=True)

def get_market_main_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="📋 Все лоты"), KeyboardButton(text="💎 Продать"))
    builder.row(KeyboardButton(text="📦 Мои лоты"))
    builder.row(KeyboardButton(text="🔙 Назад"))
    return builder.as_markup(resize_keyboard=True)

def get_review_keyboard(seller_id, order_id):
    builder = InlineKeyboardBuilder()
    for i in range(1, 6):
        builder.button(text=f"⭐️ {i}", callback_data=f"rate_{seller_id}_{order_id}_{i}")
    builder.adjust(5)
    return builder.as_markup()

def get_ban_duration_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="1 час", callback_data="ban_duration_1h")
    builder.button(text="6 часов", callback_data="ban_duration_6h")
    builder.button(text="12 часов", callback_data="ban_duration_12h")
    builder.button(text="1 день", callback_data="ban_duration_1d")
    builder.button(text="3 дня", callback_data="ban_duration_3d")
    builder.button(text="7 дней", callback_data="ban_duration_7d")
    builder.button(text="1 месяц", callback_data="ban_duration_1m")
    builder.button(text="3 месяца", callback_data="ban_duration_3m")
    builder.button(text="1 год", callback_data="ban_duration_1y")
    builder.button(text="Бессрочно", callback_data="ban_duration_0")
    builder.button(text="Отмена", callback_data="cancel_ban")
    builder.adjust(3)
    return builder.as_markup()

# ========== FSM СОСТОЯНИЯ ==========
class Form(StatesGroup):
    waiting_for_trap = State()
    eval_username = State()
    activate_promo = State()
    admin_give_prem = State()
    admin_take_prem = State()
    admin_give_mirrors = State()
    admin_give_stars = State()
    admin_take_stars = State()
    admin_give_searches = State()
    admin_broadcast = State()
    promo_create_type = State()
    promo_create_mirror = State()
    promo_create_premium = State()
    promo_create_stars = State()
    promo_create_searches = State()
    promo_delete = State()
    market_sell_username = State()
    market_sell_desc = State()
    market_sell_price = State()
    dispute_reason = State()
    review_text = State()
    add_blacklist_id = State()
    add_blacklist_reason = State()
    remove_blacklist_id = State()
    dispute_ban_reason = State()
    dispute_ban_duration = State()
    donate_amount = State()
    premium_word = State()
    premium_mask = State()
    premium_word_position = State()
    buy_searches_amount = State()

# ========== ФУНКЦИИ ДЛЯ РАБОТЫ С CRYPTO BOT ==========
STARS_TO_USDT = {
    15: round(15 * 0.015, 2),
    40: round(40 * 0.015, 2),
    75: round(75 * 0.015, 2),
    125: round(125 * 0.015, 2),
    200: round(200 * 0.015, 2)
}

def create_crypto_invoice(amount_usdt, description):
    global bot_username
    url = "https://pay.crypt.bot/api/createInvoice"
    payload = {
        "asset": "USDT",
        "amount": str(amount_usdt),
        "description": description,
        "paid_btn_name": "callback",
        "paid_btn_url": f"https://t.me/{bot_username}?start=payment"
    }
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}
    for attempt in range(3):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get("ok"):
                    return data
                else:
                    print(f"API Error: {data.get('error')}")
                    return data
            else:
                print(f"HTTP Error: {response.status_code}")
        except Exception as e:
            print(f"Attempt {attempt+1} failed: {e}")
        if attempt == 2:
            return None
        time.sleep(1)
    return None

def get_invoice_status(invoice_id):
    url = "https://pay.crypt.bot/api/getInvoices"
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}
    params = {"invoice_ids": invoice_id}
    for attempt in range(3):
        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get("ok") and data["result"]["items"]:
                    return data["result"]["items"][0]["status"]
            return None
        except Exception as e:
            print(f"Check attempt {attempt+1} failed: {e}")
        if attempt == 2:
            return None
        time.sleep(1)
    return None

# ========== БОТ ==========
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

user_filters = {}
user_digits = {}
user_cooldowns = {}
temp_dispute_data = {}

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (БЕЗ db_type) ==========
def get_user_data_safe(user_id):
    profile_data = db.get_profile(user_id)
    if not profile_data or not profile_data[0]:
        return None, 0, 0
    user_data = profile_data[0]
    active_traps = profile_data[1]
    caught_traps = profile_data[2]
    return user_data, active_traps, caught_traps

def is_premium_user(user_data):
    if not user_data:
        return False
    premium_until = user_data[6]
    if not premium_until:
        return False
    try:
        if isinstance(premium_until, str):
            return datetime.strptime(premium_until, "%Y-%m-%d %H:%M:%S") > datetime.now()
        else:
            return premium_until > datetime.now()
    except:
        return False

# ---------- ЗАПУСК ----------
@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    username = message.from_user.username or f"User{user_id}"
    referrer_id = None
    if ' ' in message.text:
        arg = message.text.split()[1]
        if arg.startswith('ref_'):
            code = arg[4:]
            db.cursor.execute("SELECT user_id FROM users WHERE ref_code=?", (code,))
            row = db.cursor.fetchone()
            if row:
                referrer_id = row[0]
    db.add_user(user_id, username, referrer_id)
    await message.answer(WELCOME_TEXT, reply_markup=get_main_keyboard())

@dp.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    if message.from_user.id in ADMIN_IDS:
        await state.clear()
        await message.answer("👑 Добро пожаловать в Админ Панель", reply_markup=get_admin_keyboard())

@dp.message(F.text == "🔙 Назад")
async def back_to_main(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(WELCOME_TEXT, reply_markup=get_main_keyboard())

@dp.message(F.text == "🔙 Отмена")
async def cancel_action(message: Message, state: FSMContext):
    await state.clear()
    if message.from_user.id in ADMIN_IDS:
        await message.answer("Действие отменено", reply_markup=get_admin_keyboard())
    else:
        await message.answer("Действие отменено", reply_markup=get_main_keyboard())

@dp.message(F.text == "🔙 Выйти в меню")
async def exit_admin(message: Message, state: FSMContext):
    if message.from_user.id in ADMIN_IDS:
        await state.clear()
        await message.answer(WELCOME_TEXT, reply_markup=get_main_keyboard())

# ---------- АДМИН: БЭКАП БД ----------
@dp.message(F.text == "💾 Бэкап БД")
async def admin_backup(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    if db.db_type == "sqlite":
        backup_path = os.path.join(BACKUP_DIR, f"userhunt_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db")
        shutil.copy2(db.db_name, backup_path)
        await message.answer(f"✅ Бэкап SQLite создан: `{backup_path}`", parse_mode="Markdown")
    else:
        await message.answer("ℹ️ Для PostgreSQL используйте штатные средства бэкапа", parse_mode="Markdown")

# ---------- АДМИН: СТАТУС СЕССИЙ ----------
@dp.message(F.text == "📊 Статус сессий")
async def admin_sessions_status(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    status_text = get_sessions_status_text()
    await message.answer(status_text, parse_mode="Markdown")

# ---------- ПОКУПКА ПОИСКОВ ----------
@dp.message(F.text == "🔍 Купить поиски")
async def buy_searches_menu(message: Message):
    await message.answer(
        "🔍 **Покупка поисков**\n\n"
        "Купите дополнительные поиски за звёзды или USDT:\n\n"
        "💰 1 поиск = 1.5 звезды\n\n"
        "Выберите вариант:",
        reply_markup=get_buy_searches_keyboard()
    )

@dp.callback_query(F.data.startswith("buy_searches_"))
async def buy_searches_callback(call: CallbackQuery, state: FSMContext):
    user_id = call.from_user.id
    option = call.data.split("_")[2]
    
    if option == "10":
        searches = 10
        stars_price = int(10 * SEARCH_PRICE_STARS)
        if db.remove_stars(user_id, stars_price):
            db.add_searches_balance(user_id, searches)
            await call.message.edit_text(f"✅ Вы купили {searches} поисков за {stars_price}⭐!\nБаланс поисков: {db.get_searches_balance(user_id)}")
        else:
            await call.answer(f"❌ Недостаточно звёзд! Нужно {stars_price}⭐", show_alert=True)
    elif option == "50":
        searches = 50
        stars_price = int(50 * SEARCH_PRICE_STARS)
        if db.remove_stars(user_id, stars_price):
            db.add_searches_balance(user_id, searches)
            await call.message.edit_text(f"✅ Вы купили {searches} поисков за {stars_price}⭐!\nБаланс поисков: {db.get_searches_balance(user_id)}")
        else:
            await call.answer(f"❌ Недостаточно звёзд! Нужно {stars_price}⭐", show_alert=True)
    elif option == "100":
        searches = 100
        stars_price = int(100 * SEARCH_PRICE_STARS)
        if db.remove_stars(user_id, stars_price):
            db.add_searches_balance(user_id, searches)
            await call.message.edit_text(f"✅ Вы купили {searches} поисков за {stars_price}⭐!\nБаланс поисков: {db.get_searches_balance(user_id)}")
        else:
            await call.answer(f"❌ Недостаточно звёзд! Нужно {stars_price}⭐", show_alert=True)
    elif option == "usdt":
        await state.set_state(Form.buy_searches_amount)
        await call.message.edit_text(
            "💰 **Покупка поисков за USDT**\n\n"
            f"1 поиск = {SEARCH_PRICE_STARS}⭐\n"
            f"1 USDT = ~100⭐ (курс Crypto Bot)\n\n"
            f"**Введите КОЛИЧЕСТВО поисков, которое хотите купить:**\n"
            f"(минимум 1, максимум 10000)",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Отмена", callback_data="cancel_buy_searches")]
            ])
        )
    await call.answer()

@dp.message(Form.buy_searches_amount)
async def buy_searches_amount_input(message: Message, state: FSMContext):
    try:
        searches = int(message.text.strip())
        if searches < 1:
            await message.answer("❌ Минимум 1 поиск")
            return
        if searches > 10000:
            await message.answer("❌ Максимум 10000 поисков за раз")
            return
    except:
        await message.answer("❌ Введите целое число. Пример: 50, 100, 500")
        return
    amount_usdt = round((searches * SEARCH_PRICE_STARS) / 100, 2)
    if amount_usdt < 0.1:
        amount_usdt = 0.1
    user_id = message.from_user.id
    username = message.from_user.username or f"User{user_id}"
    description = f"Покупка {searches} поисков"
    result = create_crypto_invoice(amount_usdt, description)
    if not result or not result.get("ok"):
        error_msg = result.get("error", "Неизвестная ошибка") if result else "Нет ответа от сервера"
        await message.answer(f"❌ **Ошибка создания счёта**\n\nКод: {error_msg}\n\nПопробуйте позже или свяжитесь с @coofw", reply_markup=get_main_keyboard())
        await state.clear()
        return
    invoice_id = result["result"]["invoice_id"]
    pay_url = result["result"]["pay_url"]
    db.add_invoice(invoice_id, user_id, 0, searches, amount_usdt, "searches")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить", url=pay_url)],
        [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"check_searches_invoice_{invoice_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])
    await message.answer(
        f"💰 **Покупка поисков за USDT**\n\n"
        f"🔍 Количество: {searches} поисков\n"
        f"💵 Сумма: {amount_usdt} USDT\n\n"
        f"После оплаты нажмите «Проверить оплату».",
        reply_markup=keyboard
    )
    await state.clear()

@dp.callback_query(F.data == "cancel_buy_searches")
async def cancel_buy_searches(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("❌ Покупка отменена.", reply_markup=get_main_keyboard())
    await call.answer()

@dp.callback_query(F.data.startswith("check_searches_invoice_"))
async def check_searches_invoice(call: CallbackQuery):
    parts = call.data.split("_")
    if len(parts) < 4:
        await call.answer("Ошибка", show_alert=True)
        return
    invoice_id = parts[3]
    user_id = call.from_user.id
    invoice = db.get_invoice(invoice_id)
    if not invoice:
        await call.answer("Счёт не найден", show_alert=True)
        return
    if invoice[5] == "paid":
        await call.answer("Этот счёт уже обработан", show_alert=True)
        return
    status = get_invoice_status(invoice_id)
    if status == "active":
        await call.answer("Счёт ещё не оплачен", show_alert=True)
    elif status == "paid":
        searches = invoice[3]
        db.add_searches_balance(user_id, searches)
        db.update_invoice_status(invoice_id, "paid")
        await call.message.edit_text(f"✅ Оплата подтверждена! Вам начислено {searches} поисков.\nБаланс поисков: {db.get_searches_balance(user_id)}")
        await call.answer("Поиски зачислены!", show_alert=True)
    else:
        await call.answer("Ошибка проверки", show_alert=True)

# ---------- ПРЕМИУМ-ФИЛЬТРЫ ----------
@dp.message(F.text == "✨ Премиум-фильтры")
async def premium_filters_menu(message: Message):
    user_id = message.from_user.id
    user_data, _, _ = get_user_data_safe(user_id)
    if not user_data:
        await message.answer("❌ Ошибка профиля. Попробуйте /start", reply_markup=get_main_keyboard())
        return
    if not is_premium_user(user_data):
        await message.answer("❌ Премиум-фильтры только для Premium пользователей!\nКупите Premium в профиле.")
        return
    await message.answer("✨ **Премиум-фильтры**\n\n🔍 Поиск по слову — найдёт юзернеймы с вашим словом\n🎭 Поиск по маске — ! (согласная), ? (гласная)", reply_markup=get_premium_filters_keyboard())

@dp.message(F.text == "🔍 Поиск по слову")
async def search_by_word_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    user_data, _, _ = get_user_data_safe(user_id)
    if not user_data:
        await message.answer("❌ Ошибка профиля. Попробуйте /start", reply_markup=get_main_keyboard())
        return
    if not is_premium_user(user_data):
        await message.answer("❌ Только для Premium!")
        return
    await state.set_state(Form.premium_word)
    await message.answer("🔍 Введите слово (от 3 до 32 букв, только латиница):", reply_markup=get_cancel_keyboard())

@dp.message(Form.premium_word)
async def search_by_word_process(message: Message, state: FSMContext):
    word = message.text.strip().lower()
    if not re.match(r'^[a-z]{3,32}$', word):
        await message.answer("❌ Только латиница, от 3 до 32 букв.", reply_markup=get_cancel_keyboard())
        return
    await state.update_data(premium_word=word)
    await state.set_state(Form.premium_word_position)
    await message.answer(
        "Где должно быть слово?\n\n"
        "🔹 В начале\n"
        "🔹 В конце\n"
        "🔹 В любом месте",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📌 В начале"), KeyboardButton(text="📍 В конце")],
                [KeyboardButton(text="🔄 В любом месте"), KeyboardButton(text="🔙 Отмена")]
            ],
            resize_keyboard=True
        )
    )

@dp.message(F.text.in_(["📌 В начале", "📍 В конце", "🔄 В любом месте"]))
async def search_by_word_position(message: Message, state: FSMContext):
    data = await state.get_data()
    word = data.get("premium_word")
    if message.text == "📌 В начале":
        position = "start"
    elif message.text == "📍 В конце":
        position = "end"
    else:
        position = "any"
    await state.clear()
    await message.answer(f"🔍 Ищу юзернеймы с '{word}'...\nЭто может занять некоторое время.", reply_markup=get_main_keyboard())
    await process_premium_search(message, word=word, position=position)

@dp.message(F.text == "🎭 Поиск по маске")
async def search_by_mask_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    user_data, _, _ = get_user_data_safe(user_id)
    if not user_data:
        await message.answer("❌ Ошибка профиля. Попробуйте /start", reply_markup=get_main_keyboard())
        return
    if not is_premium_user(user_data):
        await message.answer("❌ Только для Premium!")
        return
    await state.set_state(Form.premium_mask)
    await message.answer("🎭 Введите маску:\n! — согласная\n? — гласная\nЛюбая буква — точное совпадение\n\nПример: `!?a!?`\nДлина маски = длине юзернейма (5-32):", reply_markup=get_cancel_keyboard())

@dp.message(Form.premium_mask)
async def search_by_mask_process(message: Message, state: FSMContext):
    mask = message.text.strip().lower()
    if not re.match(r'^[!?a-z]{5,32}$', mask):
        await message.answer("❌ Маска должна содержать только !, ? и буквы. Длина от 5 до 32.", reply_markup=get_cancel_keyboard())
        return
    await state.clear()
    await message.answer(f"🎭 Ищу по маске '{mask}'...\nЭто может занять некоторое время.")
    await process_premium_search(message, mask=mask)

async def process_premium_search(message: Message, word=None, position='any', mask=None):
    user_id = message.from_user.id
    db.add_search(user_id)
    msg = await message.answer("⏳ Поиск...")
    length = 5
    unreliable_warning = False
    attempts = 0
    while length <= 32:
        if word:
            username = engine.generate_by_word(word, length, position)
        elif mask:
            username = engine.generate_by_mask(mask, length)
        else:
            break
        attempts += 1
        if attempts > MAX_SEARCH_ATTEMPTS:
            await msg.edit_text(f"❌ Не удалось найти свободный юзернейм за {MAX_SEARCH_ATTEMPTS} попыток. Попробуйте другое слово/маску.")
            return
        is_free, reliability = await check_username_hybrid(username)
        if reliability == "unreliable" and not unreliable_warning:
            await msg.edit_text("⚠️ **ВНИМАНИЕ!**\n\nВсе сессии Telegram в блоке.\nИспользую неточную проверку через t.me.\nРезультат может быть ошибочным.\n\n⏳ Продолжаю поиск...")
            unreliable_warning = True
            await asyncio.sleep(1.5)
            await msg.edit_text("⏳ Поиск...")
        if is_free is None:
            await msg.edit_text("❌ Ошибка проверки. Попробуйте позже.")
            return
        if is_free:
            score, verdict = engine.evaluate(username)
            db.add_found_nick(user_id)
            db.inc_found_nicks()
            reliability_text = "\n⚠️ Проверка неточная (сессии в блоке)" if reliability == "unreliable" else ""
            text = f"✅ **Найдено (поиск по {'слову' if word else 'маске'}):**\n\n┌ `@{username}`\n├ {len(username)} букв\n├ ⭐️ {score}/10 ({verdict})\n└ 🟢 Свободен{reliability_text}\n\n🔗 https://t.me/{username}"
            await msg.edit_text(text, disable_web_page_preview=True)
            return
        length += 1
        await asyncio.sleep(0.05)
    await msg.edit_text("❌ Не удалось найти свободный юзернейм. Попробуйте другое слово/маску.")

# ---------- ПОИСК ----------
@dp.message(F.text == "🔎 Поиск (5 букв)")
async def search_5(message: Message):
    await handle_search(message, 5)

@dp.message(F.text == "🔎 Поиск (6 букв)")
async def search_6(message: Message):
    await handle_search(message, 6)

async def handle_search(message: Message, length: int):
    user_id = message.from_user.id
    user_data, _, _ = get_user_data_safe(user_id)
    if not user_data:
        await message.answer("❌ Ошибка профиля. Попробуйте /start", reply_markup=get_main_keyboard())
        return
    today_searches = user_data[1]
    searches_balance = user_data[9] if len(user_data) > 9 else 0
    is_prem = is_premium_user(user_data)
    if user_id in ADMIN_IDS:
        db.add_search(user_id)
    elif is_prem:
        if today_searches >= 10:
            if searches_balance > 0:
                db.use_search(user_id)
                db.add_search(user_id)
                await message.answer(f"🔍 Использован 1 купленный поиск. Осталось: {searches_balance - 1}")
            else:
                await message.answer("❌ Вы использовали 10 бесплатных поисков сегодня. Купите дополнительные поиски в меню «🔍 Купить поиски»!")
                return
        else:
            db.add_search(user_id)
    else:
        if today_searches >= 3:
            if searches_balance > 0:
                db.use_search(user_id)
                db.add_search(user_id)
                await message.answer(f"🔍 Использован 1 купленный поиск. Осталось: {searches_balance - 1}")
            else:
                await message.answer("❌ Вы использовали 3 бесплатных поиска сегодня. Купите дополнительные поиски в меню «🔍 Купить поиски» или приобретите Premium!")
                return
        else:
            db.add_search(user_id)
    if time.time() - user_cooldowns.get(user_id, 0) < 2:
        await message.answer("⏳ Подождите 2 секунды")
        return
    user_cooldowns[user_id] = time.time()
    await process_generation(message, length)

async def process_generation(message: Message, length: int):
    user_id = message.from_user.id
    msg = await message.answer("⏳ Поиск свободного юзернейма...")
    f_type = user_filters.get(user_id, "🟢 Дефолт")
    u_digits = user_digits.get(user_id, False)
    if f_type == "🪞 Зеркальный 🔒":
        mirrors = db.get_mirror_searches(user_id)
        if mirrors <= 0:
            user_filters[user_id] = "🟢 Дефолт"
            f_type = "🟢 Дефолт"
            try:
                await msg.edit_text("❌ Зеркальные запросы закончились! Переключен на обычный поиск.\n⏳ Поиск...")
            except:
                pass
        else:
            db.use_mirror_search(user_id)
    attempts = 0
    unreliable_warning = False
    while True:
        attempts += 1
        username = engine.generate_random(length, f_type, u_digits)
        if attempts > MAX_SEARCH_ATTEMPTS:
            await msg.edit_text(f"❌ Не удалось найти свободный юзернейм за {MAX_SEARCH_ATTEMPTS} попыток. Попробуйте другой фильтр или длину.")
            return
        is_free, reliability = await check_username_hybrid(username)
        if reliability == "unreliable" and not unreliable_warning:
            await msg.edit_text("⚠️ **ВНИМАНИЕ!**\n\nВсе сессии Telegram в блоке.\nИспользую неточную проверку через t.me.\nРезультат может быть ошибочным.\n\n⏳ Продолжаю поиск...")
            unreliable_warning = True
            await asyncio.sleep(1.5)
            await msg.edit_text("⏳ Поиск свободного юзернейма...")
        if is_free is None:
            await msg.edit_text("❌ Ошибка проверки. Попробуйте позже.")
            return
        if is_free:
            score, verdict = engine.evaluate(username)
            db.add_found_nick(user_id)
            db.inc_found_nicks()
            reliability_text = "\n⚠️ Проверка неточная (сессии в блоке)" if reliability == "unreliable" else ""
            text = f"✅ **Найдено:**\n\n┌ `@{username}`\n├ {len(username)} букв\n├ ⭐️ {score}/10 ({verdict})\n└ 🟢 Свободен{reliability_text}\n\n🔗 https://t.me/{username}"
            await msg.edit_text(text, disable_web_page_preview=True)
            return
        if attempts % 50 == 0:
            try:
                await msg.edit_text(f"⏳ Поиск... (проверено {attempts} вариантов)")
            except:
                pass
        await asyncio.sleep(0.05)

# ---------- ФИЛЬТРЫ ----------
@dp.message(F.text == "⚙️ Фильтры")
async def filters_menu(message: Message):
    user_id = message.from_user.id
    user_data, _, _ = get_user_data_safe(user_id)
    if not user_data:
        await message.answer("❌ Ошибка профиля. Попробуйте /start", reply_markup=get_main_keyboard())
        return
    mirrors = user_data[7]
    is_prem = is_premium_user(user_data)
    if not is_prem and mirrors <= 0 and user_filters.get(user_id) in ["🪞 Зеркальный 🔒", "✨ Красивые", "📖 Со смыслом", "🔤 Любое слово", "🤬 Матерные", "📱 Telegram"]:
        await message.answer("❌ Эти фильтры только для Premium!\n🪞 Зеркальный доступен за зеркальные запросы.", reply_markup=get_main_keyboard())
        return
    curr_filter = user_filters.get(user_id, "🟢 Дефолт")
    curr_digits = "Включены" if user_digits.get(user_id, False) else "Выключены"
    await message.answer(f"⚙️ **Фильтры**\nРежим: {curr_filter}\nЦифры: {curr_digits}\n🪞 Зеркальных: {mirrors}", reply_markup=get_filters_keyboard())

@dp.message(F.text.in_(["🟢 Дефолт", "✨ Красивые", "📖 Со смыслом", "🔤 Любое слово", "🤬 Матерные", "📱 Telegram", "🪞 Зеркальный 🔒"]))
async def set_filter(message: Message):
    user_id = message.from_user.id
    if message.text == "🪞 Зеркальный 🔒":
        mirrors = db.get_mirror_searches(user_id)
        if mirrors <= 0:
            await message.answer("❌ Нет зеркальных запросов!", reply_markup=get_main_keyboard())
            return
    user_filters[user_id] = message.text
    await message.answer(f"✅ Установлен фильтр: {message.text}", reply_markup=get_main_keyboard())

@dp.message(F.text == "🔢 Включить цифры")
async def enable_digits(message: Message):
    user_id = message.from_user.id
    user_digits[user_id] = True
    await message.answer("✅ Цифры ВКЛЮЧЕНЫ", reply_markup=get_main_keyboard())

@dp.message(F.text == "🔠 Выключить цифры")
async def disable_digits(message: Message):
    user_id = message.from_user.id
    user_digits[user_id] = False
    await message.answer("✅ Цифры ВЫКЛЮЧЕНЫ", reply_markup=get_main_keyboard())

# ---------- ПРЕМИУМ ----------
@dp.message(F.text == "💎 Премиум")
async def premium_info(message: Message):
    user_id = message.from_user.id
    searches_balance = db.get_searches_balance(user_id)
    await message.answer(
        f"💎 **ПРЕМИУМ ДОСТУП**\n\n"
        f"Premium даёт:\n"
        f"• 10 поисков в день (вместо 3)\n"
        f"• Все фильтры (Красивые, Со смыслом, Любое слово, Матерные, Telegram)\n"
        f"• Премиум-фильтры (Поиск по слову, Поиск по маске)\n"
        f"• Ловушку\n\n"
        f"🔍 Купленных поисков на балансе: {searches_balance}\n\n"
        f"Купить Premium можно за звёзды: 👤 Профиль → 💎 Купить премиум",
        reply_markup=get_main_keyboard()
    )

# ---------- ЛОВУШКИ ----------
@dp.message(F.text == "🎯 Поставить ловушку")
async def trap_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    user_data, _, _ = get_user_data_safe(user_id)
    if not user_data:
        await message.answer("❌ Ошибка профиля. Попробуйте /start", reply_markup=get_main_keyboard())
        return
    if not is_premium_user(user_data):
        await message.answer("❌ Ловушка только для Premium!", reply_markup=get_main_keyboard())
        return
    active_traps = db.get_user_active_traps(user_id)
    if active_traps:
        await message.answer(f"🎯 Активная ловушка: @{active_traps[0]}", reply_markup=get_trap_keyboard())
    else:
        await state.set_state(Form.waiting_for_trap)
        await message.answer("🎯 Отправьте занятый юзернейм:", reply_markup=get_cancel_keyboard())

@dp.message(F.text == "❌ Отменить ловушку")
async def cancel_trap(message: Message):
    user_id = message.from_user.id
    active_traps = db.get_user_active_traps(user_id)
    if active_traps:
        db.cancel_trap(user_id, active_traps[0])
        await message.answer("Ловушка отменена", reply_markup=get_main_keyboard())

@dp.message(Form.waiting_for_trap)
async def set_trap(message: Message, state: FSMContext):
    user_id = message.from_user.id
    username = message.text.replace("@", "").strip()
    if len(username) < 5 or len(username) > 32 or not re.match(r'^[a-zA-Z0-9_]+$', username):
        await message.answer("❌ Неверный формат", reply_markup=get_cancel_keyboard())
        return
    try:
        is_free, reliability = await check_username_hybrid(username)
        if is_free is None:
            await message.answer("❌ Ошибка проверки. Попробуйте позже.", reply_markup=get_cancel_keyboard())
            return
        if is_free:
            reliability_text = " (проверка неточная)" if reliability == "unreliable" else ""
            await message.answer(f"🎉 @{username} уже свободен{reliability_text}!\nhttps://t.me/{username}", reply_markup=get_cancel_keyboard())
            return
    except:
        pass
    db.add_trap(user_id, username)
    await state.clear()
    await message.answer(f"Ловушка на @{username} установлена", reply_markup=get_main_keyboard())

# ---------- ПРОФИЛЬ ----------
async def send_profile(chat_id: int, user_id: int):
    user_data, active_traps, caught_traps = get_user_data_safe(user_id)
    if not user_data:
        await bot.send_message(chat_id, "❌ Ошибка профиля. Попробуйте /start", reply_markup=get_main_keyboard())
        return
    username, today_s, total_s, found_n, join_d, old_prem, premium_until, mirrors, stars, searches_balance = user_data
    prem_text = "❌ Нет"
    if is_premium_user(user_data):
        try:
            if isinstance(premium_until, str):
                if datetime.strptime(premium_until, "%Y-%m-%d %H:%M:%S") > datetime.now():
                    prem_text = f"✅ До {premium_until}"
            else:
                if premium_until > datetime.now():
                    prem_text = f"✅ До {premium_until.strftime('%Y-%m-%d %H:%M:%S') if hasattr(premium_until, 'strftime') else premium_until}"
        except:
            pass
    ref_count = db.get_referral_count(user_id)
    daily_limit = db.get_daily_limit(user_id)
    limit_text = "∞ (Админ)" if daily_limit == float('inf') else str(daily_limit)
    text = (f"👤 **ПРОФИЛЬ**\n\n"
            f"ID: `{user_id}`\n"
            f"Юзернейм: @{username}\n"
            f"💎 Премиум: {prem_text}\n"
            f"⭐️ Звезд: {stars}\n"
            f"🔍 Купленных поисков: {searches_balance}\n"
            f"📊 Лимит в день: {limit_text}\n"
            f"🔍 Использовано сегодня: {today_s}/{limit_text}\n"
            f"🪞 Зеркальных: {mirrors}\n"
            f"✅ Всего найдено: {found_n}\n"
            f"👥 Рефералов: {ref_count}\n"
            f"🎯 Ловушек: {active_traps} активных / {caught_traps} сработало")
    await bot.send_message(chat_id, text, reply_markup=get_profile_keyboard())

@dp.message(F.text == "👤 Профиль")
async def profile(message: Message):
    await send_profile(message.chat.id, message.from_user.id)

@dp.callback_query(F.data == "back_to_profile")
async def back_to_profile(call: CallbackQuery):
    await call.message.delete()
    await send_profile(call.message.chat.id, call.from_user.id)
    await call.answer()

# ---------- СТАТИСТИКА ----------
@dp.message(F.text == "📊 Статистика")
async def stats(message: Message):
    total_users, found_nicks, active_traps = db.get_stats()
    prem_users = db.get_all_premium_users()
    active_prems = 0
    now = datetime.now()
    for _, _, until in prem_users:
        try:
            if isinstance(until, str):
                if datetime.strptime(until, "%Y-%m-%d %H:%M:%S") > now:
                    active_prems += 1
            else:
                if until > now:
                    active_prems += 1
        except:
            pass
    total_donations = db.get_total_donations()
    sessions_status = get_sessions_status_text()
    await message.answer(
        f"📊 **СТАТИСТИКА**\n\n"
        f"Всего пользователей: {total_users}\n"
        f"Премиум: {active_prems}\n"
        f"Найдено ников: {found_nicks}\n"
        f"Активных ловушек: {active_traps}\n"
        f"💰 Собрано донатов: {total_donations} USDT\n\n"
        f"{sessions_status}",
        reply_markup=get_main_keyboard(),
        parse_mode="Markdown"
    )

# ---------- ОЦЕНКА ----------
@dp.message(F.text == "⭐️ Оценить юзернейм")
async def evaluate_start(message: Message, state: FSMContext):
    await state.set_state(Form.eval_username)
    await message.answer("Отправьте юзернейм для оценки:", reply_markup=get_cancel_keyboard())

@dp.message(Form.eval_username)
async def evaluate_process(message: Message, state: FSMContext):
    target = message.text.replace("@", "").strip()
    if len(target) < 5 or len(target) > 32 or not re.match(r'^[a-zA-Z0-9_]+$', target):
        await message.answer("❌ Неверный формат", reply_markup=get_cancel_keyboard())
        return
    score, verdict = engine.evaluate(target)
    await message.answer(f"📊 Оценка @{target}: ⭐ {score}/10 ({verdict})", reply_markup=get_main_keyboard())
    await state.clear()

# ---------- ПОПОЛНЕНИЕ БАЛАНСА ----------
@dp.message(F.text == "⭐️ Пополнить баланс")
async def topup_balance(message: Message):
    builder = InlineKeyboardBuilder()
    for stars, usdt in STARS_TO_USDT.items():
        builder.button(text=f"⭐️ {stars} звёзд (${usdt})", callback_data=f"topup_{stars}")
    builder.button(text="🔙 Назад", callback_data="back_to_profile")
    builder.adjust(1)
    await message.answer(
        "💎 **Пополнение баланса через USDT**\n\n"
        "Выберите количество звёзд:",
        reply_markup=builder.as_markup()
    )

@dp.callback_query(F.data.startswith("topup_"))
async def topup_selected(call: CallbackQuery):
    parts = call.data.split("_")
    if len(parts) < 2:
        await call.answer("Ошибка", show_alert=True)
        return
    stars = int(parts[1])
    usdt = STARS_TO_USDT[stars]
    user_id = call.from_user.id
    description = f"Пополнение баланса на {stars} звёзд"
    result = create_crypto_invoice(usdt, description)
    if not result or not result.get("ok"):
        error_msg = "❌ Ошибка создания счёта.\n\n"
        if result and result.get("error"):
            error_msg += f"Код ошибки: {result.get('error')}\n"
        error_msg += "Проверьте настройки Crypto Bot или попробуйте позже."
        await call.message.edit_text(error_msg)
        await call.answer("Ошибка", show_alert=True)
        return
    invoice_id = result["result"]["invoice_id"]
    pay_url = result["result"]["pay_url"]
    db.add_invoice(invoice_id, user_id, stars, 0, usdt, "topup")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить", url=pay_url)],
        [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"check_invoice_{invoice_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_profile")]
    ])
    await call.message.edit_text(
        f"💎 **Оплата {stars} звёзд**\n\n"
        f"💰 Сумма: {usdt} USDT\n\n"
        f"Нажмите кнопку ниже, чтобы оплатить через Crypto Bot.\n"
        f"После оплаты нажмите «Проверить оплату».",
        reply_markup=keyboard
    )
    await call.answer()

@dp.callback_query(F.data.startswith("check_invoice_"))
async def check_invoice(call: CallbackQuery):
    parts = call.data.split("_")
    if len(parts) < 3:
        await call.answer("Ошибка", show_alert=True)
        return
    invoice_id = parts[2]
    user_id = call.from_user.id
    invoice = db.get_invoice(invoice_id)
    if not invoice:
        await call.answer("Счёт не найден", show_alert=True)
        return
    if invoice[5] == "paid":
        await call.answer("Этот счёт уже оплачен и обработан", show_alert=True)
        return
    status = get_invoice_status(invoice_id)
    if status == "active":
        await call.answer("Счёт ещё не оплачен. Оплатите по ссылке и попробуйте снова.", show_alert=True)
    elif status == "paid":
        stars = invoice[2]
        db.add_stars(user_id, stars)
        db.update_invoice_status(invoice_id, "paid")
        await call.message.edit_text(
            f"✅ **Оплата подтверждена!**\n\n"
            f"Вам начислено {stars} звёзд на баланс.\n"
            f"Спасибо за пополнение! 🎉"
        )
        await call.answer("Оплата подтверждена! Звёзды зачислены.", show_alert=True)
    else:
        await call.answer("Ошибка проверки. Попробуйте позже.", show_alert=True)

# ---------- ПОДДЕРЖКА БОТА ----------
@dp.message(F.text == "❤️ Поддержать бота")
async def donate_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if not can_donate(user_id):
        remaining = int(60 - (time.time() - donation_cooldowns.get(user_id, 0)))
        await message.answer(
            f"⏳ **Подождите {remaining} секунд** перед следующим донатом.\n"
            f"Кулдаун нужен чтобы избежать спама. Спасибо за понимание! 🙏",
            reply_markup=get_main_keyboard()
        )
        return
    await state.set_state(Form.donate_amount)
    await message.answer(
        f"💝 **Поддержать разработку бота**\n\n"
        f"Минимальная сумма: {MIN_DONATE_USDT} USDT\n"
        f"Максимум: без ограничений\n\n"
        f"После оплаты все пользователи увидят вашу поддержку!\n\n"
        f"Введите сумму в USDT (например: 0.01, 1, 5.5):",
        reply_markup=get_cancel_keyboard()
    )

@dp.message(Form.donate_amount)
async def donate_amount_input(message: Message, state: FSMContext):
    try:
        amount = float(message.text.strip().replace(',', '.'))
        if amount < MIN_DONATE_USDT:
            await message.answer(f"❌ Минимальная сумма поддержки: {MIN_DONATE_USDT} USDT", reply_markup=get_cancel_keyboard())
            return
        if amount > 100000:
            await message.answer(f"❌ Слишком большая сумма. Максимум 100,000 USDT", reply_markup=get_cancel_keyboard())
            return
    except:
        await message.answer("❌ Введите число. Пример: 0.01, 5, 10.5", reply_markup=get_cancel_keyboard())
        return
    await state.update_data(donate_amount=amount)
    user_id = message.from_user.id
    username = message.from_user.username or f"User{user_id}"
    description = f"Поддержка бота от @{username}"
    result = create_crypto_invoice(amount, description)
    if not result or not result.get("ok"):
        error_msg = "❌ Ошибка создания счёта.\n\n"
        if result and result.get("error"):
            error_msg += f"Код ошибки: {result.get('error')}\n"
        error_msg += "Проверьте настройки Crypto Bot или попробуйте позже."
        await message.answer(error_msg, reply_markup=get_main_keyboard())
        await state.clear()
        return
    invoice_id = result["result"]["invoice_id"]
    pay_url = result["result"]["pay_url"]
    db.add_invoice(invoice_id, user_id, 0, 0, amount, "donate")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить донат", url=pay_url)],
        [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"check_donate_{invoice_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])
    await message.answer(
        f"💝 **Поддержка бота**\n\n"
        f"💰 Сумма: {amount} USDT\n\n"
        f"Нажмите кнопку ниже для оплаты.\n"
        f"После оплаты все пользователи получат уведомление о вашей поддержке! 🎉",
        reply_markup=keyboard
    )
    await state.clear()

async def broadcast_donation(username: str, amount_usdt: float):
    all_users = db.get_all_user_ids()
    text = f"🎉 **Пользователь @{username} поддержал нашего бота на {amount_usdt} USDT!**\n\nСпасибо ему/ей за помощь в развитии проекта! 🙌"
    for uid in all_users:
        try:
            await bot.send_message(uid, text)
            await asyncio.sleep(0.05)
        except:
            pass

@dp.callback_query(F.data.startswith("check_donate_"))
async def check_donate(call: CallbackQuery):
    parts = call.data.split("_")
    if len(parts) < 3:
        await call.answer("Ошибка", show_alert=True)
        return
    invoice_id = parts[2]
    user_id = call.from_user.id
    invoice = db.get_invoice(invoice_id)
    if not invoice:
        await call.answer("Счёт не найден", show_alert=True)
        return
    if invoice[5] == "paid":
        await call.answer("Этот донат уже обработан", show_alert=True)
        return
    status = get_invoice_status(invoice_id)
    if status == "active":
        await call.answer("Счёт ещё не оплачен. Оплатите по ссылке и попробуйте снова.", show_alert=True)
    elif status == "paid":
        amount_usdt = invoice[4]
        username = call.from_user.username or f"User{user_id}"
        db.add_donation(user_id, username, amount_usdt, invoice_id)
        db.update_invoice_status(invoice_id, "paid")
        set_donate_cooldown(user_id)
        await call.message.edit_text(
            f"✅ **Спасибо за поддержку!**\n\n"
            f"Вы пожертвовали {amount_usdt} USDT на развитие бота.\n"
            f"Огромное спасибо от всей команды! 🙏\n\n"
            f"⏳ Следующий донат можно будет сделать через 60 секунд."
        )
        asyncio.create_task(broadcast_donation(username, amount_usdt))
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(admin_id, f"💝 НОВЫЙ ДОНАТ!\n\nПользователь: @{username} (ID: {user_id})\nСумма: {amount_usdt} USDT\nВсего донатов: {db.get_total_donations()} USDT")
            except:
                pass
        await call.answer("Спасибо за поддержку!", show_alert=True)
    else:
        await call.answer("Ошибка проверки. Попробуйте позже.", show_alert=True)

@dp.callback_query(F.data == "back_to_main")
async def back_to_main_from_donate(call: CallbackQuery):
    await call.message.delete()
    await call.message.answer(WELCOME_TEXT, reply_markup=get_main_keyboard())
    await call.answer()

# ---------- КУПЛЯ ПРЕМИУМА ЗА ЗВЁЗДЫ ----------
@dp.message(F.text == "💎 Купить премиум")
async def buy_premium_menu(message: Message):
    await message.answer("Выберите срок:", reply_markup=get_premium_prices_keyboard())

async def buy_premium_handler(message: Message, days: int, price: int):
    user_id = message.from_user.id
    stars = db.get_stars(user_id)
    if stars < price:
        await message.answer(f"❌ Недостаточно звёзд! Нужно {price}, у вас {stars}", reply_markup=get_profile_keyboard())
        return
    if db.remove_stars(user_id, price):
        new_until = db.add_premium_time(user_id, timedelta(days=days))
        await message.answer(f"✅ Premium активирован до {new_until}!\n\nТеперь у вас 10 поисков в день и доступ ко всем фильтрам!", reply_markup=get_profile_keyboard())
    else:
        await message.answer("❌ Ошибка", reply_markup=get_profile_keyboard())

@dp.message(F.text == "1 день (15⭐)")
async def buy_premium_1day(message: Message):
    await buy_premium_handler(message, 1, 15)

@dp.message(F.text == "3 дня (40⭐)")
async def buy_premium_3day(message: Message):
    await buy_premium_handler(message, 3, 40)

@dp.message(F.text == "7 дней (75⭐)")
async def buy_premium_7day(message: Message):
    await buy_premium_handler(message, 7, 75)

@dp.message(F.text == "14 дней (125⭐)")
async def buy_premium_14day(message: Message):
    await buy_premium_handler(message, 14, 125)

@dp.message(F.text == "30 дней (200⭐)")
async def buy_premium_30day(message: Message):
    await buy_premium_handler(message, 30, 200)

# ---------- РЕФЕРАЛЬНАЯ ССЫЛКА ----------
@dp.message(F.text == "🔗 Реферальная ссылка")
async def referral_link(message: Message):
    user_id = message.from_user.id
    code = db.get_ref_code(user_id)
    bot_username = await get_bot_username()
    link = f"https://t.me/{bot_username}?start=ref_{code}"
    await message.answer(
        f"🔗 **Ваша реферальная ссылка:**\n\n{link}\n\nПриглашённый +15⭐, вы +25⭐",
        reply_markup=get_main_keyboard()
    )

# ---------- ПРОМОКОДЫ ----------
@dp.message(F.text == "🎫 Активировать промокод")
async def activate_promo_start(message: Message, state: FSMContext):
    await state.set_state(Form.activate_promo)
    await message.answer("Введите промокод:", reply_markup=get_cancel_keyboard())

@dp.message(Form.activate_promo)
async def activate_promo_process(message: Message, state: FSMContext):
    code = message.text.strip()
    success, msg, _ = db.use_promocode(code, message.from_user.id)
    await state.clear()
    await message.answer(msg, reply_markup=get_main_keyboard())

# ---------- МАРКЕТ ----------
@dp.message(F.text == "🛒 Маркет")
async def market_main(message: Message):
    await message.answer("🛒 **Маркет юзернеймов**\n\nВыберите действие:", reply_markup=get_market_main_keyboard())

@dp.message(F.text == "📦 Мои лоты")
async def my_lots(message: Message):
    user_id = message.from_user.id
    if db.is_market_banned(user_id):
        await message.answer("❌ Вы в чёрном списке маркета и не можете просматривать свои лоты.", reply_markup=get_market_main_keyboard())
        return
    lots = db.get_user_market_lots(user_id)
    if not lots:
        await message.answer("У вас нет активных лотов.", reply_markup=get_market_main_keyboard())
        return
    text = "📦 **Ваши лоты:**\n\n"
    builder = InlineKeyboardBuilder()
    for lid, uname, price, desc, created in lots:
        text += f"ID: {lid} | @{uname} | {price}⭐\n{desc[:40] if desc else ''}\n\n"
        builder.button(text=f"🗑 Удалить {uname}", callback_data=f"del_lot_{lid}")
    builder.button(text="🔙 Назад", callback_data="back_to_market")
    builder.adjust(1)
    await message.answer(text, reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("del_lot_"))
async def delete_lot_callback(call: CallbackQuery):
    parts = call.data.split("_")
    if len(parts) < 3:
        await call.answer("Ошибка", show_alert=True)
        return
    lot_id = int(parts[2])
    user_id = call.from_user.id
    if db.delete_market_lot(lot_id, user_id):
        await call.answer("Лот удалён", show_alert=True)
        await call.message.delete()
        await call.message.answer("Лот удалён.", reply_markup=get_market_main_keyboard())
    else:
        await call.answer("Не удалось удалить лот", show_alert=True)

@dp.message(F.text == "💎 Продать")
async def sell_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if db.is_market_banned(user_id):
        await message.answer("❌ Вы в чёрном списке маркета и не можете продавать лоты.", reply_markup=get_market_main_keyboard())
        return
    await state.set_state(Form.market_sell_username)
    await message.answer("Отправьте юзернейм (без @), который хотите продать:", reply_markup=get_cancel_keyboard())

@dp.message(Form.market_sell_username)
async def sell_username(message: Message, state: FSMContext):
    username = message.text.strip().lower()
    if not re.match(r'^[a-z0-9_]{5,}$', username):
        await message.answer("❌ Неверный формат. Только латиница, цифры, _, минимум 5 символов.", reply_markup=get_cancel_keyboard())
        return
    await state.update_data(market_username=username)
    await state.set_state(Form.market_sell_desc)
    await message.answer("Введите описание (или '-' чтобы пропустить):", reply_markup=get_cancel_keyboard())

@dp.message(Form.market_sell_desc)
async def sell_desc(message: Message, state: FSMContext):
    desc = message.text.strip()
    if desc == "-":
        desc = ""
    if len(desc) > 100:
        desc = desc[:100]
    await state.update_data(market_desc=desc)
    await state.set_state(Form.market_sell_price)
    await message.answer("Введите цену в звёздах (целое число):", reply_markup=get_cancel_keyboard())

@dp.message(Form.market_sell_price)
async def sell_price(message: Message, state: FSMContext):
    try:
        price = int(message.text.strip())
        if price <= 0:
            raise
    except:
        await message.answer("Цена должна быть положительным целым числом.", reply_markup=get_cancel_keyboard())
        return
    data = await state.get_data()
    username = data.get("market_username")
    desc = data.get("market_desc")
    user_id = message.from_user.id
    lot_id = db.add_market_lot(user_id, username, price, desc)
    if lot_id is None:
        await message.answer("❌ Вы в чёрном списке и не можете создавать лоты.", reply_markup=get_market_main_keyboard())
        return
    await state.clear()
    await message.answer(f"✅ Лот #{lot_id} создан!\n@{username} выставлен за {price}⭐", reply_markup=get_market_main_keyboard())

market_offset = {}

@dp.message(F.text == "📋 Все лоты")
async def list_all_lots(message: Message):
    user_id = message.from_user.id
    market_offset[user_id] = 0
    await show_lots_page(message, user_id, 0)

async def show_lots_page(message: Message, user_id: int, offset: int):
    lots = db.get_market_lots(offset, 7)
    if not lots:
        await message.answer("Нет активных лотов.", reply_markup=get_market_main_keyboard())
        return
    text = f"📋 **Все лоты (страница {offset//7 + 1}):**\n\n"
    builder = InlineKeyboardBuilder()
    for lot in lots:
        lid, seller, uname, price, desc, created = lot
        text += f"ID: {lid} | @{uname} | {price}⭐\n{desc[:40] if desc else ''}\n\n"
        builder.button(text=f"💰 Купить {uname}", callback_data=f"view_lot_{lid}")
    builder.adjust(1)
    nav_builder = InlineKeyboardBuilder()
    if offset >= 7:
        nav_builder.button(text="◀️ Назад", callback_data=f"lots_page_{offset-7}")
    if len(lots) == 7:
        nav_builder.button(text="Вперёд ▶️", callback_data=f"lots_page_{offset+7}")
    nav_builder.button(text="🔙 В меню", callback_data="back_to_market")
    nav_builder.adjust(2)
    for btn in nav_builder.buttons:
        builder.add(btn)
    await message.answer(text, reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("lots_page_"))
async def lots_page_callback(call: CallbackQuery):
    parts = call.data.split("_")
    if len(parts) < 3:
        await call.answer("Ошибка", show_alert=True)
        return
    offset = int(parts[2])
    user_id = call.from_user.id
    await call.message.delete()
    await show_lots_page(call.message, user_id, offset)

@dp.callback_query(F.data == "back_to_market")
async def back_to_market_callback(call: CallbackQuery):
    await call.message.delete()
    await call.message.answer("🛒 Маркет", reply_markup=get_market_main_keyboard())
    await call.answer()

@dp.callback_query(F.data.startswith("view_lot_"))
async def view_lot(call: CallbackQuery):
    parts = call.data.split("_")
    if len(parts) < 3:
        await call.answer("Ошибка", show_alert=True)
        return
    lot_id = int(parts[2])
    lot = db.get_market_lot(lot_id)
    if not lot:
        await call.answer("Лот больше не существует", show_alert=True)
        return
    lid, seller, uname, price, desc, created = lot
    seller_profile = db.get_profile(seller)
    seller_name = seller_profile[0][0] if seller_profile and seller_profile[0] else str(seller)
    avg_rating = db.get_seller_avg_rating(seller)
    reviews = db.get_seller_reviews(seller)[:3]
    text = f"**Лот #{lid}**\n\n"
    text += f"👤 Владелец: @{seller_name}\n"
    text += f"🔹 Юзернейм: @{uname}\n"
    text += f"💰 Цена: {price}⭐\n"
    text += f"📝 Описание: {desc if desc else 'нет'}\n"
    text += f"⭐️ Средняя оценка продавца: {avg_rating}/5\n"
    if reviews:
        text += "\n📝 **Последние отзывы:**\n"
        for r in reviews[:3]:
            text += f"⭐️ {r[0]}/5 | {r[1][:50]}\n"
    builder = InlineKeyboardBuilder()
    builder.button(text="💰 Купить лот", callback_data=f"buy_lot_{lid}")
    builder.button(text="🔙 Назад", callback_data="back_to_market")
    builder.adjust(1)
    await call.message.edit_text(text, reply_markup=builder.as_markup())
    await call.answer()

@dp.callback_query(F.data.startswith("buy_lot_"))
async def buy_lot(call: CallbackQuery):
    parts = call.data.split("_")
    if len(parts) < 3:
        await call.answer("Ошибка", show_alert=True)
        return
    lot_id = int(parts[2])
    lot = db.get_market_lot(lot_id)
    if not lot:
        await call.answer("Лот уже продан или не существует", show_alert=True)
        return
    lid, seller, uname, price, desc, created = lot
    if seller == call.from_user.id:
        await call.answer("Нельзя купить свой собственный лот", show_alert=True)
        return
    if db.is_market_banned(call.from_user.id):
        await call.answer("Вы в чёрном списке маркета и не можете покупать лоты", show_alert=True)
        return
    if db.is_market_banned(seller):
        await call.answer("Продавец в чёрном списке, покупка невозможна", show_alert=True)
        return
    order_id = db.create_order(lot_id, call.from_user.id, seller)
    if order_id is None:
        await call.answer("Ошибка создания заказа. Возможно, вы или продавец в ЧС.", show_alert=True)
        return
    seller_profile = db.get_profile(seller)
    seller_name = seller_profile[0][0] if seller_profile and seller_profile[0] else str(seller)
    text = f"🛒 **Заказ #{order_id}**\n\n"
    text += f"Товар: @{uname}\n"
    text += f"Цена: {price}⭐\n"
    text += f"Продавец: @{seller_name}\n"
    text += f"Покупатель: @{call.from_user.username or call.from_user.id}\n\n"
    text += "Для завершения покупки свяжитесь с продавцом и нажмите **Подтвердить заказ** после получения юзернейма.\n"
    text += "Если возникнут проблемы, используйте кнопку **Открыть спор**."
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить заказ", callback_data=f"confirm_order_{order_id}")
    builder.button(text="⚠️ Открыть спор", callback_data=f"open_dispute_{order_id}")
    builder.button(text="🔙 Назад", callback_data=f"view_lot_{lot_id}")
    builder.adjust(1)
    await call.message.edit_text(text, reply_markup=builder.as_markup())
    await call.answer()
    try:
        await bot.send_message(seller, f"🆕 Новый заказ #{order_id}!\nПокупатель: @{call.from_user.username or call.from_user.id}\nТовар: @{uname}\nЦена: {price}⭐\nДля открытия спора используйте кнопку ниже.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⚠️ Открыть спор", callback_data=f"open_dispute_{order_id}")]]))
    except Exception as e:
        print(f"Ошибка уведомления продавца: {e}")

# ---------- ПОДТВЕРЖДЕНИЕ ЗАКАЗА И ОТЗЫВ ----------
@dp.callback_query(F.data.startswith("confirm_order_"))
async def confirm_order(call: CallbackQuery, state: FSMContext):
    parts = call.data.split("_")
    if len(parts) < 3:
        await call.answer("Ошибка", show_alert=True)
        return
    order_id = int(parts[2])
    order = db.get_order(order_id)
    if not order:
        await call.answer("Заказ не найден", show_alert=True)
        return
    if order[2] != call.from_user.id:
        await call.answer("Это не ваш заказ", show_alert=True)
        return
    if order[4] != 'pending':
        await call.answer("Заказ уже обработан", show_alert=True)
        return
    db.confirm_order(order_id)
    seller_id = order[3]
    try:
        await bot.send_message(seller_id, f"✅ Покупатель @{call.from_user.username or call.from_user.id} подтвердил получение юзернейма по заказу #{order_id}. Сделка завершена.")
    except Exception as e:
        print(f"Ошибка уведомления продавца: {e}")
    await call.answer("Заказ подтверждён! Спасибо за покупку.", show_alert=True)
    lot_id = order[1]
    lot = db.get_market_lot(lot_id)
    if lot:
        seller_id = lot[1]
        await state.update_data(review_seller_id=seller_id, review_order_id=order_id)
        await call.message.answer(
            "📝 **Оцените сделку!**\n\nПожалуйста, оцените продавца по шкале от 1 до 5 звёзд:",
            reply_markup=get_review_keyboard(seller_id, order_id)
        )
    await call.message.delete()

@dp.callback_query(F.data.startswith("rate_"))
async def rate_seller(call: CallbackQuery, state: FSMContext):
    parts = call.data.split("_")
    if len(parts) < 4:
        await call.answer("Ошибка", show_alert=True)
        return
    seller_id = int(parts[1])
    order_id = int(parts[2])
    rating = int(parts[3])
    await state.update_data(review_seller_id=seller_id, review_order_id=order_id, review_rating=rating)
    await state.set_state(Form.review_text)
    await call.message.answer("✍️ Напишите текстовый отзыв (или '-' чтобы пропустить):", reply_markup=get_cancel_keyboard())
    await call.answer()

@dp.message(Form.review_text)
async def review_text(message: Message, state: FSMContext):
    data = await state.get_data()
    seller_id = data.get("review_seller_id")
    order_id = data.get("review_order_id")
    rating = data.get("review_rating")
    text = message.text.strip()
    if text == "-":
        text = ""
    buyer_id = message.from_user.id
    db.add_review(seller_id, buyer_id, rating, text)
    await state.clear()
    await message.answer("✅ Спасибо за отзыв! Он поможет другим пользователям.", reply_markup=get_main_keyboard())

# ---------- ОТКРЫТИЕ СПОРА ----------
@dp.callback_query(F.data.startswith("open_dispute_"))
async def open_dispute(call: CallbackQuery, state: FSMContext):
    parts = call.data.split("_")
    if len(parts) < 3:
        await call.answer("Ошибка", show_alert=True)
        return
    order_id = int(parts[2])
    order = db.get_order(order_id)
    if not order:
        await call.answer("Заказ не найден", show_alert=True)
        return
    if order[2] != call.from_user.id and order[3] != call.from_user.id:
        await call.answer("Вы не участник сделки", show_alert=True)
        return
    await state.update_data(dispute_order_id=order_id)
    await state.set_state(Form.dispute_reason)
    await call.message.answer("📝 Напишите причину спора (подробно):", reply_markup=get_cancel_keyboard())
    await call.answer()

@dp.message(Form.dispute_reason)
async def dispute_reason(message: Message, state: FSMContext):
    data = await state.get_data()
    order_id = data.get("dispute_order_id")
    order = db.get_order(order_id)
    if not order:
        await message.answer("Ошибка", reply_markup=get_main_keyboard())
        await state.clear()
        return
    reason = message.text.strip()
    dispute_id = db.add_dispute(order_id, order[2], order[3], reason, message.from_user.id)
    await state.clear()
    await message.answer(f"⚖️ Спор #{dispute_id} открыт. Администратор рассмотрит его в ближайшее время.", reply_markup=get_main_keyboard())
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, f"⚠️ Новый спор #{dispute_id}\nПокупатель: {order[2]}\nПродавец: {order[3]}\nПричина: {reason}\nОткрыл: {'покупатель' if message.from_user.id == order[2] else 'продавец'}")
        except Exception as e:
            print(f"Ошибка уведомления админа: {e}")

# ---------- АДМИН: СПОРЫ ----------
@dp.message(F.text == "⚖️ Споры")
async def admin_disputes(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    disputes = db.get_open_disputes()
    if not disputes:
        await message.answer("Нет открытых споров.")
        return
    for d in disputes:
        opener = "покупатель" if d[8] == d[2] else "продавец"
        buyer_profile = db.get_profile(d[2])
        seller_profile = db.get_profile(d[3])
        buyer_info = f"{d[2]} (@{buyer_profile[0][0] if buyer_profile and buyer_profile[0] else '?'})"
        seller_info = f"{d[3]} (@{seller_profile[0][0] if seller_profile and seller_profile[0] else '?'})"
        text = f"⚖️ Спор #{d[0]} | Заказ #{d[1]}\nПокупатель: {buyer_info}\nПродавец: {seller_info}\nПричина: {d[4][:100]}\nОткрыл: {opener}"
        builder = InlineKeyboardBuilder()
        builder.button(text="🔍 Рассмотреть", callback_data=f"admin_resolve_dispute_{d[0]}")
        builder.adjust(1)
        await message.answer(text, reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("admin_resolve_dispute_"))
async def admin_resolve_dispute(call: CallbackQuery):
    parts = call.data.split("_")
    if len(parts) < 4:
        await call.answer("Ошибка", show_alert=True)
        return
    dispute_id = int(parts[3])
    dispute = None
    for d in db.get_open_disputes():
        if d[0] == dispute_id:
            dispute = d
            break
    if not dispute:
        await call.answer("Спор уже решён", show_alert=True)
        return
    temp_dispute_data[call.from_user.id] = {
        "dispute_id": dispute_id,
        "order_id": dispute[1],
        "buyer_id": dispute[2],
        "seller_id": dispute[3],
        "reason": dispute[4]
    }
    buyer_profile = db.get_profile(dispute[2])
    seller_profile = db.get_profile(dispute[3])
    buyer_info = f"{dispute[2]} (@{buyer_profile[0][0] if buyer_profile and buyer_profile[0] else '?'})"
    seller_info = f"{dispute[3]} (@{seller_profile[0][0] if seller_profile and seller_profile[0] else '?'})"
    text = f"⚖️ Спор #{dispute[0]}\nЗаказ #{dispute[1]}\nПокупатель: {buyer_info}\nПродавец: {seller_info}\nПричина: {dispute[4][:200]}"
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Решить в пользу покупателя", callback_data=f"resolve_dispute_{dispute_id}_buyer")
    builder.button(text="✅ Решить в пользу продавца", callback_data=f"resolve_dispute_{dispute_id}_seller")
    builder.button(text="🔙 Назад", callback_data="back_to_disputes")
    builder.adjust(1)
    await call.message.edit_text(text, reply_markup=builder.as_markup())
    await call.answer()

@dp.callback_query(F.data.startswith("resolve_dispute_"))
async def resolve_dispute_decision(call: CallbackQuery, state: FSMContext):
    parts = call.data.split("_")
    if len(parts) < 4:
        await call.answer("Ошибка", show_alert=True)
        return
    dispute_id = int(parts[2])
    decision = parts[3]
    temp_data = temp_dispute_data.get(call.from_user.id, {})
    if not temp_data or temp_data.get("dispute_id") != dispute_id:
        await call.answer("Ошибка: данные спора не найдены", show_alert=True)
        return
    if decision == "buyer":
        winner_id = temp_data["buyer_id"]
        loser_id = temp_data["seller_id"]
        winner_role = "покупатель"
    else:
        winner_id = temp_data["seller_id"]
        loser_id = temp_data["buyer_id"]
        winner_role = "продавец"
    temp_data["winner_id"] = winner_id
    temp_data["loser_id"] = loser_id
    temp_data["winner_role"] = winner_role
    temp_dispute_data[call.from_user.id] = temp_data
    await state.update_data(dispute_loser_id=loser_id, dispute_winner_id=winner_id)
    await state.set_state(Form.dispute_ban_reason)
    await call.message.answer(f"Введите причину для добавления пользователя {loser_id} в ЧС:", reply_markup=get_cancel_keyboard())
    await call.answer()

@dp.message(Form.dispute_ban_reason)
async def dispute_ban_reason(message: Message, state: FSMContext):
    reason = message.text.strip()
    await state.update_data(ban_reason=reason)
    await state.set_state(Form.dispute_ban_duration)
    await message.answer("Выберите срок блокировки:", reply_markup=get_ban_duration_keyboard())

@dp.callback_query(F.data.startswith("ban_duration_"))
async def dispute_ban_duration(call: CallbackQuery, state: FSMContext):
    duration_str = call.data.split("_")[2]
    data = await state.get_data()
    reason = data.get("ban_reason", "Нарушение правил маркета")
    loser_id = data.get("dispute_loser_id")
    temp_data = temp_dispute_data.get(call.from_user.id, {})
    dispute_id = temp_data.get("dispute_id")
    order_id = temp_data.get("order_id")
    winner_role = temp_data.get("winner_role", "покупатель")
    if duration_str == "0":
        until = None
        duration_text = "бессрочно"
    else:
        unit = duration_str[-1]
        value = int(duration_str[:-1])
        if unit == 'h':
            delta = timedelta(hours=value)
            duration_text = f"{value} часов"
        elif unit == 'd':
            delta = timedelta(days=value)
            duration_text = f"{value} дней"
        elif unit == 'm':
            delta = timedelta(days=value * 30)
            duration_text = f"{value} месяцев"
        elif unit == 'y':
            delta = timedelta(days=value * 365)
            duration_text = f"{value} лет"
        else:
            delta = None
            duration_text = "бессрочно"
        until = datetime.now() + delta if delta else None
    db.add_to_blacklist(loser_id, reason, call.from_user.id, until)
    resolution = f"Победитель: {winner_role}"
    db.resolve_dispute(dispute_id, call.from_user.id, resolution)
    if order_id:
        db.cursor.execute("SELECT lot_id FROM market_orders WHERE id=?", (order_id,))
        row = db.cursor.fetchone()
        if row:
            db.cursor.execute("DELETE FROM market_lots WHERE id=?", (row[0],))
            db.conn.commit()
    await state.clear()
    if call.from_user.id in temp_dispute_data:
        del temp_dispute_data[call.from_user.id]
    await call.message.edit_text(f"✅ Спор #{dispute_id} решён!\nПользователь {loser_id} добавлен в ЧС на {duration_text}.\nПричина: {reason}")
    await call.answer("Спор решён", show_alert=True)

@dp.callback_query(F.data == "cancel_ban")
async def cancel_ban(call: CallbackQuery, state: FSMContext):
    await state.clear()
    if call.from_user.id in temp_dispute_data:
        del temp_dispute_data[call.from_user.id]
    await call.message.edit_text("❌ Добавление в ЧС отменено.")
    await call.answer()

@dp.callback_query(F.data == "back_to_disputes")
async def back_to_disputes(call: CallbackQuery):
    await call.message.delete()
    await admin_disputes(call.message)

# ---------- АДМИН ПАНЕЛЬ ----------
@dp.message(F.text == "ℹ️ Информация")
async def admin_info(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    new_u, new_s, total_s = db.get_admin_info_stats()
    await message.answer(f"ℹ️ **Информация (за сегодня)**\n\n👥 Новых: {new_u}\n🔍 Поисков: {new_s}\n📊 Всего: {total_s}")

@dp.message(F.text == "👥 Список премиум")
async def admin_premium_list(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    users = db.get_all_premium_users()
    now = datetime.now()
    active_users = []
    for uid, uname, until in users:
        try:
            if isinstance(until, str):
                if datetime.strptime(until, "%Y-%m-%d %H:%M:%S") > now:
                    active_users.append(f"• `{uid}` (@{uname}) — до {until}")
            else:
                if until > now:
                    active_users.append(f"• `{uid}` (@{uname}) — до {until.strftime('%Y-%m-%d %H:%M:%S') if hasattr(until, 'strftime') else until}")
        except:
            pass
    msg = "💎 **Активные Premium:**\n\n" + "\n".join(active_users) if active_users else "Активных Premium нет"
    await message.answer(msg)

@dp.message(F.text == "💎 Выдать премиум")
async def admin_give_premium(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    await state.set_state(Form.admin_give_prem)
    await message.answer("✍️ Отправьте ID и время (15d, 2m, 1y)", reply_markup=get_cancel_keyboard())

@dp.message(F.text == "❌ Забрать премиум")
async def admin_take_premium(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    await state.set_state(Form.admin_take_prem)
    await message.answer("✍️ Отправьте ID пользователя", reply_markup=get_cancel_keyboard())

@dp.message(F.text == "🪞 Выдать зеркала")
async def admin_give_mirrors(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    await state.set_state(Form.admin_give_mirrors)
    await message.answer("✍️ Пример: `123456789 50`", reply_markup=get_cancel_keyboard())

@dp.message(F.text == "⭐️ Выдать звёзды")
async def admin_give_stars(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    await state.set_state(Form.admin_give_stars)
    await message.answer("✍️ Пример: `123456789 50`", reply_markup=get_cancel_keyboard())

@dp.message(F.text == "⭐️ Забрать звёзды")
async def admin_take_stars(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    await state.set_state(Form.admin_take_stars)
    await message.answer("✍️ Пример: `123456789 10`", reply_markup=get_cancel_keyboard())

@dp.message(F.text == "🔍 Выдать поиски")
async def admin_give_searches(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    await state.set_state(Form.admin_give_searches)
    await message.answer("✍️ Пример: `123456789 50` (ID количество)", reply_markup=get_cancel_keyboard())

@dp.message(Form.admin_give_searches, F.text != "🔙 Отмена")
async def admin_give_searches_input(message: Message, state: FSMContext):
    parts = message.text.split()
    if len(parts) == 2:
        try:
            target_id = int(parts[0])
            amount = int(parts[1])
            db.add_searches_balance(target_id, amount)
            await state.clear()
            await message.answer(f"✅ {amount} поисков выдано {target_id}", reply_markup=get_admin_keyboard())
        except:
            await message.answer("❌ Ошибка")
    else:
        await message.answer("❌ Формат: ID количество")

@dp.message(F.text == "📢 Рассылка")
async def admin_broadcast(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    await state.set_state(Form.admin_broadcast)
    await message.answer("📢 Отправьте сообщение для рассылки:", reply_markup=get_cancel_keyboard())

@dp.message(F.text == "🎫 Промокоды")
async def admin_promocodes(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    await message.answer("🎫 **Управление промокодами**", reply_markup=get_promocode_admin_keyboard())

@dp.message(F.text == "📊 Донаты")
async def admin_donations(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    donations = db.get_donations_history(50)
    total = db.get_total_donations()
    if not donations:
        await message.answer("💝 История донатов пуста.")
        return
    text = f"💝 **ИСТОРИЯ ДОНАТОВ**\n\nВсего собрано: {total} USDT\n\n"
    for don in donations:
        don_id, user_id, username, amount, created_at = don
        text += f"• @{username} (ID: {user_id}) — {amount} USDT — {created_at[:16]}\n"
    if len(text) > 4000:
        parts = [text[i:i+4000] for i in range(0, len(text), 4000)]
        for part in parts:
            await message.answer(part)
    else:
        await message.answer(text)

@dp.message(F.text == "➕ Добавить в ЧС")
async def admin_add_blacklist(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    await state.set_state(Form.add_blacklist_id)
    await message.answer("Введите ID пользователя, которого нужно добавить в ЧС:", reply_markup=get_cancel_keyboard())

@dp.message(StateFilter(Form.add_blacklist_id))
async def process_add_blacklist_id(message: Message, state: FSMContext):
    try:
        user_id = int(message.text.strip())
        await state.update_data(blacklist_user_id=user_id)
        await state.set_state(Form.add_blacklist_reason)
        await message.answer("Введите причину добавления в ЧС:", reply_markup=get_cancel_keyboard())
    except:
        await message.answer("❌ ID должен быть числом. Попробуйте ещё раз.", reply_markup=get_cancel_keyboard())

@dp.message(StateFilter(Form.add_blacklist_reason))
async def process_add_blacklist_reason(message: Message, state: FSMContext):
    reason = message.text.strip()
    await state.update_data(blacklist_reason=reason)
    await state.set_state("add_blacklist_duration")
    await message.answer("Выберите срок блокировки:", reply_markup=get_ban_duration_keyboard())

@dp.callback_query(F.data.startswith("ban_duration_"))
async def process_add_blacklist_duration(call: CallbackQuery, state: FSMContext):
    duration_str = call.data.split("_")[2]
    data = await state.get_data()
    user_id = data.get("blacklist_user_id")
    reason = data.get("blacklist_reason")
    if duration_str == "0":
        until = None
        duration_text = "бессрочно"
    else:
        unit = duration_str[-1]
        value = int(duration_str[:-1])
        if unit == 'h':
            delta = timedelta(hours=value)
            duration_text = f"{value} часов"
        elif unit == 'd':
            delta = timedelta(days=value)
            duration_text = f"{value} дней"
        elif unit == 'm':
            delta = timedelta(days=value * 30)
            duration_text = f"{value} месяцев"
        elif unit == 'y':
            delta = timedelta(days=value * 365)
            duration_text = f"{value} лет"
        else:
            delta = None
            duration_text = "бессрочно"
        until = datetime.now() + delta if delta else None
    db.add_to_blacklist(user_id, reason, call.from_user.id, until)
    await state.clear()
    await call.message.edit_text(f"✅ Пользователь {user_id} добавлен в ЧС.\nПричина: {reason}\nСрок: {duration_text}")
    await call.answer("Готово", show_alert=True)

@dp.message(F.text == "➖ Убрать из ЧС")
async def admin_remove_blacklist(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    await state.set_state(Form.remove_blacklist_id)
    await message.answer("Введите ID пользователя, которого нужно удалить из ЧС:", reply_markup=get_cancel_keyboard())

@dp.message(StateFilter(Form.remove_blacklist_id))
async def process_remove_blacklist(message: Message, state: FSMContext):
    try:
        user_id = int(message.text.strip())
        db.remove_from_blacklist(user_id)
        await state.clear()
        await message.answer(f"✅ Пользователь {user_id} удалён из ЧС.", reply_markup=get_admin_keyboard())
        try:
            await bot.send_message(user_id, f"✅ Вы удалены из чёрного списка маркета. Теперь вы снова можете продавать и покупать лоты.")
        except:
            pass
    except:
        await message.answer("❌ ID должен быть числом. Попробуйте ещё раз.", reply_markup=get_cancel_keyboard())

@dp.message(F.text == "📋 Список ЧС")
async def admin_blacklist_list(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    blacklist = db.get_blacklist()
    if not blacklist:
        await message.answer("Чёрный список пуст.")
        return
    text = "🚫 **Чёрный список маркета:**\n\n"
    for uid, reason, banned_at, banned_until, admin_id in blacklist:
        until_text = f"до {banned_until}" if banned_until else "бессрочно"
        text += f"• `{uid}`\n   Причина: {reason}\n   Забанен: {banned_at}\n   Срок: {until_text}\n   Админ: {admin_id}\n\n"
    await message.answer(text)

# ---------- ПРОМОКОДЫ (админ) ----------
@dp.message(F.text == "➕ Создать промокод")
async def admin_promo_create_type(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    await state.set_state(Form.promo_create_type)
    await message.answer("Выберите тип:", reply_markup=get_promocode_type_keyboard())

@dp.message(F.text == "📋 Список промокодов")
async def admin_promo_list(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    promos = db.get_all_promocodes()
    if not promos:
        await message.answer("Нет промокодов", reply_markup=get_promocode_admin_keyboard())
        return
    text = "📋 **Список промокодов:**\n\n"
    for code, ptype, reward, max_uses, used in promos:
        if ptype == "mirror":
            text += f"🔹 `{code}` | зеркала | {reward} | {used}/{max_uses}\n"
        elif ptype == "premium":
            text += f"🔹 `{code}` | премиум | {reward} | {used}/{max_uses}\n"
        elif ptype == "stars":
            text += f"🔹 `{code}` | звёзды | {reward}⭐ | {used}/{max_uses}\n"
        elif ptype == "searches":
            text += f"🔹 `{code}` | поиски | {reward} | {used}/{max_uses}\n"
    await message.answer(text, reply_markup=get_promocode_admin_keyboard())

@dp.message(F.text == "🗑 Удалить промокод")
async def admin_promo_delete(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    await state.set_state(Form.promo_delete)
    await message.answer("Введите название промокода:", reply_markup=get_cancel_keyboard())

@dp.message(Form.promo_delete)
async def admin_promo_delete_input(message: Message, state: FSMContext):
    code = message.text.strip()
    if db.delete_promocode(code):
        await state.clear()
        await message.answer(f"✅ Промокод `{code}` удалён", reply_markup=get_admin_keyboard())
    else:
        await message.answer(f"❌ Промокод `{code}` не найден", reply_markup=get_promocode_admin_keyboard())

@dp.message(Form.promo_create_type)
async def admin_promo_type_choice(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    if message.text == "🪞 Зеркальные поиски":
        await state.set_state(Form.promo_create_mirror)
        await message.answer("Формат: `код активаций награда`\nПример: `mirror100 10 50`", reply_markup=get_cancel_keyboard())
    elif message.text == "Премиум":
        await state.set_state(Form.promo_create_premium)
        await message.answer("Введите данные для промокода на премиум в формате:\n`код количество_активаций время`\n\nПример: `prem7d 5 7d`\n\nВремя: h (часы), d (дни), m (месяцы), y (годы).", reply_markup=get_cancel_keyboard())
    elif message.text == "⭐️ Звёзды":
        await state.set_state(Form.promo_create_stars)
        await message.answer("Формат: `код активаций звёзды`\nПример: `stars100 10 100`", reply_markup=get_cancel_keyboard())
    elif message.text == "🔍 Обычные поиски":
        await state.set_state(Form.promo_create_searches)
        await message.answer("Формат: `код активаций количество`\nПример: `searches100 10 50`", reply_markup=get_cancel_keyboard())
    elif message.text == "🔙 Назад":
        await state.clear()
        await message.answer("Управление промокодами", reply_markup=get_promocode_admin_keyboard())

@dp.message(Form.promo_create_mirror)
async def admin_promo_create_mirror(message: Message, state: FSMContext):
    parts = message.text.split()
    if len(parts) == 3:
        code, max_uses, reward = parts
        if not re.match(r'^[a-zA-Z0-9_]+$', code):
            await message.answer("❌ Только буквы, цифры, _")
            return
        try:
            max_uses = int(max_uses)
            reward = int(reward)
            if max_uses <= 0 or reward <= 0:
                raise
        except:
            await message.answer("❌ Неверные числа")
            return
        if db.create_promocode(code, "mirror", str(reward), max_uses):
            await state.clear()
            await message.answer(f"✅ Промокод {code} создан!", reply_markup=get_admin_keyboard())
        else:
            await message.answer("❌ Промокод уже существует")
    else:
        await message.answer("❌ Неверный формат")

@dp.message(Form.promo_create_premium)
async def admin_promo_create_premium(message: Message, state: FSMContext):
    parts = message.text.split()
    if len(parts) == 3:
        code, max_uses, duration = parts
        if not re.match(r'^[a-zA-Z0-9_]+$', code):
            await message.answer("❌ Только буквы, цифры, _")
            return
        try:
            max_uses = int(max_uses)
            if max_uses <= 0:
                raise
            if not re.match(r'^\d+[hdmy]$', duration):
                raise
        except:
            await message.answer("❌ Неверный формат")
            return
        if db.create_promocode(code, "premium", duration, max_uses):
            await state.clear()
            await message.answer(f"✅ Промокод {code} создан!", reply_markup=get_admin_keyboard())
        else:
            await message.answer("❌ Промокод уже существует")
    else:
        await message.answer("❌ Неверный формат")

@dp.message(Form.promo_create_stars)
async def admin_promo_create_stars(message: Message, state: FSMContext):
    parts = message.text.split()
    if len(parts) == 3:
        code, max_uses, reward = parts
        if not re.match(r'^[a-zA-Z0-9_]+$', code):
            await message.answer("❌ Только буквы, цифры, _")
            return
        try:
            max_uses = int(max_uses)
            reward = int(reward)
            if max_uses <= 0 or reward <= 0:
                raise
        except:
            await message.answer("❌ Неверные числа")
            return
        if db.create_promocode(code, "stars", str(reward), max_uses):
            await state.clear()
            await message.answer(f"✅ Промокод {code} создан!", reply_markup=get_admin_keyboard())
        else:
            await message.answer("❌ Промокод уже существует")
    else:
        await message.answer("❌ Неверный формат")

@dp.message(Form.promo_create_searches)
async def admin_promo_create_searches(message: Message, state: FSMContext):
    parts = message.text.split()
    if len(parts) == 3:
        code, max_uses, reward = parts
        if not re.match(r'^[a-zA-Z0-9_]+$', code):
            await message.answer("❌ Только буквы, цифры, _")
            return
        try:
            max_uses = int(max_uses)
            reward = int(reward)
            if max_uses <= 0 or reward <= 0:
                raise
        except:
            await message.answer("❌ Неверные числа")
            return
        if db.create_promocode(code, "searches", str(reward), max_uses):
            await state.clear()
            await message.answer(f"✅ Промокод {code} создан!", reply_markup=get_admin_keyboard())
        else:
            await message.answer("❌ Промокод уже существует")
    else:
        await message.answer("❌ Неверный формат")

# ---------- АДМИН: ВВОД ДАННЫХ ----------
@dp.message(Form.admin_give_prem, F.text != "🔙 Отмена")
async def admin_give_prem_input(message: Message, state: FSMContext):
    parts = message.text.split()
    if len(parts) == 2:
        try:
            target_id = int(parts[0])
            duration = parts[1].lower()
            match = re.match(r'^(\d+)([hdmy])$', duration)
            if match:
                val = int(match.group(1))
                unit = match.group(2)
                if unit == 'h': delta = timedelta(hours=val)
                elif unit == 'd': delta = timedelta(days=val)
                elif unit == 'm': delta = timedelta(days=val*30)
                elif unit == 'y': delta = timedelta(days=val*365)
                else: delta = None
                if delta:
                    new_date = db.add_premium_time(target_id, delta)
                    await state.clear()
                    await message.answer(f"✅ Премиум выдан {target_id} до {new_date}", reply_markup=get_admin_keyboard())
                    try:
                        await bot.send_message(target_id, f"🎉 Premium до {new_date}!\nТеперь у вас 10 поисков в день и доступ ко всем фильтрам!")
                    except:
                        pass
        except:
            await message.answer("❌ Ошибка")
    else:
        await message.answer("❌ Формат: ID время")

@dp.message(Form.admin_take_prem, F.text != "🔙 Отмена")
async def admin_take_prem_input(message: Message, state: FSMContext):
    try:
        target_id = int(message.text)
        db.take_premium(target_id)
        await state.clear()
        await message.answer(f"✅ Премиум снят с {target_id}", reply_markup=get_admin_keyboard())
    except:
        await message.answer("❌ Ошибка")

@dp.message(Form.admin_give_mirrors, F.text != "🔙 Отмена")
async def admin_give_mirrors_input(message: Message, state: FSMContext):
    parts = message.text.split()
    if len(parts) == 2:
        try:
            target_id = int(parts[0])
            amount = int(parts[1])
            db.add_mirror_searches(target_id, amount)
            await state.clear()
            await message.answer(f"✅ {amount} зеркал выдано {target_id}", reply_markup=get_admin_keyboard())
        except:
            await message.answer("❌ Ошибка")
    else:
        await message.answer("❌ Формат: ID количество")

@dp.message(Form.admin_give_stars, F.text != "🔙 Отмена")
async def admin_give_stars_input(message: Message, state: FSMContext):
    parts = message.text.split()
    if len(parts) == 2:
        try:
            target_id = int(parts[0])
            amount = int(parts[1])
            db.add_stars(target_id, amount)
            await state.clear()
            await message.answer(f"✅ {amount} звёзд выдано {target_id}", reply_markup=get_admin_keyboard())
        except:
            await message.answer("❌ Ошибка")
    else:
        await message.answer("❌ Формат: ID количество")

@dp.message(Form.admin_take_stars, F.text != "🔙 Отмена")
async def admin_take_stars_input(message: Message, state: FSMContext):
    parts = message.text.split()
    if len(parts) == 2:
        try:
            target_id = int(parts[0])
            amount = int(parts[1])
            if db.remove_stars(target_id, amount):
                await state.clear()
                await message.answer(f"✅ Снято {amount} звёзд с {target_id}", reply_markup=get_admin_keyboard())
            else:
                await message.answer("❌ Недостаточно звёзд")
        except:
            await message.answer("❌ Ошибка")
    else:
        await message.answer("❌ Формат: ID количество")

@dp.message(Form.admin_broadcast, F.text != "🔙 Отмена")
async def admin_broadcast_input(message: Message, state: FSMContext):
    users = db.get_all_user_ids()
    await message.answer(f"⏳ Рассылка для {len(users)} пользователей...")
    await state.clear()
    success = 0
    for uid in users:
        try:
            await bot.send_message(uid, message.text)
            success += 1
            await asyncio.sleep(0.05)
        except:
            pass
    await message.answer(f"✅ Рассылка завершена! {success}/{len(users)}", reply_markup=get_admin_keyboard())

@dp.message(Command("givepremium"))
async def cmd_givepremium(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    parts = message.text.split()
    if len(parts) == 3:
        try:
            target_id = int(parts[1])
            days = int(parts[2])
            db.add_premium_time(target_id, timedelta(days=days))
            await message.answer(f"✅ Premium выдан {target_id} на {days} дней")
        except:
            pass

@dp.message(Command("premiumlist"))
async def cmd_premiumlist(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    users = db.get_all_premium_users()
    msg = "💎 Премиум:\n" + "\n".join([f"{uid} (@{uname}) до {until}" for uid, uname, until in users]) if users else "Нет"
    await message.answer(msg)

# ---------- ФОНОВЫЕ ЗАДАЧИ ----------
async def trap_worker():
    await asyncio.sleep(5)
    print("Снайпер запущен")
    while True:
        try:
            active_traps = db.get_all_active_traps()
            for t_user_id, t_username in active_traps:
                try:
                    is_free, reliability = await check_username_hybrid(t_username)
                    if is_free is None:
                        continue
                    if is_free:
                        score, verdict = engine.evaluate(t_username)
                        reliability_text = " (проверка неточная)" if reliability == "unreliable" else ""
                        msg = f"🚨 ЛОВУШКА! @{t_username} СВОБОДЕН{reliability_text}\n⭐ {score}/10 ({verdict})\nhttps://t.me/{t_username}"
                        await bot.send_message(t_user_id, msg)
                        db.mark_trap_caught(t_user_id, t_username)
                except:
                    pass
                await asyncio.sleep(2)
        except:
            pass
        await asyncio.sleep(30)

async def main():
    global bot_username
    temp_dispute_data.clear()
    await load_user_sessions()
    bot_username = await get_bot_username()
    print(f"✅ Бот запущен! Username: @{bot_username}")
    print(f"💾 Тип БД: {db.db_type.upper()}")
    if db.db_type == "sqlite":
        print(f"💾 БД: {os.path.abspath(db.db_name)}")
    asyncio.create_task(trap_worker())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())