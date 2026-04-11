import sys
import subprocess
import asyncio
import random
import string
import sqlite3
import os
import time
import re
import hashlib
import logging
import json
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Optional, Dict, List, Tuple

def install_reqs():
    reqs = ["aiogram", "aiohttp", "fake-useragent"]
    for r in reqs:
        try:
            __import__(r)
        except ImportError:
            print(f"⏳ Установка {r}...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", r])

install_reqs()

import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import Message, CallbackQuery, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from fake_useragent import UserAgent

# ========== КОНФИГ ==========
BOT_TOKEN = "8793286826:AAHSMKyNp9UW9Cg17FBjaPK4Gr7kpOEEvyc"
CRYPTOBOT_TOKEN = "562001:AA93Uyx3t5L4S9Vxl0rhKM16eLdbDhK5fcQ"
ADMIN_IDS = [8484944484]

FREE_SEARCH_LIMIT = 3
PREMIUM_SEARCH_LIMIT = 100
ADMIN_SEARCH_LIMIT = 999999

FREE_MASS_LIMIT = 3
PREMIUM_MASS_LIMIT = 100

SEARCH_DELAY = 3.0
MIN_DONATE_USDT = 0.1

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

WELCOME_TEXT = (
    "🚀 Приветствуем в CooSearch!\n"
    "Лучший бот для сбора юзернеймов.\n"
    f"🔍 Лимиты: обычные {FREE_SEARCH_LIMIT} поиска/день, Premium {PREMIUM_SEARCH_LIMIT} поисков/день\n"
    "👑 Купить Premium: @coofw\n"
    "📢 Наш канал: @CooSearch"
)

# ========== HTTP ПОИСК (АСИНХРОННЫЙ, БЕЗ СЕССИЙ) ==========
ua = UserAgent()

async def check_username_http(username: str) -> Optional[bool]:
    """
    Проверяет юзернейм через t.me/{username}
    True  → свободен (можно забирать)
    False → занят (реальный юзер или фрагмент-продажа)
    None  → ошибка (попробовать позже)
    """
    url = f"https://t.me/{username}"
    headers = {
        'User-Agent': ua.random,
        'Accept-Language': 'en-US,en;q=0.9',
        'Cache-Control': 'no-cache'
    }
    
    timeout = aiohttp.ClientTimeout(total=5)
    
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers, ssl=False) as response:
                if response.status == 404:
                    return True
                if response.status != 200:
                    return None
                
                html = await response.text()
                html_lower = html.lower()
                
                # Fragment-продажа (НЕ ПОКАЗЫВАЕМ)
                fragment_keywords = [
                    'this username is on sale', 'fragment.com', 'buy this username',
                    'place a bid', 'starting bid', 'current bid', 'ton'
                ]
                for kw in fragment_keywords:
                    if kw in html_lower:
                        return False
                
                # Реальный пользователь (НЕ ПОКАЗЫВАЕМ)
                real_keywords = [
                    'tgme_page_title', 'tgme_page_extra', 'last seen',
                    'subscribers', 'members', 'channel'
                ]
                for kw in real_keywords:
                    if kw in html_lower:
                        return False
                
                # Удалённый или замороженный (ПОКАЗЫВАЕМ)
                deleted_keywords = [
                    'sorry, this username is already taken',
                    'this username is not available', 'account deleted'
                ]
                for kw in deleted_keywords:
                    if kw in html_lower:
                        return True
                
                # Пустая страница — считаем свободным
                if len(html) < 500:
                    return True
                
                return False
                
    except asyncio.TimeoutError:
        return None
    except aiohttp.ClientError:
        return None
    except Exception:
        return None


# ========== БАЗА ДАННЫХ С WAL ==========
class DatabaseManager:
    def __init__(self, db_name="userhunt.db"):
        self.conn = sqlite3.connect(db_name, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.setup()

    def setup(self):
        # WAL ОПТИМИЗАЦИЯ
        self.cursor.execute("PRAGMA journal_mode=WAL")
        self.cursor.execute("PRAGMA synchronous=NORMAL")
        self.cursor.execute("PRAGMA cache_size=-10000")
        self.cursor.execute("PRAGMA temp_store=MEMORY")
        self.cursor.execute("PRAGMA mmap_size=268435456")
        
        # users
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            today_searches INTEGER DEFAULT 0,
            last_search_date TEXT,
            total_searches INTEGER DEFAULT 0,
            found_nicks INTEGER DEFAULT 0,
            join_date TEXT,
            premium_until TEXT,
            mirror_searches INTEGER DEFAULT 0,
            stars INTEGER DEFAULT 0,
            referrer_id INTEGER,
            ref_code TEXT,
            filter_requests INTEGER DEFAULT 0,
            mask_requests INTEGER DEFAULT 0
        )''')
        
        # user_filters (в БД)
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS user_filters (
            user_id INTEGER PRIMARY KEY,
            filter_type TEXT DEFAULT '🟢 Обычный',
            use_digits INTEGER DEFAULT 0
        )''')
        
        # temp_disputes (в БД)
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS temp_disputes (
            admin_id INTEGER,
            dispute_id INTEGER,
            order_id INTEGER,
            buyer_id INTEGER,
            seller_id INTEGER,
            created_at TEXT,
            PRIMARY KEY (admin_id, dispute_id)
        )''')
        
        # traps
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS traps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            target_username TEXT,
            status TEXT DEFAULT 'active'
        )''')
        
        # global_stats
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS global_stats (
            key TEXT PRIMARY KEY,
            value INTEGER DEFAULT 0
        )''')
        self.cursor.execute("INSERT OR IGNORE INTO global_stats VALUES ('found_nicks', 0)")
        self.cursor.execute("INSERT OR IGNORE INTO global_stats VALUES ('total_mirror_searches', 0)")
        self.cursor.execute("INSERT OR IGNORE INTO global_stats VALUES ('total_donations_usdt', 0)")
        
        # market
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS market_lots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_id INTEGER,
            username TEXT,
            price INTEGER,
            description TEXT,
            created_at TEXT,
            status TEXT DEFAULT 'active'
        )''')
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS market_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lot_id INTEGER,
            buyer_id INTEGER,
            seller_id INTEGER,
            status TEXT DEFAULT 'pending',
            created_at TEXT,
            confirmed_at TEXT
        )''')
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_id INTEGER,
            buyer_id INTEGER,
            rating INTEGER,
            text TEXT,
            created_at TEXT
        )''')
        
        # disputes
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS disputes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER,
            buyer_id INTEGER,
            seller_id INTEGER,
            reason TEXT,
            status TEXT DEFAULT 'open',
            resolved_by INTEGER,
            resolution TEXT,
            created_at TEXT,
            opener_id INTEGER
        )''')
        
        # promocodes
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS promocodes (
            code TEXT PRIMARY KEY,
            promo_type TEXT,
            reward TEXT,
            max_uses INTEGER,
            used INTEGER DEFAULT 0
        )''')
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS user_promocodes (
            user_id INTEGER,
            code TEXT,
            activated_at TEXT,
            PRIMARY KEY (user_id, code)
        )''')
        
        # blacklist
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS blacklist (
            user_id INTEGER PRIMARY KEY,
            reason TEXT,
            banned_at TEXT,
            banned_until TEXT,
            banned_by INTEGER
        )''')
        
        # crypto_invoices
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS crypto_invoices (
            invoice_id TEXT PRIMARY KEY,
            user_id INTEGER,
            stars INTEGER,
            amount_usdt REAL,
            status TEXT DEFAULT 'pending',
            created_at TEXT,
            invoice_type TEXT DEFAULT 'topup'
        )''')
        
        # donations
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS donations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            amount_usdt REAL,
            invoice_id TEXT,
            created_at TEXT
        )''')
        
        # referrals
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS referrals (
            referrer_id INTEGER,
            referred_id INTEGER,
            date TEXT
        )''')
        
        # search_queue (только для массового поиска)
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS search_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            count INTEGER DEFAULT 1,
            status TEXT DEFAULT 'pending',
            priority INTEGER DEFAULT 0,
            created_at TEXT,
            started_at TEXT,
            completed_at TEXT,
            results TEXT
        )''')
        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_queue_priority ON search_queue(priority, status, created_at)")
        
        # hot_nicks_cache
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS hot_nicks_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data TEXT,
            updated_at TEXT
        )''')
        
        # search_stats
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS search_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            search_count INTEGER DEFAULT 1,
            last_searched TEXT,
            UNIQUE(username)
        )''')
        self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_search_stats_date ON search_stats(last_searched)")
        
        # roulette_cooldown
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS roulette_cooldown (
            user_id INTEGER PRIMARY KEY,
            last_play TEXT
        )''')
        
        self.conn.commit()
        logger.warning("БД инициализирована с WAL")

    # ========== ФИЛЬТРЫ ==========
    def get_user_filter(self, user_id):
        self.cursor.execute("SELECT filter_type, use_digits FROM user_filters WHERE user_id = ?", (user_id,))
        row = self.cursor.fetchone()
        if row:
            return row[0], bool(row[1])
        return "🟢 Обычный", False
    
    def set_user_filter(self, user_id, filter_type, use_digits):
        self.cursor.execute("INSERT OR REPLACE INTO user_filters (user_id, filter_type, use_digits) VALUES (?, ?, ?)",
                           (user_id, filter_type, 1 if use_digits else 0))
        self.conn.commit()
    
    # ========== СПОРЫ ==========
    def save_temp_dispute(self, admin_id, dispute_id, order_id, buyer_id, seller_id):
        self.cursor.execute("INSERT OR REPLACE INTO temp_disputes (admin_id, dispute_id, order_id, buyer_id, seller_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                           (admin_id, dispute_id, order_id, buyer_id, seller_id, datetime.now().isoformat()))
        self.conn.commit()
    
    def get_temp_dispute(self, admin_id):
        self.cursor.execute("SELECT dispute_id, order_id, buyer_id, seller_id FROM temp_disputes WHERE admin_id = ?", (admin_id,))
        row = self.cursor.fetchone()
        if row:
            return {'dispute_id': row[0], 'order_id': row[1], 'buyer_id': row[2], 'seller_id': row[3]}
        return None
    
    def delete_temp_dispute(self, admin_id):
        self.cursor.execute("DELETE FROM temp_disputes WHERE admin_id = ?", (admin_id,))
        self.conn.commit()
    
    # ========== ОСНОВНЫЕ МЕТОДЫ ==========
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
    
    def add_user(self, user_id, username, referrer_id=None):
        self.cursor.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
        if not self.cursor.fetchone():
            ref_code = hashlib.md5(f"{user_id}{random.random()}".encode()).hexdigest()[:8]
            self.cursor.execute(
                "INSERT INTO users (user_id, username, join_date, ref_code, stars) VALUES (?, ?, ?, ?, ?)",
                (user_id, username, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), ref_code, 0)
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
        self.cursor.execute("SELECT username, today_searches, total_searches, found_nicks, join_date, premium_until, mirror_searches, stars, filter_requests, mask_requests FROM users WHERE user_id = ?", (user_id,))
        user_data = self.cursor.fetchone()
        if not user_data:
            return None, 0, 0
        self.cursor.execute("SELECT COUNT(*) FROM traps WHERE user_id = ? AND status = 'active'", (user_id,))
        active_traps = self.cursor.fetchone()[0]
        self.cursor.execute("SELECT COUNT(*) FROM traps WHERE user_id = ? AND status = 'caught'", (user_id,))
        caught_traps = self.cursor.fetchone()[0]
        return user_data, active_traps, caught_traps
    
    def get_user_limit(self, user_id):
        if user_id in ADMIN_IDS:
            return ADMIN_SEARCH_LIMIT
        if self.is_premium(user_id):
            return PREMIUM_SEARCH_LIMIT
        return FREE_SEARCH_LIMIT
    
    def get_remaining_searches(self, user_id):
        today = datetime.now().strftime("%Y-%m-%d")
        self.cursor.execute("SELECT today_searches, last_search_date FROM users WHERE user_id = ?", (user_id,))
        row = self.cursor.fetchone()
        if not row:
            return self.get_user_limit(user_id)
        if row[1] != today:
            return self.get_user_limit(user_id)
        limit = self.get_user_limit(user_id)
        used = row[0] or 0
        return max(0, limit - used)
    
    def add_search(self, user_id, count=1):
        today = datetime.now().strftime("%Y-%m-%d")
        self.cursor.execute("SELECT last_search_date, today_searches FROM users WHERE user_id = ?", (user_id,))
        row = self.cursor.fetchone()
        if not row:
            self.cursor.execute("INSERT INTO users (user_id, today_searches, last_search_date, total_searches) VALUES (?, ?, ?, ?)",
                               (user_id, count, today, count))
        elif row[0] != today:
            self.cursor.execute("UPDATE users SET today_searches = ?, last_search_date = ?, total_searches = total_searches + ? WHERE user_id = ?",
                               (count, today, count, user_id))
        else:
            self.cursor.execute("UPDATE users SET today_searches = today_searches + ?, total_searches = total_searches + ? WHERE user_id = ?",
                               (count, count, user_id))
        self.conn.commit()
    
    def add_found_nick(self, user_id):
        self.cursor.execute("UPDATE users SET found_nicks = found_nicks + 1 WHERE user_id = ?", (user_id,))
        self.conn.commit()
        self.cursor.execute("UPDATE global_stats SET value = value + 1 WHERE key = 'found_nicks'")
        self.conn.commit()
    
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
        self.conn.commit()
        self.cursor.execute("UPDATE global_stats SET value = value + 1 WHERE key = 'total_mirror_searches'")
        self.conn.commit()
    
    def add_filter_request(self, user_id, amount):
        self.cursor.execute("UPDATE users SET filter_requests = filter_requests + ? WHERE user_id = ?", (amount, user_id))
        self.conn.commit()
    
    def get_filter_requests(self, user_id):
        self.cursor.execute("SELECT filter_requests FROM users WHERE user_id = ?", (user_id,))
        row = self.cursor.fetchone()
        return row[0] if row else 0
    
    def use_filter_request(self, user_id):
        self.cursor.execute("UPDATE users SET filter_requests = filter_requests - 1 WHERE user_id = ? AND filter_requests > 0", (user_id,))
        self.conn.commit()
    
    def add_mask_request(self, user_id, amount):
        self.cursor.execute("UPDATE users SET mask_requests = mask_requests + ? WHERE user_id = ?", (amount, user_id))
        self.conn.commit()
    
    def get_mask_requests(self, user_id):
        self.cursor.execute("SELECT mask_requests FROM users WHERE user_id = ?", (user_id,))
        row = self.cursor.fetchone()
        return row[0] if row else 0
    
    def use_mask_request(self, user_id):
        self.cursor.execute("UPDATE users SET mask_requests = mask_requests - 1 WHERE user_id = ? AND mask_requests > 0", (user_id,))
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
    
    def get_stats(self):
        self.cursor.execute("SELECT COUNT(*) FROM users")
        total_users = self.cursor.fetchone()[0]
        self.cursor.execute("SELECT value FROM global_stats WHERE key = 'found_nicks'")
        row = self.cursor.fetchone()
        found_nicks = row[0] if row else 0
        self.cursor.execute("SELECT COUNT(*) FROM traps WHERE status = 'active'")
        active_traps = self.cursor.fetchone()[0]
        return total_users, found_nicks, active_traps
    
    def get_all_user_ids(self):
        self.cursor.execute("SELECT user_id FROM users")
        return [row[0] for row in self.cursor.fetchall()]
    
    def get_all_premium_users(self):
        self.cursor.execute("SELECT user_id, username, premium_until FROM users WHERE premium_until IS NOT NULL")
        return self.cursor.fetchall()
    
    def is_premium(self, user_id):
        if user_id in ADMIN_IDS:
            return True
        self.cursor.execute("SELECT premium_until FROM users WHERE user_id = ?", (user_id,))
        row = self.cursor.fetchone()
        if not row or not row[0]:
            return False
        try:
            return datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S") > datetime.now()
        except:
            return False
    
    def add_premium_time(self, user_id, delta):
        self.cursor.execute("SELECT premium_until FROM users WHERE user_id = ?", (user_id,))
        row = self.cursor.fetchone()
        current = row[0] if row else None
        now = datetime.now()
        if current:
            try:
                current_dt = datetime.strptime(current, "%Y-%m-%d %H:%M:%S")
                if current_dt < now:
                    current_dt = now
            except:
                current_dt = now
        else:
            current_dt = now
        new_str = (current_dt + delta).strftime("%Y-%m-%d %H:%M:%S")
        self.cursor.execute("UPDATE users SET premium_until = ? WHERE user_id = ?", (new_str, user_id))
        self.conn.commit()
        return new_str
    
    def take_premium(self, user_id):
        self.cursor.execute("UPDATE users SET premium_until = NULL WHERE user_id = ?", (user_id,))
        self.conn.commit()
    
    # ========== МАРКЕТ ==========
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
        safe_text = (text[:500] if text else "")[:500]
        self.cursor.execute("INSERT INTO reviews (seller_id, buyer_id, rating, text, created_at) VALUES (?, ?, ?, ?, ?)",
                            (seller_id, buyer_id, rating, safe_text, datetime.now().isoformat()))
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
    
    # ========== ПРОМОКОДЫ ==========
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
    
    # ========== ИНВОЙСЫ ==========
    def add_invoice(self, invoice_id, user_id, stars, amount_usdt, invoice_type="topup"):
        self.cursor.execute("INSERT INTO crypto_invoices (invoice_id, user_id, stars, amount_usdt, created_at, invoice_type) VALUES (?, ?, ?, ?, ?, ?)",
                            (invoice_id, user_id, stars, amount_usdt, datetime.now().isoformat(), invoice_type))
        self.conn.commit()
    
    def get_invoice(self, invoice_id):
        self.cursor.execute("SELECT * FROM crypto_invoices WHERE invoice_id = ?", (invoice_id,))
        return self.cursor.fetchone()
    
    def update_invoice_status(self, invoice_id, status):
        self.cursor.execute("UPDATE crypto_invoices SET status = ? WHERE invoice_id = ?", (status, invoice_id))
        self.conn.commit()
    
    # ========== ДОНАТЫ ==========
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
    
    # ========== ОЧЕРЕДЬ ДЛЯ МАССОВОГО ПОИСКА ==========
    def get_user_priority(self, user_id):
        if user_id in ADMIN_IDS:
            return 2
        if self.is_premium(user_id):
            return 1
        return 0
    
    def add_mass_to_queue(self, user_id, count):
        priority = self.get_user_priority(user_id)
        self.cursor.execute("""
            INSERT INTO search_queue (user_id, count, priority, created_at, status)
            VALUES (?, ?, ?, ?, 'pending')
        """, (user_id, count, priority, datetime.now().isoformat()))
        self.conn.commit()
        return self.cursor.lastrowid
    
    def get_queue_position(self, user_id):
        self.cursor.execute("""
            WITH user_queue AS (
                SELECT created_at, priority FROM search_queue 
                WHERE user_id = ? AND status = 'pending'
                ORDER BY created_at LIMIT 1
            )
            SELECT COUNT(*) FROM search_queue 
            WHERE status = 'pending' 
            AND (priority > (SELECT priority FROM user_queue)
                 OR (priority = (SELECT priority FROM user_queue) 
                     AND created_at < (SELECT created_at FROM user_queue)))
        """, (user_id,))
        row = self.cursor.fetchone()
        return row[0] if row else 0
    
    def get_next_queue_item(self):
        self.cursor.execute("""
            SELECT id, user_id, count, priority
            FROM search_queue 
            WHERE status = 'pending'
            ORDER BY priority DESC, created_at ASC
            LIMIT 1
        """)
        row = self.cursor.fetchone()
        if row:
            return {'id': row[0], 'user_id': row[1], 'count': row[2], 'priority': row[3]}
        return None
    
    def start_queue_item(self, queue_id):
        self.cursor.execute("UPDATE search_queue SET status = 'processing', started_at = ? WHERE id = ?", 
                           (datetime.now().isoformat(), queue_id))
        self.conn.commit()
    
    def complete_queue_item(self, queue_id, results):
        self.cursor.execute("UPDATE search_queue SET status = 'completed', completed_at = ?, results = ? WHERE id = ?",
                           (datetime.now().isoformat(), json.dumps(results), queue_id))
        self.conn.commit()
    
    def get_user_queue_items(self, user_id, limit=10):
        self.cursor.execute("""
            SELECT id, count, status, created_at, completed_at, results
            FROM search_queue 
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (user_id, limit))
        return self.cursor.fetchall()
    
    def get_queue_stats(self):
        self.cursor.execute("SELECT COUNT(*) FROM search_queue WHERE status='pending'")
        pending = self.cursor.fetchone()[0]
        self.cursor.execute("SELECT COUNT(*) FROM search_queue WHERE status='processing'")
        processing = self.cursor.fetchone()[0]
        self.cursor.execute("SELECT COUNT(*) FROM search_queue WHERE status='pending' AND priority=2")
        admin_pending = self.cursor.fetchone()[0]
        self.cursor.execute("SELECT COUNT(*) FROM search_queue WHERE status='pending' AND priority=1")
        premium_pending = self.cursor.fetchone()[0]
        self.cursor.execute("SELECT COUNT(*) FROM search_queue WHERE status='pending' AND priority=0")
        free_pending = self.cursor.fetchone()[0]
        return pending, processing, admin_pending, premium_pending, free_pending
    
    def get_queue_items(self, limit=20):
        self.cursor.execute("""
            SELECT id, user_id, count, status, priority, created_at
            FROM search_queue
            WHERE status IN ('pending', 'processing')
            ORDER BY priority DESC, created_at ASC
            LIMIT ?
        """, (limit,))
        return self.cursor.fetchall()
    
    def cancel_queue_item(self, queue_id, user_id):
        self.cursor.execute("DELETE FROM search_queue WHERE id = ? AND user_id = ? AND status = 'pending'", (queue_id, user_id))
        self.conn.commit()
        return self.cursor.rowcount > 0
    
    # ========== ГОРЯЧИЕ НИКИ ==========
    def log_search_query(self, username):
        self.cursor.execute("""
            INSERT INTO search_stats (username, search_count, last_searched)
            VALUES (?, 1, ?)
            ON CONFLICT(username) DO UPDATE SET
                search_count = search_count + 1,
                last_searched = excluded.last_searched
        """, (username.lower(), datetime.now().isoformat()))
        self.conn.commit()
    
    def get_hot_nicks(self, limit=10):
        today = datetime.now().strftime("%Y-%m-%d")
        self.cursor.execute("""
            SELECT username, search_count
            FROM search_stats
            WHERE last_searched LIKE ?
            ORDER BY search_count DESC
            LIMIT ?
        """, (today + "%", limit))
        return self.cursor.fetchall()
    
    def get_hot_nicks_cached(self):
        self.cursor.execute("SELECT data, updated_at FROM hot_nicks_cache ORDER BY id DESC LIMIT 1")
        row = self.cursor.fetchone()
        if row:
            updated_at = datetime.fromisoformat(row[1])
            if datetime.now() - updated_at < timedelta(hours=6):
                return json.loads(row[0]), updated_at
        return None, None
    
    def update_hot_nicks_cache(self, data):
        self.cursor.execute("DELETE FROM hot_nicks_cache")
        self.cursor.execute("INSERT INTO hot_nicks_cache (data, updated_at) VALUES (?, ?)",
                           (json.dumps(data), datetime.now().isoformat()))
        self.conn.commit()
    
    # ========== РУЛЕТКА ==========
    def can_play_roulette(self, user_id):
        if user_id in ADMIN_IDS:
            return True
        self.cursor.execute("SELECT last_play FROM roulette_cooldown WHERE user_id = ?", (user_id,))
        row = self.cursor.fetchone()
        if not row:
            return True
        last_play = datetime.fromisoformat(row[0])
        return datetime.now() - last_play >= timedelta(days=1)
    
    def set_roulette_cooldown(self, user_id):
        if user_id in ADMIN_IDS:
            return
        self.cursor.execute("INSERT OR REPLACE INTO roulette_cooldown (user_id, last_play) VALUES (?, ?)",
                           (user_id, datetime.now().isoformat()))
        self.conn.commit()

db = DatabaseManager()


# ========== ОЦЕНКА ЮЗЕРНЕЙМОВ ==========
class EvaluatorEngine:
    def __init__(self):
        self.dictionary = set()
        self.dict_loaded = False
        
    def load_dict(self):
        if self.dict_loaded:
            return
        try:
            req = requests.get("https://raw.githubusercontent.com/charlesreid1/five-letter-words/master/sgb-words.txt", timeout=5)
            words = req.text.splitlines()
            for w in words:
                if w.isalpha():
                    self.dictionary.add(w.lower())
            self.dict_loaded = True
        except:
            pass
    
    def generate_random(self, length=5, filter_type="🟢 Обычный", use_digits=False):
        charset = string.ascii_lowercase
        if use_digits:
            charset += string.digits
        if filter_type == "🪞 Зеркальный 🔒":
            p1 = random.choice(charset)
            p2 = random.choice(charset)
            p3 = random.choice(charset)
            return p1 + p2 + p3 + p2 + p1
        return ''.join(random.choices(charset, k=length))
    
    def evaluate(self, username: str):
        username = username.lower()
        length = len(username)
        self.load_dict()
        if username in self.dictionary and not any(c.isdigit() for c in username):
            return 10, "💎 ЭКСКЛЮЗИВ"
        score = 3.0
        if length <= 4: score += 3.0
        elif length == 5: score += 1.0
        elif length == 6: score -= 1.0
        elif length >= 7: score -= 2.0
        unique_chars = len(set(username))
        if unique_chars == 1:
            score = 10.0
        else:
            is_palindrome = username == username[::-1]
            if is_palindrome:
                if unique_chars == 2: score = 10.0
                elif unique_chars == 3: score += 2.0
                else: score += 1.0
            else:
                if unique_chars == 2: score += 2.0
                elif unique_chars == 3: score += 1.0
            max_streak = 1
            current_streak = 1
            for i in range(1, len(username)):
                if username[i] == username[i-1]:
                    current_streak += 1
                    max_streak = max(max_streak, current_streak)
                else:
                    current_streak = 1
            if score != 10.0:
                if max_streak >= 4: score += 2.0
                elif max_streak == 3: score += 1.0
        has_digits = any(c.isdigit() for c in username)
        if has_digits and score > 0:
            if not (score == 10.0 and unique_chars == 1):
                score /= 1.5
        score = max(1.0, min(10.0, score))
        final_score = round(score, 1)
        if final_score.is_integer():
            final_score = int(final_score)
        if final_score == 10: verdict = "💎 ЭКСКЛЮЗИВ"
        elif final_score >= 8.0: verdict = "👑 ЭЛИТА"
        elif final_score >= 5.0: verdict = "👍 ХОРОШИЙ"
        elif final_score >= 3.0: verdict = "🔹 БАЗОВЫЙ"
        else: verdict = "💩 МУСОР"
        return final_score, verdict

engine = EvaluatorEngine()


# ========== ОДИНОЧНЫЙ ПОИСК (МГНОВЕННЫЙ) ==========
async def perform_single_search(length: int, filter_type: str, use_digits: bool, user_id: int) -> Optional[Dict]:
    """Одиночный поиск через HTTP, без очереди"""
    for attempt in range(30):  # максимум 30 попыток (~3 секунды)
        username = engine.generate_random(length, filter_type, use_digits)
        is_free = await check_username_http(username)
        if is_free is None:
            await asyncio.sleep(0.1)
            continue
        if is_free:
            score, verdict = engine.evaluate(username)
            db.add_found_nick(user_id)
            db.log_search_query(username)
            return {
                'username': username,
                'score': score,
                'verdict': verdict,
                'length': length
            }
        await asyncio.sleep(0.05)
    return None


# ========== МАССОВЫЙ ПОИСК (ДЛЯ ВОРКЕРА) ==========
async def perform_mass_search(count: int, filter_type: str, use_digits: bool, user_id: int, search_length: int = 5) -> List[Dict]:
    """Массовый поиск для воркера"""
    results = []
    for i in range(count):
        # Проверяем остаток лимита
        remaining = db.get_remaining_searches(user_id)
        if remaining <= 0 and user_id not in ADMIN_IDS:
            break
        
        for attempt in range(30):
            username = engine.generate_random(search_length, filter_type, use_digits)
            is_free = await check_username_http(username)
            if is_free is None:
                await asyncio.sleep(0.1)
                continue
            if is_free:
                score, verdict = engine.evaluate(username)
                db.add_found_nick(user_id)
                db.log_search_query(username)
                db.add_search(user_id)  # уменьшаем лимит
                results.append({
                    'username': username,
                    'score': score,
                    'verdict': verdict,
                    'length': search_length
                })
                break
            await asyncio.sleep(0.05)
        
        # Задержка между запросами в массовке (3 секунды)
        if i < count - 1:
            await asyncio.sleep(SEARCH_DELAY)
    
    return results


# ========== ОЧЕРЕДЬ-ВОРКЕР (ТОЛЬКО ДЛЯ МАССОВОГО ПОИСКА) ==========
queue_worker_running = True

async def queue_worker(bot_instance: Bot):
    global queue_worker_running
    logger.warning("Очередь-воркер запущен (только массовый поиск)")
    
    while queue_worker_running:
        try:
            item = db.get_next_queue_item()
            if not item:
                await asyncio.sleep(1)
                continue
            
            db.start_queue_item(item['id'])
            
            try:
                await bot_instance.send_message(item['user_id'], f"🔄 Начинаю массовый поиск ({item['count']} ников)...")
            except:
                pass
            
            filter_type, use_digits = db.get_user_filter(item['user_id'])
            
            results = await perform_mass_search(
                item['count'], filter_type, use_digits, item['user_id'], 5
            )
            
            if results:
                txt_content = f"# CooSearch Mass Search Results\n# {len(results)} ников найдено\n\n"
                for r in results:
                    txt_content += f"@{r['username']} - {r['score']}/10 ({r['verdict']})\nhttps://t.me/{r['username']}\n\n"
                
                txt_filename = f"mass_search_{item['user_id']}_{int(time.time())}.txt"
                with open(txt_filename, 'w', encoding='utf-8') as f:
                    f.write(txt_content)
                
                with open(txt_filename, 'rb') as f:
                    await bot_instance.send_document(item['user_id'], BufferedInputFile(f.read(), filename=txt_filename), caption=f"📊 Найдено ников: {len(results)}")
                
                os.remove(txt_filename)
                
                sample = "\n".join([f"• @{r['username']}" for r in results[:5]])
                if len(results) > 5:
                    sample += f"\n... и ещё {len(results)-5}"
                await bot_instance.send_message(item['user_id'], f"✅ Массовый поиск завершён!\nНайдено: {len(results)} ников\n\n{sample}")
            else:
                await bot_instance.send_message(item['user_id'], "❌ Массовый поиск не нашёл ни одного свободного ника.")
            
            db.complete_queue_item(item['id'], results)
            
        except Exception as e:
            logger.error(f"Ошибка в queue_worker: {e}", exc_info=True)
            await asyncio.sleep(5)
    
    logger.warning("Очередь-воркер остановлен")


# ========== CRYPTO BOT ФУНКЦИИ ==========
STARS_TO_USDT = {15: 0.23, 40: 0.60, 75: 1.13, 125: 1.88, 200: 3.00}

def create_crypto_invoice(amount_usdt, description):
    url = "https://pay.crypt.bot/api/createInvoice"
    payload = {
        "asset": "USDT",
        "amount": str(amount_usdt),
        "description": description,
        "paid_btn_name": "callback",
        "paid_btn_url": f"https://t.me/{BOT_TOKEN.split(':')[0]}?start=payment"
    }
    headers = {"Crypto-Pay-API-Token": CRYPTOBOT_TOKEN}
    for attempt in range(3):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=10)
            return response.json()
        except:
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
            data = response.json()
            if data.get("ok") and data["result"]["items"]:
                return data["result"]["items"][0]["status"]
            return None
        except:
            if attempt == 2:
                return None
            time.sleep(1)
    return None


# ========== БОТ ==========
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

user_cooldowns = {}


# ========== КЛАВИАТУРЫ ==========
def get_main_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="🔎 Поиск (5 букв)"), KeyboardButton(text="🔎 Поиск (6 букв)"))
    builder.row(KeyboardButton(text="⚙️ Фильтры"), KeyboardButton(text="⭐️ Оценить юзернейм"))
    builder.row(KeyboardButton(text="🎯 Поставить ловушку"), KeyboardButton(text="📊 Статистика"))
    builder.row(KeyboardButton(text="👤 Профиль"), KeyboardButton(text="🛒 Маркет"))
    builder.row(KeyboardButton(text="💎 Премиум"), KeyboardButton(text="🔗 Реферальная ссылка"))
    builder.row(KeyboardButton(text="❤️ Поддержать бота"), KeyboardButton(text="🎫 Активировать промокод"))
    builder.row(KeyboardButton(text="📦 Массовый поиск"), KeyboardButton(text="📋 Моя очередь"))
    builder.row(KeyboardButton(text="🔥 Горячие ники"), KeyboardButton(text="🎰 Рулетка"))
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
    builder.row(KeyboardButton(text="⭐️ Забрать звёзды"), KeyboardButton(text="📢 Рассылка"))
    builder.row(KeyboardButton(text="🎫 Промокоды"), KeyboardButton(text="⚖️ Споры"))
    builder.row(KeyboardButton(text="🚫 Чёрный список"), KeyboardButton(text="📊 Донаты"))
    builder.row(KeyboardButton(text="📋 Очередь админ"), KeyboardButton(text="🔙 Выйти в меню"))
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
    builder.row(KeyboardButton(text="🔙 Назад"))
    return builder.as_markup(resize_keyboard=True)

def get_filters_keyboard():
    builder = ReplyKeyboardBuilder()
    builder.row(KeyboardButton(text="🟢 Обычный"), KeyboardButton(text="🪞 Зеркальный 🔒"))
    builder.row(KeyboardButton(text="🔢 Включить цифры"), KeyboardButton(text="🔠 Выключить цифры"))
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
    admin_broadcast = State()
    promo_create_type = State()
    promo_create_mirror = State()
    promo_create_premium = State()
    promo_create_stars = State()
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
    mass_search_count = State()
    cancel_queue_id = State()


# ========== ОСНОВНЫЕ ОБРАБОТЧИКИ ==========
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
        await message.answer("👑 Админ Панель", reply_markup=get_admin_keyboard())

@dp.message(F.text == "🔙 Назад")
async def back_to_main(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(WELCOME_TEXT, reply_markup=get_main_keyboard())

@dp.message(F.text == "🔙 Отмена")
async def cancel_action(message: Message, state: FSMContext):
    await state.clear()
    if message.from_user.id in ADMIN_IDS:
        await message.answer("Отменено", reply_markup=get_admin_keyboard())
    else:
        await message.answer("Отменено", reply_markup=get_main_keyboard())

@dp.message(F.text == "🔙 Выйти в меню")
async def exit_admin(message: Message, state: FSMContext):
    if message.from_user.id in ADMIN_IDS:
        await state.clear()
        await message.answer(WELCOME_TEXT, reply_markup=get_main_keyboard())


# ========== ОБЫЧНЫЙ ПОИСК (МГНОВЕННЫЙ, БЕЗ ОЧЕРЕДИ) ==========
@dp.message(F.text == "🔎 Поиск (5 букв)")
async def search_5(message: Message):
    await handle_single_search(message, 5)

@dp.message(F.text == "🔎 Поиск (6 букв)")
async def search_6(message: Message):
    await handle_single_search(message, 6)

async def handle_single_search(message: Message, length: int):
    user_id = message.from_user.id
    
    # Проверка лимита
    remaining = db.get_remaining_searches(user_id)
    if remaining <= 0 and user_id not in ADMIN_IDS:
        limit = db.get_user_limit(user_id)
        await message.answer(f"❌ Лимит {limit} поисков/день исчерпан. Купи Premium!", reply_markup=get_main_keyboard())
        return
    
    # Кулдаун 3 секунды
    if time.time() - user_cooldowns.get(user_id, 0) < 3:
        await message.answer("⏳ Подожди 3 секунды")
        return
    
    user_cooldowns[user_id] = time.time()
    
    # Получаем фильтры
    filter_type, use_digits = db.get_user_filter(user_id)
    
    # Проверка зеркальных
    if filter_type == "🪞 Зеркальный 🔒":
        mirrors = db.get_mirror_searches(user_id)
        if mirrors <= 0:
            filter_type = "🟢 Обычный"
            db.set_user_filter(user_id, filter_type, use_digits)
            await message.answer("❌ Зеркальные закончились! Переключен на обычный.")
        else:
            db.use_mirror_search(user_id)
    
    # Уменьшаем лимит
    db.add_search(user_id)
    
    # Поиск
    msg = await message.answer("🔍 Ищу свободный юзернейм...")
    
    result = await perform_single_search(length, filter_type, use_digits, user_id)
    
    if result:
        text = f"✅ **Найдено:**\n\n┌ `@{result['username']}`\n├ {result['length']} букв\n├ ⭐️ {result['score']}/10 ({result['verdict']})\n└ 🟢 Свободен\n\n🔗 https://t.me/{result['username']}"
        await msg.edit_text(text, disable_web_page_preview=True)
    else:
        await msg.edit_text("❌ Не удалось найти свободный юзернейм. Попробуй позже.")


# ========== МАССОВЫЙ ПОИСК (ЧЕРЕЗ ОЧЕРЕДЬ) ==========
@dp.message(F.text == "📦 Массовый поиск")
async def mass_search_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    max_count = PREMIUM_MASS_LIMIT if db.get_user_priority(user_id) >= 1 else FREE_MASS_LIMIT
    await state.set_state(Form.mass_search_count)
    await message.answer(f"📦 **Массовый поиск**\n\nМаксимум: {max_count}\nВведите количество (1-{max_count}):", reply_markup=get_cancel_keyboard())

@dp.message(Form.mass_search_count)
async def mass_search_count_input(message: Message, state: FSMContext):
    try:
        count = int(message.text.strip())
        user_id = message.from_user.id
        max_count = PREMIUM_MASS_LIMIT if db.get_user_priority(user_id) >= 1 else FREE_MASS_LIMIT
        
        if count < 1 or count > max_count:
            await message.answer(f"❌ Введите число от 1 до {max_count}", reply_markup=get_cancel_keyboard())
            return
        
        remaining = db.get_remaining_searches(user_id)
        if remaining <= 0 and user_id not in ADMIN_IDS:
            limit = db.get_user_limit(user_id)
            await message.answer(f"❌ Лимит {limit} поисков/день исчерпан.", reply_markup=get_main_keyboard())
            await state.clear()
            return
        
        # Добавляем в очередь массового поиска
        db.add_mass_to_queue(user_id, count)
        position = db.get_queue_position(user_id)
        
        priority = db.get_user_priority(user_id)
        priority_text = "админ" if priority == 2 else "премиум" if priority == 1 else "фри"
        
        await message.answer(
            f"✅ **{count} запросов добавлено в очередь!**\n\n"
            f"📊 Приоритет: {priority_text}\n"
            f"🔢 Позиция: {position}\n"
            f"⏱️ Ожидание: ~{position * SEARCH_DELAY} сек\n"
            f"📅 Осталось поисков: {remaining}\n\n"
            f"❌ Отменить: /cancel_queue",
            reply_markup=get_main_keyboard()
        )
        await state.clear()
    except ValueError:
        await message.answer("❌ Введите целое число.", reply_markup=get_cancel_keyboard())


# ========== ОТМЕНА ЗАПРОСА ==========
@dp.message(Command("cancel_queue"))
async def cancel_queue_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    items = db.get_user_queue_items(user_id, 5)
    if not items:
        await message.answer("❌ У тебя нет активных запросов в очереди.")
        return
    
    text = "**Твои запросы в очереди:**\n\n"
    builder = InlineKeyboardBuilder()
    for item in items:
        if item[2] == 'pending':  # status index 2
            text += f"🆔 #{item[0]} | массовый x{item[1]} | {item[2]}\n"
            builder.button(text=f"❌ Отменить #{item[0]}", callback_data=f"cancel_queue_{item[0]}")
    
    if not builder.buttons:
        await message.answer("❌ Нет активных запросов на отмену.")
        return
    
    builder.button(text="🔙 Назад", callback_data="back_to_main")
    builder.adjust(1)
    await message.answer(text, reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("cancel_queue_"))
async def cancel_queue_item(call: CallbackQuery):
    queue_id = int(call.data.split("_")[2])
    user_id = call.from_user.id
    
    if db.cancel_queue_item(queue_id, user_id):
        await call.answer("✅ Запрос отменён!", show_alert=True)
        await call.message.edit_text("✅ Запрос успешно отменён.")
    else:
        await call.answer("❌ Не удалось отменить (возможно, уже обрабатывается)", show_alert=True)


# ========== МОЯ ОЧЕРЕДЬ ==========
@dp.message(F.text == "📋 Моя очередь")
async def my_queue_status(message: Message):
    user_id = message.from_user.id
    position = db.get_queue_position(user_id)
    priority = db.get_user_priority(user_id)
    priority_text = "🟢 админ" if priority == 2 else "🟣 премиум" if priority == 1 else "🔵 фри"
    pending, processing, _, _, _ = db.get_queue_stats()
    remaining = db.get_remaining_searches(user_id)
    limit = db.get_user_limit(user_id)
    
    await message.answer(
        f"📊 **Твоя очередь**\n\n"
        f"Приоритет: {priority_text}\n"
        f"Позиция: {position}\n"
        f"⏳ Всего в очереди: {pending}\n"
        f"🔧 В работе: {processing}\n"
        f"⏱️ Ожидание: ~{position * SEARCH_DELAY} сек\n"
        f"📅 Поисков сегодня: {limit - remaining}/{limit}\n\n"
        f"❌ Отменить запрос: /cancel_queue",
        reply_markup=get_main_keyboard()
    )


# ========== СТАТИСТИКА ОЧЕРЕДИ (АДМИН) ==========
@dp.message(F.text == "📋 Очередь админ")
async def admin_queue_stats(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    pending, processing, admin_pending, premium_pending, free_pending = db.get_queue_stats()
    items = db.get_queue_items(15)
    
    text = f"📊 **СТАТИСТИКА ОЧЕРЕДИ**\n\n"
    text += f"⏳ В очереди: {pending}\n"
    text += f"🔧 В работе: {processing}\n"
    text += f"🟢 Админов: {admin_pending}\n"
    text += f"🟣 Премиум: {premium_pending}\n"
    text += f"🔵 Фри: {free_pending}\n"
    text += f"⏱️ Среднее ожидание: ~{pending * SEARCH_DELAY} сек\n\n"
    
    if items:
        text += "**Последние в очереди:**\n"
        for item in items:
            priority_emoji = "🟢" if item[4] == 2 else "🟣" if item[4] == 1 else "🔵"
            text += f"{priority_emoji} #{item[0]} | user {item[1]} | массовый x{item[2]} | {item[3]}\n"
    
    await message.answer(text)


# ========== ГОРЯЧИЕ НИКИ ==========
@dp.message(F.text == "🔥 Горячие ники")
async def hot_nicks(message: Message):
    cached, updated_at = db.get_hot_nicks_cached()
    if cached and updated_at:
        cache_age = (datetime.now() - updated_at).total_seconds() / 3600
        cache_text = f" (обновлено {cache_age:.1f} ч назад)"
        hot_data = cached
    else:
        hot = db.get_hot_nicks(15)
        hot_data = {'nicks': [{'username': h[0], 'count': h[1]} for h in hot], 'total': sum(h[1] for h in hot)}
        db.update_hot_nicks_cache(hot_data)
        cache_text = " (только что обновлено)"
    
    if not hot_data['nicks']:
        await message.answer("🔥 Пока нет данных. Начни искать!", reply_markup=get_main_keyboard())
        return
    
    text = f"🔥 **ГОРЯЧИЕ НИКИ**{cache_text}\n\n📊 Всего запросов: {hot_data['total']}\n\n**Топ-10:**\n"
    for i, nick in enumerate(hot_data['nicks'][:10], 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
        text += f"{medal} `{nick['username']}` — {nick['count']}\n"
    await message.answer(text, reply_markup=get_main_keyboard())


# ========== РУЛЕТКА ==========
@dp.message(F.text == "🎰 Рулетка")
async def roulette(message: Message):
    user_id = message.from_user.id
    is_premium = db.is_premium(user_id) or user_id in ADMIN_IDS
    
    if not db.can_play_roulette(user_id):
        await message.answer("⏳ Ты уже крутил сегодня! Приходи завтра.", reply_markup=get_main_keyboard())
        return
    
    msg = await message.answer("🎡 Крутим... 3")
    await asyncio.sleep(1)
    await msg.edit_text("🎡 Крутим... 2")
    await asyncio.sleep(1)
    await msg.edit_text("🎡 Крутим... 1")
    await asyncio.sleep(1)
    
    # Призы для всех пользователей
    prizes = [
        {"name": "🔍 +1 поиск", "type": "search", "value": 1},
        {"name": "🔍 +2 поиска", "type": "search", "value": 2},
        {"name": "🔍 +3 поиска", "type": "search", "value": 3},
        {"name": "⚙️ +1 фильтр", "type": "filter", "value": 1},
        {"name": "⚙️ +2 фильтра", "type": "filter", "value": 2},
        {"name": "🎭 +1 маска", "type": "mask", "value": 1},
        {"name": "🎭 +2 маски", "type": "mask", "value": 2},
        {"name": "💎 Премиум 10мин", "type": "premium", "value": 10},
        {"name": "💎 Премиум 1ч", "type": "premium", "value": 60},
        {"name": "💎 Премиум 1д", "type": "premium", "value": 1440},
    ]
    
    # Звёздные призы (только для премиум)
    star_prizes = [
        {"name": "⭐ 15 звёзд!", "type": "stars", "value": 15},
        {"name": "⭐⭐ 50 звёзд!", "type": "stars", "value": 50},
        {"name": "⭐⭐⭐ 100 звёзд!", "type": "stars", "value": 100},
    ]
    
    available_prizes = prizes.copy()
    if is_premium:
        available_prizes.extend(star_prizes)
    
    weights = []
    for p in available_prizes:
        if p["type"] == "stars":
            weights.append(1)
        elif p["type"] == "premium":
            weights.append(5)
        elif p["type"] in ("filter", "mask"):
            weights.append(15)
        else:
            weights.append(25)
    
    result = random.choices(available_prizes, weights=weights, k=1)[0]
    
    reward_text = ""
    if result["type"] == "search":
        db.add_search(user_id, result["value"])
        reward_text = f"✅ +{result['value']} поисков сегодня!"
    elif result["type"] == "filter":
        db.add_filter_request(user_id, result["value"])
        reward_text = f"✅ +{result['value']} фильтр-запросов!"
    elif result["type"] == "mask":
        db.add_mask_request(user_id, result["value"])
        reward_text = f"✅ +{result['value']} маска-запросов!"
    elif result["type"] == "premium":
        delta = timedelta(minutes=result["value"])
        new_until = db.add_premium_time(user_id, delta)
        reward_text = f"✅ Премиум до {new_until}!"
    elif result["type"] == "stars":
        db.add_stars(user_id, result["value"])
        reward_text = f"⭐ +{result['value']} звёзд!"
    
    db.set_roulette_cooldown(user_id)
    
    await msg.delete()
    await message.answer(
        f"🎰 **Рулетка:**\n\n{result['name']}\n{reward_text}\n\n⭐️ Баланс: {db.get_stars(user_id)}\n🔍 Осталось: {db.get_remaining_searches(user_id)}",
        reply_markup=get_main_keyboard()
    )


# ========== ОЦЕНКА ЮЗЕРНЕЙМА ==========
@dp.message(F.text == "⭐️ Оценить юзернейм")
async def evaluate_start(message: Message, state: FSMContext):
    await state.set_state(Form.eval_username)
    await message.answer("Отправь юзернейм для оценки:", reply_markup=get_cancel_keyboard())

@dp.message(Form.eval_username)
async def evaluate_process(message: Message, state: FSMContext):
    target = message.text.replace("@", "").strip()
    if len(target) < 5 or len(target) > 32 or not re.match(r'^[a-zA-Z0-9_]+$', target):
        await message.answer("❌ Неверный формат", reply_markup=get_cancel_keyboard())
        return
    score, verdict = engine.evaluate(target)
    await message.answer(f"📊 Оценка @{target}: ⭐ {score}/10 ({verdict})", reply_markup=get_main_keyboard())
    await state.clear()


# ========== ПРЕМИУМ ==========
@dp.message(F.text == "💎 Премиум")
async def premium_info(message: Message):
    await message.answer(
        f"💎 **ПРЕМИУМ ДОСТУП**\n\n"
        f"Premium даёт: {PREMIUM_SEARCH_LIMIT} поисков/день, ловушку, фильтры, маски.\n"
        f"🎁 Бонус: звёздные призы в рулетке!\n\n"
        f"Купить за звёзды в профиле",
        reply_markup=get_main_keyboard()
    )


# ========== ФИЛЬТРЫ ==========
@dp.message(F.text == "⚙️ Фильтры")
async def filters_menu(message: Message):
    user_id = message.from_user.id
    profile_data = db.get_profile(user_id)
    if not profile_data or not profile_data[0]:
        await message.answer("❌ Ошибка профиля. Напиши /start", reply_markup=get_main_keyboard())
        return
    
    user_data = profile_data[0]
    premium_until = user_data[5]
    mirrors = user_data[6]
    filter_requests = db.get_filter_requests(user_id)
    mask_requests = db.get_mask_requests(user_id)
    is_prem = db.is_premium(user_id)
    
    if not is_prem and mirrors <= 0 and filter_requests <= 0 and mask_requests <= 0:
        await message.answer("❌ Фильтры только для Premium! Или выиграй в рулетке.", reply_markup=get_main_keyboard())
        return
    
    curr_filter, curr_digits = db.get_user_filter(user_id)
    digits_text = "Включены" if curr_digits else "Выключены"
    
    await message.answer(
        f"⚙️ **ФИЛЬТРЫ**\n\n"
        f"Режим: {curr_filter}\n"
        f"Цифры: {digits_text}\n"
        f"🪞 Зеркальных: {mirrors}\n"
        f"⚙️ Фильтр-запросов: {filter_requests}\n"
        f"🎭 Маска-запросов: {mask_requests}",
        reply_markup=get_filters_keyboard()
    )

@dp.message(F.text.in_(["🟢 Обычный", "🪞 Зеркальный 🔒"]))
async def set_filter(message: Message):
    user_id = message.from_user.id
    if message.text == "🪞 Зеркальный 🔒":
        mirrors = db.get_mirror_searches(user_id)
        if mirrors <= 0:
            await message.answer("❌ Нет зеркальных! Выиграй в рулетке.", reply_markup=get_main_keyboard())
            return
    curr_filter, curr_digits = db.get_user_filter(user_id)
    db.set_user_filter(user_id, message.text, curr_digits)
    await message.answer(f"✅ Фильтр: {message.text}", reply_markup=get_main_keyboard())

@dp.message(F.text == "🔢 Включить цифры")
async def enable_digits(message: Message):
    user_id = message.from_user.id
    curr_filter, _ = db.get_user_filter(user_id)
    db.set_user_filter(user_id, curr_filter, True)
    await message.answer("✅ Цифры ВКЛЮЧЕНЫ", reply_markup=get_main_keyboard())

@dp.message(F.text == "🔠 Выключить цифры")
async def disable_digits(message: Message):
    user_id = message.from_user.id
    curr_filter, _ = db.get_user_filter(user_id)
    db.set_user_filter(user_id, curr_filter, False)
    await message.answer("✅ Цифры ВЫКЛЮЧЕНЫ", reply_markup=get_main_keyboard())


# ========== ЛОВУШКИ ==========
@dp.message(F.text == "🎯 Поставить ловушку")
async def trap_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if not db.is_premium(user_id):
        await message.answer("❌ Ловушка только для Premium!", reply_markup=get_main_keyboard())
        return
    active_traps = db.get_user_active_traps(user_id)
    if active_traps:
        await message.answer(f"🎯 Активная ловушка: @{active_traps[0]}", reply_markup=get_trap_keyboard())
    else:
        await state.set_state(Form.waiting_for_trap)
        await message.answer("🎯 Отправь занятый юзернейм:", reply_markup=get_cancel_keyboard())

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
    is_free = await check_username_http(username)
    if is_free is None:
        await message.answer("❌ Ошибка. Попробуй позже.", reply_markup=get_cancel_keyboard())
        return
    if is_free:
        await message.answer(f"🎉 @{username} уже свободен!\nhttps://t.me/{username}", reply_markup=get_cancel_keyboard())
        return
    db.add_trap(user_id, username)
    await state.clear()
    await message.answer(f"Ловушка на @{username} установлена", reply_markup=get_main_keyboard())


# ========== ПРОФИЛЬ (ФИКС) ==========
@dp.message(F.text == "👤 Профиль")
async def profile(message: Message):
    user_id = message.from_user.id
    profile_data = db.get_profile(user_id)
    if not profile_data or not profile_data[0]:
        await message.answer("❌ Ошибка профиля. Напиши /start", reply_markup=get_main_keyboard())
        return
    user_data, active_t, caught_t = profile_data
    username, today_s, total_s, found_n, join_d, premium_until, mirrors, stars, filter_req, mask_req = user_data
    prem_text = "❌ Нет"
    if premium_until:
        try:
            if datetime.strptime(premium_until, "%Y-%m-%d %H:%M:%S") > datetime.now():
                prem_text = f"✅ До {premium_until}"
        except:
            pass
    
    remaining = db.get_remaining_searches(user_id)
    limit = db.get_user_limit(user_id)
    ref_count = db.get_referral_count(user_id)
    
    await message.answer(
        f"👤 **ПРОФИЛЬ**\n\n"
        f"ID: `{user_id}`\n"
        f"Юзернейм: @{username}\n"
        f"💎 Премиум: {prem_text}\n"
        f"⭐️ Звезд: {stars}\n"
        f"🪞 Зеркальных: {mirrors}\n"
        f"⚙️ Фильтр-запросов: {filter_req}\n"
        f"🎭 Маска-запросов: {mask_req}\n"
        f"📅 Поисков сегодня: {limit - remaining}/{limit}\n"
        f"📊 Всего найдено: {found_n}\n"
        f"👥 Рефералов: {ref_count}\n"
        f"🎯 Ловушек: {active_t} активных / {caught_t} сработало",
        reply_markup=get_profile_keyboard()
    )


# ========== ПОПОЛНЕНИЕ ЧЕРЕЗ USDT ==========
@dp.message(F.text == "⭐️ Пополнить баланс")
async def topup_balance(message: Message):
    builder = InlineKeyboardBuilder()
    for stars, usdt in STARS_TO_USDT.items():
        builder.button(text=f"⭐️ {stars} звёзд (${usdt})", callback_data=f"topup_{stars}")
    builder.button(text="🔙 Назад", callback_data="back_to_profile")
    builder.adjust(1)
    await message.answer("💎 **Пополнение через USDT**\n\nВыбери количество звёзд:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("topup_"))
async def topup_selected(call: CallbackQuery):
    stars = int(call.data.split("_")[1])
    usdt = STARS_TO_USDT[stars]
    user_id = call.from_user.id
    
    result = create_crypto_invoice(usdt, f"Пополнение на {stars} звёзд")
    if not result or not result.get("ok"):
        await call.message.edit_text("❌ Ошибка создания счёта")
        await call.answer("Ошибка", show_alert=True)
        return
    
    invoice_id = result["result"]["invoice_id"]
    pay_url = result["result"]["pay_url"]
    db.add_invoice(invoice_id, user_id, stars, usdt, "topup")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить", url=pay_url)],
        [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"check_invoice_{invoice_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_profile")]
    ])
    await call.message.edit_text(f"💎 **Оплата {stars} звёзд**\n💰 {usdt} USDT\n\nПосле оплаты нажми «Проверить оплату».", reply_markup=keyboard)
    await call.answer()

@dp.callback_query(F.data.startswith("check_invoice_"))
async def check_invoice(call: CallbackQuery):
    invoice_id = call.data.split("_")[2]
    user_id = call.from_user.id
    
    invoice = db.get_invoice(invoice_id)
    if not invoice or invoice[5] == "paid":
        await call.answer("Уже оплачено", show_alert=True)
        return
    
    status = get_invoice_status(invoice_id)
    if status == "active":
        await call.answer("Счёт не оплачен", show_alert=True)
    elif status == "paid":
        stars = invoice[3]
        db.add_stars(user_id, stars)
        db.update_invoice_status(invoice_id, "paid")
        await call.message.edit_text(f"✅ Оплачено! Начислено {stars} звёзд.")
        await call.answer("Готово!", show_alert=True)
    else:
        await call.answer("Ошибка", show_alert=True)


# ========== ПОДДЕРЖКА БОТА ==========
@dp.message(F.text == "❤️ Поддержать бота")
async def donate_start(message: Message, state: FSMContext):
    await state.set_state(Form.donate_amount)
    await message.answer(f"💝 **Поддержать бота**\n\nМинимум: {MIN_DONATE_USDT} USDT\nВведи сумму:", reply_markup=get_cancel_keyboard())

@dp.message(Form.donate_amount)
async def donate_amount_input(message: Message, state: FSMContext):
    try:
        amount = float(message.text.strip().replace(',', '.'))
        if amount < MIN_DONATE_USDT:
            await message.answer(f"❌ Минимум {MIN_DONATE_USDT} USDT", reply_markup=get_cancel_keyboard())
            return
    except:
        await message.answer("❌ Введи число", reply_markup=get_cancel_keyboard())
        return
    
    user_id = message.from_user.id
    username = message.from_user.username or f"User{user_id}"
    result = create_crypto_invoice(amount, f"Поддержка от @{username}")
    
    if not result or not result.get("ok"):
        await message.answer("❌ Ошибка создания счёта", reply_markup=get_main_keyboard())
        await state.clear()
        return
    
    invoice_id = result["result"]["invoice_id"]
    pay_url = result["result"]["pay_url"]
    db.add_invoice(invoice_id, user_id, 0, amount, "donate")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Оплатить", url=pay_url)],
        [InlineKeyboardButton(text="✅ Проверить оплату", callback_data=f"check_donate_{invoice_id}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")]
    ])
    await message.answer(f"💝 **Поддержка**\n💰 {amount} USDT\n\nПосле оплаты все получат уведомление!", reply_markup=keyboard)
    await state.clear()

@dp.callback_query(F.data.startswith("check_donate_"))
async def check_donate(call: CallbackQuery):
    invoice_id = call.data.split("_")[2]
    user_id = call.from_user.id
    
    invoice = db.get_invoice(invoice_id)
    if not invoice or invoice[5] == "paid":
        await call.answer("Уже обработано", show_alert=True)
        return
    
    status = get_invoice_status(invoice_id)
    if status == "paid":
        amount_usdt = invoice[4]
        username = call.from_user.username or f"User{user_id}"
        
        db.add_donation(user_id, username, amount_usdt, invoice_id)
        db.update_invoice_status(invoice_id, "paid")
        await call.message.edit_text(f"✅ Спасибо! Вы пожертвовали {amount_usdt} USDT")
        
        all_users = db.get_all_user_ids()
        broadcast_text = f"🎉 @{username} поддержал бота на {amount_usdt} USDT! Спасибо!"
        for uid in all_users:
            try:
                await call.bot.send_message(uid, broadcast_text)
                await asyncio.sleep(0.05)
            except:
                pass
        
        for admin_id in ADMIN_IDS:
            try:
                await call.bot.send_message(admin_id, f"💝 Новый донат: @{username} - {amount_usdt} USDT")
            except:
                pass
        
        await call.answer("Спасибо!", show_alert=True)
    else:
        await call.answer("Счёт не оплачен", show_alert=True)

@dp.callback_query(F.data == "back_to_profile")
async def back_to_profile(call: CallbackQuery):
    await call.message.delete()
    await profile(call.message)
    await call.answer()

@dp.callback_query(F.data == "back_to_main")
async def back_to_main_callback(call: CallbackQuery):
    await call.message.delete()
    await call.message.answer(WELCOME_TEXT, reply_markup=get_main_keyboard())
    await call.answer()


# ========== КУПЛЯ ПРЕМИУМА ==========
@dp.message(F.text == "💎 Купить премиум")
async def buy_premium_menu(message: Message):
    await message.answer("Выбери срок:", reply_markup=get_premium_prices_keyboard())

async def buy_premium_handler(message: Message, days: int, price: int):
    user_id = message.from_user.id
    
    profile_data = db.get_profile(user_id)
    if not profile_data or not profile_data[0]:
        await message.answer("❌ Ошибка профиля. Напиши /start", reply_markup=get_main_keyboard())
        return
    
    if db.is_market_banned(user_id):
        await message.answer("❌ Ты в ЧС", reply_markup=get_profile_keyboard())
        return
    
    stars = db.get_stars(user_id)
    if stars < price:
        await message.answer(f"❌ Нужно {price}⭐, у тебя {stars}", reply_markup=get_profile_keyboard())
        return
    
    if db.remove_stars(user_id, price):
        new_until = db.add_premium_time(user_id, timedelta(days=days))
        await message.answer(f"✅ Premium до {new_until}!\n🎁 Теперь доступны звёздные призы в рулетке!", reply_markup=get_profile_keyboard())
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


# ========== СТАТИСТИКА ==========
@dp.message(F.text == "📊 Статистика")
async def stats(message: Message):
    total_users, found_nicks, active_traps = db.get_stats()
    prem_users = db.get_all_premium_users()
    active_prems = 0
    now = datetime.now()
    for _, _, until in prem_users:
        try:
            if datetime.strptime(until, "%Y-%m-%d %H:%M:%S") > now:
                active_prems += 1
        except:
            pass
    total_donations = db.get_total_donations()
    pending, processing, _, _, _ = db.get_queue_stats()
    
    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Очередь", callback_data="queue_stats")
    builder.button(text="🔙 В меню", callback_data="back_to_main")
    builder.adjust(1)
    
    await message.answer(
        f"📊 **СТАТИСТИКА**\n\n"
        f"👥 Пользователей: {total_users}\n"
        f"💎 Премиум: {active_prems}\n"
        f"✅ Найдено ников: {found_nicks}\n"
        f"🎯 Ловушек: {active_traps}\n"
        f"💰 Донатов: {total_donations} USDT\n"
        f"⏳ В очереди: {pending}",
        reply_markup=builder.as_markup()
    )

@dp.callback_query(F.data == "queue_stats")
async def queue_stats_callback(call: CallbackQuery):
    pending, processing, admin_pending, premium_pending, free_pending = db.get_queue_stats()
    items = db.get_queue_items(10)
    
    text = f"📊 **СТАТИСТИКА ОЧЕРЕДИ**\n\n"
    text += f"⏳ В очереди: {pending}\n"
    text += f"🔧 В работе: {processing}\n"
    text += f"🟢 Админов: {admin_pending}\n"
    text += f"🟣 Премиум: {premium_pending}\n"
    text += f"🔵 Фри: {free_pending}\n"
    text += f"⏱️ Среднее ожидание: ~{pending * SEARCH_DELAY} сек\n\n"
    
    if items:
        text += "**Ближайшие:**\n"
        for item in items[:5]:
            priority_emoji = "🟢" if item[4] == 2 else "🟣" if item[4] == 1 else "🔵"
            text += f"{priority_emoji} массовый x{item[2]}\n"
    
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_stats")]]))
    await call.answer()

@dp.callback_query(F.data == "back_to_stats")
async def back_to_stats(call: CallbackQuery):
    await stats(call.message)


# ========== РЕФЕРАЛКА ==========
@dp.message(F.text == "🔗 Реферальная ссылка")
async def referral_link(message: Message):
    user_id = message.from_user.id
    code = db.get_ref_code(user_id)
    bot_username = (await bot.get_me()).username
    link = f"https://t.me/{bot_username}?start=ref_{code}"
    await message.answer(f"🔗 **Твоя рефералка:**\n\n{link}\n\nПриглашённый +15⭐, ты +25⭐", reply_markup=get_main_keyboard())


# ========== ПРОМОКОДЫ ==========
@dp.message(F.text == "🎫 Активировать промокод")
async def activate_promo_start(message: Message, state: FSMContext):
    await state.set_state(Form.activate_promo)
    await message.answer("Введи промокод:", reply_markup=get_cancel_keyboard())

@dp.message(Form.activate_promo)
async def activate_promo_process(message: Message, state: FSMContext):
    code = message.text.strip()
    success, msg, _ = db.use_promocode(code, message.from_user.id)
    await state.clear()
    await message.answer(msg, reply_markup=get_main_keyboard())


# ========== МАРКЕТ ==========
@dp.message(F.text == "🛒 Маркет")
async def market_main(message: Message):
    await message.answer("🛒 **Маркет**\n\nВыбери действие:", reply_markup=get_market_main_keyboard())

@dp.message(F.text == "📦 Мои лоты")
async def my_lots(message: Message):
    user_id = message.from_user.id
    if db.is_market_banned(user_id):
        await message.answer("❌ Ты в ЧС", reply_markup=get_market_main_keyboard())
        return
    lots = db.get_user_market_lots(user_id)
    if not lots:
        await message.answer("Нет активных лотов.", reply_markup=get_market_main_keyboard())
        return
    text = "📦 **Твои лоты:**\n\n"
    builder = InlineKeyboardBuilder()
    for lid, uname, price, desc, created in lots:
        text += f"ID: {lid} | @{uname} | {price}⭐\n{desc[:40] if desc else ''}\n\n"
        builder.button(text=f"🗑 Удалить {uname}", callback_data=f"del_lot_{lid}")
    builder.button(text="🔙 Назад", callback_data="back_to_market")
    builder.adjust(1)
    await message.answer(text, reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("del_lot_"))
async def delete_lot_callback(call: CallbackQuery):
    lot_id = int(call.data.split("_")[2])
    user_id = call.from_user.id
    if db.delete_market_lot(lot_id, user_id):
        await call.answer("Лот удалён", show_alert=True)
        await call.message.delete()
    else:
        await call.answer("Не удалось", show_alert=True)

@dp.message(F.text == "💎 Продать")
async def sell_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if db.is_market_banned(user_id):
        await message.answer("❌ Ты в ЧС", reply_markup=get_market_main_keyboard())
        return
    await state.set_state(Form.market_sell_username)
    await message.answer("Отправь юзернейм (без @):", reply_markup=get_cancel_keyboard())

@dp.message(Form.market_sell_username)
async def sell_username(message: Message, state: FSMContext):
    username = message.text.strip().lower()
    if not re.match(r'^[a-z0-9_]{5,}$', username):
        await message.answer("❌ Неверный формат", reply_markup=get_cancel_keyboard())
        return
    await state.update_data(market_username=username)
    await state.set_state(Form.market_sell_desc)
    await message.answer("Введи описание (или '-'):", reply_markup=get_cancel_keyboard())

@dp.message(Form.market_sell_desc)
async def sell_desc(message: Message, state: FSMContext):
    desc = message.text.strip()
    if desc == "-":
        desc = ""
    if len(desc) > 200:
        desc = desc[:200]
        await message.answer("⚠️ Описание обрезано до 200 символов.")
    await state.update_data(market_desc=desc)
    await state.set_state(Form.market_sell_price)
    await message.answer("Введи цену в звёздах:", reply_markup=get_cancel_keyboard())

@dp.message(Form.market_sell_price)
async def sell_price(message: Message, state: FSMContext):
    try:
        price = int(message.text.strip())
        if price <= 0:
            raise ValueError
    except:
        await message.answer("❌ Цена должна быть положительным числом", reply_markup=get_cancel_keyboard())
        return
    data = await state.get_data()
    username = data.get("market_username")
    desc = data.get("market_desc")
    user_id = message.from_user.id
    lot_id = db.add_market_lot(user_id, username, price, desc)
    if lot_id is None:
        await message.answer("❌ Ты в ЧС", reply_markup=get_market_main_keyboard())
        return
    await state.clear()
    await message.answer(f"✅ Лот #{lot_id} создан!\n@{username} за {price}⭐", reply_markup=get_market_main_keyboard())

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
    text = f"📋 **Все лоты (стр. {offset//7 + 1}):**\n\n"
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
    offset = int(call.data.split("_")[2])
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
    lot_id = int(call.data.split("_")[2])
    lot = db.get_market_lot(lot_id)
    if not lot:
        await call.answer("Лот не существует", show_alert=True)
        return
    lid, seller, uname, price, desc, created = lot
    seller_profile = db.get_profile(seller)
    seller_name = seller_profile[0][0] if seller_profile and seller_profile[0] else str(seller)
    avg_rating = db.get_seller_avg_rating(seller)
    text = f"**Лот #{lid}**\n\n👤 Владелец: @{seller_name}\n🔹 @{uname}\n💰 {price}⭐\n⭐️ Оценка: {avg_rating}/5"
    builder = InlineKeyboardBuilder()
    builder.button(text="💰 Купить", callback_data=f"buy_lot_{lid}")
    builder.button(text="🔙 Назад", callback_data="back_to_market")
    await call.message.edit_text(text, reply_markup=builder.as_markup())
    await call.answer()

@dp.callback_query(F.data.startswith("buy_lot_"))
async def buy_lot(call: CallbackQuery):
    lot_id = int(call.data.split("_")[2])
    lot = db.get_market_lot(lot_id)
    if not lot:
        await call.answer("Лот продан", show_alert=True)
        return
    lid, seller, uname, price, desc, created = lot
    if seller == call.from_user.id:
        await call.answer("Нельзя купить свой лот", show_alert=True)
        return
    if db.is_market_banned(call.from_user.id) or db.is_market_banned(seller):
        await call.answer("Ты или продавец в ЧС", show_alert=True)
        return
    order_id = db.create_order(lot_id, call.from_user.id, seller)
    if not order_id:
        await call.answer("Ошибка создания заказа", show_alert=True)
        return
    text = f"🛒 **Заказ #{order_id}**\n\n@{uname} за {price}⭐\nСвяжись с продавцом."
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Подтвердить", callback_data=f"confirm_order_{order_id}")
    builder.button(text="⚠️ Спор", callback_data=f"open_dispute_{order_id}")
    await call.message.edit_text(text, reply_markup=builder.as_markup())
    await call.answer()


# ========== ПОДТВЕРЖДЕНИЕ ЗАКАЗА ==========
@dp.callback_query(F.data.startswith("confirm_order_"))
async def confirm_order(call: CallbackQuery, state: FSMContext):
    order_id = int(call.data.split("_")[2])
    order = db.get_order(order_id)
    if not order or order[2] != call.from_user.id or order[4] != 'pending':
        await call.answer("Ошибка", show_alert=True)
        return
    db.confirm_order(order_id)
    await call.answer("Заказ подтверждён!", show_alert=True)
    await call.message.delete()
    await call.message.answer("Оцени сделку:", reply_markup=get_review_keyboard(order[3], order_id))

@dp.callback_query(F.data.startswith("rate_"))
async def rate_seller(call: CallbackQuery, state: FSMContext):
    _, seller_id, order_id, rating = call.data.split("_")
    await state.update_data(review_seller_id=int(seller_id), review_order_id=int(order_id), review_rating=int(rating))
    await state.set_state(Form.review_text)
    await call.message.answer("Напиши отзыв (или '-'):", reply_markup=get_cancel_keyboard())
    await call.answer()

@dp.message(Form.review_text)
async def review_text(message: Message, state: FSMContext):
    data = await state.get_data()
    text = message.text.strip() if message.text.strip() != "-" else ""
    text = text[:500]
    db.add_review(data.get("review_seller_id"), message.from_user.id, data.get("review_rating"), text)
    await state.clear()
    await message.answer("Спасибо за отзыв!", reply_markup=get_main_keyboard())


# ========== ОТКРЫТИЕ СПОРА ==========
@dp.callback_query(F.data.startswith("open_dispute_"))
async def open_dispute(call: CallbackQuery, state: FSMContext):
    order_id = int(call.data.split("_")[2])
    order = db.get_order(order_id)
    if not order:
        await call.answer("Заказ не найден", show_alert=True)
        return
    await state.update_data(dispute_order_id=order_id)
    await state.set_state(Form.dispute_reason)
    await call.message.answer("Напиши причину спора:", reply_markup=get_cancel_keyboard())
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
    dispute_id = db.add_dispute(order_id, order[2], order[3], message.text.strip(), message.from_user.id)
    await state.clear()
    await message.answer(f"Спор #{dispute_id} открыт. Админ рассмотрит.", reply_markup=get_main_keyboard())
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, f"⚠️ Новый спор #{dispute_id}\nЗаказ #{order_id}")
        except:
            pass


# ========== АДМИН: СПОРЫ ==========
@dp.message(F.text == "⚖️ Споры")
async def admin_disputes(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    disputes = db.get_open_disputes()
    if not disputes:
        await message.answer("Нет открытых споров.")
        return
    for d in disputes:
        text = f"⚖️ Спор #{d[0]} | Заказ #{d[1]}\nПричина: {d[4][:100]}"
        builder = InlineKeyboardBuilder()
        builder.button(text="🔍 Рассмотреть", callback_data=f"admin_resolve_dispute_{d[0]}")
        await message.answer(text, reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("admin_resolve_dispute_"))
async def admin_resolve_dispute(call: CallbackQuery):
    dispute_id = int(call.data.split("_")[3])
    dispute = None
    for d in db.get_open_disputes():
        if d[0] == dispute_id:
            dispute = d
            break
    if not dispute:
        await call.answer("Спор решён", show_alert=True)
        return
    
    db.save_temp_dispute(call.from_user.id, dispute_id, dispute[1], dispute[2], dispute[3])
    
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ В пользу покупателя", callback_data=f"resolve_dispute_{dispute_id}_buyer")
    builder.button(text="✅ В пользу продавца", callback_data=f"resolve_dispute_{dispute_id}_seller")
    await call.message.edit_text(f"Спор #{dispute_id}\nПричина: {dispute[4][:200]}", reply_markup=builder.as_markup())
    await call.answer()

@dp.callback_query(F.data.startswith("resolve_dispute_"))
async def resolve_dispute_decision(call: CallbackQuery, state: FSMContext):
    parts = call.data.split("_")
    dispute_id = int(parts[2])
    decision = parts[3]
    
    temp_data = db.get_temp_dispute(call.from_user.id)
    if not temp_data or temp_data.get("dispute_id") != dispute_id:
        await call.answer("Ошибка: данные спора не найдены", show_alert=True)
        return
    
    loser_id = temp_data["seller_id"] if decision == "buyer" else temp_data["buyer_id"]
    
    await state.update_data(dispute_loser_id=loser_id)
    await state.set_state(Form.dispute_ban_reason)
    await call.message.answer(f"Причина бана для {loser_id}:", reply_markup=get_cancel_keyboard())
    await call.answer()

@dp.message(Form.dispute_ban_reason)
async def dispute_ban_reason(message: Message, state: FSMContext):
    await state.update_data(ban_reason=message.text.strip())
    await state.set_state(Form.dispute_ban_duration)
    await message.answer("Выбери срок:", reply_markup=get_ban_duration_keyboard())

@dp.callback_query(F.data.startswith("ban_duration_"))
async def dispute_ban_duration(call: CallbackQuery, state: FSMContext):
    duration_str = call.data.split("_")[2]
    data = await state.get_data()
    reason = data.get("ban_reason", "Нарушение")
    loser_id = data.get("dispute_loser_id")
    
    temp_data = db.get_temp_dispute(call.from_user.id)
    dispute_id = temp_data.get("dispute_id")
    order_id = temp_data.get("order_id")
    
    if duration_str == "0":
        until = None
        duration_text = "бессрочно"
    else:
        unit = duration_str[-1]
        value = int(duration_str[:-1])
        if unit == 'h': delta = timedelta(hours=value); duration_text = f"{value} часов"
        elif unit == 'd': delta = timedelta(days=value); duration_text = f"{value} дней"
        elif unit == 'm': delta = timedelta(days=value*30); duration_text = f"{value} месяцев"
        elif unit == 'y': delta = timedelta(days=value*365); duration_text = f"{value} лет"
        else: delta = None; duration_text = "бессрочно"
        until = datetime.now() + delta if delta else None
    
    db.add_to_blacklist(loser_id, reason, call.from_user.id, until)
    db.resolve_dispute(dispute_id, call.from_user.id, f"Победитель: {decision}")
    
    if order_id:
        db.cursor.execute("SELECT lot_id FROM market_orders WHERE id=?", (order_id,))
        row = db.cursor.fetchone()
        if row:
            db.cursor.execute("DELETE FROM market_lots WHERE id=?", (row[0],))
            db.conn.commit()
    
    db.delete_temp_dispute(call.from_user.id)
    await state.clear()
    await call.message.edit_text(f"✅ Спор #{dispute_id} решён!\n{loser_id} в ЧС на {duration_text}")
    await call.answer("Готово", show_alert=True)

@dp.callback_query(F.data == "cancel_ban")
async def cancel_ban(call: CallbackQuery, state: FSMContext):
    await state.clear()
    db.delete_temp_dispute(call.from_user.id)
    await call.message.edit_text("❌ Отменено")
    await call.answer()


# ========== АДМИН ПАНЕЛЬ ==========
@dp.message(F.text == "ℹ️ Информация")
async def admin_info(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    total_users, found_nicks, _ = db.get_stats()
    await message.answer(f"👥 Пользователей: {total_users}\n✅ Найдено ников: {found_nicks}")

@dp.message(F.text == "👥 Список премиум")
async def admin_premium_list(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    users = db.get_all_premium_users()
    msg = "💎 Premium:\n" + "\n".join([f"{uid} (@{uname}) до {until}" for uid, uname, until in users]) if users else "Нет"
    await message.answer(msg)

@dp.message(F.text == "💎 Выдать премиум")
async def admin_give_premium(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    await state.set_state(Form.admin_give_prem)
    await message.answer("Формат: ID время (15d, 2m, 1y)", reply_markup=get_cancel_keyboard())

@dp.message(F.text == "❌ Забрать премиум")
async def admin_take_premium(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    await state.set_state(Form.admin_take_prem)
    await message.answer("Введи ID:", reply_markup=get_cancel_keyboard())

@dp.message(F.text == "🪞 Выдать зеркала")
async def admin_give_mirrors(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    await state.set_state(Form.admin_give_mirrors)
    await message.answer("Формат: ID количество", reply_markup=get_cancel_keyboard())

@dp.message(F.text == "⭐️ Выдать звёзды")
async def admin_give_stars(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    await state.set_state(Form.admin_give_stars)
    await message.answer("Формат: ID количество", reply_markup=get_cancel_keyboard())

@dp.message(F.text == "⭐️ Забрать звёзды")
async def admin_take_stars(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    await state.set_state(Form.admin_take_stars)
    await message.answer("Формат: ID количество", reply_markup=get_cancel_keyboard())

@dp.message(F.text == "📢 Рассылка")
async def admin_broadcast(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    await state.set_state(Form.admin_broadcast)
    await message.answer("Введи сообщение для рассылки:", reply_markup=get_cancel_keyboard())

@dp.message(F.text == "🎫 Промокоды")
async def admin_promocodes(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    await message.answer("Управление промокодами", reply_markup=get_promocode_admin_keyboard())

@dp.message(F.text == "🚫 Чёрный список")
async def admin_blacklist_menu(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    await message.answer("Чёрный список маркета", reply_markup=get_blacklist_admin_keyboard())

@dp.message(F.text == "📊 Донаты")
async def admin_donations(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    donations = db.get_donations_history(30)
    total = db.get_total_donations()
    if not donations:
        await message.answer("Донатов нет")
        return
    text = f"💝 Донаты: {total} USDT\n\n"
    for d in donations:
        text += f"@{d[2]} - {d[3]} USDT\n"
    await message.answer(text)


# ========== АДМИН: ВВОД ДАННЫХ ==========
@dp.message(Form.admin_give_prem)
async def admin_give_prem_input(message: Message, state: FSMContext):
    parts = message.text.split()
    if len(parts) == 2:
        try:
            target_id = int(parts[0])
            duration = parts[1]
            match = re.match(r'^(\d+)([hdmy])$', duration.lower())
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
                    await message.answer(f"✅ Premium выдан до {new_date}")
                    try:
                        await bot.send_message(target_id, f"🎉 Premium до {new_date}!")
                    except:
                        pass
        except:
            await message.answer("❌ Ошибка")
    else:
        await message.answer("❌ Формат: ID время")

@dp.message(Form.admin_take_prem)
async def admin_take_prem_input(message: Message, state: FSMContext):
    try:
        target_id = int(message.text)
        db.take_premium(target_id)
        await state.clear()
        await message.answer(f"✅ Премиум снят с {target_id}")
    except:
        await message.answer("❌ Ошибка")

@dp.message(Form.admin_give_mirrors)
async def admin_give_mirrors_input(message: Message, state: FSMContext):
    parts = message.text.split()
    if len(parts) == 2:
        try:
            target_id = int(parts[0])
            amount = int(parts[1])
            db.add_mirror_searches(target_id, amount)
            await state.clear()
            await message.answer(f"✅ {amount} зеркал выдано")
        except:
            await message.answer("❌ Ошибка")
    else:
        await message.answer("❌ Формат: ID количество")

@dp.message(Form.admin_give_stars)
async def admin_give_stars_input(message: Message, state: FSMContext):
    parts = message.text.split()
    if len(parts) == 2:
        try:
            target_id = int(parts[0])
            amount = int(parts[1])
            db.add_stars(target_id, amount)
            await state.clear()
            await message.answer(f"✅ {amount} звёзд выдано")
        except:
            await message.answer("❌ Ошибка")
    else:
        await message.answer("❌ Формат: ID количество")

@dp.message(Form.admin_take_stars)
async def admin_take_stars_input(message: Message, state: FSMContext):
    parts = message.text.split()
    if len(parts) == 2:
        try:
            target_id = int(parts[0])
            amount = int(parts[1])
            if db.remove_stars(target_id, amount):
                await state.clear()
                await message.answer(f"✅ {amount} звёзд снято")
            else:
                await message.answer("❌ Недостаточно звёзд")
        except:
            await message.answer("❌ Ошибка")
    else:
        await message.answer("❌ Формат: ID количество")

@dp.message(Form.admin_broadcast)
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
    await message.answer(f"✅ Рассылка завершена! {success}/{len(users)}")


# ========== ЧЁРНЫЙ СПИСОК ==========
@dp.message(F.text == "➕ Добавить в ЧС")
async def admin_add_blacklist(message: Message, state: FSMContext):
    await state.set_state(Form.add_blacklist_id)
    await message.answer("Введи ID пользователя:", reply_markup=get_cancel_keyboard())

@dp.message(StateFilter(Form.add_blacklist_id))
async def process_add_blacklist_id(message: Message, state: FSMContext):
    try:
        user_id = int(message.text.strip())
        await state.update_data(blacklist_user_id=user_id)
        await state.set_state(Form.add_blacklist_reason)
        await message.answer("Введи причину:", reply_markup=get_cancel_keyboard())
    except:
        await message.answer("❌ ID должен быть числом", reply_markup=get_cancel_keyboard())

@dp.message(StateFilter(Form.add_blacklist_reason))
async def process_add_blacklist_reason(message: Message, state: FSMContext):
    reason = message.text.strip()
    await state.update_data(blacklist_reason=reason)
    await state.set_state("add_blacklist_duration")
    await message.answer("Выбери срок:", reply_markup=get_ban_duration_keyboard())

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
        if unit == 'h': delta = timedelta(hours=value); duration_text = f"{value} часов"
        elif unit == 'd': delta = timedelta(days=value); duration_text = f"{value} дней"
        elif unit == 'm': delta = timedelta(days=value*30); duration_text = f"{value} месяцев"
        elif unit == 'y': delta = timedelta(days=value*365); duration_text = f"{value} лет"
        else: delta = None; duration_text = "бессрочно"
        until = datetime.now() + delta if delta else None
    
    db.add_to_blacklist(user_id, reason, call.from_user.id, until)
    await state.clear()
    await call.message.edit_text(f"✅ {user_id} в ЧС на {duration_text}")
    await call.answer()

@dp.message(F.text == "➖ Убрать из ЧС")
async def admin_remove_blacklist(message: Message, state: FSMContext):
    await state.set_state(Form.remove_blacklist_id)
    await message.answer("Введи ID пользователя:", reply_markup=get_cancel_keyboard())

@dp.message(StateFilter(Form.remove_blacklist_id))
async def process_remove_blacklist(message: Message, state: FSMContext):
    try:
        user_id = int(message.text.strip())
        db.remove_from_blacklist(user_id)
        await state.clear()
        await message.answer(f"✅ {user_id} удалён из ЧС")
    except:
        await message.answer("❌ ID должен быть числом", reply_markup=get_cancel_keyboard())

@dp.message(F.text == "📋 Список ЧС")
async def admin_blacklist_list(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    blacklist = db.get_blacklist()
    if not blacklist:
        await message.answer("ЧС пуст")
        return
    text = "🚫 ЧС:\n"
    for uid, reason, _, until, _ in blacklist:
        until_text = f"до {until}" if until else "бессрочно"
        text += f"• {uid} - {reason} ({until_text})\n"
    await message.answer(text)


# ========== ПРОМОКОДЫ ==========
@dp.message(F.text == "➕ Создать промокод")
async def admin_promo_create_type(message: Message, state: FSMContext):
    await state.set_state(Form.promo_create_type)
    await message.answer("Выбери тип:", reply_markup=get_promocode_type_keyboard())

@dp.message(F.text == "📋 Список промокодов")
async def admin_promo_list(message: Message):
    if message.from_user.id not in ADMIN_IDS: return
    promos = db.get_all_promocodes()
    if not promos:
        await message.answer("Нет промокодов")
        return
    text = "📋 Промокоды:\n"
    for code, ptype, reward, max_uses, used in promos:
        text += f"• {code} | {ptype} | {reward} | {used}/{max_uses}\n"
    await message.answer(text)

@dp.message(F.text == "🗑 Удалить промокод")
async def admin_promo_delete(message: Message, state: FSMContext):
    await state.set_state(Form.promo_delete)
    await message.answer("Введи название промокода:", reply_markup=get_cancel_keyboard())

@dp.message(Form.promo_delete)
async def admin_promo_delete_input(message: Message, state: FSMContext):
    if db.delete_promocode(message.text.strip()):
        await state.clear()
        await message.answer("✅ Промокод удалён")
    else:
        await message.answer("❌ Не найден")

@dp.message(Form.promo_create_type)
async def admin_promo_type_choice(message: Message, state: FSMContext):
    if message.text == "🪞 Зеркальные поиски":
        await state.set_state(Form.promo_create_mirror)
        await message.answer("Формат: код активаций награда\nПример: test 10 50")
    elif message.text == "Премиум":
        await state.set_state(Form.promo_create_premium)
        await message.answer("Формат: код активаций время\nПример: prem 5 7d")
    elif message.text == "⭐️ Звёзды":
        await state.set_state(Form.promo_create_stars)
        await message.answer("Формат: код активаций звёзды\nПример: stars 10 100")
    else:
        await state.clear()
        await message.answer("Управление промокодами", reply_markup=get_promocode_admin_keyboard())

@dp.message(Form.promo_create_mirror)
async def admin_promo_create_mirror(message: Message, state: FSMContext):
    parts = message.text.split()
    if len(parts) == 3:
        code, max_uses, reward = parts
        if db.create_promocode(code, "mirror", reward, int(max_uses)):
            await state.clear()
            await message.answer(f"✅ Промокод {code} создан")
        else:
            await message.answer("❌ Уже существует")
    else:
        await message.answer("❌ Неверный формат")

@dp.message(Form.promo_create_premium)
async def admin_promo_create_premium(message: Message, state: FSMContext):
    parts = message.text.split()
    if len(parts) == 3:
        code, max_uses, duration = parts
        if db.create_promocode(code, "premium", duration, int(max_uses)):
            await state.clear()
            await message.answer(f"✅ Промокод {code} создан")
        else:
            await message.answer("❌ Уже существует")
    else:
        await message.answer("❌ Неверный формат")

@dp.message(Form.promo_create_stars)
async def admin_promo_create_stars(message: Message, state: FSMContext):
    parts = message.text.split()
    if len(parts) == 3:
        code, max_uses, reward = parts
        if db.create_promocode(code, "stars", reward, int(max_uses)):
            await state.clear()
            await message.answer(f"✅ Промокод {code} создан")
        else:
            await message.answer("❌ Уже существует")
    else:
        await message.answer("❌ Неверный формат")


# ========== ФОНОВЫЕ ЗАДАЧИ ==========
async def trap_worker():
    await asyncio.sleep(5)
    logger.warning("Снайпер запущен")
    while True:
        try:
            active_traps = db.get_all_active_traps()
            for t_user_id, t_username in active_traps:
                is_free = await check_username_http(t_username)
                if is_free is True:
                    score, verdict = engine.evaluate(t_username)
                    msg = f"🚨 ЛОВУШКА! @{t_username} СВОБОДЕН\n⭐ {score}/10 ({verdict})\nhttps://t.me/{t_username}"
                    await bot.send_message(t_user_id, msg)
                    db.mark_trap_caught(t_user_id, t_username)
                await asyncio.sleep(2)
        except:
            pass
        await asyncio.sleep(30)

async def hot_nicks_updater():
    while True:
        try:
            hot = db.get_hot_nicks(15)
            hot_data = {'nicks': [{'username': h[0], 'count': h[1]} for h in hot], 'total': sum(h[1] for h in hot)}
            db.update_hot_nicks_cache(hot_data)
        except:
            pass
        await asyncio.sleep(6 * 3600)

async def main():
    asyncio.create_task(trap_worker())
    asyncio.create_task(queue_worker(bot))
    asyncio.create_task(hot_nicks_updater())
    
    logger.warning("✅ Бот запущен с HTTP поиском и WAL!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())