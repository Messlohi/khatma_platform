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
# Required for PythonAnywhere free tier, irrelevant locally
PROXY_URL = "http://proxy.server:3128" if "PYTHONANYWHERE_DOMAIN" in os.environ else None

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
                is_active INTEGER DEFAULT 1,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )''')
            
            # Migration: Ensure updated_at exists
            try:
                c.execute("ALTER TABLE khatmas ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
            except: pass
            
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
            
            # --- Migrations for existing tables ---
            try:
                c.execute("ALTER TABLE users ADD COLUMN khatma_id TEXT")
            except: pass
            
            try:
                c.execute("ALTER TABLE hizb_assignments ADD COLUMN khatma_id TEXT")
            except: pass
            
            try:
                c.execute("ALTER TABLE completed_hizb ADD COLUMN khatma_id TEXT")
            except: pass
            
            try:
                c.execute("ALTER TABLE settings ADD COLUMN khatma_id TEXT")
            except: pass
            
            try:
                c.execute("ALTER TABLE intentions ADD COLUMN khatma_id TEXT")
            except: pass
            # --------------------------------------

            # Initialize Global State (for Telegram bot backward compatibility)
            c.execute("INSERT OR IGNORE INTO groups (id, title, last_update) VALUES (?, ?, ?)", (GLOBAL_GID, "Main Khatma", time.time()))
            
            conn.commit()

    def bump(self):
        with self.get_connection() as conn:
            conn.execute("UPDATE groups SET last_update = ? WHERE id = ?", (time.time(), GLOBAL_GID))
            conn.commit()

    def bump_khatma(self, khatma_id):
        if not khatma_id: return
        with self.get_connection() as conn:
            conn.execute("UPDATE khatmas SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (khatma_id,))
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
            return row and row[0] is not None and int(row[0]) == int(uid)

    def assign_hizb(self, user_id, hizb_num, khatma_id=None):
        try:
            with self.get_connection() as conn:
                conn.execute("INSERT INTO hizb_assignments (group_id, user_id, hizb_number, khatma_id) VALUES (?, ?, ?, ?)", 
                           (GLOBAL_GID, int(user_id), int(hizb_num), khatma_id))
                conn.commit(); self.bump_khatma(khatma_id); self.bump(); return True
        except Exception as e:
            print(f"DEBUG: assign_hizb failed: {e}")
            return False

    def unassign_hizb(self, user_id, hizb_num, khatma_id=None):
        with self.get_connection() as conn:
            if khatma_id:
                c = conn.execute("DELETE FROM hizb_assignments WHERE khatma_id = ? AND user_id = ? AND hizb_number = ?", 
                               (khatma_id, int(user_id), int(hizb_num)))
            else:
                c = conn.execute("DELETE FROM hizb_assignments WHERE group_id = ? AND user_id = ? AND hizb_number = ?", 
                               (GLOBAL_GID, int(user_id), int(hizb_num)))
            conn.commit()
            conn.commit()
            if c.rowcount > 0: self.bump(); self.bump_khatma(khatma_id); return True
        return False

    def mark_done(self, user_id, hizb_num, khatma_id=None):
        with self.get_connection() as conn:
            if khatma_id:
                c = conn.execute("DELETE FROM hizb_assignments WHERE khatma_id = ? AND user_id = ? AND hizb_number = ?", 
                               (khatma_id, int(user_id), int(hizb_num)))
                if c.rowcount == 0: return False
                conn.execute("INSERT INTO completed_hizb (group_id, user_id, hizb_number, khatma_id) VALUES (?, ?, ?, ?)", 
                           (GLOBAL_GID, int(user_id), int(hizb_num), khatma_id))
                conn.commit(); self.bump(); self.bump_khatma(khatma_id)
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

    def undo_completion(self, user_id, hizb_num, khatma_id=None):
        with self.get_connection() as conn:
            # Check if actually completed by this user
            if khatma_id:
                c = conn.execute("DELETE FROM completed_hizb WHERE khatma_id = ? AND user_id = ? AND hizb_number = ?", 
                               (khatma_id, int(user_id), int(hizb_num)))
            else:
                c = conn.execute("DELETE FROM completed_hizb WHERE group_id = ? AND user_id = ? AND hizb_number = ?", 
                               (GLOBAL_GID, int(user_id), int(hizb_num)))
            
            if c.rowcount > 0:
                # Move back to assignments
                if khatma_id:
                    conn.execute("INSERT INTO hizb_assignments (group_id, user_id, hizb_number, khatma_id) VALUES (?, ?, ?, ?)", 
                               (GLOBAL_GID, int(user_id), int(hizb_num), khatma_id))
                    # Also update total_khatmas if it was incremented? 
                    # Actually if we undo, we might need to decrement total if it was *just* completed?
                    # But the total increments on the *transition* to 60. 
                    # If we undo, and the count drops below 60, we don't necessarily decrement "total_khatmas" (history).
                    # We just assume the current round is now incomplete.
                    # Since mark_done increments total_khatmas and clears the board, "Undoing" the *last* hizb is tricky because the board is cleared!
                    # If the board was cleared, the user doesn't "own" the completed hizb anymore (it's gone).
                    # So "Undo" only works for hizbs that are currently in the `completed_hizb` table (i.e. the round is NOT finished yet).
                    # This is correct behavior. If round finished, it's too late to undo specific hizb (it's history).
                    self.bump_khatma(khatma_id)
                else:
                    conn.execute("INSERT INTO hizb_assignments (group_id, user_id, hizb_number) VALUES (?, ?, ?)", 
                               (GLOBAL_GID, int(user_id), int(hizb_num)))
                conn.commit(); self.bump(); return True
            return False

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
            else:
                 # Get Active for global (backward compat)
                active_rows = conn.execute(
                    "SELECT COALESCE(u.full_name, 'مشارك (تليجرام)'), ha.hizb_number FROM hizb_assignments ha LEFT JOIN users u ON ha.user_id = u.id WHERE ha.group_id = ?", 
                    (GLOBAL_GID,)
                ).fetchall()

            # Get Completed
            if khatma_id:
                comp_rows = conn.execute(
                     "SELECT COALESCE(u.full_name, 'مشارك'), ch.hizb_number FROM completed_hizb ch LEFT JOIN users u ON ch.user_id = u.id WHERE ch.khatma_id = ?", 
                     (khatma_id,)
                ).fetchall()
            else:
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


    def get_khatma_full_details(self, khatma_id):
        with self.get_connection() as conn:
            # 1. Basic Info
            k = conn.execute("SELECT id, name, admin_uid, intention, deadline, total_khatmas, created_at FROM khatmas WHERE id = ?", (khatma_id,)).fetchone()
            if not k: return None
            
            # 2. Admin Info
            admin = conn.execute("SELECT full_name, web_pin FROM users WHERE id = ?", (k[2],)).fetchone()
            admin_info = {"name": admin[0] if admin else "Unknown", "pin": admin[1] if admin else "????", "uid": k[2]}

            # 3. Users & Progress (Detailed)
            # Fetch all assignments
            assignments = conn.execute("SELECT hizb_number, user_id FROM hizb_assignments WHERE khatma_id = ?", (khatma_id,)).fetchall()
            completed = conn.execute("SELECT hizb_number, user_id FROM completed_hizb WHERE khatma_id = ?", (khatma_id,)).fetchall()
            
            # Build Hizb Map
            hizb_map = {}
            for i in range(1, 61): hizb_map[i] = {"status": "available", "user": None, "uid": None}
            
            user_hizbs = {} # uid -> {active: [], completed: []}
            
            # Users Map
            users_rows = conn.execute("SELECT id, full_name, web_pin FROM users WHERE khatma_id = ?", (khatma_id,)).fetchall()
            users_dict = {r[0]: {"name": r[1], "pin": r[2]} for r in users_rows}
            
            for h, uid in assignments:
                u = users_dict.get(uid)
                hizb_map[h] = {"status": "active", "user": u["name"] if u else "Unknown", "uid": uid}
                if uid not in user_hizbs: user_hizbs[uid] = {"active": [], "completed": []}
                user_hizbs[uid]["active"].append(h)
                
            for h, uid in completed:
                u = users_dict.get(uid)
                hizb_map[h] = {"status": "completed", "user": u["name"] if u else "Unknown", "uid": uid}
                if uid not in user_hizbs: user_hizbs[uid] = {"active": [], "completed": []}
                user_hizbs[uid]["completed"].append(h)
                
            # Format Users List
            users_list = []
            for uid, info in users_dict.items():
                uh = user_hizbs.get(uid, {"active": [], "completed": []})
                users_list.append({
                    "id": uid, "name": info["name"], "pin": info["pin"],
                    "active": sorted(uh["active"]), "completed": sorted(uh["completed"])
                })
            
            return {
                "info": {"id": k[0], "name": k[1], "intention": k[3], "deadline": k[4], "total": k[5], "created": k[6]},
                "admin": admin_info,
                "users": users_list,
                "hizb_map": hizb_map
            }

    # --- Dev Tools ---
    def get_all_khatmas(self, limit=20, offset=0, query="", min_progress=0, active_since=""):
         with self.get_connection() as conn:
            # Join with completed count for progress
            sql = """
                SELECT k.id, k.name, k.created_at, k.total_khatmas,
                       (SELECT COUNT(*) FROM completed_hizb ch WHERE ch.khatma_id = k.id) as current_completed,
                       (SELECT COUNT(*) FROM users u WHERE u.khatma_id = k.id) as user_count,
                       k.updated_at
                FROM khatmas k
                WHERE 1=1
            """
            params = []
            if query:
                sql += " AND (k.name LIKE ? OR k.id LIKE ?) "
                params.extend([f"%{query}%", f"%{query}%"])
            
            if active_since:
                 # Ensure active_since is YYYY-MM-DD format roughly
                 sql += " AND k.updated_at >= ?"
                 params.append(active_since)

            sql += " ORDER BY k.updated_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            
            rows = conn.execute(sql, params).fetchall()
            
            # Post-filter for progress (easier in python than complex SQL subquery filter sometimes, but SQL is better. 
            # Doing in python for simplicity of "current_completed / 60" logic)
            results = []
            for r in rows:
                progress = int((r[4] / 60) * 100)
                if progress >= min_progress:
                     results.append({
                        "id": r[0], "name": r[1], "created_at": r[2], "total_khatmas": r[3], 
                        "current_progress": r[4], "user_count": r[5], "updated_at": r[6]
                    })
            
            return results

    def get_global_stats(self):
        with self.get_connection() as conn:
            total_khatmas = conn.execute("SELECT COUNT(*) FROM khatmas").fetchone()[0]
            total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            total_reads = conn.execute("SELECT COUNT(*) FROM completed_hizb").fetchone()[0]
            return {"khatmas": total_khatmas, "users": total_users, "reads": total_reads}
            
    def delete_khatma(self, khatma_id):
        with self.get_connection() as conn:
            conn.execute("DELETE FROM khatmas WHERE id = ?", (khatma_id,))
            conn.execute("DELETE FROM users WHERE khatma_id = ?", (khatma_id,))
            conn.execute("DELETE FROM hizb_assignments WHERE khatma_id = ?", (khatma_id,))
            conn.execute("DELETE FROM completed_hizb WHERE khatma_id = ?", (khatma_id,))
            conn.execute("DELETE FROM settings WHERE khatma_id = ?", (khatma_id,))
            conn.execute("DELETE FROM intentions WHERE khatma_id = ?", (khatma_id,))
            conn.commit()
            return True



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
    
    def create_khatma(self, name, admin_name, admin_pin, intention="", deadline=None):
        khatma_id = self.generate_khatma_id()
        if not deadline:
            deadline = (datetime.datetime.now() + datetime.timedelta(days=7)).strftime("%Y-%m-%d %H:%M")
        
        with self.get_connection() as conn:
            admin_uid = None
            if admin_name and admin_pin:
                # Create admin user
                admin_uid = -int(time.time())
                conn.execute("INSERT INTO users (id, full_name, username, web_pin, khatma_id) VALUES (?, ?, ?, ?, ?)",
                            (admin_uid, admin_name, "web_admin", admin_pin, khatma_id))
            
            # Create khatma
            conn.execute("""INSERT INTO khatmas (id, name, admin_uid, intention, deadline, total_khatmas) 
                           VALUES (?, ?, ?, ?, ?, 0)""",
                        (khatma_id, name, admin_uid, intention, deadline))
            
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

    def update_khatma(self, khatma_id, intention=None, deadline=None, total_khatmas=None):
        with self.get_connection() as conn:
            if intention is not None:
                conn.execute("UPDATE khatmas SET intention = ? WHERE id = ?", (intention, khatma_id))
            if deadline is not None:
                conn.execute("UPDATE khatmas SET deadline = ? WHERE id = ?", (deadline, khatma_id))
            if total_khatmas is not None:
                conn.execute("UPDATE khatmas SET total_khatmas = ? WHERE id = ?", (total_khatmas, khatma_id))
            conn.commit(); self.bump(); self.bump_khatma(khatma_id)
            return True

    def update_user_pin(self, uid, new_pin, khatma_id):
        with self.get_connection() as conn:
            conn.execute("UPDATE users SET web_pin = ? WHERE id = ? AND khatma_id = ?", (new_pin, uid, khatma_id))
            conn.commit()
            return True

    def remove_user_from_khatma(self, uid, khatma_id):
        with self.get_connection() as conn:
            conn.execute("DELETE FROM users WHERE id = ? AND khatma_id = ?", (uid, khatma_id))
            conn.execute("DELETE FROM hizb_assignments WHERE user_id = ? AND khatma_id = ?", (uid, khatma_id))
            conn.execute("DELETE FROM completed_hizb WHERE user_id = ? AND khatma_id = ?", (uid, khatma_id))
            conn.commit(); self.bump(); self.bump_khatma(khatma_id)
            return True


# --- Bot Handlers ---
db = DatabaseManager(DB_FILE)
TOKEN = os.environ.get("BOT_TOKEN", "8587551117:AAHnsUgMSeqlYRMcRnu4JJkSjC3Lb8cRaGI")

# Only initialize Telegram bot if token is provided
if TOKEN:
    req_kwargs = {"read_timeout": 60, "connect_timeout": 60}
    if PROXY_URL:
        req_kwargs["proxy_url"] = PROXY_URL
    
    try:
        req_conf = HTTPXRequest(**req_kwargs)
    except TypeError:
        # Fallback: remove proxy_url if rejected (e.g., incompatible version)
        if "proxy_url" in req_kwargs:
            del req_kwargs["proxy_url"]
        req_conf = HTTPXRequest(**req_kwargs)
        print("⚠️ Warning: HTTPXRequest rejected proxy_url. Chat bot might not work on PA.")
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
    
    if not name:
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


# --- Developer Dashboard ---
DEV_ACCESS_KEY = os.environ.get("DEV_ACCESS_KEY", "dev1234")

def require_dev_auth(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get("X-Dev-Key")
        if not key or key != DEV_ACCESS_KEY:
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

@app.route(f"/{os.environ.get('DEV_ROUTE', 'developer')}")
def developer_dashboard():
    return render_template("developer.html")

@app.route("/api/dev/stats")
@require_dev_auth
def dev_stats():
    return jsonify(db.get_global_stats())

@app.route("/api/dev/khatmas")
@require_dev_auth

def dev_khatmas():
    page = int(request.args.get("page", 1))
    limit = int(request.args.get("limit", 20))
    query = request.args.get("q", "").strip()
    min_progress = int(request.args.get("min_progress", 0))
    active_since = request.args.get("active_since", "").strip()
    
    offset = (page - 1) * limit
    
    khatmas = db.get_all_khatmas(limit=limit, offset=offset, query=query, min_progress=min_progress, active_since=active_since)
    return jsonify({
        "khatmas": khatmas,
        "page": page,
        "limit": limit
    })

@app.route("/api/dev/khatma/details")
@require_dev_auth
def dev_khatma_details():
    kid = request.args.get("khatma_id")
    if not kid: return jsonify({"error": "Missing ID"}), 400
    
    details = db.get_khatma_full_details(kid)
    if not details: return jsonify({"error": "Not Found"}), 404
    
    return jsonify(details)

@app.route("/api/dev/khatma/remove_user", methods=["POST"])
@require_dev_auth
def dev_remove_user():
    d = request.get_json()
    uid = d.get("uid")
    kid = d.get("khatma_id")
    if not uid or not kid: return jsonify({"error": "Missing params"}), 400
    db.remove_user_from_khatma(uid, kid)
    return jsonify({"success": True})

@app.route("/api/dev/khatma/reset", methods=["POST"])
@require_dev_auth
def dev_reset_khatma():
    d = request.get_json()
    kid = d.get("khatma_id")
    if not kid: return jsonify({"error": "Missing ID"}), 400
    
    with db.get_connection() as conn:
        conn.execute("DELETE FROM hizb_assignments WHERE khatma_id = ?", (kid,))
        conn.execute("DELETE FROM completed_hizb WHERE khatma_id = ?", (kid,))
        conn.commit()
    db.bump(); db.bump_khatma(kid)
    return jsonify({"success": True})

@app.route("/api/dev/khatma/delete", methods=["POST"])
@require_dev_auth
def dev_delete_khatma():
    d = request.get_json()
    kid = d.get("khatma_id")
    if not kid: return jsonify({"error": "Missing ID"}), 400
    db.delete_khatma(kid)
    return jsonify({"success": True})

@app.route("/api/dev/khatmas/bulk_delete", methods=["POST"])
@require_dev_auth
def dev_bulk_delete():
    d = request.get_json()
    ids = d.get("ids", [])
    if not ids: return jsonify({"error": "No IDs"}), 400
    for kid in ids:
        db.delete_khatma(kid)
    return jsonify({"success": True})

# --- Admin API ---
# Hardcoded credentials REMOVED

@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    d = request.get_json()
    # name = d.get("name") # Only check against DB now
    # pin = d.get("pin")
    khatma_id = d.get("khatma_id")
    
    # Check against specific khatma admin
    if khatma_id:
        k = db.get_khatma(khatma_id)
        if k and k['admin_uid']:
             # We need to verify pin logic. The admin login flow typically sends name/pin.
             # Current implementation in index.html sends name/pin/khatma_id.
             # We should verify against users table for that khatma, where username='web_admin' usually?
             # Actually, create_khatma inserts with username='web_admin' and full_name=admin_name.
             
             # Let's check users table
             users = db.get_all_users(khatma_id)
             for u in users:
                 if u['id'] == k['admin_uid']:
                      if str(u.get('web_pin')) == str(d.get("pin")) and u.get('name') == d.get("name"):
                          return jsonify({"success": True, "uid": u['id'], "is_admin": True})

    return jsonify({"error": "بيانات الدخول غير صحيحة"}), 403

@app.route("/api/admin/users")
def admin_users():
    uid = request.args.get("uid")
    khatma_id = request.args.get("khatma_id")
    
    if not db.is_admin(uid, khatma_id):
        return jsonify({"error": "Unauthorized"}), 403
        
    return jsonify({"users": db.get_all_users(khatma_id)}) 

@app.route("/api/admin/user_hizbs")
def admin_user_hizbs():
    admin_uid = request.args.get("admin_uid")
    target_uid = request.args.get("uid")
    
    # We need to find the khatma_id for the target user to verify admin
    # This might be tricky if target_uid is all we have.
    # However, the admin panel knows its own context (khatma_id).
    # Let's require khatma_id param from client for security check.
    khatma_id = request.args.get("khatma_id")
    
    if not db.is_admin(admin_uid, khatma_id):
        return jsonify({"error": "Unauthorized"}), 403
        
    return jsonify({"hizbs": db.get_user_hizbs(target_uid)})

@app.route("/api/admin/control", methods=["POST"])
def admin_control():
    d = request.get_json(); action = d.get("action"); uid = d.get("uid"); hizb = d.get("hizb")
    khatma_id = d.get("khatma_id") # NEW: Support multi-tenancy
    admin_uid = d.get("admin_uid") # NEW: Security check
    
    if not db.is_admin(admin_uid, khatma_id):
        return jsonify({"error": "Unauthorized"}), 403
    
    if action == "unassign":
        if db.unassign_hizb(uid, hizb, khatma_id): return jsonify({"success": True})
    elif action == "assign":
        if db.assign_hizb(uid, hizb, khatma_id): return jsonify({"success": True})
    elif action == "assign_bulk":
        hizbs = d.get("hizbs", [])
        for h in hizbs:
            db.assign_hizb(uid, h, khatma_id)
        return jsonify({"success": True})
    elif action == "update_pin":
        pin = d.get("pin")
        if db.update_user_pin(uid, pin, khatma_id): return jsonify({"success": True})
    elif action == "complete":
        res = db.mark_done(uid, hizb, khatma_id)
        if res == "completed": 
            # Reset specifically for this Khatma? Or currently reset() is global?
            # db.reset() is GLOBAL and dangerous.
            # TODO: Implement khatma-specific reset. For now, mark done loop handles it?
            # mark_done returns 'completed' but doesn't auto-reset the DB for multi-tenant yet?
            # Actually db.reset() clears global tables.
            # For multi-tenant, we should implement a per-Khatma reset or just increment counter.
            if khatma_id:
                # Custom logic for web khatma completion: increment counter, maybe clear assignments?
                # Currently just returning success.
                pass 
            else:
                db.reset() # Global bot reset
            return jsonify({"success": True, "completed": True})
        if res: return jsonify({"success": True})
    elif action == "reset_pin":
        db.reset_user_pin(uid); return jsonify({"success": True})
    
    # --- Settings Updates (Multi-Tenant Aware) ---
    elif action == "deadline":
        if khatma_id:
            db.update_khatma(khatma_id, deadline=f"{hizb} 23:59")
        else:
            db.set_setting("deadline", f"{hizb} 23:59")
        return jsonify({"success": True})
    elif action == "update_total":
        try:
            new_total = int(hizb)
            if khatma_id:
                db.update_khatma(khatma_id, total_khatmas=new_total)
            else:
                db.set_setting("total_khatmas", str(new_total))
            return jsonify({"success": True})
        except ValueError:
            return jsonify({"error": "Invalid number"}), 400
    elif action == "update_intention":
        intention_text = hizb
        if intention_text:
            if khatma_id:
                db.update_khatma(khatma_id, intention=intention_text)
            else:
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

@app.route("/<khatma_id>")
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

@app.route("/robots.txt")
def robots():
    return Response("User-agent: *\nAllow: /", mimetype="text/plain")

@app.route("/sitemap.xml")
def sitemap():
    # Basic sitemap listing the homepage
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://khatma.pythonanywhere.com/</loc>
    <lastmod>{date}</lastmod>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>
</urlset>""".format(date=datetime.date.today().isoformat())
    return Response(xml, mimetype="application/xml")

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

@app.route("/api/undo_complete", methods=["POST"])
def api_undo_complete():
    d = request.get_json(); ur = d.get("uid")
    uid = int(ur) if (ur and (str(ur).isdigit() or (str(ur).startswith('-') and str(ur)[1:].isdigit()))) else None
    khatma_id = d.get("khatma_id")
    if db.undo_completion(uid, int(d.get("hizb")), khatma_id): return jsonify({"success": True})
    return jsonify({"error": "فشل"}), 400

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
