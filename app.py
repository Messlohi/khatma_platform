import os
import sqlite3
import datetime
import asyncio
import logging
import time
from flask import Flask, request, render_template, jsonify, Response
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters
)
from telegram.request import HTTPXRequest

# --- Configuration & Paths (Smart-Sync) ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(BASE_DIR, "khatma.db")
GLOBAL_GID = 1  # Unified Global ID for Bot and Web
TOTAL_HIZBS = 60
PROXY_URL = "http://proxy.server:3128" # Required for PythonAnywhere

# --- Messages (Arabic) ---
MSG_WELCOME = (
    "أهلاً بكم في بوت ختمة عائلة العلمي.\n\n"
    "هذه ختمة جماعية نجعلها في ميزان **يمة** (فطوم العلمي) و **با** (إدريس العلمي) (رحمهما الله)، نرجو من الله أن يوصل ثوابها إليهما.\n\n"
    "الأوامر المتاحة:\n"
    "/join - اختيار حزب\n"
    "/return - إرجاع حزب\n"
    "/hizb - معرفة أحزابك\n"
    "/done - تسجيل إتمام قراءة\n"
    "/status - حالة الختمة\n"
    "/reset - إعادة تعيين الختمة"
)
MSG_SELECT_HIZB = "مرحباً {name}. اختر الأحزاب التي تريد قراءتها:"
MSG_HIZB_TAKEN = "تم اختيار الحزب {hizb} بنجاح ✅"
MSG_RETURN_SELECT = "اختر الحزب الذي تريد إرجاعه:"
MSG_DONE_SELECT = "اختر الحزب الذي أتممت قراءته:"
MSG_KHATMA_COMPLETE = "🎉 **تم كمال الختمة بفضل الله** 🎉\n\nاللهم اجعل ثواب ما قرأناه نوراً على قبر والدينا."

# --- Database Manager ---
class DatabaseManager:
    def __init__(self, db_file):
        self.db_file = db_file
        self.init_db()

    def get_connection(self):
        try:
            conn = sqlite3.connect(self.db_file, timeout=20)
            conn.execute("SELECT 1"); return conn
        except: return sqlite3.connect(self.db_file, timeout=20)

    def init_db(self):
        with self.get_connection() as conn:
            c = conn.cursor()
            
            # New: Khatmas table for multi-tenancy
            c.execute('''CREATE TABLE IF NOT EXISTS khatmas (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                admin_uid INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                intention TEXT,
                deadline TEXT,
                total_khatmas INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1
            )''')
            
            # Legacy tables (keeping for Telegram bot compatibility)
            c.execute('CREATE TABLE IF NOT EXISTS groups (id INTEGER PRIMARY KEY, title TEXT, last_update REAL)')
            
            # Add khatma_id to existing tables - REMOVED UNIQUE from full_name to allow multi-tenant
            c.execute('CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, full_name TEXT, username TEXT, web_pin TEXT, khatma_id TEXT)')
            c.execute("""CREATE TABLE IF NOT EXISTS hizb_assignments (
                group_id INTEGER, 
                user_id INTEGER, 
                hizb_number INTEGER, 
                khatma_id TEXT,
                PRIMARY KEY (khatma_id, hizb_number)
            )""")
            c.execute('CREATE TABLE IF NOT EXISTS completed_hizb (id INTEGER PRIMARY KEY AUTOINCREMENT, group_id INTEGER, user_id INTEGER, hizb_number INTEGER, khatma_id TEXT)')
            c.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT, value TEXT, khatma_id TEXT, PRIMARY KEY (key, khatma_id))')
            c.execute('CREATE TABLE IF NOT EXISTS intentions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT, text TEXT, timestamp REAL, khatma_id TEXT)')
            
            # Initialize Global State (for Telegram bot backward compatibility)
            c.execute("INSERT OR IGNORE INTO groups (id, title, last_update) VALUES (?, ?, ?)", (GLOBAL_GID, "Main Khatma", time.time()))
            
            conn.commit()

    def bump(self):
        with self.get_connection() as conn:
            conn.execute("UPDATE groups SET last_update = ? WHERE id = ?", (time.time(), GLOBAL_GID))
            conn.commit()

    def get_v(self):
        with self.get_connection() as conn:
            row = conn.execute("SELECT last_update FROM groups WHERE id = ?", (GLOBAL_GID,)).fetchone()
            return row[0] if row else 0

    def register_user(self, user_id, full_name, username):
        with self.get_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO users (id, full_name, username) VALUES (?, ?, ?)", (user_id, full_name, username))
            conn.commit()

    def register_web_user(self, name, pin=None, khatma_id=None):
        name, pin = str(name).strip(), (str(pin).strip() if pin else "")
        with self.get_connection() as conn:
            # Search within khatma if provided
            if khatma_id:
                r = conn.execute("SELECT id, web_pin FROM users WHERE full_name = ? AND khatma_id = ?", (name, khatma_id)).fetchone()
            else:
                r = conn.execute("SELECT id, web_pin FROM users WHERE full_name = ?", (name,)).fetchone()
            
            if r:
                uid, dbp = r; dbp = str(dbp).strip() if dbp else ""
                if dbp == "" or dbp == pin:
                    if dbp == "" and pin != "": conn.execute("UPDATE users SET web_pin = ? WHERE id = ?", (pin, int(uid))); conn.commit(); self.bump()
                    return int(uid), "success"
                return None, "wrong_pin"
            else:
                # Generate unique ID with microsecond precision to avoid collisions
                import random
                wid = -int(time.time() * 1000000 + random.randint(0, 999999))
                try:
                    conn.execute("INSERT INTO users (id, full_name, username, web_pin, khatma_id) VALUES (?, ?, ?, ?, ?)", 
                               (wid, name, "web_user", pin if pin else None, khatma_id))
                    conn.commit(); self.bump(); return int(wid), "success"
                except sqlite3.IntegrityError:
                    # ID collision - retry with new ID
                    wid = -int(time.time() * 1000000 + random.randint(0, 999999))
                    conn.execute("INSERT INTO users (id, full_name, username, web_pin, khatma_id) VALUES (?, ?, ?, ?, ?)", 
                               (wid, name, "web_user", pin if pin else None, khatma_id))
                    conn.commit(); self.bump(); return int(wid), "success"

    def is_admin(self, uid, khatma_id):
        """Check if user is admin of the specified Khatma"""
        if not uid or not khatma_id:
            return False
        with self.get_connection() as conn:
            row = conn.execute("SELECT admin_uid FROM khatmas WHERE id = ?", (khatma_id,)).fetchone()
            return row and int(row[0]) == int(uid)

    def assign_hizb(self, user_id, hizb_num, khatma_id=None):
        try:
            with self.get_connection() as conn:
                conn.execute("INSERT INTO hizb_assignments VALUES (?, ?, ?, ?)", 
                           (GLOBAL_GID, int(user_id), int(hizb_num), khatma_id))
                conn.commit(); self.bump(); return True
        except: return False

    def unassign_hizb(self, user_id, hizb_num, khatma_id=None):
        with self.get_connection() as conn:
            if khatma_id:
                c = conn.execute("DELETE FROM hizb_assignments WHERE khatma_id = ? AND user_id = ? AND hizb_number = ?", 
                               (khatma_id, int(user_id), int(hizb_num)))
            else:
                c = conn.execute("DELETE FROM hizb_assignments WHERE group_id = ? AND user_id = ? AND hizb_number = ?", 
                               (GLOBAL_GID, int(user_id), int(hizb_num)))
            conn.commit()
            if c.rowcount > 0: self.bump(); return True
        return False

    def mark_done(self, user_id, hizb_num, khatma_id=None):
        with self.get_connection() as conn:
            if khatma_id:
                c = conn.execute("DELETE FROM hizb_assignments WHERE khatma_id = ? AND user_id = ? AND hizb_number = ?", 
                               (khatma_id, int(user_id), int(hizb_num)))
                if c.rowcount == 0: return False
                conn.execute("INSERT INTO completed_hizb (group_id, user_id, hizb_number, khatma_id) VALUES (?, ?, ?, ?)", 
                           (GLOBAL_GID, int(user_id), int(hizb_num), khatma_id))
                conn.commit(); self.bump()
                # Check for completion
                comp_count = conn.execute("SELECT COUNT(*) FROM completed_hizb WHERE khatma_id = ?", (khatma_id,)).fetchone()[0]
            else:
                c = conn.execute("DELETE FROM hizb_assignments WHERE group_id = ? AND user_id = ? AND hizb_number = ?", 
                               (GLOBAL_GID, int(user_id), int(hizb_num)))
                if c.rowcount == 0: return False
                conn.execute("INSERT INTO completed_hizb (group_id, user_id, hizb_number) VALUES (?, ?, ?)", 
                           (GLOBAL_GID, int(user_id), int(hizb_num)))
                conn.commit(); self.bump()
                comp_count = conn.execute("SELECT COUNT(*) FROM completed_hizb WHERE group_id = ?", (GLOBAL_GID,)).fetchone()[0]
            
            return "completed" if comp_count >= 60 else True

    def mark_all_done(self, user_id):
        with self.get_connection() as conn:
            hizbs = [r[0] for r in conn.execute("SELECT hizb_number FROM hizb_assignments WHERE group_id = ? AND user_id = ?", (GLOBAL_GID, int(user_id))).fetchall()]
            if not hizbs: return []
            conn.execute("DELETE FROM hizb_assignments WHERE group_id = ? AND user_id = ?", (GLOBAL_GID, int(user_id)))
            for h in hizbs: conn.execute("INSERT INTO completed_hizb (group_id, user_id, hizb_number) VALUES (?, ?, ?)", (GLOBAL_GID, int(user_id), int(h)))
            conn.commit(); self.bump()
            
            # Check for completion
            comp_count = conn.execute("SELECT COUNT(*) FROM completed_hizb WHERE group_id = ?", (GLOBAL_GID,)).fetchone()[0]
            return "completed" if comp_count >= 60 else hizbs

    def get_available(self, khatma_id=None):
        with self.get_connection() as conn:
            if khatma_id:
                taken = {r[0] for r in conn.execute(
                    "SELECT hizb_number FROM hizb_assignments WHERE khatma_id = ? UNION SELECT hizb_number FROM completed_hizb WHERE khatma_id = ?", 
                    (khatma_id, khatma_id)).fetchall()}
            else:
                taken = {r[0] for r in conn.execute(
                    "SELECT hizb_number FROM hizb_assignments WHERE group_id = ? UNION SELECT hizb_number FROM completed_hizb WHERE group_id = ?", 
                    (GLOBAL_GID, GLOBAL_GID)).fetchall()}
            return [h for h in range(1, 61) if h not in taken]

    def get_user_assignments(self, user_id, khatma_id=None):
        with self.get_connection() as conn:
            if khatma_id:
                return [r[0] for r in conn.execute("SELECT hizb_number FROM hizb_assignments WHERE khatma_id = ? AND user_id = ?", 
                                                  (khatma_id, int(user_id))).fetchall()]
            else:
                return [r[0] for r in conn.execute("SELECT hizb_number FROM hizb_assignments WHERE group_id = ? AND user_id = ?", 
                                                  (GLOBAL_GID, int(user_id))).fetchall()]

    def get_status(self, khatma_id=None):
        with self.get_connection() as conn:
            if khatma_id:
                c = conn.execute("SELECT COUNT(*) FROM completed_hizb WHERE khatma_id = ?", (khatma_id,)).fetchone()[0]
                a = conn.execute("SELECT COUNT(*) FROM hizb_assignments WHERE khatma_id = ?", (khatma_id,)).fetchone()[0]
                rows = conn.execute(
                    "SELECT COALESCE(u.full_name, 'مشارك'), ha.hizb_number FROM hizb_assignments ha LEFT JOIN users u ON ha.user_id = u.id WHERE ha.khatma_id = ?", 
                    (khatma_id,)).fetchall()
            else:
                c = conn.execute("SELECT COUNT(*) FROM completed_hizb WHERE group_id = ?", (GLOBAL_GID,)).fetchone()[0]
                a = conn.execute("SELECT COUNT(*) FROM hizb_assignments WHERE group_id = ?", (GLOBAL_GID,)).fetchone()[0]
                rows = conn.execute(
                    "SELECT COALESCE(u.full_name, 'مشارك (تليجرام)'), ha.hizb_number FROM hizb_assignments ha LEFT JOIN users u ON ha.user_id = u.id WHERE ha.group_id = ?", 
                    (GLOBAL_GID,)).fetchall()
            
            ass = {}
            for n, h in rows:
                if n not in ass: ass[n] = []
                ass[n].append(h)
            return int(c), int(a), ass

    def get_participants_activity(self, khatma_id=None):
        with self.get_connection() as conn:
            # Filter by khatma_id if provided, otherwise use GLOBAL_GID for backward compatibility
            if khatma_id:
                # Get Active for this Khatma
                active_rows = conn.execute(
                    "SELECT COALESCE(u.full_name, 'مشارك'), ha.hizb_number FROM hizb_assignments ha LEFT JOIN users u ON ha.user_id = u.id WHERE ha.khatma_id = ?", 
                    (khatma_id,)
                ).fetchall()
                # Get Completed for this Khatma
                comp_rows = conn.execute(
                    "SELECT COALESCE(u.full_name, 'مشارك'), ch.hizb_number FROM completed_hizb ch LEFT JOIN users u ON ch.user_id = u.id WHERE ch.khatma_id = ?", 
                    (khatma_id,)
                ).fetchall()
            else:
                # Legacy: Get Active for global bot
                active_rows = conn.execute(
                    "SELECT COALESCE(u.full_name, 'مشارك (تليجرام)'), ha.hizb_number FROM hizb_assignments ha LEFT JOIN users u ON ha.user_id = u.id WHERE ha.group_id = ?", 
                    (GLOBAL_GID,)
                ).fetchall()
                # Get Completed for global bot
                comp_rows = conn.execute(
                    "SELECT COALESCE(u.full_name, 'مشارك (تليجرام)'), ch.hizb_number FROM completed_hizb ch LEFT JOIN users u ON ch.user_id = u.id WHERE ch.group_id = ?", 
                    (GLOBAL_GID,)
                ).fetchall()
            
            data = {}
            for name, hizb in active_rows:
                if name not in data: data[name] = {"active": [], "completed": []}
                data[name]["active"].append(hizb)
                
            for name, hizb in comp_rows:
                if name not in data: data[name] = {"active": [], "completed": []}
                data[name]["completed"].append(hizb)
                
            # Convert to list
            return [{"name": k, "active": sorted(v["active"]), "completed": sorted(v["completed"])} for k, v in data.items()]

    def get_all_users(self, khatma_id=None):
        with self.get_connection() as conn:
            # Get users with counts of active and completed hizbs
            # IMPORTANT: Filter by khatma_id if provided
            if khatma_id:
                query = """
                    SELECT u.id, u.full_name, u.web_pin,
                           (SELECT COUNT(*) FROM hizb_assignments ha WHERE ha.user_id = u.id AND ha.khatma_id = ?) as active_count,
                           (SELECT COUNT(*) FROM completed_hizb ch WHERE ch.user_id = u.id AND ch.khatma_id = ?) as completed_count
                    FROM users u
                    WHERE u.khatma_id = ?
                """
                rows = conn.execute(query, (khatma_id, khatma_id, khatma_id)).fetchall()
            else:
                query = """
                    SELECT u.id, u.full_name, u.web_pin,
                           (SELECT COUNT(*) FROM hizb_assignments ha WHERE ha.user_id = u.id) as active_count,
                           (SELECT COUNT(*) FROM completed_hizb ch WHERE ch.user_id = u.id) as completed_count
                    FROM users u
                """
                rows = conn.execute(query).fetchall()
            return [{"id": r[0], "name": r[1], "pin": r[2], "active": r[3], "completed": r[4]} for r in rows]

    def reset_user_pin(self, user_id):
        with self.get_connection() as conn:
            conn.execute("UPDATE users SET web_pin = NULL WHERE id = ?", (int(user_id),))
            conn.commit(); self.bump()

    def get_user_name(self, user_id):
        with self.get_connection() as conn:
            r = conn.execute("SELECT full_name FROM users WHERE id = ?", (int(user_id),)).fetchone()
            return r[0] if r else None
    
    def update_user_name(self, user_id, new_name):
        with self.get_connection() as conn:
            conn.execute("UPDATE users SET full_name = ? WHERE id = ?", (new_name, int(user_id)))
            conn.commit(); self.bump()

    def get_user_hizbs(self, user_id):
        with self.get_connection() as conn:
            return [r[0] for r in conn.execute("SELECT hizb_number FROM hizb_assignments WHERE user_id = ?", (int(user_id),)).fetchall()]

    def increment_total_completions(self):
        with self.get_connection() as conn:
            curr = conn.execute("SELECT value FROM settings WHERE key = 'total_khatmas'").fetchone()
            new_val = int(curr[0] or 0) + 1
            conn.execute("UPDATE settings SET value = ? WHERE key = 'total_khatmas'", (str(new_val),))
            conn.commit(); self.bump()

    def add_intention(self, uid, name, text):
        with self.get_connection() as conn:
            conn.execute("INSERT INTO intentions (user_id, name, text, timestamp) VALUES (?, ?, ?, ?)", 
                         (uid, name, text, time.time()))
            conn.commit(); self.bump()

    def delete_intention(self, uid, dua_id):
        # We delete by ID andUID for security
        with self.get_connection() as conn:
            conn.execute("DELETE FROM intentions WHERE user_id = ? AND id = ?", (uid, int(dua_id)))
            conn.commit(); self.bump()

    def get_intentions(self):
        with self.get_connection() as conn:
            # Added id to allow safe deletion
            rows = conn.execute("SELECT id, name, text, user_id FROM intentions ORDER BY id DESC LIMIT 50").fetchall()
            return [{"id": r[0], "name": r[1], "text": r[2], "uid": r[3]} for r in rows]

    def reset(self):
        with self.get_connection() as conn:
            self.increment_total_completions() # Increment count on reset
            conn.execute("DELETE FROM hizb_assignments WHERE group_id = ?", (GLOBAL_GID,))
            conn.execute("DELETE FROM completed_hizb WHERE group_id = ?", (GLOBAL_GID,))
            conn.execute("DELETE FROM users") 
            conn.execute("DELETE FROM intentions") # Clear wall for new khatma? Or keep it? Let's clear to keep it fresh.
            # Reset deadline to 7 days from now
            new_deadline = (datetime.datetime.now() + datetime.timedelta(days=7)).strftime("%Y-%m-%d %H:%M")
            conn.execute("UPDATE settings SET value = ? WHERE key = 'deadline'", (new_deadline,))
            conn.commit(); self.bump()

    def get_setting(self, key):
        with self.get_connection() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return row[0] if row else None

    def set_setting(self, key, value):
        with self.get_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
            conn.commit(); self.bump()
    
    # --- Multi-Tenant Khatma Functions ---
    def generate_khatma_id(self):
        import random, string
        while True:
            kid = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
            with self.get_connection() as conn:
                exists = conn.execute("SELECT 1 FROM khatmas WHERE id = ?", (kid,)).fetchone()
                if not exists: return kid
    
    def create_khatma(self, name, admin_name, admin_pin, intention=""):
        khatma_id = self.generate_khatma_id()
        default_deadline = (datetime.datetime.now() + datetime.timedelta(days=7)).strftime("%Y-%m-%d %H:%M")
        
        with self.get_connection() as conn:
            # Create admin user
            admin_uid = -int(time.time())
            conn.execute("INSERT INTO users (id, full_name, username, web_pin, khatma_id) VALUES (?, ?, ?, ?, ?)",
                        (admin_uid, admin_name, "web_admin", admin_pin, khatma_id))
            
            # Create khatma
            conn.execute("""INSERT INTO khatmas (id, name, admin_uid, intention, deadline, total_khatmas) 
                           VALUES (?, ?, ?, ?, ?, 0)""",
                        (khatma_id, name, admin_uid, intention, default_deadline))
            
            conn.commit()
            self.bump()
        
        return khatma_id, admin_uid
    
    def get_khatma(self, khatma_id):
        with self.get_connection() as conn:
            row = conn.execute("SELECT id, name, admin_uid, intention, deadline, total_khatmas FROM khatmas WHERE id = ?", 
                             (khatma_id,)).fetchone()
            if row:
                return {"id": row[0], "name": row[1], "admin_uid": row[2], "intention": row[3], 
                       "deadline": row[4], "total_khatmas": row[5]}
        return None

# --- Bot Handlers ---
db = DatabaseManager(DB_FILE)
TOKEN = os.environ.get("BOT_TOKEN")

# Only initialize Telegram bot if token is provided
if TOKEN:
    req_conf = HTTPXRequest(proxy_url=PROXY_URL, read_timeout=60, connect_timeout=60)
    application = ApplicationBuilder().token(TOKEN).request(req_conf).build()
else:
    application = None
    print("⚠️  No BOT_TOKEN found - Telegram bot disabled (web-only mode)")

async def start(u, c):
    db.register_user(u.effective_user.id, u.effective_user.full_name, u.effective_user.username)
    await u.message.reply_text(MSG_WELCOME, parse_mode="Markdown")

async def join_khatma(u, c):
    avail = db.get_available()
    if not avail: return await u.message.reply_text("عذراً، لا توجد أحزاب متاحة.")
    kb = []; row = []
    for h in range(1, 61):
        txt, cb = (str(h), f"assign_{h}") if h in avail else ("✖️", "ignore")
        row.append(InlineKeyboardButton(txt, callback_data=cb))
        if len(row) == 8: kb.append(row); row = []
    if row: kb.append(row)
    await u.message.reply_text(MSG_SELECT_HIZB.format(name=u.effective_user.full_name), reply_markup=InlineKeyboardMarkup(kb))

async def callback_handler(u, c):
    q = u.callback_query; await q.answer()
    if q.data.startswith("assign_"):
        h = int(q.data.split("_")[1])
        # Force register user so /status can find their name
        db.register_user(u.effective_user.id, u.effective_user.full_name, u.effective_user.username)
        if db.assign_hizb(u.effective_user.id, h):
            await c.bot.send_message(u.effective_chat.id, MSG_HIZB_TAKEN.format(hizb=h))
            # Update keyboard
            avail = db.get_available(); kb = []; row = []
            for i in range(1, 61):
                txt, d = (str(i), f"assign_{i}") if i in avail else ("✖️", "ignore")
                row.append(InlineKeyboardButton(txt, callback_data=d))
                if len(row) == 8: kb.append(row); row = []
            if row: kb.append(row)
            try: await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(kb))
            except: pass
    elif q.data.startswith("unassign_"):
        h = int(q.data.split("_")[1])
        if db.unassign_hizb(u.effective_user.id, h):
            await q.edit_message_text(f"تم إرجاع الحزب {h} بنجاح.")
    elif q.data == "done_all":
        res = db.mark_all_done(u.effective_user.id)
        if res == "completed":
            await q.edit_message_text(MSG_KHATMA_COMPLETE, parse_mode="Markdown")
            db.reset()
        elif res:
            await q.edit_message_text(f"تقبل الله منك، تم إتمام الأحزاب: {', '.join(str(x) for x in res)}")
    elif q.data.startswith("done_"):
        h = int(q.data.split("_")[1])
        res = db.mark_done(u.effective_user.id, h)
        if res == "completed":
            await q.edit_message_text(MSG_KHATMA_COMPLETE, parse_mode="Markdown")
            db.reset()
        elif res:
            await q.edit_message_text(f"تقبل الله منك، تم إتمام الحزب {h}.")
    elif q.data == "confirm_reset":
        db.reset(); await q.edit_message_text("تمت إعادة تعيين الختمة بنجاح ✅")

async def my_hizb(u, c):
    h = db.get_user_assignments(u.effective_user.id)
    await u.message.reply_text(f"أحزابك الحالية: {', '.join(str(x) for x in h)}" if h else "ليس لديك أحزاب محجوزة.")

async def return_hizb(u, c):
    h = db.get_user_assignments(u.effective_user.id)
    if not h: return await u.message.reply_text("ليس لديك أحزاب لإرجاعها.")
    kb = [[InlineKeyboardButton(f"إلغاء حجز حزب {x}", callback_data=f"unassign_{x}")] for x in h]
    await u.message.reply_text(MSG_RETURN_SELECT, reply_markup=InlineKeyboardMarkup(kb))

async def done_hizb(u, c):
    h = db.get_user_assignments(u.effective_user.id)
    if not h: return await u.message.reply_text("ليس لديك أحزاب مخصصة.")
    kb = [[InlineKeyboardButton(f"إتمام حزب {x}", callback_data=f"done_{x}")] for x in h]
    if len(h) > 1: kb.append([InlineKeyboardButton("✅ إتمام الكل", callback_data="done_all")])
    await u.message.reply_text(MSG_DONE_SELECT, reply_markup=InlineKeyboardMarkup(kb))

async def status(u, c):
    comp, act, ass = db.get_status()
    msg = f"📊 المكتملة: {comp} | ⏳ قيد القراءة: {act}\n📌 متبقي: {60-comp-act}\n👤 القراء:\n"
    for n, x in ass.items(): msg += f"• {n}: {', '.join(str(i) for i in x)}\n"
    await u.message.reply_text(msg if ass else "لا يوجد قراء حالياً.")

async def reset(u, c):
    member = await c.bot.get_chat_member(u.effective_chat.id, u.effective_user.id)
    if member.status not in ['administrator', 'creator']: return await u.message.reply_text("عذراً، للمشرفين فقط.")
    kb = [[InlineKeyboardButton("نعم، متأكد ✅", callback_data="confirm_reset")], [InlineKeyboardButton("لا، إلغاء ❌", callback_data="cancel_reset")]]
    await u.message.reply_text("هل أنت متأكد من مسح جميع البيانات؟", reply_markup=InlineKeyboardMarkup(kb))

async def set_deadline_cmd(u, c):
    member = await c.bot.get_chat_member(u.effective_chat.id, u.effective_user.id)
    if member.status not in ['administrator', 'creator']: return await u.message.reply_text("عذراً، للمشرفين فقط.")
    
    if not c.args:
        curr = db.get_setting("deadline")
        return await u.message.reply_text(f"الرجاء تحديد التاريخ. حالياً: `{curr}`\nمثال: `/deadline 2026-02-01`", parse_mode="Markdown")
    
    new_date = c.args[0]
    # Simple validation YYYY-MM-DD
    try:
        datetime.datetime.strptime(new_date, "%Y-%m-%d")
        db.set_setting("deadline", f"{new_date} 23:59")
        await u.message.reply_text(f"تم تحديث موعد الختمة إلى: `{new_date}` ✅", parse_mode="Markdown")
    except:
        await u.message.reply_text("صيغة التاريخ غير صحيحة. استخدم: YYYY-MM-DD")

async def keyword_handler(u, c):
    t = (u.message.text or "").strip()
    if any(k in t for k in ["ختمة", "بداية", "مساعدة"]): await start(u, c)
    elif any(k in t for k in ["حجز", "اشتراك"]): await join_khatma(u, c)
    elif any(k in t for k in ["تم", "خلصت"]): await done_hizb(u, c)
    elif any(k in t for k in ["إرجاع", "أرجع"]): await return_hizb(u, c)
    elif any(k in t for k in ["حزبي", "أحزابي"]): await my_hizb(u, c)
    elif any(k in t for k in ["حالة", "وين وصلنا"]): await status(u, c)

# --- Flask & Webhooks ---
app = Flask(__name__)

# Only register bot handlers if bot is initialized
if application:
    application.add_handler(CommandHandler(["start", "help"], start))
    application.add_handler(CommandHandler("join", join_khatma))
    application.add_handler(CommandHandler("return", return_hizb))
    application.add_handler(CommandHandler("hizb", my_hizb))
    application.add_handler(CommandHandler("done", done_hizb))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("reset", reset))
    application.add_handler(CommandHandler("deadline", set_deadline_cmd))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, keyword_handler))
    application.add_handler(CallbackQueryHandler(callback_handler))


# --- Multi-Tenant API ---
@app.route("/api/khatma/create", methods=["POST"])
def create_khatma_api():
    data = request.get_json()
    name = data.get("name")
    admin_name = data.get("admin_name")
    admin_pin = data.get("admin_pin")
    intention = data.get("intention", "")
    deadline = data.get("deadline")  # NEW: Get deadline from payload
    
    if not name or not admin_name or not admin_pin:
        return jsonify({"error": "Missing required fields"}), 400
    
    try:
        # Format deadline to include time if only date is provided
        if deadline:
            deadline_formatted = f"{deadline} 23:59"
        else:
            # Default: 1 week from now
            from datetime import datetime, timedelta
            default_deadline = datetime.now() + timedelta(days=7)
            deadline_formatted = default_deadline.strftime("%Y-%m-%d 23:59")
        
        khatma_id, admin_uid = db.create_khatma(name, admin_name, admin_pin, intention, deadline_formatted)
        return jsonify({"success": True, "khatma_id": khatma_id, "admin_uid": admin_uid})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- Admin API ---
ADMIN_NAME = "Admin"
ADMIN_PIN = "0000"

@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    d = request.get_json()
    if d.get("name") == ADMIN_NAME and str(d.get("pin")) == ADMIN_PIN:
        return jsonify({"success": True})
    return jsonify({"error": "بيانات الدخول غير صحيحة"}), 403

@app.route("/api/admin/users")
def admin_users():
    khatma_id = request.args.get("khatma_id")
    return jsonify({"users": db.get_all_users(khatma_id)})

@app.route("/api/admin/user_hizbs")
def admin_user_hizbs():
    uid = request.args.get("uid")
    if not uid: return jsonify({"error": "UID missing"}), 400
    return jsonify({"hizbs": db.get_user_hizbs(uid)})

@app.route("/api/admin/control", methods=["POST"])
def admin_control():
    d = request.get_json(); action = d.get("action"); uid = d.get("uid"); hizb = d.get("hizb")
    if action == "unassign":
        if db.unassign_hizb(uid, hizb): return jsonify({"success": True})
    elif action == "complete":
        res = db.mark_done(uid, hizb)
        if res == "completed": db.reset(); return jsonify({"success": True, "completed": True})
        if res: return jsonify({"success": True})
    elif action == "reset_pin":
        db.reset_user_pin(uid); return jsonify({"success": True})
    elif action == "deadline":
        db.set_setting("deadline", f"{hizb} 23:59")
        return jsonify({"success": True})
    elif action == "update_total":
        try:
            new_total = int(hizb)  # hizb parameter contains the new total value
            db.set_setting("total_khatmas", str(new_total))
            return jsonify({"success": True})
        except ValueError:
            return jsonify({"error": "Invalid number"}), 400
    elif action == "update_intention":
        intention_text = hizb  # hizb parameter contains the intention text
        if intention_text:
            db.set_setting("intention", intention_text)
            return jsonify({"success": True})
        return jsonify({"error": "Intention text is required"}), 400
    return jsonify({"error": "Action failed"}), 400

@app.route("/api/user/update_name", methods=["POST"])
def update_user_name():
    data = request.get_json()
    uid = data.get("uid")
    new_name = data.get("name")
    requester_uid = data.get("requester_uid")
    
    if not uid or not new_name or not requester_uid:
        return jsonify({"error": "Missing required fields"}), 400
    
    
    # Authorization: user can edit own name, or requester is admin
    requester_name = db.get_user_name(requester_uid)
    if str(uid) == str(requester_uid) or requester_name == "Admin":
        try:
            db.update_user_name(uid, new_name)
            return jsonify({"success": True})
        except Exception as e:
            if "UNIQUE constraint failed" in str(e):
                return jsonify({"error": "Name already taken"}), 400
            return jsonify({"error": "Database error"}), 500
    
    return jsonify({"error": "Unauthorized"}), 403

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    async def process():
        up = Update.de_json(request.get_json(force=True), application.bot)
        await application.process_update(up)
    loop = asyncio.get_event_loop()
    loop.run_until_complete(process())
    return "OK", 200

@app.route("/")
def homepage():
    return render_template("index.html")

@app.route("/khatma/<khatma_id>")
def khatma_page(khatma_id):
    # Verify khatma exists
    khatma = db.get_khatma(khatma_id)
    if not khatma:
        return "Khatma not found", 404
    return render_template("khatma.html", khatma_id=khatma_id, khatma=khatma)


@app.route("/manifest.json")
def manifest():
    return Response("""{
  "name": "ختم القرآن - عائلة العلمي",
  "short_name": "Khatma",
  "description": "Khatma Quran for the Alami Family",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#065f46",
  "theme_color": "#065f46",
  "icons": [{
      "src": "/static/icon.png",
      "sizes": "512x512",
      "type": "image/png",
      "purpose": "any maskable"
  }]
}""", mimetype="application/json")

@app.route("/sw.js")
def sw():
    return Response("self.addEventListener('fetch', function(event) {});", mimetype="application/javascript")

@app.route("/api/khatma")
def api_status():
    try:
        ur = request.args.get("uid")
        khatma_id = request.args.get("khatma_id")  # NEW: Get khatma_id
        
        # Fix: Only convert to int if it's actually numeric digits
        uid = int(ur) if (ur and (ur.isdigit() or (ur.startswith('-') and ur[1:].isdigit()))) else None
        
        # Get data - pass khatma_id if provided
        c, a, ass = db.get_status(khatma_id)
        v = db.get_v()
        avail = db.get_available(khatma_id)
        my_ass = db.get_user_assignments(uid, khatma_id) if uid else []
        
        # Get khatma-specific settings or default
        if khatma_id:
            khatma = db.get_khatma(khatma_id)
            deadline = khatma['deadline'] if khatma else None
            total = khatma['total_khatmas'] if khatma else 0
            intention = khatma['intention'] if khatma else ""
            khatma_name = khatma['name'] if khatma else "Khatma"
        else:
            deadline = db.get_setting("deadline")
            total = db.get_setting("total_khatmas")
            intention = db.get_setting("intention") or ""
            khatma_name = "ختمة عائلة العلمي"
        
        intentions = db.get_intentions()
        parts = db.get_participants_activity(khatma_id) if khatma_id else db.get_participants_activity()

        return jsonify({
            "completed_count": int(c), "active_count": int(a), "remaining_count": 60-int(c)-int(a),
            "version": v or 0, "assignments": ass, "available_hizbs": avail, "my_assignments": my_ass,
            "deadline": deadline, "total_khatmas": total or 0, "intentions": intentions,
            "participants": parts, "intention": intention, "khatma_name": khatma_name
        })
    except Exception as e:
        print(f"DEBUG: api_status failed: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/check_update")
def check_update(): return jsonify({"version": db.get_v()})


@app.route("/api/login", methods=["POST"])
def api_login():
    d = request.get_json()
    khatma_id = d.get("khatma_id")
    uid, s = db.register_web_user(d.get("name"), d.get("pin"), khatma_id)
    if s == "wrong_pin": return jsonify({"error": "الرمز السري غير صحيح"}), 403
    
    # Check if user is admin of this Khatma
    is_admin = db.is_admin(uid, khatma_id) if khatma_id else False
    
    return jsonify({"success": True, "uid": uid, "is_admin": is_admin})

@app.route("/api/intention", methods=["POST"])
def api_add_intention():
    d = request.get_json(); ur = d.get("uid"); name = d.get("name"); text = d.get("text")
    uid = int(ur) if (ur and (str(ur).isdigit() or (str(ur).startswith('-') and str(ur)[1:].isdigit()))) else None
    if not name or not text: return jsonify({"error": "Missing data"}), 400
    db.add_intention(uid, name, text)
    return jsonify({"success": True})

@app.route("/api/intention/delete", methods=["POST"])
def api_delete_intention():
    d = request.get_json(); ur = d.get("uid"); dua_id = d.get("id")
    uid = int(ur) if (ur and (str(ur).isdigit() or (str(ur).startswith('-') and str(ur)[1:].isdigit()))) else None
    if not dua_id: return jsonify({"error": "Missing ID"}), 400
    db.delete_intention(uid, dua_id)
    return jsonify({"success": True})

@app.route("/api/join", methods=["POST"])
def api_join():
    d = request.get_json()
    khatma_id = d.get("khatma_id")  # NEW
    uid, s = db.register_web_user(d.get("name"), d.get("pin"), khatma_id)
    if s == "wrong_pin": return jsonify({"error": "الرمز السري غير صحيح"}), 403
    if db.assign_hizb(uid, int(d.get("hizb")), khatma_id): return jsonify({"success": True, "uid": uid})
    return jsonify({"error": "الحزب محجوز"}), 400

@app.route("/api/done", methods=["POST"])
def api_done():
    d = request.get_json()
    ur = d.get("uid")
    khatma_id = d.get("khatma_id")  # NEW
    uid = int(ur) if (ur and (str(ur).isdigit() or (str(ur).startswith('-') and str(ur)[1:].isdigit()))) else None
    res = db.mark_done(uid, int(d.get("hizb")), khatma_id)
    if res == "completed":
        # Auto-increment total for this khatma and reset
        if khatma_id:
            khatma = db.get_khatma(khatma_id)
            if khatma:
                new_total = khatma['total_khatmas'] + 1
                with db.get_connection() as conn:
                    conn.execute("UPDATE khatmas SET total_khatmas = ? WHERE id = ?", (new_total, khatma_id))
                    # Reset assignments/completed for this khatma
                    conn.execute("DELETE FROM hizb_assignments WHERE khatma_id = ?", (khatma_id,))
                    conn.execute("DELETE FROM completed_hizb WHERE khatma_id = ?", (khatma_id,))
                    conn.commit()
                    db.bump()
        else:
            db.reset()  # Legacy bot behavior
        return jsonify({"success": True, "completed": True})
    if res: return jsonify({"success": True})
    return jsonify({"error": "فشل"}), 400

@app.route("/api/done_all", methods=["POST"])
def api_done_all():
    d = request.get_json(); ur = d.get("uid")
    uid = int(ur) if (ur and (str(ur).isdigit() or (str(ur).startswith('-') and str(ur)[1:].isdigit()))) else None
    res = db.mark_all_done(uid)
    if res == "completed":
        db.reset()
        return jsonify({"success": True, "completed": True})
    if res: return jsonify({"success": True})
    return jsonify({"error": "لا يوجد أحزاب لإتمامها"}), 400

@app.route("/api/return", methods=["POST"])
def api_return():
    d = request.get_json()
    ur = d.get("uid")
    khatma_id = d.get("khatma_id")  # NEW
    uid = int(ur) if (ur and (str(ur).isdigit() or (str(ur).startswith('-') and str(ur)[1:].isdigit()))) else None
    if db.unassign_hizb(uid, int(d.get("hizb")), khatma_id): return jsonify({"success": True})
    return jsonify({"error": "فشل"}), 400


# Only initialize bot on startup if it exists
if application:
    async def init_b(): await application.initialize(); await application.start()
    loop = asyncio.get_event_loop(); loop.run_until_complete(init_b())

if __name__ == "__main__": 
    app.run(port=5000, debug=True)
