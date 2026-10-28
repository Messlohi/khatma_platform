import os
import re
import sqlite3
import datetime
import asyncio
import logging
import json
import time
import random
from flask import Flask, request, render_template, jsonify, Response, send_file
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
DB_FILE = os.environ.get("KHATMA_DB") or os.path.join(BASE_DIR, "khatma.db")
GLOBAL_GID = 1  # Unified Global ID for Bot and Web
TOTAL_HIZBS = 60
# Required for PythonAnywhere free tier, irrelevant locally
PROXY_URL = "http://proxy.server:3128" if "PYTHONANYWHERE_DOMAIN" in os.environ else None


def parse_name_list(raw):
    """Split a pasted list of names (newlines, commas, Arabic separators) into a list."""
    return [name for name, _per in parse_participants(raw)]


def parse_participants(raw):
    """Parse a pasted participant list, optionally with a per-person quota.

    Accepted forms (one per line or comma separated)::

        أحمد محمد
        سارة علي: 3
        عمر = 1
        خالد - 2

    Returns a list of ``(name, per_person_or_None)`` tuples.
    """
    if raw is None:
        return []
    if isinstance(raw, dict):
        items = [raw]
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        items = re.split(r"[\n\r,،;؛]+", str(raw))

    participants, seen = [], set()
    for item in items:
        per = None
        if isinstance(item, dict):
            name = str(item.get("name") or "")
            per = item.get("hizbs_per_person", item.get("per_person", item.get("hizbs")))
        else:
            text = str(item)
            # Tolerate pasted numbered/bulleted lists ("1. أحمد", "2) أحمد", "- أحمد")
            text = re.sub(
                r"^\s*(?:[-•*·]+\s*|\(?[0-9\u0660-\u0669]{1,2}[\.\)\-:]?\s*)",
                "", text,
            ).strip()
            match = re.match(r"^\s*(.+?)\s*(?:[:=]|\s-\s)\s*(\d{1,2})\s*$", text)
            if match:
                name, per = match.group(1), int(match.group(2))
            else:
                name = text
        name = " ".join(name.split())
        if not name or name in seen:
            continue
        seen.add(name)
        try:
            per = max(1, min(60, int(per))) if per is not None else None
        except (TypeError, ValueError):
            per = None
        participants.append((name, per))
    return participants


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
            conn = sqlite3.connect(self.db_file, timeout=30) # Increased timeout
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            return conn
        except Exception as e:
            print(f"Connection Error: {e}")
            return sqlite3.connect(self.db_file, timeout=30)

    def init_db(self):
        conn = self.get_connection()
        try:
            c = conn.cursor()
            
            # Migration logic moved after table creation
            
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
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                selection_type TEXT DEFAULT 'sequential'
            )''')
            
            # Migration: Add selection_type column if not exists
            try:
                c.execute("ALTER TABLE khatmas ADD COLUMN selection_type TEXT DEFAULT 'sequential'")
            except: pass

            # --- Custom Khatma / Rotation rules (Feature: custom khatmas) ---
            # mode: 'open' = free manual booking (legacy), 'rotation' = daily rotating rounds
            for col, ddl in [
                ("mode", "TEXT DEFAULT 'open'"),
                ("hizbs_per_person", "INTEGER DEFAULT 2"),
                ("rotation_step", "INTEGER DEFAULT 2"),
                ("rotation_start_date", "TEXT"),
                ("rotation_days", "INTEGER DEFAULT 30"),
                ("eod_time", "TEXT DEFAULT '23:59'"),
                ("auto_complete_eod", "INTEGER DEFAULT 1"),
                ("require_eod_confirm", "INTEGER DEFAULT 0"),
                ("current_round", "INTEGER DEFAULT 0"),
                ("last_eod_date", "TEXT"),
                ("rotation_finished", "INTEGER DEFAULT 0"),
                ("rules_json", "TEXT"),
            ]:
                try:
                    c.execute(f"ALTER TABLE khatmas ADD COLUMN {col} {ddl}")
                except: pass

            # New: Activity log table for audit trail
            c.execute('''CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                khatma_id TEXT,
                user_id INTEGER,
                user_name TEXT,
                action TEXT NOT NULL,
                hizb_number INTEGER,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                details TEXT
            )''')
            
            # Migration: Ensure selection_type exists and has valid values
            try:
                c.execute("UPDATE khatmas SET selection_type = 'sequential' WHERE selection_type IS NULL")
            except: pass
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
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (khatma_id, hizb_number)
            )""")
            c.execute('CREATE TABLE IF NOT EXISTS completed_hizb (id INTEGER PRIMARY KEY AUTOINCREMENT, group_id INTEGER, user_id INTEGER, hizb_number INTEGER, khatma_id TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)')
            c.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT, value TEXT, khatma_id TEXT, PRIMARY KEY (key, khatma_id))')
            c.execute('CREATE TABLE IF NOT EXISTS intentions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT, text TEXT, timestamp REAL, khatma_id TEXT)')

            # --- Custom Khatma members & rounds ---
            # Members are the ordered seats used by the rotation engine.
            c.execute('''CREATE TABLE IF NOT EXISTS khatma_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                khatma_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                slot INTEGER DEFAULT 0,
                hizbs_per_person INTEGER,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(khatma_id, user_id)
            )''')
            # One row per closed day/round (snapshot kept for history).
            c.execute('''CREATE TABLE IF NOT EXISTS khatma_rounds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                khatma_id TEXT NOT NULL,
                round_number INTEGER NOT NULL,
                round_date TEXT,
                status TEXT DEFAULT 'closed',
                closed_by INTEGER,
                closed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                auto_closed INTEGER DEFAULT 0,
                details TEXT,
                UNIQUE(khatma_id, round_number)
            )''')
            
            # --- Migrations for existing tables ---
            try:
                c.execute("ALTER TABLE users ADD COLUMN khatma_id TEXT")
            except: pass
            
            # RACE CONDITION FIX: Unique Index on (khatma_id, full_name)
            # This prevents two users with same name being created in parallel.
            try:
                c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_khatma_user_name ON users(khatma_id, full_name)")
            except: pass
            
            conn.commit()
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
            
            # Missing columns for activity feed
            try:
                c.execute("ALTER TABLE hizb_assignments ADD COLUMN timestamp DATETIME DEFAULT CURRENT_TIMESTAMP")
            except: pass
            try:
                c.execute("ALTER TABLE completed_hizb ADD COLUMN timestamp DATETIME DEFAULT CURRENT_TIMESTAMP")
            except: pass

            # Round tracking for rotation khatmas (ignored by open/manual khatmas)
            try:
                c.execute("ALTER TABLE hizb_assignments ADD COLUMN round_number INTEGER DEFAULT 1")
            except: pass
            try:
                c.execute("ALTER TABLE completed_hizb ADD COLUMN round_number INTEGER DEFAULT 1")
            except: pass
            # --------------------------------------

            # Migration: Ensure updated_at and last_update are numeric
            # If they are strings (like '2026-02-03...'), reset them to current time.time()
            try:
                conn.execute("UPDATE khatmas SET updated_at = ? WHERE typeof(updated_at) = 'text'", (time.time(),))
                conn.execute("UPDATE groups SET last_update = ? WHERE typeof(last_update) = 'text'", (time.time(),))
                conn.commit()
            except: pass
            
            # Initialize Global State (for Telegram bot backward compatibility)
            c.execute("INSERT OR IGNORE INTO groups (id, title, last_update) VALUES (?, ?, ?)", (GLOBAL_GID, "Main Khatma", time.time()))
            conn.commit()
            
        finally:
            conn.close()


    def bump(self):
        conn = self.get_connection()
        try:
            conn.execute("UPDATE groups SET last_update = ? WHERE id = ?", (time.time(), GLOBAL_GID))
            conn.commit()
        finally:
            conn.close()

    def bump_khatma(self, khatma_id):
        if not khatma_id: return
        conn = self.get_connection()
        try:
            conn.execute("UPDATE khatmas SET updated_at = ? WHERE id = ?", (time.time(), khatma_id))
            conn.commit()
        finally:
            conn.close()

    def log_activity(self, khatma_id, user_id, user_name, action, hizb_number=None, details=None, conn=None):
        """Log an activity for audit trail. Can reuse an existing connection/transaction."""
        try:
            if conn:
                conn.execute(
                    "INSERT INTO activity_log (khatma_id, user_id, user_name, action, hizb_number, details) VALUES (?, ?, ?, ?, ?, ?)",
                    (khatma_id, user_id, user_name, action, hizb_number, details)
                )
            else:
                c = self.get_connection()
                try:
                    c.execute(
                        "INSERT INTO activity_log (khatma_id, user_id, user_name, action, hizb_number, details) VALUES (?, ?, ?, ?, ?, ?)",
                        (khatma_id, user_id, user_name, action, hizb_number, details)
                    )
                    c.commit()
                finally:
                    c.close()
        except Exception as e:
            print(f"Error logging activity: {e}")

    def get_v(self, khatma_id=None):
        conn = self.get_connection()
        try:
            if khatma_id:
                row = conn.execute("SELECT updated_at FROM khatmas WHERE id = ?", (khatma_id,)).fetchone()
            else:
                row = conn.execute("SELECT last_update FROM groups WHERE id = ?", (GLOBAL_GID,)).fetchone()
            
            if row and row[0]:
                try: return float(row[0])
                except (ValueError, TypeError): return 0.0
            return 0.0
        except Exception:
            return 0.0
        finally:
            conn.close()

    def register_user(self, user_id, full_name, username):
        conn = self.get_connection()
        try:
            conn.execute("INSERT OR REPLACE INTO users (id, full_name, username) VALUES (?, ?, ?)", (user_id, full_name, username))
            conn.commit()
        finally:
            conn.close()

    def normalize_arabic(self, text):
        text = text or ""
        # Remove tashkeel
        tashkeel = ["\u064B", "\u064C", "\u064D", "\u064E", "\u064F", "\u0650", "\u0651", "\u0652"]
        for t in tashkeel: text = text.replace(t, "")
        
        # Unify Alef
        text = text.replace("\u0622", "\u0627") # Alif Madda
        text = text.replace("\u0623", "\u0627") # Alif Hamza Above
        text = text.replace("\u0625", "\u0627") # Alif Hamza Below
        
        # Unify Ya / Alif Maqsura (End of word usually, but simple replace works for most user typos)
        text = text.replace("\u0649", "\u064A") # Alif Maqsura -> Ya
        
        # Unify Taa Marbuta
        text = text.replace("\u0629", "\u0647") # Taa Marbuta -> Ha
        
        # Remove invisible chars (ZWSP, LRM, RLM)
        text = text.replace("\u200B", "").replace("\u200E", "").replace("\u200F", "")
        
        # Normalize spaces
        return " ".join(text.split())

    def register_web_user(self, name, pin=None, khatma_id=None):
        raw_name, pin = str(name).strip(), (str(pin).strip() if pin else "")
        norm_name = self.normalize_arabic(raw_name)

        conn = self.get_connection()
        try:
            # 1. Try exact match first (Performance)
            if khatma_id:
                query = "SELECT id, full_name, web_pin FROM users WHERE khatma_id = ?"
                rows = conn.execute(query, (khatma_id,)).fetchall()
            else:
                rows = conn.execute("SELECT id, full_name, web_pin FROM users WHERE full_name = ?", (raw_name,)).fetchall()

            # 2. Iterate and check normalized names
            matched_user = None
            for r in rows:
                uid, db_name, db_pin = r
                if self.normalize_arabic(db_name) == norm_name:
                    matched_user = r
                    break
            
            if matched_user:
                uid, db_name, dbp = matched_user
                dbp = str(dbp).strip() if dbp else ""
                if dbp == "" or dbp == pin:
                    if dbp == "" and pin != "": 
                        conn.execute("UPDATE users SET web_pin = ? WHERE id = ?", (pin, int(uid)))
                        conn.commit(); self.bump()
                    return int(uid), "success"
                return None, "wrong_pin"
            else:
                # Create new
                import random
                wid = -int(time.time() * 1000000 + random.randint(0, 999999))
                try:
                    conn.execute("INSERT INTO users (id, full_name, username, web_pin, khatma_id) VALUES (?, ?, ?, ?, ?)", 
                               (wid, raw_name, "web_user", pin if pin else None, khatma_id))
                    conn.commit(); self.bump(); return int(wid), "success"
                except sqlite3.IntegrityError:
                    # RACE CONDITION HIT or ID conflict
                    # Check if it was the name constraint
                    if khatma_id:
                        existing = conn.execute("SELECT id, web_pin FROM users WHERE full_name = ? AND khatma_id = ?", (raw_name, khatma_id)).fetchone()
                        if existing:
                            uid, dbp = existing
                            dbp = str(dbp).strip() if dbp else ""
                            # Update pin if needed
                            if dbp == "" or dbp == pin:
                                if dbp == "" and pin != "": 
                                    conn.execute("UPDATE users SET web_pin = ? WHERE id = ?", (pin, int(uid)))
                                    conn.commit(); self.bump()
                                return int(uid), "success"
                            return None, "wrong_pin"
                            
                    # If not name conflict, must be ID collision - retry with new ID
                    wid = -int(time.time() * 1000000 + random.randint(0, 999999))
                    conn.execute("INSERT INTO users (id, full_name, username, web_pin, khatma_id) VALUES (?, ?, ?, ?, ?)", 
                               (wid, raw_name, "web_user", pin if pin else None, khatma_id))
                    conn.commit(); self.bump(); return int(wid), "success"
        finally:
            conn.close()

    def is_admin(self, uid, khatma_id):
        """Check if user is admin of the specified Khatma"""
        if not uid or not khatma_id:
            return False
        conn = self.get_connection()
        try:
            row = conn.execute("SELECT admin_uid FROM khatmas WHERE id = ?", (khatma_id,)).fetchone()
            try:
                return bool(row and row[0] is not None and int(row[0]) == int(uid))
            except (TypeError, ValueError):
                return False
        finally:
            conn.close()

    def verify_admin_credentials(self, uid, khatma_id, pin):
        """Strict check: UID must be Admin AND Pin must match"""
        if not self.is_admin(uid, khatma_id):
            return False
        
        conn = self.get_connection()
        try:
            # Check user's PIN
            row = conn.execute("SELECT web_pin FROM users WHERE id = ?", (uid,)).fetchone()
            return row and str(row[0]) == str(pin)
        finally:
            conn.close()



    def unassign_hizb(self, user_id, hizb_num, khatma_id=None):
        conn = self.get_connection()
        try:
            # Get user name for logging
            user_row = conn.execute("SELECT full_name FROM users WHERE id = ?", (int(user_id),)).fetchone()
            user_name = user_row[0] if user_row else f"User {user_id}"
            
            if khatma_id:
                c = conn.execute("DELETE FROM hizb_assignments WHERE khatma_id = ? AND user_id = ? AND hizb_number = ?", 
                               (khatma_id, int(user_id), int(hizb_num)))
            else:
                c = conn.execute("DELETE FROM hizb_assignments WHERE group_id = ? AND user_id = ? AND hizb_number = ?", 
                               (GLOBAL_GID, int(user_id), int(hizb_num)))
            conn.commit()
            if c.rowcount > 0: 
                self.bump(); self.bump_khatma(khatma_id)
                # Log activity
                self.log_activity(khatma_id, int(user_id), user_name, 'returned', hizb_num)
                return True
            return False
        finally:
            conn.close()



    def undo_completion(self, user_id, hizb_num, khatma_id=None):
        conn = self.get_connection()
        try:
                # Get user name for logging
                user_row = conn.execute("SELECT full_name FROM users WHERE id = ?", (int(user_id),)).fetchone()
                user_name = user_row[0] if user_row else f"User {user_id}"
                
                # Check if actually completed by this user
                round_no = 1
                rotation = False
                if khatma_id:
                    mode_row = conn.execute(
                        "SELECT mode, current_round FROM khatmas WHERE id = ?", (khatma_id,)
                    ).fetchone()
                    if mode_row and (mode_row[0] or "open") == "rotation":
                        rotation = True
                        round_no = int(mode_row[1] or 0) + 1
                        c = conn.execute(
                            "DELETE FROM completed_hizb WHERE khatma_id = ? AND user_id = ? AND hizb_number = ? "
                            "AND COALESCE(round_number, 1) = ?",
                            (khatma_id, int(user_id), int(hizb_num), round_no))
                    else:
                        c = conn.execute("DELETE FROM completed_hizb WHERE khatma_id = ? AND user_id = ? AND hizb_number = ?", 
                                       (khatma_id, int(user_id), int(hizb_num)))
                else:
                    c = conn.execute("DELETE FROM completed_hizb WHERE group_id = ? AND user_id = ? AND hizb_number = ?", 
                                   (GLOBAL_GID, int(user_id), int(hizb_num)))
                
                if c.rowcount > 0:
                    # Move back to assignments - use OR REPLACE to be safe
                    gid = khatma_id if khatma_id else GLOBAL_GID
                    conn.execute("INSERT OR REPLACE INTO hizb_assignments (group_id, user_id, hizb_number, khatma_id, round_number) VALUES (?, ?, ?, ?, ?)", 
                               (gid, int(user_id), int(hizb_num), khatma_id, round_no))
                    conn.commit()
                    
                    self.bump()
                    if khatma_id: self.bump_khatma(khatma_id)
                    # Log activity
                    self.log_activity(khatma_id, int(user_id), user_name, 'undone', hizb_num)
                    return True
                return False
        except Exception as e:
            print(f"Error in undo_completion: {e}")
            return False
        finally:
            conn.close()

    def mark_all_done(self, user_id, khatma_id=None):
        """Mark multiple hizbs as done in a single transaction."""
        conn = self.get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE") # Lock for write
            # Get user name for logging
            user_row = conn.execute("SELECT full_name FROM users WHERE id = ?", (int(user_id),)).fetchone()
            user_name = user_row[0] if user_row else f"User {user_id}"
            
            gid = khatma_id if khatma_id else GLOBAL_GID
            round_no = 1
            rotation = False
            if khatma_id:
                mode_row = conn.execute(
                    "SELECT mode, current_round, rotation_finished FROM khatmas WHERE id = ?", (khatma_id,)
                ).fetchone()
                if mode_row and (mode_row[0] or "open") == "rotation":
                    if int(mode_row[2] or 0):
                        return []
                    rotation = True
                    round_no = int(mode_row[1] or 0) + 1

            if khatma_id:
                hizbs = [r[0] for r in conn.execute("SELECT hizb_number FROM hizb_assignments WHERE khatma_id = ? AND user_id = ?", (khatma_id, int(user_id))).fetchall()]
                if not hizbs: return []
                conn.execute("DELETE FROM hizb_assignments WHERE khatma_id = ? AND user_id = ?", (khatma_id, int(user_id)))
                # Batch Insert
                items = [(gid, int(user_id), int(h), khatma_id, round_no) for h in hizbs]
                conn.executemany("INSERT INTO completed_hizb (group_id, user_id, hizb_number, khatma_id, round_number, timestamp) VALUES (?, ?, ?, ?, ?, datetime('now'))", items)
                # Bump metadata
                conn.execute("UPDATE khatmas SET updated_at = ? WHERE id = ?", (time.time(), khatma_id))
            else:
                hizbs = [r[0] for r in conn.execute("SELECT hizb_number FROM hizb_assignments WHERE group_id = ? AND user_id = ?", (GLOBAL_GID, int(user_id))).fetchall()]
                if not hizbs: return []
                conn.execute("DELETE FROM hizb_assignments WHERE group_id = ? AND user_id = ?", (GLOBAL_GID, int(user_id)))
                # Batch Insert
                items = [(GLOBAL_GID, int(user_id), int(h)) for h in hizbs]
                conn.executemany("INSERT INTO completed_hizb (group_id, user_id, hizb_number, round_number, timestamp) VALUES (?, ?, ?, 1, datetime('now'))", items)
            
            # Global bump
            conn.execute("UPDATE groups SET last_update = ? WHERE id = ?", (time.time(), GLOBAL_GID))
            
            # Log activity (using same connection)
            for h in hizbs:
                self.log_activity(khatma_id, int(user_id), user_name, 'completed', h, f"Completed all ({len(hizbs)} hizbs)", conn=conn)
            
            conn.commit()
            
            if rotation:
                return hizbs

            # Check for completion
            if khatma_id:
                comp_count = conn.execute("SELECT COUNT(*) FROM completed_hizb WHERE khatma_id = ?", (khatma_id,)).fetchone()[0]
            else:
                comp_count = conn.execute("SELECT COUNT(*) FROM completed_hizb WHERE group_id = ?", (GLOBAL_GID,)).fetchone()[0]
            return "completed" if comp_count >= 60 else hizbs
        except Exception as e:
            import traceback; traceback.print_exc()
            print(f"ERROR: mark_all_done failed: {e}", flush=True)
            return []
        finally:
            conn.close()

    def get_available(self, khatma_id=None):
        conn = self.get_connection()
        try:
            if khatma_id:
                mode_row = conn.execute(
                    "SELECT mode, current_round, rotation_finished FROM khatmas WHERE id = ?", (khatma_id,)
                ).fetchone()
                if mode_row and (mode_row[0] or "open") == "rotation":
                    if int(mode_row[2] or 0):
                        return []
                    active_round = int(mode_row[1] or 0) + 1
                    taken = {r[0] for r in conn.execute(
                        "SELECT hizb_number FROM hizb_assignments WHERE khatma_id = ? "
                        "UNION SELECT hizb_number FROM completed_hizb WHERE khatma_id = ? "
                        "AND COALESCE(round_number, 1) = ?",
                        (khatma_id, khatma_id, active_round)).fetchall()}
                else:
                    taken = {r[0] for r in conn.execute(
                        "SELECT hizb_number FROM hizb_assignments WHERE khatma_id = ? UNION SELECT hizb_number FROM completed_hizb WHERE khatma_id = ?", 
                        (khatma_id, khatma_id)).fetchall()}
            else:
                taken = {r[0] for r in conn.execute(
                    "SELECT hizb_number FROM hizb_assignments WHERE group_id = ? UNION SELECT hizb_number FROM completed_hizb WHERE group_id = ?", 
                    (GLOBAL_GID, GLOBAL_GID)).fetchall()}
            return [h for h in range(1, 61) if h not in taken]
        finally:
            conn.close()

    def get_user_assignments(self, user_id, khatma_id=None):
        conn = self.get_connection()
        try:
            if khatma_id:
                return [r[0] for r in conn.execute("SELECT hizb_number FROM hizb_assignments WHERE khatma_id = ? AND user_id = ?", 
                                                  (khatma_id, int(user_id))).fetchall()]
            else:
                return [r[0] for r in conn.execute("SELECT hizb_number FROM hizb_assignments WHERE group_id = ? AND user_id = ?", 
                                                  (GLOBAL_GID, int(user_id))).fetchall()]
        finally:
            conn.close()

    def get_user_completions(self, user_id, khatma_id=None):
        conn = self.get_connection()
        try:
            if khatma_id:
                mode_row = conn.execute(
                    "SELECT mode, current_round FROM khatmas WHERE id = ?", (khatma_id,)
                ).fetchone()
                if mode_row and (mode_row[0] or "open") == "rotation":
                    active_round = int(mode_row[1] or 0) + 1
                    return [r[0] for r in conn.execute(
                        "SELECT hizb_number FROM completed_hizb WHERE user_id = ? AND khatma_id = ? "
                        "AND COALESCE(round_number, 1) = ?",
                        (int(user_id), khatma_id, active_round)).fetchall()]
                return [r[0] for r in conn.execute("SELECT hizb_number FROM completed_hizb WHERE user_id = ? AND khatma_id = ?", (int(user_id), khatma_id)).fetchall()]
            else:
                 return [r[0] for r in conn.execute("SELECT hizb_number FROM completed_hizb WHERE user_id = ? AND group_id = ?", (int(user_id), GLOBAL_GID)).fetchall()]
        finally:
            conn.close()

    def get_status(self, khatma_id=None):
        conn = self.get_connection()
        try:
            if khatma_id:
                mode_row = conn.execute(
                    "SELECT mode, current_round, rotation_finished FROM khatmas WHERE id = ?", (khatma_id,)
                ).fetchone()
                if mode_row and (mode_row[0] or "open") == "rotation":
                    active_round = int(mode_row[1] or 0) + 1
                    if int(mode_row[2] or 0):
                        c = TOTAL_HIZBS
                    else:
                        c = conn.execute(
                            "SELECT COUNT(*) FROM completed_hizb WHERE khatma_id = ? AND COALESCE(round_number, 1) = ?",
                            (khatma_id, active_round)).fetchone()[0]
                else:
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
        finally:
            conn.close()

    def get_participants_activity(self, khatma_id=None):
        conn = self.get_connection()
        try:
            # Filter by khatma_id if provided, otherwise use GLOBAL_GID for backward compatibility
            if khatma_id:
                # Get Active for this Khatma
                active_rows = conn.execute(
                    "SELECT COALESCE(u.full_name, 'مشارك'), ha.hizb_number, u.id FROM hizb_assignments ha LEFT JOIN users u ON ha.user_id = u.id WHERE ha.khatma_id = ?", 
                    (khatma_id,)
                ).fetchall()
            else:
                 # Get Active for global (backward compat)
                active_rows = conn.execute(
                    "SELECT COALESCE(u.full_name, 'مشارك (تليجرام)'), ha.hizb_number, u.id FROM hizb_assignments ha LEFT JOIN users u ON ha.user_id = u.id WHERE ha.group_id = ?", 
                    (GLOBAL_GID,)
                ).fetchall()

            # Get Completed (rotation khatmas only show the active round)
            if khatma_id:
                mode_row = conn.execute(
                    "SELECT mode, current_round FROM khatmas WHERE id = ?", (khatma_id,)
                ).fetchone()
                if mode_row and (mode_row[0] or "open") == "rotation":
                    active_round = int(mode_row[1] or 0) + 1
                    comp_rows = conn.execute(
                         "SELECT COALESCE(u.full_name, 'مشارك'), ch.hizb_number, u.id FROM completed_hizb ch "
                         "LEFT JOIN users u ON ch.user_id = u.id WHERE ch.khatma_id = ? AND COALESCE(ch.round_number, 1) = ?", 
                         (khatma_id, active_round)
                    ).fetchall()
                else:
                    comp_rows = conn.execute(
                         "SELECT COALESCE(u.full_name, 'مشارك'), ch.hizb_number, u.id FROM completed_hizb ch LEFT JOIN users u ON ch.user_id = u.id WHERE ch.khatma_id = ?", 
                         (khatma_id,)
                    ).fetchall()
            else:
                 comp_rows = conn.execute(
                     "SELECT COALESCE(u.full_name, 'مشارك (تليجرام)'), ch.hizb_number, u.id FROM completed_hizb ch LEFT JOIN users u ON ch.user_id = u.id WHERE ch.group_id = ?", 
                     (GLOBAL_GID,)
                ).fetchall()
            
            data = {}
            for name, hizb, uid in active_rows:
                if name not in data: data[name] = {"active": [], "completed": [], "id": uid}
                data[name]["active"].append(hizb)
                if uid and not data[name].get("id"): data[name]["id"] = uid # Ensure ID is captured
                
            for name, hizb, uid in comp_rows:
                if name not in data: data[name] = {"active": [], "completed": []}
                data[name]["completed"].append(hizb)
                if uid and not data[name].get("id"): data[name]["id"] = uid
                
            # Convert to list
            return [{"name": k, "active": sorted(v["active"]), "completed": sorted(v["completed"]), "id": v.get("id")} for k, v in data.items()]
        finally:
            conn.close()


    def get_khatma_full_details(self, khatma_id):
        conn = self.get_connection()
        try:
            # 1. Basic Info
            k = conn.execute("SELECT id, name, admin_uid, intention, deadline, total_khatmas, created_at FROM khatmas WHERE id = ?", (khatma_id,)).fetchone()
            if not k: return None
            
            # 2. Admin Info
            admin = conn.execute("SELECT full_name, web_pin FROM users WHERE id = ?", (k[2],)).fetchone()
            admin_info = {"name": admin[0] if admin else "Unknown", "pin": admin[1] if admin else "????", "uid": k[2]}

            # 3. Users & Progress (Detailed)
            # Fetch all assignments
            assignments = conn.execute("SELECT hizb_number, user_id FROM hizb_assignments WHERE khatma_id = ?", (khatma_id,)).fetchall()
            # Rotation khatmas only display the current day's completions
            mode_row = conn.execute(
                "SELECT mode, current_round, rotation_finished FROM khatmas WHERE id = ?", (khatma_id,)
            ).fetchone()
            rotation = bool(mode_row and (mode_row[0] or "open") == "rotation")
            if rotation:
                active_round = int(mode_row[1] or 0) + 1
                completed = conn.execute(
                    "SELECT hizb_number, user_id FROM completed_hizb WHERE khatma_id = ? AND COALESCE(round_number, 1) = ?",
                    (khatma_id, active_round)).fetchall()
            else:
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
        finally:
            conn.close()

    # --- Dev Tools ---
    def get_all_khatmas(self, limit=20, offset=0, query="", min_progress=0, active_since=""):
        conn = self.get_connection()
        try:
            # Join with completed count for progress
            sql = """
                SELECT k.id, k.name, k.created_at, k.total_khatmas,
                       (SELECT COUNT(DISTINCT ch.hizb_number) FROM completed_hizb ch WHERE ch.khatma_id = k.id) as current_completed,
                       (SELECT COUNT(*) FROM users u WHERE u.khatma_id = k.id) as user_count,
                       k.updated_at
                FROM khatmas k
                WHERE 1=1
            """
            params = []
            if query:
                sql += " AND (k.name LIKE ? OR k.id LIKE ?) "
                params.extend([f"%{query}%", f"%{query}%"])
            
            sql += " ORDER BY k.updated_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            
            rows = conn.execute(sql, params).fetchall()
            
            results = []
            for r in rows:
                progress = int((r[4] / 60) * 100)
                
                # Format timestamps for frontend (YYYY-MM-DD HH:MM:SS)
                created_ts = r[2]
                updated_ts = r[6]
                
                try:
                    if isinstance(updated_ts, (int, float)):
                        updated_ts = datetime.datetime.fromtimestamp(updated_ts).strftime('%Y-%m-%d %H:%M:%S')
                except: pass
                
                if progress >= min_progress:
                     results.append({
                        "id": r[0], "name": r[1], "created_at": str(created_ts), "total_khatmas": r[3], 
                        "current_progress": r[4], "user_count": r[5], "updated_at": str(updated_ts)
                    })
            
            return results
        finally:
            conn.close()

    def get_global_stats(self):
        conn = self.get_connection()
        try:
            total_khatmas = conn.execute("SELECT COUNT(*) FROM khatmas").fetchone()[0]
            total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            total_reads = conn.execute("SELECT COUNT(*) FROM completed_hizb").fetchone()[0]
            return {"khatmas": total_khatmas, "users": total_users, "reads": total_reads}
        finally:
            conn.close()
            
    def delete_khatma(self, khatma_id):
        conn = self.get_connection()
        try:
            conn.execute("DELETE FROM khatmas WHERE id = ?", (khatma_id,))
            conn.execute("DELETE FROM users WHERE khatma_id = ?", (khatma_id,))
            conn.execute("DELETE FROM hizb_assignments WHERE khatma_id = ?", (khatma_id,))
            conn.execute("DELETE FROM completed_hizb WHERE khatma_id = ?", (khatma_id,))
            conn.execute("DELETE FROM settings WHERE khatma_id = ?", (khatma_id,))
            conn.execute("DELETE FROM intentions WHERE khatma_id = ?", (khatma_id,))
            conn.execute("DELETE FROM khatma_members WHERE khatma_id = ?", (khatma_id,))
            conn.execute("DELETE FROM khatma_rounds WHERE khatma_id = ?", (khatma_id,))
            conn.commit()
            return True
        finally:
            conn.close()



    def get_all_users(self, khatma_id=None):
        conn = self.get_connection()
        try:
            # Get users with counts of active and completed hizbs
            # IMPORTANT: Filter by khatma_id if provided
            if khatma_id:
                mode_row = conn.execute(
                    "SELECT mode, current_round FROM khatmas WHERE id = ?", (khatma_id,)
                ).fetchone()
                if mode_row and (mode_row[0] or "open") == "rotation":
                    active_round = int(mode_row[1] or 0) + 1
                    query = """
                        SELECT u.id, u.full_name, u.web_pin,
                               (SELECT COUNT(*) FROM hizb_assignments ha WHERE ha.user_id = u.id AND ha.khatma_id = ?) as active_count,
                               (SELECT COUNT(*) FROM completed_hizb ch WHERE ch.user_id = u.id AND ch.khatma_id = ?
                                AND COALESCE(ch.round_number, 1) = ?) as completed_count
                        FROM users u
                        WHERE u.khatma_id = ?
                    """
                    rows = conn.execute(query, (khatma_id, khatma_id, active_round, khatma_id)).fetchall()
                else:
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
        finally:
            conn.close()

    def reset_user_pin(self, user_id):
        conn = self.get_connection()
        try:
            kid = conn.execute("SELECT khatma_id FROM users WHERE id = ?", (int(user_id),)).fetchone()
            conn.execute("UPDATE users SET web_pin = NULL WHERE id = ?", (int(user_id),))
            conn.commit(); self.bump()
            if kid and kid[0]: self.bump_khatma(kid[0])
        finally:
            conn.close()

    def get_user_name(self, user_id):
        conn = self.get_connection()
        try:
            r = conn.execute("SELECT full_name FROM users WHERE id = ?", (int(user_id),)).fetchone()
            return r[0] if r else None
        finally:
            conn.close()
    
    def update_user_profile(self, user_id, new_name, new_pin=None):
        conn = self.get_connection()
        try:
            kid = conn.execute("SELECT khatma_id FROM users WHERE id = ?", (int(user_id),)).fetchone()
            if new_pin is not None:
                conn.execute("UPDATE users SET full_name = ?, web_pin = ? WHERE id = ?", (new_name, new_pin, int(user_id)))
            else:
                conn.execute("UPDATE users SET full_name = ? WHERE id = ?", (new_name, int(user_id)))
            conn.commit(); self.bump()
            if kid and kid[0]: self.bump_khatma(kid[0])
        finally:
            conn.close()

    def get_user_hizbs(self, user_id):
        conn = self.get_connection()
        try:
            return [r[0] for r in conn.execute("SELECT hizb_number FROM hizb_assignments WHERE user_id = ?", (int(user_id),)).fetchall()]
        finally:
            conn.close()

    def increment_total_completions(self):
        conn = self.get_connection()
        try:
            curr = conn.execute("SELECT value FROM settings WHERE key = 'total_khatmas'").fetchone()
            new_val = int(curr[0] or 0) + 1
            conn.execute("UPDATE settings SET value = ? WHERE key = 'total_khatmas'", (str(new_val),))
            conn.commit(); self.bump()
        finally:
            conn.close()
            

    def add_intention(self, uid, name, text, khatma_id=None):
        conn = self.get_connection()
        try:
            conn.execute("INSERT INTO intentions (user_id, name, text, timestamp, khatma_id) VALUES (?, ?, ?, ?, ?)", 
                         (uid, name, text, time.time(), khatma_id))
            conn.commit(); self.bump()
            if khatma_id: self.bump_khatma(khatma_id)
        finally:
            conn.close()

    def delete_intention(self, uid, dua_id, khatma_id=None):
        # We delete by ID andUID for security
        conn = self.get_connection()
        try:
            conn.execute("DELETE FROM intentions WHERE user_id = ? AND id = ?", (uid, int(dua_id)))
            conn.commit(); self.bump()
            if khatma_id: self.bump_khatma(khatma_id)
        finally:
            conn.close()

    def get_recent_activity(self, khatma_id=None, limit=5, offset=0):
        conn = self.get_connection()
        try:
            # Union of joined and completed events
            sql = """
                SELECT 'joined' as type, u.full_name, ha.hizb_number, ha.timestamp
                FROM hizb_assignments ha
                JOIN users u ON ha.user_id = u.id
                WHERE ha.khatma_id = ?
                UNION ALL
                SELECT 'completed' as type, u.full_name, ch.hizb_number, ch.timestamp
                FROM completed_hizb ch
                JOIN users u ON ch.user_id = u.id
                WHERE ch.khatma_id = ?
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?
            """
            params = (khatma_id, khatma_id, limit, offset)
            rows = conn.execute(sql, params).fetchall()
            return [{"type": r[0], "name": r[1], "hizb": r[2], "timestamp": r[3], "time": r[3]} for r in rows]
        except Exception as e:
            # If timestamp columns don't exist yet, return empty activity
            print(f"WARNING: get_recent_activity failed (likely missing columns): {e}")
            return []
        finally:
            conn.close()

    def assign_hizb(self, user_id, hizb, khatma_id=None):
        conn = self.get_connection()
        try:
            # Get user name for logging
            user_row = conn.execute("SELECT full_name FROM users WHERE id = ?", (int(user_id),)).fetchone()
            user_name = user_row[0] if user_row else f"User {user_id}"
            
            gid = khatma_id if khatma_id else GLOBAL_GID
            round_no = 1
            if khatma_id:
                mode_row = conn.execute(
                    "SELECT mode, current_round, rotation_finished FROM khatmas WHERE id = ?", (khatma_id,)
                ).fetchone()
                if mode_row and (mode_row[0] or "open") == "rotation":
                    if int(mode_row[2] or 0):
                        return False
                    round_no = int(mode_row[1] or 0) + 1
            
            # Check availability
            row = conn.execute("SELECT user_id FROM hizb_assignments WHERE hizb_number = ? AND (khatma_id = ? OR group_id = ?)", (hizb, khatma_id, gid)).fetchone()
            if row: return False
            
            conn.execute("INSERT INTO hizb_assignments (group_id, user_id, hizb_number, khatma_id, round_number, timestamp) VALUES (?, ?, ?, ?, ?, datetime('now'))", 
                       (gid, int(user_id), int(hizb), khatma_id, round_no))
            conn.commit(); self.bump()
            if khatma_id: self.bump_khatma(khatma_id)
            
            # Log activity
            self.log_activity(khatma_id, int(user_id), user_name, 'reserved', hizb)
            return True
        except Exception as e:
            print(f"DEBUG: assign_hizb failed: {e}")
            return False
        finally:
            conn.close()

    def assign_hizb_batch(self, user_id, hizbs, khatma_id=None):
        """Assign multiple hizbs in a single exclusive transaction."""
        conn = self.get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            # Get user name for logging
            user_row = conn.execute("SELECT full_name FROM users WHERE id = ?", (int(user_id),)).fetchone()
            user_name = user_row[0] if user_row else f"User {user_id}"
            
            gid = khatma_id if khatma_id else GLOBAL_GID
            round_no = 1
            if khatma_id:
                mode_row = conn.execute(
                    "SELECT mode, current_round, rotation_finished FROM khatmas WHERE id = ?", (khatma_id,)
                ).fetchone()
                if mode_row and (mode_row[0] or "open") == "rotation":
                    if int(mode_row[2] or 0):
                        return []
                    round_no = int(mode_row[1] or 0) + 1
            booked = []
            
            for hizb in hizbs:
                # Check availability
                row = conn.execute("SELECT user_id FROM hizb_assignments WHERE hizb_number = ? AND khatma_id = ?", (hizb, khatma_id)).fetchone()
                if not row:
                    conn.execute("INSERT INTO hizb_assignments (group_id, user_id, hizb_number, khatma_id, round_number, timestamp) VALUES (?, ?, ?, ?, ?, datetime('now'))", 
                               (gid, int(user_id), int(hizb), khatma_id, round_no))
                    booked.append(int(hizb))
                    # Log activity (using same connection)
                    self.log_activity(khatma_id, int(user_id), user_name, 'reserved', hizb, conn=conn)
            
            # Bump metadata
            conn.execute("UPDATE groups SET last_update = ? WHERE id = ?", (time.time(), GLOBAL_GID))
            if khatma_id:
                conn.execute("UPDATE khatmas SET updated_at = ? WHERE id = ?", (time.time(), khatma_id))
            
            conn.commit()
            return booked
        except Exception as e:
            print(f"DEBUG: assign_hizb_batch failed: {e}")
            return []
        finally:
            conn.close()

    def mark_done(self, user_id, hizb, khatma_id=None):
        conn = self.get_connection()
        try:
            # Get user name for logging
            user_row = conn.execute("SELECT full_name FROM users WHERE id = ?", (int(user_id),)).fetchone()
            user_name = user_row[0] if user_row else f"User {user_id}"
            
            gid = khatma_id if khatma_id else GLOBAL_GID
            round_no = 1
            rotation = False
            if khatma_id:
                mode_row = conn.execute(
                    "SELECT mode, current_round, rotation_finished FROM khatmas WHERE id = ?", (khatma_id,)
                ).fetchone()
                if mode_row and (mode_row[0] or "open") == "rotation":
                    if int(mode_row[2] or 0):
                        return False
                    rotation = True
                    round_no = int(mode_row[1] or 0) + 1
            
            # Verify the assignment belongs to this user before completing it.
            if khatma_id:
                c = conn.execute(
                    "DELETE FROM hizb_assignments WHERE hizb_number = ? AND khatma_id = ? AND user_id = ?",
                    (hizb, khatma_id, int(user_id))
                )
            else:
                c = conn.execute(
                    "DELETE FROM hizb_assignments WHERE hizb_number = ? AND group_id = ? AND user_id = ?",
                    (hizb, GLOBAL_GID, int(user_id))
                )
            if c.rowcount == 0:
                return False
            
            conn.execute("INSERT INTO completed_hizb (group_id, user_id, hizb_number, khatma_id, round_number, timestamp) VALUES (?, ?, ?, ?, ?, datetime('now'))", 
                       (gid, int(user_id), int(hizb), khatma_id, round_no))
            
            # Bump metdata
            conn.execute("UPDATE groups SET last_update = ? WHERE id = ?", (time.time(), GLOBAL_GID))
            if khatma_id:
                conn.execute("UPDATE khatmas SET updated_at = ? WHERE id = ?", (time.time(), khatma_id))
            
            conn.commit()
            
            # Log activity
            self.log_activity(khatma_id, int(user_id), user_name, 'completed', hizb)
            
            # Rotation rounds are closed by the EOD engine, never mid-day.
            if rotation:
                return True

            # Check for khatma completion
            if khatma_id:
                count = conn.execute("SELECT COUNT(*) FROM completed_hizb WHERE khatma_id = ?", (khatma_id,)).fetchone()[0]
            else:
                count = conn.execute("SELECT COUNT(*) FROM completed_hizb WHERE group_id = ?", (GLOBAL_GID,)).fetchone()[0]
            
            return "completed" if count >= 60 else True
        finally:
            conn.close()

    def get_intentions(self, khatma_id=None):
        conn = self.get_connection()
        try:
            if khatma_id:
                rows = conn.execute("SELECT id, name, text, user_id FROM intentions WHERE khatma_id = ? ORDER BY id DESC LIMIT 50", (khatma_id,)).fetchall()
            else:
                rows = conn.execute("SELECT id, name, text, user_id FROM intentions WHERE khatma_id IS NULL ORDER BY id DESC LIMIT 50").fetchall()
            return [{"id": r[0], "name": r[1], "text": r[2], "uid": r[3]} for r in rows]
        finally:
            conn.close()

    def reset(self, khatma_id=None, group_id=None):
        """Reset a Khatma or Group assignments in a single transaction."""
        conn = self.get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if khatma_id:
                # Localized reset
                conn.execute("UPDATE khatmas SET total_khatmas = total_khatmas + 1 WHERE id = ?", (khatma_id,))
                conn.execute("UPDATE khatmas SET deadline = '' WHERE id = ?", (khatma_id,))
                conn.execute("DELETE FROM hizb_assignments WHERE khatma_id = ? OR group_id = ?", (khatma_id, khatma_id))
                conn.execute("DELETE FROM completed_hizb WHERE khatma_id = ? OR group_id = ?", (khatma_id, khatma_id))
                # If this is a rotation khatma, restart the rotation from round 1.
                mode_row = conn.execute("SELECT mode FROM khatmas WHERE id = ?", (khatma_id,)).fetchone()
                if mode_row and (mode_row[0] or "open") == "rotation":
                    conn.execute("DELETE FROM khatma_rounds WHERE khatma_id = ?", (khatma_id,))
                    conn.execute(
                        "UPDATE khatmas SET current_round = 0, last_eod_date = NULL, rotation_finished = 0, "
                        "rotation_start_date = ? WHERE id = ?",
                        (datetime.date.today().isoformat(), khatma_id)
                    )
                # Bump
                conn.execute("UPDATE khatmas SET updated_at = ? WHERE id = ?", (time.time(), khatma_id))
            else:
                target_gid = group_id if group_id else GLOBAL_GID
                self.increment_total_completions() # Increment count on reset
                conn.execute("DELETE FROM hizb_assignments WHERE group_id = ?", (target_gid,))
                conn.execute("DELETE FROM completed_hizb WHERE group_id = ?", (target_gid,))
                if target_gid == GLOBAL_GID:
                    conn.execute("DELETE FROM users WHERE khatma_id IS NULL") # Only clear global users
                    conn.execute("DELETE FROM intentions WHERE khatma_id IS NULL") 
                    # Reset deadline to 7 days from now
                    new_deadline = (datetime.datetime.now() + datetime.timedelta(days=7)).strftime("%Y-%m-%d %H:%M")
                    conn.execute("UPDATE settings SET value = ? WHERE key = 'deadline'", (new_deadline,))
            
            conn.execute("UPDATE groups SET last_update = ? WHERE id = ?", (time.time(), GLOBAL_GID))
            conn.commit()
        finally:
            conn.close()

        # Restart the rotation with a fresh round 1 (outside the transaction
        # because assign_rotation_round opens its own connection).
        if khatma_id and self.is_rotation_khatma(khatma_id):
            self.assign_rotation_round(khatma_id, 1)

    def get_setting(self, key):
        conn = self.get_connection()
        try:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def set_setting(self, key, value):
        conn = self.get_connection()
        try:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
            conn.commit(); self.bump()
        finally:
            conn.close()
    
    # --- Multi-Tenant Khatma Functions ---
    def generate_khatma_id(self):
        import random, string
        while True:
            kid = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
            conn = self.get_connection()
            try:
                exists = conn.execute("SELECT 1 FROM khatmas WHERE id = ?", (kid,)).fetchone()
                if not exists: return kid
            finally:
                conn.close()
    
    def create_khatma(self, name, admin_id, admin_name, admin_pin, intention="", deadline="", rules=None):
        rules = rules or {}
        conn = self.get_connection()
        try:
            kid = self.generate_khatma_id()
            # Register admin for this khatma
            conn.execute("INSERT INTO users (id, full_name, web_pin, khatma_id) VALUES (?, ?, ?, ?)", (admin_id, admin_name, admin_pin, kid))
            # Create khatma (custom rotation rules are optional)
            mode = rules.get("mode") if rules.get("mode") in ("open", "rotation") else "open"
            try:
                per_person = max(1, min(60, int(rules.get("hizbs_per_person") or 2)))
                rotation_step = max(1, min(60, int(rules.get("rotation_step") or per_person)))
                rotation_days = max(1, min(365, int(rules.get("rotation_days") or 30)))
            except (ValueError, TypeError):
                per_person, rotation_step, rotation_days = 2, 2, 30
            start_date = rules.get("rotation_start_date")
            if start_date:
                parsed = self._parse_dt(start_date)
                start_date = parsed.date().isoformat() if parsed else datetime.date.today().isoformat()
            eod_time = rules.get("eod_time") or "23:59"
            parsed_eod = self._parse_dt(eod_time, "%H:%M")
            eod_time = parsed_eod.strftime("%H:%M") if parsed_eod else "23:59"
            auto_complete = 1 if str(rules.get("auto_complete_eod", 1)).lower() in ("1", "true", "yes", "on") else 0
            require_confirm = 1 if str(rules.get("require_eod_confirm", 0)).lower() in ("1", "true", "yes", "on") else 0

            conn.execute(
                """INSERT INTO khatmas (id, name, admin_uid, intention, deadline, total_khatmas,
                                        mode, hizbs_per_person, rotation_step, rotation_start_date,
                                        rotation_days, eod_time, auto_complete_eod, require_eod_confirm)
                   VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (kid, name, admin_id, intention, deadline, mode, per_person, rotation_step,
                 start_date, rotation_days, eod_time, auto_complete, require_confirm))
            
            conn.commit()
            self.bump()
            return kid, admin_id
        finally:
            conn.close()
    
    def get_khatma(self, khatma_id):
        conn = self.get_connection()
        try:
            row = conn.execute(
                "SELECT id, name, admin_uid, intention, deadline, total_khatmas, selection_type, "
                "mode, hizbs_per_person, rotation_step, rotation_start_date, rotation_days, eod_time, "
                "auto_complete_eod, require_eod_confirm, current_round, rotation_finished "
                "FROM khatmas WHERE id = ?", (khatma_id,)
            ).fetchone()
            if row:
                return {
                    "id": row[0], "name": row[1], "admin_uid": row[2], "intention": row[3],
                    "deadline": row[4], "total_khatmas": row[5], "selection_type": row[6],
                    "mode": row[7] or "open", "hizbs_per_person": row[8],
                    "rotation_step": row[9], "rotation_start_date": row[10],
                    "rotation_days": row[11], "eod_time": row[12],
                    "auto_complete_eod": row[13], "require_eod_confirm": row[14],
                    "current_round": row[15], "rotation_finished": row[16],
                }
            return None
        finally:
            conn.close()
    
    def get_khatma_selection_type(self, khatma_id):
        """Get the selection type for a khatma (sequential, range, sahm, manual)"""
        conn = self.get_connection()
        try:
            row = conn.execute("SELECT selection_type FROM khatmas WHERE id = ?", (khatma_id,)).fetchone()
            return row[0] if row else 'sequential'
        finally:
            conn.close()

    def update_khatma(self, khatma_id, **kwargs):
        conn = self.get_connection()
        try:
            if 'intention' in kwargs:
                conn.execute("UPDATE khatmas SET intention = ? WHERE id = ?", (kwargs['intention'], khatma_id))
            if 'deadline' in kwargs:
                conn.execute("UPDATE khatmas SET deadline = ? WHERE id = ?", (kwargs['deadline'], khatma_id))
            if 'total_khatmas' in kwargs:
                conn.execute("UPDATE khatmas SET total_khatmas = ? WHERE id = ?", (kwargs['total_khatmas'], khatma_id))
            if 'selection_type' in kwargs:
                conn.execute("UPDATE khatmas SET selection_type = ? WHERE id = ?", (kwargs['selection_type'], khatma_id))
            conn.commit(); self.bump(); self.bump_khatma(khatma_id)
            return True
        finally:
            conn.close()

    def get_activity_logs(self, khatma_id, limit=100):
        """Get activity logs for a khatma, ordered by most recent first."""
        conn = self.get_connection()
        try:
            rows = conn.execute(
                "SELECT id, user_id, user_name, action, hizb_number, timestamp, details FROM activity_log WHERE khatma_id = ? ORDER BY timestamp DESC LIMIT ?",
                (khatma_id, limit)
            ).fetchall()
            return [
                {
                    "id": r[0], "user_id": r[1], "user_name": r[2], "action": r[3],
                    "hizb_number": r[4], "timestamp": r[5], "details": r[6]
                }
                for r in rows
            ]
        finally:
            conn.close()

    def get_all_activity_logs(self, limit=100):
        """Get all activity logs, ordered by most recent first."""
        conn = self.get_connection()
        try:
            rows = conn.execute(
                "SELECT id, khatma_id, user_id, user_name, action, hizb_number, timestamp, details FROM activity_log ORDER BY timestamp DESC LIMIT ?",
                (limit,)
            ).fetchall()
            return [
                {
                    "id": r[0], "khatma_id": r[1], "user_id": r[2], "user_name": r[3], "action": r[4],
                    "hizb_number": r[5], "timestamp": r[6], "details": r[7]
                }
                for r in rows
            ]
        finally:
            conn.close()

    def update_user_pin(self, uid, new_pin, khatma_id):
        conn = self.get_connection()
        try:
            conn.execute("UPDATE users SET web_pin = ? WHERE id = ? AND khatma_id = ?", (new_pin, uid, khatma_id))
            conn.commit()
            return True
        finally:
            conn.close()

    def remove_user_from_khatma(self, uid, khatma_id):
        conn = self.get_connection()
        try:
            conn.execute("DELETE FROM users WHERE id = ? AND khatma_id = ?", (uid, khatma_id))
            conn.execute("DELETE FROM hizb_assignments WHERE user_id = ? AND khatma_id = ?", (uid, khatma_id))
            conn.execute("DELETE FROM completed_hizb WHERE user_id = ? AND khatma_id = ?", (uid, khatma_id))
            conn.execute("DELETE FROM khatma_members WHERE user_id = ? AND khatma_id = ?", (uid, khatma_id))
            conn.commit(); self.bump(); self.bump_khatma(khatma_id)
            return True
        finally:
            conn.close()

    # ==================================================================
    # Custom Khatma rules, rotation engine & assignment manager
    # ==================================================================
    ROTATION_DEFAULTS = {
        "mode": "open",
        "hizbs_per_person": 2,
        "rotation_step": 2,
        "rotation_start_date": None,
        "rotation_days": 30,
        "eod_time": "23:59",
        "auto_complete_eod": 1,
        "require_eod_confirm": 0,
        "current_round": 0,
        "last_eod_date": None,
        "rotation_finished": 0,
    }

    def _parse_dt(self, value, fmt=None):
        """Small tolerant datetime parser used by the rotation engine."""
        if value in (None, ""):
            return None
        value = str(value).strip()
        formats = [fmt] if fmt else ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"]
        for f in formats:
            try:
                return datetime.datetime.strptime(value, f)
            except (ValueError, TypeError):
                continue
        return None

    def get_khatma_rules(self, khatma_id):
        """Return the custom rules for a khatma, with safe defaults."""
        if not khatma_id:
            return dict(self.ROTATION_DEFAULTS)
        conn = self.get_connection()
        try:
            row = conn.execute(
                "SELECT mode, hizbs_per_person, rotation_step, rotation_start_date, rotation_days, "
                "eod_time, auto_complete_eod, require_eod_confirm, current_round, last_eod_date, rotation_finished "
                "FROM khatmas WHERE id = ?", (khatma_id,)
            ).fetchone()
        finally:
            conn.close()

        if not row:
            return dict(self.ROTATION_DEFAULTS)

        keys = ["mode", "hizbs_per_person", "rotation_step", "rotation_start_date",
                "rotation_days", "eod_time", "auto_complete_eod", "require_eod_confirm",
                "current_round", "last_eod_date", "rotation_finished"]
        rules = dict(zip(keys, row))
        rules["mode"] = rules.get("mode") or "open"
        rules["hizbs_per_person"] = int(rules.get("hizbs_per_person") or 2)
        rules["rotation_step"] = int(rules.get("rotation_step") or rules["hizbs_per_person"])
        rules["rotation_days"] = int(rules.get("rotation_days") or 30)
        rules["eod_time"] = rules.get("eod_time") or "23:59"
        rules["auto_complete_eod"] = int(rules.get("auto_complete_eod") if rules.get("auto_complete_eod") is not None else 1)
        rules["require_eod_confirm"] = int(rules.get("require_eod_confirm") or 0)
        rules["current_round"] = int(rules.get("current_round") or 0)
        rules["rotation_finished"] = int(rules.get("rotation_finished") or 0)
        return rules

    def update_khatma_rules(self, khatma_id, **kwargs):
        """Persist a subset of custom khatma rules. Values are validated/clamped."""
        allowed = {
            "mode", "hizbs_per_person", "rotation_step", "rotation_start_date",
            "rotation_days", "eod_time", "auto_complete_eod", "require_eod_confirm",
            "current_round", "last_eod_date", "rotation_finished",
        }
        clean = {}
        for key, value in kwargs.items():
            if key not in allowed or value is None:
                continue
            if key in ("hizbs_per_person", "rotation_step", "rotation_days"):
                try:
                    value = max(1, min(365, int(value)))
                except (ValueError, TypeError):
                    continue
            if key in ("auto_complete_eod", "require_eod_confirm", "rotation_finished"):
                value = 1 if str(value).lower() in ("1", "true", "yes", "on") else 0
            if key == "mode" and value not in ("open", "rotation"):
                continue
            if key == "eod_time":
                parsed = self._parse_dt(value, "%H:%M")
                if not parsed:
                    continue
                value = parsed.strftime("%H:%M")
            if key == "rotation_start_date" and value:
                parsed = self._parse_dt(value)
                value = parsed.date().isoformat() if parsed else None
            clean[key] = value

        if not clean:
            return False

        conn = self.get_connection()
        try:
            sets = ", ".join(f"{key} = ?" for key in clean)
            conn.execute(f"UPDATE khatmas SET {sets}, updated_at = ? WHERE id = ?",
                         list(clean.values()) + [time.time(), khatma_id])
            conn.commit(); self.bump(); self.bump_khatma(khatma_id)
            return True
        finally:
            conn.close()

    def get_active_round(self, khatma_id):
        return int(self.get_khatma_rules(khatma_id).get("current_round") or 0) + 1

    def is_rotation_khatma(self, khatma_id):
        return self.get_khatma_rules(khatma_id).get("mode") == "rotation"

    def sync_khatma_members(self, khatma_id, per_person=None):
        """Ensure every user of the khatma has a stable member seat."""
        conn = self.get_connection()
        try:
            users = conn.execute(
                "SELECT id FROM users WHERE khatma_id = ? ORDER BY full_name COLLATE NOCASE", (khatma_id,)
            ).fetchall()
            existing = {int(r[0]) for r in conn.execute(
                "SELECT user_id FROM khatma_members WHERE khatma_id = ?", (khatma_id,)
            ).fetchall()}
            max_slot = conn.execute(
                "SELECT COALESCE(MAX(slot), -1) FROM khatma_members WHERE khatma_id = ?", (khatma_id,)
            ).fetchone()[0]
            for (uid,) in users:
                if int(uid) in existing:
                    continue
                max_slot += 1
                conn.execute(
                    "INSERT OR IGNORE INTO khatma_members (khatma_id, user_id, slot, hizbs_per_person, is_active) "
                    "VALUES (?, ?, ?, ?, 1)", (khatma_id, int(uid), max_slot, per_person)
                )
            conn.commit()
        finally:
            conn.close()

    def get_khatma_members(self, khatma_id):
        conn = self.get_connection()
        try:
            rows = conn.execute(
                "SELECT km.user_id, km.slot, km.hizbs_per_person, km.is_active, u.full_name "
                "FROM khatma_members km LEFT JOIN users u ON km.user_id = u.id "
                "WHERE km.khatma_id = ? ORDER BY km.slot, km.id", (khatma_id,)
            ).fetchall()
            return [{
                "user_id": int(r[0]), "slot": int(r[1] or 0),
                "hizbs_per_person": r[2], "is_active": int(r[3] or 0),
                "name": r[4] or f"User {r[0]}"
            } for r in rows]
        finally:
            conn.close()

    def get_member_uid_by_name(self, khatma_id, name):
        """Return the user id in this khatma whose name matches (normalized)."""
        norm = self.normalize_arabic(name)
        conn = self.get_connection()
        try:
            rows = conn.execute(
                "SELECT id, full_name FROM users WHERE khatma_id = ?", (khatma_id,)
            ).fetchall()
        finally:
            conn.close()
        for uid, full_name in rows:
            if self.normalize_arabic(full_name) == norm:
                return int(uid)
        return None

    def set_khatma_members(self, khatma_id, user_ids, per_person=None):
        """Replace the ordered member list used by the rotation engine."""
        conn = self.get_connection()
        try:
            conn.execute("DELETE FROM khatma_members WHERE khatma_id = ?", (khatma_id,))
            for slot, uid in enumerate(user_ids):
                conn.execute(
                    "INSERT OR IGNORE INTO khatma_members (khatma_id, user_id, slot, hizbs_per_person, is_active) "
                    "VALUES (?, ?, ?, ?, 1)", (khatma_id, int(uid), slot, per_person)
                )
            conn.commit()
        finally:
            conn.close()

    def update_khatma_member(self, khatma_id, user_id, is_active=None, per_person=None, slot=None):
        conn = self.get_connection()
        try:
            exists = conn.execute(
                "SELECT 1 FROM khatma_members WHERE khatma_id = ? AND user_id = ?",
                (khatma_id, int(user_id))
            ).fetchone()
            if not exists:
                max_slot = conn.execute(
                    "SELECT COALESCE(MAX(slot), -1) FROM khatma_members WHERE khatma_id = ?",
                    (khatma_id,)
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO khatma_members (khatma_id, user_id, slot, hizbs_per_person, is_active) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (khatma_id, int(user_id),
                     int(slot) if slot is not None else int(max_slot) + 1,
                     per_person, 1 if is_active is None else (1 if is_active else 0))
                )
                exists = True
            if is_active is not None:
                conn.execute("UPDATE khatma_members SET is_active = ? WHERE khatma_id = ? AND user_id = ?",
                             (1 if is_active else 0, khatma_id, int(user_id)))
            if per_person is not None:
                conn.execute("UPDATE khatma_members SET hizbs_per_person = ? WHERE khatma_id = ? AND user_id = ?",
                             (max(1, min(60, int(per_person))), khatma_id, int(user_id)))
            if slot is not None:
                conn.execute("UPDATE khatma_members SET slot = ? WHERE khatma_id = ? AND user_id = ?",
                             (int(slot), khatma_id, int(user_id)))
            conn.commit(); self.bump_khatma(khatma_id)
            return True
        finally:
            conn.close()

    def add_khatma_members_by_names(self, khatma_id, names, per_person=None):
        """Create (or reuse) users from a pasted list of names and seat them.

        Returns the user ids in the order they were added. Names are matched
        with the same Arabic normalization used by the login flow, so pasting
        an existing participant does not create a duplicate account.
        """
        uids, seen = [], set()
        for raw in names or []:
            name = str(raw).strip()
            if not name:
                continue
            norm = self.normalize_arabic(name)
            if norm in seen:
                continue
            seen.add(norm)
            uid, status = self.register_web_user(name, "", khatma_id)
            if uid is None:
                # Existing name protected by a PIN: reuse the account as-is.
                conn = self.get_connection()
                try:
                    rows = conn.execute(
                        "SELECT id, full_name FROM users WHERE khatma_id = ?", (khatma_id,)
                    ).fetchall()
                finally:
                    conn.close()
                for rid, rname in rows:
                    if self.normalize_arabic(rname) == norm:
                        uid = int(rid)
                        break
            if uid is not None and int(uid) not in uids:
                uids.append(int(uid))
        self.sync_khatma_members(khatma_id, per_person)
        return uids

    def assign_rotation_round(self, khatma_id, round_number=None):
        """Assign hizbs for one rotation round.

        Each member owns a contiguous block of seats. Every round the whole
        seat map shifts by `rotation_step`, so with 30 members x 2 hizbs and
        step=2 all 60 hizbs are covered every round and each member reads the
        full Quran after 30 rounds.
        """
        rules = self.get_khatma_rules(khatma_id)
        round_number = int(round_number or (int(rules.get("current_round") or 0) + 1))
        members = [m for m in self.get_khatma_members(khatma_id) if int(m.get("is_active") or 0) == 1]
        if not members:
            return []

        step = max(1, int(rules.get("rotation_step") or rules.get("hizbs_per_person") or 1))
        default_per = max(1, int(rules.get("hizbs_per_person") or 1))
        round_index = max(0, round_number - 1)

        seat = 0
        pairs = []
        for member in members:
            per = max(1, int(member.get("hizbs_per_person") or default_per))
            for _ in range(per):
                hizb = ((seat + round_index * step) % TOTAL_HIZBS) + 1
                pairs.append((member["user_id"], hizb))
                seat += 1

        conn = self.get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            booked = []
            for uid, hizb in pairs:
                taken = conn.execute(
                    "SELECT user_id FROM hizb_assignments WHERE khatma_id = ? AND hizb_number = ?",
                    (khatma_id, hizb)
                ).fetchone()
                if not taken:
                    taken = conn.execute(
                        "SELECT 1 FROM completed_hizb WHERE khatma_id = ? AND hizb_number = ? "
                        "AND COALESCE(round_number, 1) = ?",
                        (khatma_id, hizb, round_number)
                    ).fetchone()
                if taken:
                    continue
                conn.execute(
                    "INSERT INTO hizb_assignments (group_id, user_id, hizb_number, khatma_id, round_number, timestamp) "
                    "VALUES (?, ?, ?, ?, ?, datetime('now'))",
                    (khatma_id, int(uid), int(hizb), khatma_id, round_number)
                )
                booked.append([int(uid), int(hizb)])
            conn.execute("UPDATE khatmas SET updated_at = ? WHERE id = ?", (time.time(), khatma_id))
            conn.commit()
            return booked
        finally:
            conn.close()

    def initialize_rotation(self, khatma_id, start_date=None, reset=True, user_ids=None, per_person=None):
        """Start (or restart) the daily rotation for a khatma."""
        if start_date:
            parsed = self._parse_dt(start_date)
            start_date = (parsed.date() if parsed else datetime.date.today()).isoformat()
        else:
            start_date = datetime.date.today().isoformat()

        if user_ids is not None:
            self.set_khatma_members(khatma_id, user_ids, per_person)
        else:
            self.sync_khatma_members(khatma_id, per_person)

        conn = self.get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if reset:
                conn.execute("DELETE FROM hizb_assignments WHERE khatma_id = ?", (khatma_id,))
                conn.execute("DELETE FROM completed_hizb WHERE khatma_id = ?", (khatma_id,))
                conn.execute("DELETE FROM khatma_rounds WHERE khatma_id = ?", (khatma_id,))
            conn.execute(
                "UPDATE khatmas SET mode = 'rotation', rotation_start_date = ?, current_round = 0, "
                "last_eod_date = NULL, rotation_finished = 0, updated_at = ? WHERE id = ?",
                (start_date, time.time(), khatma_id)
            )
            if per_person is not None:
                conn.execute("UPDATE khatmas SET hizbs_per_person = ? WHERE id = ?",
                             (max(1, min(60, int(per_person))), khatma_id))
            conn.commit()
        finally:
            conn.close()

        booked = self.assign_rotation_round(khatma_id, 1)
        self.log_activity(khatma_id, None, "النظام", "rotation_started", None,
                          f"بدأت الختمة الدوارة من {start_date}")
        return {"start_date": start_date, "assigned": booked,
                "members": len(self.get_khatma_members(khatma_id))}

    def close_round(self, khatma_id, closed_by=None, auto=False, complete_pending=None):
        """Close the current rotation round, snapshot it, and start the next."""
        conn = self.get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT mode, current_round, rotation_days, auto_complete_eod, rotation_start_date "
                "FROM khatmas WHERE id = ?", (khatma_id,)
            ).fetchone()
            if not row or (row[0] or "open") != "rotation":
                conn.rollback()
                return None

            current_round = int(row[1] or 0)
            rotation_days = int(row[2] or 30)
            round_number = current_round + 1
            if round_number > rotation_days:
                conn.rollback()
                return {"round": current_round, "finished": True, "completed": 0}

            if complete_pending is None:
                complete_pending = int(row[3] if row[3] is not None else 1) == 1

            assignments = conn.execute(
                "SELECT user_id, hizb_number FROM hizb_assignments WHERE khatma_id = ?", (khatma_id,)
            ).fetchall()
            existing = conn.execute(
                "SELECT user_id, hizb_number FROM completed_hizb WHERE khatma_id = ? AND COALESCE(round_number, 1) = ?",
                (khatma_id, round_number)
            ).fetchall()
            completed_pairs = {(int(u), int(h)) for u, h in existing}

            if complete_pending:
                for uid, hizb in assignments:
                    key = (int(uid), int(hizb))
                    if key in completed_pairs:
                        continue
                    conn.execute(
                        "INSERT INTO completed_hizb (group_id, user_id, hizb_number, khatma_id, round_number, timestamp) "
                        "VALUES (?, ?, ?, ?, ?, datetime('now'))",
                        (khatma_id, int(uid), int(hizb), khatma_id, round_number)
                    )
                    completed_pairs.add(key)

            snapshot = {
                "assignments": [[int(u), int(h)] for u, h in assignments],
                "completed": sorted([list(p) for p in completed_pairs]),
                "complete_pending": bool(complete_pending),
            }

            conn.execute("DELETE FROM hizb_assignments WHERE khatma_id = ?", (khatma_id,))

            start_dt = self._parse_dt(row[4], "%Y-%m-%d")
            if start_dt:
                round_date = (start_dt.date() + datetime.timedelta(days=round_number - 1)).isoformat()
            else:
                round_date = datetime.date.today().isoformat()

            conn.execute(
                "INSERT OR REPLACE INTO khatma_rounds "
                "(khatma_id, round_number, round_date, status, closed_by, auto_closed, details) "
                "VALUES (?, ?, ?, 'closed', ?, ?, ?)",
                (khatma_id, round_number, round_date, closed_by, 1 if auto else 0, json.dumps(snapshot, ensure_ascii=False))
            )

            finished = 1 if round_number >= rotation_days else 0
            conn.execute(
                "UPDATE khatmas SET current_round = ?, total_khatmas = COALESCE(total_khatmas, 0) + 1, "
                "last_eod_date = ?, rotation_finished = ?, updated_at = ? WHERE id = ?",
                (round_number, round_date, finished, time.time(), khatma_id)
            )
            conn.commit()
        except Exception:
            try: conn.rollback()
            except Exception: pass
            raise
        finally:
            conn.close()

        if not finished:
            self.assign_rotation_round(khatma_id, round_number + 1)

        self.log_activity(
            khatma_id, closed_by, "admin" if closed_by else "النظام",
            "round_closed", None,
            f"إغلاق اليوم {round_number}" + (" تلقائياً" if auto else "") + f" ({len(completed_pairs)} حزب)"
        )
        return {"round": round_number, "finished": bool(finished),
                "completed": len(completed_pairs), "snapshot": snapshot}

    def process_due_rounds(self, khatma_id=None):
        """Lazily close any rotation rounds whose EOD time has passed.

        There is no always-on worker on the free hosting tier, so this is
        called from the read API and can also be triggered by the admin or a
        scheduled HTTP request.
        """
        now = datetime.datetime.now()
        conn = self.get_connection()
        try:
            if khatma_id:
                rows = conn.execute(
                    "SELECT id FROM khatmas WHERE id = ? AND mode = 'rotation' "
                    "AND COALESCE(require_eod_confirm, 0) = 0 AND COALESCE(rotation_finished, 0) = 0",
                    (khatma_id,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id FROM khatmas WHERE mode = 'rotation' "
                    "AND COALESCE(require_eod_confirm, 0) = 0 AND COALESCE(rotation_finished, 0) = 0"
                ).fetchall()
        finally:
            conn.close()

        results = []
        for (kid,) in rows:
            rules = self.get_khatma_rules(kid)
            start = self._parse_dt(rules.get("rotation_start_date"), "%Y-%m-%d")
            if not start:
                continue
            eod = self._parse_dt(rules.get("eod_time"), "%H:%M")
            hour, minute = (eod.hour, eod.minute) if eod else (23, 59)
            first_eod = datetime.datetime.combine(start.date(), datetime.time(hour, minute))
            if now < first_eod:
                continue

            due = int((now - first_eod).total_seconds() // 86400) + 1
            due = min(due, int(rules.get("rotation_days") or 30))
            current = int(rules.get("current_round") or 0)
            closed = 0
            while current < due:
                result = self.close_round(kid, auto=True)
                if not result:
                    break
                current += 1
                closed += 1
                if result.get("finished"):
                    break
            if closed:
                results.append({"khatma_id": kid, "closed": closed})
        return results

    def get_eod_status(self, khatma_id):
        rules = self.get_khatma_rules(khatma_id)
        if rules.get("mode") != "rotation":
            return {"mode": "open"}

        now = datetime.datetime.now()
        start = self._parse_dt(rules.get("rotation_start_date"), "%Y-%m-%d") or now
        eod = self._parse_dt(rules.get("eod_time"), "%H:%M")
        hour, minute = (eod.hour, eod.minute) if eod else (23, 59)
        first_eod = datetime.datetime.combine(start.date(), datetime.time(hour, minute))
        current = int(rules.get("current_round") or 0)
        rotation_days = int(rules.get("rotation_days") or 30)
        next_eod = first_eod + datetime.timedelta(days=current)
        if next_eod < now and int(rules.get("require_eod_confirm") or 0) == 0:
            due = min(int((now - first_eod).total_seconds() // 86400) + 1, rotation_days)
            next_eod = first_eod + datetime.timedelta(days=due)

        return {
            "mode": "rotation",
            "current_round": current,
            "active_round": current + 1,
            "rotation_days": rotation_days,
            "finished": bool(rules.get("rotation_finished")),
            "next_eod": None if rules.get("rotation_finished") else next_eod.strftime("%Y-%m-%d %H:%M"),
            "overdue": bool(not rules.get("rotation_finished") and next_eod < now),
            "require_eod_confirm": bool(rules.get("require_eod_confirm")),
        }

    def get_khatma_assignment_board(self, khatma_id):
        """Data used by the admin assignment manager board."""
        self.sync_khatma_members(khatma_id)
        rules = self.get_khatma_rules(khatma_id)
        active_round = int(rules.get("current_round") or 0) + 1
        members = {m["user_id"]: m for m in self.get_khatma_members(khatma_id)}

        conn = self.get_connection()
        try:
            assignments = conn.execute(
                "SELECT hizb_number, user_id FROM hizb_assignments WHERE khatma_id = ? ORDER BY hizb_number",
                (khatma_id,)
            ).fetchall()
            completions = conn.execute(
                "SELECT hizb_number, user_id FROM completed_hizb "
                "WHERE khatma_id = ? AND COALESCE(round_number, 1) = ? ORDER BY hizb_number",
                (khatma_id, active_round)
            ).fetchall()
            # Users with their per-round counts
            users = conn.execute(
                "SELECT u.id, u.full_name, u.web_pin, "
                "(SELECT COUNT(*) FROM hizb_assignments ha WHERE ha.user_id = u.id AND ha.khatma_id = ?) AS active, "
                "(SELECT COUNT(*) FROM completed_hizb ch WHERE ch.user_id = u.id AND ch.khatma_id = ? "
                " AND COALESCE(ch.round_number, 1) = ?) AS completed "
                "FROM users u WHERE u.khatma_id = ? ORDER BY u.full_name COLLATE NOCASE",
                (khatma_id, khatma_id, active_round, khatma_id)
            ).fetchall()
        finally:
            conn.close()

        hizb_map = {}
        for hizb, uid in assignments:
            hizb_map[int(hizb)] = {"status": "active", "uid": int(uid)}
        for hizb, uid in completions:
            hizb_map[int(hizb)] = {"status": "completed", "uid": int(uid)}

        unassigned_hizbs = [h for h in range(1, TOTAL_HIZBS + 1) if h not in hizb_map]

        active_by_uid, done_by_uid = {}, {}
        for _h, _u in assignments:
            active_by_uid[int(_u)] = active_by_uid.get(int(_u), 0) + 1
        for _h, _u in completions:
            done_by_uid[int(_u)] = done_by_uid.get(int(_u), 0) + 1
        members_short, shortfall = [], 0
        quota_warnings = []
        step = max(1, int(rules.get("rotation_step") or rules.get("hizbs_per_person") or 1))
        for member in members.values():
            if int(member.get("is_active") or 0) != 1:
                continue
            target = max(1, int(member.get("hizbs_per_person") or rules.get("hizbs_per_person") or 1))
            if target < step:
                quota_warnings.append({
                    "uid": int(member["user_id"]), "name": member.get("name"),
                    "per_person": target, "step": step,
                })
            have = active_by_uid.get(int(member["user_id"]), 0) + done_by_uid.get(int(member["user_id"]), 0)
            if have < target:
                shortfall += target - have
                members_short.append(int(member["user_id"]))

        user_list = []
        for uid, name, pin, active, completed in users:
            member = members.get(int(uid))
            user_list.append({
                "id": int(uid), "name": name, "pin": pin,
                "active": int(active or 0), "completed": int(completed or 0),
                "is_member": bool(member),
                "slot": member["slot"] if member else None,
                "member_active": bool(member["is_active"]) if member else False,
                "per_person": (member["hizbs_per_person"] if member and member["hizbs_per_person"]
                               else int(rules.get("hizbs_per_person") or 2)),
            })

        return {
            "rules": rules,
            "users": user_list,
            "members": list(members.values()),
            "hizb_map": hizb_map,
            "unassigned_hizbs": unassigned_hizbs,
            "coverage": len(hizb_map),
            "shortfall": shortfall,
            "members_short": members_short,
            "quota_warnings": quota_warnings,
            "eod": self.get_eod_status(khatma_id),
            "active_round": active_round,
        }

    def apply_assignment_changes(self, khatma_id, changes):
        """Atomic bulk assign/unassign/complete used by the admin board."""
        rules = self.get_khatma_rules(khatma_id)
        active_round = int(rules.get("current_round") or 0) + 1
        results = {"assigned": [], "unassigned": [], "completed": [], "errors": []}

        conn = self.get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            for change in changes or []:
                try:
                    uid = int(change.get("uid"))
                except (TypeError, ValueError):
                    continue

                for hizb in change.get("unassign", []) or []:
                    cur = conn.execute(
                        "DELETE FROM hizb_assignments WHERE khatma_id = ? AND user_id = ? AND hizb_number = ?",
                        (khatma_id, uid, int(hizb))
                    )
                    if cur.rowcount:
                        results["unassigned"].append([uid, int(hizb)])

                for hizb in change.get("complete", []) or []:
                    cur = conn.execute(
                        "DELETE FROM hizb_assignments WHERE khatma_id = ? AND user_id = ? AND hizb_number = ?",
                        (khatma_id, uid, int(hizb))
                    )
                    if cur.rowcount:
                        conn.execute(
                            "INSERT INTO completed_hizb (group_id, user_id, hizb_number, khatma_id, round_number, timestamp) "
                            "VALUES (?, ?, ?, ?, ?, datetime('now'))",
                            (khatma_id, uid, int(hizb), khatma_id, active_round)
                        )
                        results["completed"].append([uid, int(hizb)])

                for hizb in change.get("assign", []) or []:
                    taken = conn.execute(
                        "SELECT user_id FROM hizb_assignments WHERE khatma_id = ? AND hizb_number = ?",
                        (khatma_id, int(hizb))
                    ).fetchone()
                    if not taken:
                        done = conn.execute(
                            "SELECT user_id FROM completed_hizb WHERE khatma_id = ? AND hizb_number = ? "
                            "AND COALESCE(round_number, 1) = ?",
                            (khatma_id, int(hizb), active_round)
                        ).fetchone()
                        taken = done
                    if taken:
                        results["errors"].append(f"الحزب {hizb} محجوز مسبقاً")
                        continue
                    conn.execute(
                        "INSERT OR IGNORE INTO hizb_assignments "
                        "(group_id, user_id, hizb_number, khatma_id, round_number, timestamp) "
                        "VALUES (?, ?, ?, ?, ?, datetime('now'))",
                        (khatma_id, uid, int(hizb), khatma_id, active_round)
                    )
                    results["assigned"].append([uid, int(hizb)])

            conn.execute("UPDATE khatmas SET updated_at = ? WHERE id = ?", (time.time(), khatma_id))
            conn.commit()
            return results
        except Exception:
            try: conn.rollback()
            except Exception: pass
            raise
        finally:
            conn.close()

    def rebalance_round(self, khatma_id, round_number=None, only_uid=None):
        """Re-apply the rotation seat map to the current round.

        Keeps completed hizbs untouched, never steals another member's hizb and
        removes a member's assignments that are no longer part of their target
        (used when their per-person count or active flag changes).
        """
        rules = self.get_khatma_rules(khatma_id)
        if rules.get("mode") != "rotation":
            return {"assigned": 0, "removed": 0, "shortfall": 0, "unassigned_hizbs": []}
        round_number = int(round_number or (int(rules.get("current_round") or 0) + 1))
        step = max(1, int(rules.get("rotation_step") or rules.get("hizbs_per_person") or 1))
        default_per = max(1, int(rules.get("hizbs_per_person") or 1))
        round_index = max(0, round_number - 1)

        members = [m for m in self.get_khatma_members(khatma_id) if int(m.get("is_active") or 0) == 1]
        targets = {}
        seat = 0
        for member in members:
            uid = int(member["user_id"])
            per = max(1, int(member.get("hizbs_per_person") or default_per))
            if only_uid is not None and uid != int(only_uid):
                seat += per
                continue
            targets[uid] = {((seat + i + round_index * step) % TOTAL_HIZBS) + 1 for i in range(per)}
            seat += per

        if only_uid is not None and int(only_uid) not in targets:
            targets[int(only_uid)] = set()  # inactive or excluded -> drop assignments

        conn = self.get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            current = {}
            for hizb, uid in conn.execute(
                "SELECT hizb_number, user_id FROM hizb_assignments WHERE khatma_id = ?", (khatma_id,)
            ).fetchall():
                current[int(hizb)] = int(uid)
            completed = {int(r[0]) for r in conn.execute(
                "SELECT hizb_number FROM completed_hizb WHERE khatma_id = ? AND COALESCE(round_number, 1) = ?",
                (khatma_id, round_number)
            ).fetchall()}

            removed = 0
            for hizb, uid in list(current.items()):
                if uid in targets and hizb not in targets[uid]:
                    conn.execute(
                        "DELETE FROM hizb_assignments WHERE khatma_id = ? AND hizb_number = ?",
                        (khatma_id, hizb)
                    )
                    current.pop(hizb, None)
                    removed += 1

            assigned = 0
            shortfall = 0
            for uid, wanted in targets.items():
                have = {h for h, u in current.items() if u == uid}
                for hizb in sorted(wanted - have):
                    if hizb in current or hizb in completed:
                        continue
                    conn.execute(
                        "INSERT INTO hizb_assignments "
                        "(group_id, user_id, hizb_number, khatma_id, round_number, timestamp) "
                        "VALUES (?, ?, ?, ?, ?, datetime('now'))",
                        (khatma_id, uid, hizb, khatma_id, round_number)
                    )
                    current[hizb] = uid
                    assigned += 1
                    have.add(hizb)

                # If the canonical seat hizbs are already taken, top up the
                # quota from whatever is still free today.
                missing = len(wanted) - len(have)
                if missing > 0:
                    spare = [h for h in range(1, TOTAL_HIZBS + 1)
                             if h not in current and h not in completed]
                    for hizb in spare[:missing]:
                        conn.execute(
                            "INSERT INTO hizb_assignments "
                            "(group_id, user_id, hizb_number, khatma_id, round_number, timestamp) "
                            "VALUES (?, ?, ?, ?, ?, datetime('now'))",
                            (khatma_id, uid, hizb, khatma_id, round_number)
                        )
                        current[hizb] = uid
                        assigned += 1
                        have.add(hizb)
                shortfall += max(0, len(wanted) - len(have))

            conn.execute("UPDATE khatmas SET updated_at = ? WHERE id = ?", (time.time(), khatma_id))
            conn.commit()
            unassigned = [h for h in range(1, TOTAL_HIZBS + 1) if h not in current and h not in completed]
            return {"assigned": assigned, "removed": removed,
                    "shortfall": shortfall, "unassigned_hizbs": unassigned}
        except Exception:
            try: conn.rollback()
            except Exception: pass
            raise
        finally:
            conn.close()

    def fill_round_gaps(self, khatma_id, round_number=None, per_person=None):
        """Give each active member up to their quota from hizbs left unassigned."""
        rules = self.get_khatma_rules(khatma_id)
        round_number = int(round_number or (int(rules.get("current_round") or 0) + 1))
        default_per = max(1, int(per_person or rules.get("hizbs_per_person") or 1))
        members = [m for m in self.get_khatma_members(khatma_id) if int(m.get("is_active") or 0) == 1]

        conn = self.get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            current = {}
            for hizb, uid in conn.execute(
                "SELECT hizb_number, user_id FROM hizb_assignments WHERE khatma_id = ?", (khatma_id,)
            ).fetchall():
                current[int(hizb)] = int(uid)
            completed = {int(r[0]) for r in conn.execute(
                "SELECT hizb_number FROM completed_hizb WHERE khatma_id = ? AND COALESCE(round_number, 1) = ?",
                (khatma_id, round_number)
            ).fetchall()}
            free = [h for h in range(1, TOTAL_HIZBS + 1)
                    if h not in current and h not in completed]

            assigned = 0
            for member in members:
                uid = int(member["user_id"])
                target = max(1, int(member.get("hizbs_per_person") or default_per))
                have = sum(1 for u in current.values() if u == uid)
                while have < target and free:
                    hizb = free.pop(0)
                    conn.execute(
                        "INSERT INTO hizb_assignments "
                        "(group_id, user_id, hizb_number, khatma_id, round_number, timestamp) "
                        "VALUES (?, ?, ?, ?, ?, datetime('now'))",
                        (khatma_id, uid, hizb, khatma_id, round_number)
                    )
                    current[hizb] = uid
                    assigned += 1
                    have += 1

            conn.execute("UPDATE khatmas SET updated_at = ? WHERE id = ?", (time.time(), khatma_id))
            conn.commit()
            return {"assigned": assigned, "unassigned_hizbs": free,
                    "coverage": len(current) + len(completed)}
        except Exception:
            try: conn.rollback()
            except Exception: pass
            raise
        finally:
            conn.close()

    def auto_distribute_hizbs(self, khatma_id, per_person=None, user_ids=None, reset=True):
        """Evenly spread the 60 hizbs across the selected members."""
        self.sync_khatma_members(khatma_id)
        if user_ids is not None:
            self.set_khatma_members(khatma_id, user_ids, per_person)

        rules = self.get_khatma_rules(khatma_id)
        per_person = max(1, int(per_person or rules.get("hizbs_per_person") or 1))
        active_round = int(rules.get("current_round") or 0) + 1
        members = [m for m in self.get_khatma_members(khatma_id) if int(m.get("is_active") or 0) == 1]

        conn = self.get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            if reset:
                conn.execute("DELETE FROM hizb_assignments WHERE khatma_id = ?", (khatma_id,))
            taken = {int(r[0]) for r in conn.execute(
                "SELECT hizb_number FROM hizb_assignments WHERE khatma_id = ?", (khatma_id,)
            ).fetchall()}
            available = [h for h in range(1, TOTAL_HIZBS + 1) if h not in taken]

            booked = 0
            idx = 0
            for member in members:
                for _ in range(per_person):
                    if idx >= len(available):
                        break
                    hizb = available[idx]
                    conn.execute(
                        "INSERT OR IGNORE INTO hizb_assignments "
                        "(group_id, user_id, hizb_number, khatma_id, round_number, timestamp) "
                        "VALUES (?, ?, ?, ?, ?, datetime('now'))",
                        (khatma_id, int(member["user_id"]), hizb, khatma_id, active_round)
                    )
                    booked += 1
                    idx += 1

            conn.execute("UPDATE khatmas SET updated_at = ? WHERE id = ?", (time.time(), khatma_id))
            conn.commit()
            return {"assigned": booked, "unfilled": len(available) - booked, "members": len(members)}
        except Exception:
            try: conn.rollback()
            except Exception: pass
            raise
        finally:
            conn.close()


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
            db.reset(group_id=u.effective_chat.id)
        elif res:
            await q.edit_message_text(f"تقبل الله منك، تم إتمام الأحزاب: {', '.join(str(x) for x in res)}")
    elif q.data.startswith("done_"):
        h = int(q.data.split("_")[1])
        res = db.mark_done(u.effective_user.id, h)
        if res == "completed":
            await q.edit_message_text(MSG_KHATMA_COMPLETE, parse_mode="Markdown")
            db.reset(group_id=u.effective_chat.id)
        elif res:
            await q.edit_message_text(f"تقبل الله منك، تم إتمام الحزب {h}.")
    elif q.data == "confirm_reset":
        db.reset(group_id=u.effective_chat.id); await q.edit_message_text("تمت إعادة تعيين الختمة بنجاح ✅")

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
            # If it contains T (from datetime-local) or : (time), use it (replace T with space)
            if "T" in deadline:
                deadline_formatted = deadline.replace("T", " ")
            elif ":" in deadline:
                deadline_formatted = deadline
            else:
                 # Date only
                deadline_formatted = f"{deadline} 23:59"
            
            # Validate not in past
            try:
                dt = datetime.datetime.strptime(deadline_formatted, "%Y-%m-%d %H:%M")
                if dt < datetime.datetime.now():
                    return jsonify({"error": "Deadline cannot be in the past"}), 400
            except ValueError:
                 # Try with seconds if present, though HTML input usually doesn't send them
                try:
                    dt = datetime.datetime.strptime(deadline_formatted, "%Y-%m-%d %H:%M:%S")
                    if dt < datetime.datetime.now():
                        return jsonify({"error": "Deadline cannot be in the past"}), 400
                except: pass # Ignore parse error here, let it slide or handle later? 
                             # Actually let's assume if we constructed it, it's valid format or we fail.
                             # If user sent weird string, strptime fails.
        else:
            deadline_formatted = None

        
        # Generate a unique admin_uid (Negative timestamp-based, consistent with register_web_user)
        admin_uid = -int(time.time() * 1000 + random.randint(0, 999))

        # Custom khatma rules can be sent nested or as top-level fields.
        rules = data.get("rules") if isinstance(data.get("rules"), dict) else {}
        if not rules:
            rules = {
                "mode": data.get("mode", "open"),
                "hizbs_per_person": data.get("hizbs_per_person", 2),
                "rotation_step": data.get("rotation_step", 2),
                "rotation_days": data.get("rotation_days", 30),
                "rotation_start_date": data.get("rotation_start_date"),
                "eod_time": data.get("eod_time", "23:59"),
                "auto_complete_eod": data.get("auto_complete_eod", 1),
                "require_eod_confirm": data.get("require_eod_confirm", 0),
            }
        
        khatma_id, admin_uid = db.create_khatma(
            name, admin_uid, admin_name, admin_pin, intention, deadline_formatted, rules=rules
        )

        # Optional: seed the rotation with a pasted list of participant names.
        participant_items = parse_participants(data.get("participants"))
        participant_names = [name for name, _per in participant_items]
        participant_uids = []
        rotation_started = False
        assigned = 0
        if participant_names:
            participant_uids = db.add_khatma_members_by_names(
                khatma_id, participant_names, rules.get("hizbs_per_person")
            )
            if rules.get("mode") == "rotation" and participant_uids:
                db.initialize_rotation(
                    khatma_id,
                    start_date=rules.get("rotation_start_date"),
                    reset=True,
                    user_ids=participant_uids,
                    per_person=rules.get("hizbs_per_person"),
                )
                # The organizer is not a reader unless their own name was pasted.
                # This must happen after initialize_rotation(), which replaces members.
                if admin_uid not in participant_uids:
                    db.update_khatma_member(khatma_id, admin_uid, is_active=0)

                has_quotas = False
                for name, per in participant_items:
                    if per is None:
                        continue
                    uid = db.get_member_uid_by_name(khatma_id, name)
                    if uid is not None:
                        db.update_khatma_member(khatma_id, uid, per_person=per)
                        has_quotas = True
                if has_quotas:
                    db.rebalance_round(khatma_id)
                rotation_started = True
                assigned = db.get_status(khatma_id)[1]

        return jsonify({"success": True, "khatma_id": khatma_id, "admin_uid": admin_uid,
                        "mode": rules.get("mode", "open"),
                        "participants": len(participant_uids),
                        "rotation_started": rotation_started,
                        "assigned": assigned})
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
    
    conn = db.get_connection()
    try:
        conn.execute("DELETE FROM hizb_assignments WHERE khatma_id = ?", (kid,))
        conn.execute("DELETE FROM completed_hizb WHERE khatma_id = ?", (kid,))
        conn.commit()
    finally:
        conn.close()
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
                      stored_pin = u.get('pin', u.get('web_pin'))
                      if str(stored_pin) == str(d.get("pin")) and u.get('name') == d.get("name"):
                          return jsonify({"success": True, "uid": u['id'], "is_admin": True})

    return jsonify({"error": "بيانات الدخول غير صحيحة"}), 403

@app.route("/api/admin/users")
def admin_users():
    uid = request.args.get("uid")
    khatma_id = request.args.get("khatma_id")
    pin = request.headers.get("X-Admin-Pin")
    
    if not db.verify_admin_credentials(uid, khatma_id, pin):
        return jsonify({"error": "Unauthorized"}), 403
        
    return jsonify({"users": db.get_all_users(khatma_id)}) 


@app.route("/api/admin/user_hizbs")
def admin_user_hizbs():
    admin_uid = request.args.get("admin_uid")
    target_uid = request.args.get("uid")
    khatma_id = request.args.get("khatma_id")
    pin = request.headers.get("X-Admin-Pin")
    
    if not db.verify_admin_credentials(admin_uid, khatma_id, pin):
        return jsonify({"error": "Unauthorized"}), 403
        
    return jsonify({"hizbs": db.get_user_hizbs(target_uid)})

@app.route("/api/admin/control", methods=["POST"])
def admin_control():
    d = request.get_json(); action = d.get("action"); uid = d.get("uid"); hizb = d.get("hizb")
    khatma_id = d.get("khatma_id") # NEW: Support multi-tenancy
    admin_uid = d.get("admin_uid") # NEW: Security check
    pin = d.get("admin_pin")       # NEW: Pin check
    
    # Auth Logic: Check for Dev Key OR Admin Credentials
    dev_key = request.headers.get("X-Dev-Key")
    is_dev = dev_key and dev_key == DEV_ACCESS_KEY
    
    if not is_dev and not db.verify_admin_credentials(admin_uid, khatma_id, pin):
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
            if hizb:
                new_deadline = hizb.replace("T", " ") if ("T" in hizb or ":" in hizb) else f"{hizb} 23:59"
                
                # Check for past date
                try:
                    # Flexible parsing
                    fmt = "%Y-%m-%d %H:%M" if len(new_deadline) <= 16 else "%Y-%m-%d %H:%M:%S"
                    dt = datetime.datetime.strptime(new_deadline, fmt)
                    if dt < datetime.datetime.now():
                         return jsonify({"error": "Deadline cannot be in the past"}), 400
                except: pass 
            else:
                new_deadline = None
            db.update_khatma(khatma_id, deadline=new_deadline)
        else:
            # Global setting (legacy)
            if hizb:
                val = hizb.replace("T", " ") if ("T" in hizb or ":" in hizb) else f"{hizb} 23:59"
            else:
                val = None
            db.set_setting("deadline", val)
            # settings table key=value. If None, maybe delete key? 
            # Current implementation of set_setting uses INSERT OR REPLACE. 
            # If value is NULL, it depends on table schema. Value is TEXT.
            # strict check: if val is None, maybe we want to delete it or set to empty string?
            # Let's set to empty string for legacy compatibility or just delete.
            # Existing set_setting: conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
            # If value is None, it inserts NULL.
            db.set_setting("deadline", val)
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

# --- Assignment Manager & Custom Khatma Rules API ---

def _admin_auth_from_payload(payload):
    """Validate admin credentials for JSON admin endpoints."""
    dev_key = request.headers.get("X-Dev-Key")
    if dev_key and dev_key == DEV_ACCESS_KEY:
        return True
    uid = payload.get("admin_uid")
    khatma_id = payload.get("khatma_id")
    pin = payload.get("admin_pin") or request.headers.get("X-Admin-Pin")
    return bool(db.verify_admin_credentials(uid, khatma_id, pin))


@app.route("/api/admin/assignment_board")
def admin_assignment_board():
    """Everything the admin needs to manage assignments in one call."""
    admin_uid = request.args.get("uid")
    khatma_id = request.args.get("khatma_id")
    pin = request.headers.get("X-Admin-Pin")
    if not db.verify_admin_credentials(admin_uid, khatma_id, pin):
        return jsonify({"error": "Unauthorized"}), 403

    try:
        db.process_due_rounds(khatma_id)
    except Exception as e:
        print(f"WARNING: process_due_rounds failed: {e}", flush=True)

    return jsonify({"success": True, **db.get_khatma_assignment_board(khatma_id)})


@app.route("/api/admin/assignment/save", methods=["POST"])
def admin_assignment_save():
    d = request.get_json() or {}
    if not _admin_auth_from_payload(d):
        return jsonify({"error": "Unauthorized"}), 403
    khatma_id = d.get("khatma_id")
    if not khatma_id:
        return jsonify({"error": "khatma_id required"}), 400
    try:
        result = db.apply_assignment_changes(khatma_id, d.get("changes") or [])
        return jsonify({"success": True, **result})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/assignment/auto", methods=["POST"])
def admin_assignment_auto():
    d = request.get_json() or {}
    if not _admin_auth_from_payload(d):
        return jsonify({"error": "Unauthorized"}), 403
    khatma_id = d.get("khatma_id")
    if not khatma_id:
        return jsonify({"error": "khatma_id required"}), 400

    try:
        per_person = int(d.get("per_person") or 2)
    except (TypeError, ValueError):
        per_person = 2
    user_ids = d.get("user_ids")
    if user_ids is not None:
        try:
            user_ids = [int(x) for x in user_ids]
        except (TypeError, ValueError):
            return jsonify({"error": "user_ids must be a list of integers"}), 400

    try:
        if d.get("start_rotation"):
            start_date = d.get("start_date") or datetime.date.today().isoformat()
            result = db.initialize_rotation(
                khatma_id, start_date=start_date, reset=bool(d.get("reset", True)),
                user_ids=user_ids, per_person=per_person
            )
            return jsonify({"success": True, "mode": "rotation", **result})
        result = db.auto_distribute_hizbs(
            khatma_id, per_person=per_person, user_ids=user_ids,
            reset=bool(d.get("reset", True))
        )
        return jsonify({"success": True, "mode": db.get_khatma_rules(khatma_id).get("mode", "open"), **result})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/rotation/start", methods=["POST"])
def admin_rotation_start():
    d = request.get_json() or {}
    if not _admin_auth_from_payload(d):
        return jsonify({"error": "Unauthorized"}), 403
    khatma_id = d.get("khatma_id")
    if not khatma_id:
        return jsonify({"error": "khatma_id required"}), 400

    user_ids = d.get("user_ids")
    if user_ids is not None:
        try:
            user_ids = [int(x) for x in user_ids]
        except (TypeError, ValueError):
            return jsonify({"error": "user_ids must be a list of integers"}), 400

    try:
        result = db.initialize_rotation(
            khatma_id,
            start_date=d.get("start_date"),
            reset=bool(d.get("reset", True)),
            user_ids=user_ids,
            per_person=d.get("per_person"),
        )
        return jsonify({"success": True, "mode": "rotation", **result,
                        "rules": db.get_khatma_rules(khatma_id)})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/round/close", methods=["POST"])
def admin_close_round():
    d = request.get_json() or {}
    if not _admin_auth_from_payload(d):
        return jsonify({"error": "Unauthorized"}), 403
    khatma_id = d.get("khatma_id")
    if not khatma_id:
        return jsonify({"error": "khatma_id required"}), 400

    closed_by = d.get("admin_uid")
    try:
        closed_by = int(closed_by) if closed_by is not None else None
    except (TypeError, ValueError):
        closed_by = None

    complete_pending = d.get("complete_pending")
    if complete_pending is not None:
        complete_pending = str(complete_pending).lower() in ("1", "true", "yes", "on")

    try:
        result = db.close_round(
            khatma_id, closed_by=closed_by, auto=False, complete_pending=complete_pending
        )
        if not result:
            return jsonify({"error": "هذه الختمة ليست في وضع التدوير اليومي"}), 400
        return jsonify({"success": True, **result,
                        "eod": db.get_eod_status(khatma_id)})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/rules", methods=["POST"])
def admin_update_rules():
    d = request.get_json() or {}
    if not _admin_auth_from_payload(d):
        return jsonify({"error": "Unauthorized"}), 403
    khatma_id = d.get("khatma_id")
    if not khatma_id:
        return jsonify({"error": "khatma_id required"}), 400

    rules = d.get("rules") if isinstance(d.get("rules"), dict) else {
        key: d.get(key) for key in (
            "mode", "hizbs_per_person", "rotation_step", "rotation_start_date",
            "rotation_days", "eod_time", "auto_complete_eod", "require_eod_confirm"
        ) if key in d
    }
    try:
        db.update_khatma_rules(khatma_id, **rules)
        return jsonify({"success": True, "rules": db.get_khatma_rules(khatma_id),
                        "eod": db.get_eod_status(khatma_id)})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/member/update", methods=["POST"])
def admin_update_member():
    d = request.get_json() or {}
    if not _admin_auth_from_payload(d):
        return jsonify({"error": "Unauthorized"}), 403
    khatma_id = d.get("khatma_id")
    uid = d.get("uid")
    if not khatma_id or uid is None:
        return jsonify({"error": "khatma_id and uid required"}), 400
    try:
        db.update_khatma_member(
            khatma_id, int(uid),
            is_active=d.get("is_active", d.get("active")),
            per_person=d.get("per_person"),
            slot=d.get("slot"),
        )
        result = {"success": True}
        rules = db.get_khatma_rules(khatma_id)
        started = int(rules.get("current_round") or 0) > 0 or db.get_status(khatma_id)[1] > 0
        if rules.get("mode") == "rotation" and started:
            result.update(db.rebalance_round(khatma_id, only_uid=int(uid)))
        return jsonify(result)
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/assignment/fill", methods=["POST"])
def admin_fill_assignment_gaps():
    """Fill the current round's unassigned hizbs up to each member's quota."""
    d = request.get_json() or {}
    if not _admin_auth_from_payload(d):
        return jsonify({"error": "Unauthorized"}), 403
    khatma_id = d.get("khatma_id")
    if not khatma_id:
        return jsonify({"error": "khatma_id required"}), 400
    try:
        result = db.fill_round_gaps(khatma_id, per_person=d.get("per_person"))
        return jsonify({"success": True, **result})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/members/add", methods=["POST"])
def admin_add_members():
    """Bulk-add participants by name (one per line) and auto-assign if rotating."""
    d = request.get_json() or {}
    if not _admin_auth_from_payload(d):
        return jsonify({"error": "Unauthorized"}), 403
    khatma_id = d.get("khatma_id")
    if not khatma_id:
        return jsonify({"error": "khatma_id required"}), 400

    items = parse_participants(d.get("names"))
    names = [name for name, _per in items]
    if not names:
        return jsonify({"error": "أدخل اسماً واحداً على الأقل"}), 400

    rules = db.get_khatma_rules(khatma_id)
    per_person = d.get("per_person") or rules.get("hizbs_per_person")
    try:
        uids = db.add_khatma_members_by_names(khatma_id, names, per_person)
        result = {"success": True, "added": len(uids), "names": len(names), "uids": uids,
                  "rotation": None, "assigned": 0, "unassigned_hizbs": []}

        # Optional per-person quotas (name: count syntax).
        for name, per in items:
            if per is None:
                continue
            uid = db.get_member_uid_by_name(khatma_id, name)
            if uid is not None:
                db.update_khatma_member(khatma_id, uid, per_person=per)

        if rules.get("mode") == "rotation" and uids:
            current_round = int(rules.get("current_round") or 0)
            active_count = db.get_status(khatma_id)[1]
            if current_round == 0 and active_count == 0:
                # Rotation has not started yet: start it now with everyone seated.
                db.initialize_rotation(
                    khatma_id,
                    start_date=rules.get("rotation_start_date"),
                    reset=False,
                    per_person=per_person,
                )
                result["rotation"] = "started"
                result["assigned"] = db.get_status(khatma_id)[1]
            else:
                assigned = 0
                for uid in uids:
                    info = db.rebalance_round(khatma_id, only_uid=uid)
                    assigned += info.get("assigned", 0)
                    result["unassigned_hizbs"] = info.get("unassigned_hizbs", [])
                result["rotation"] = "advanced" if assigned else "round_full"
                result["assigned"] = assigned
        return jsonify(result)
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/cron/eod", methods=["GET", "POST"])
def cron_eod():
    """External scheduler hook (e.g. PythonAnywhere scheduled task)."""
    key = request.args.get("key") or request.headers.get("X-Dev-Key")
    if key != DEV_ACCESS_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify({"success": True, "closed": db.process_due_rounds()})


@app.route("/api/user/update_name", methods=["POST"])
def update_user_name():
    data = request.get_json()
    uid = data.get("uid")
    new_name = data.get("name")
    new_pin = data.get("pin") # Optional
    requester_uid = data.get("requester_uid")
    khatma_id = data.get("khatma_id")
    
    if not uid or not new_name or not requester_uid:
        return jsonify({"error": "Missing required fields"}), 400
    
    # Authorization: user can edit own name, or requester is admin
    # Legacy check for "Admin" name is removed, now strictly checks DB admin status if khatma_id provided
    is_self = str(uid) == str(requester_uid)
    is_admin = False
    
    if khatma_id:
        is_admin = db.is_admin(requester_uid, khatma_id)
    else:
        # Fallback for legacy global admin if needed, checking name
        requester_name = db.get_user_name(requester_uid)
        if requester_name == "Admin": is_admin = True

    if is_self or is_admin:
        try:
            db.update_user_profile(uid, new_name, new_pin)
            return jsonify({"success": True})
        except Exception as e:
            if "UNIQUE constraint failed" in str(e):
                return jsonify({"error": "Name already taken"}), 400
            import traceback; traceback.print_exc()
            return jsonify({"error": f"Database error: {str(e)}"}), 500
    
    return jsonify({"error": "Unauthorized"}), 403

@app.route("/api/khatma/delete_user", methods=["POST"])
def delete_khatma_user():
    data = request.get_json()
    uid = data.get("uid")
    khatma_id = data.get("khatma_id")
    requester_uid = data.get("requester_uid")
    
    if not uid or not khatma_id or not requester_uid:
        return jsonify({"error": "Missing required fields"}), 400
        
    if not db.is_admin(requester_uid, khatma_id):
        return jsonify({"error": "Unauthorized: Admin access required"}), 403
        
    # Prevent admin from deleting themselves? Maybe good safety, but user asked for "possibility to delete user"
    if str(uid) == str(requester_uid):
         return jsonify({"error": "Cannot delete yourself"}), 400

    try:
        db.remove_user_from_khatma(uid, khatma_id)
        return jsonify({"success": True})
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": f"Delete Error: {str(e)}"}), 500

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
        khatma_id = request.args.get("khatma_id")
        if not khatma_id: khatma_id = None # Normalize empty string to None

        # Lazily close any rotation rounds whose EOD time has passed.
        if khatma_id:
            try:
                db.process_due_rounds(khatma_id)
            except Exception as e:
                print(f"WARNING: process_due_rounds failed: {e}", flush=True)
        
        # Stick to integer for UID
        uid = int(ur) if (ur and (ur.isdigit() or (ur.startswith('-') and ur[1:].isdigit()))) else None
        
        # Get data - pass khatma_id if provided
        c, a, ass = db.get_status(khatma_id)
        v = db.get_v(khatma_id)
        avail = db.get_available(khatma_id)
        my_ass = db.get_user_assignments(uid, khatma_id) if uid else []
        my_comp = db.get_user_completions(uid, khatma_id) if uid else []
        
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
        
        intentions = db.get_intentions(khatma_id)
        parts = db.get_participants_activity(khatma_id) if khatma_id else db.get_participants_activity()
        recent_activity = db.get_recent_activity(khatma_id, limit=8)

        rules = db.get_khatma_rules(khatma_id) if khatma_id else dict(db.ROTATION_DEFAULTS)
        eod = db.get_eod_status(khatma_id) if khatma_id else {"mode": "open"}

        return jsonify({
            "completed_count": int(c), "active_count": int(a), "remaining_count": max(0, 60-int(c)-int(a)),
            "version": v or 0, "assignments": ass, "available_hizbs": avail, 
            "my_assignments": my_ass, "my_completions": my_comp,
            "deadline": deadline, "total_khatmas": total or 0, "intentions": intentions,
            "participants": parts, "intention": intention, "khatma_name": khatma_name,
            "recent_activity": recent_activity,
            "rules": rules, "eod": eod
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        print(f"DEBUG: api_status failed: {e}")
        return jsonify({"error": f"Status Error: {str(e)}"}), 500

@app.route("/api/activity")
def api_activity():
    khatma_id = request.args.get("khatma_id")
    if not khatma_id:
        return jsonify({"error": "khatma_id required"}), 400
    try:
        offset = int(request.args.get("offset", 0))
        limit  = int(request.args.get("limit", 10))
        # Fetch one extra to know if there are more pages
        items = db.get_recent_activity(khatma_id, limit=limit + 1, offset=offset)
        has_more = len(items) > limit
        return jsonify({"items": items[:limit], "has_more": has_more, "next_offset": offset + limit})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/check_update")
def check_update(): 
    khatma_id = request.args.get("khatma_id")
    if not khatma_id: khatma_id = None
    v = db.get_v(khatma_id)
    # print(f"CHECK UPDATE: khatma={khatma_id} v={v}", flush=True)
    return jsonify({"version": v})


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
    d = request.get_json(); ur = d.get("uid"); name = d.get("name"); text = d.get("text"); khatma_id = d.get("khatma_id")
    if not khatma_id: khatma_id = None
    uid = int(ur) if (ur and (str(ur).isdigit() or (str(ur).startswith('-') and str(ur)[1:].isdigit()))) else None
    if not name or not text: return jsonify({"error": "Missing data"}), 400
    db.add_intention(uid, name, text, khatma_id)
    return jsonify({"success": True})

@app.route("/api/intention/delete", methods=["POST"])
def api_delete_intention():
    d = request.get_json(); ur = d.get("uid"); dua_id = d.get("id"); khatma_id = d.get("khatma_id")
    if not khatma_id: khatma_id = None
    uid = int(ur) if (ur and (str(ur).isdigit() or (str(ur).startswith('-') and str(ur)[1:].isdigit()))) else None
    if not dua_id: return jsonify({"error": "Missing ID"}), 400
    db.delete_intention(uid, dua_id, khatma_id)
    return jsonify({"success": True})

@app.route("/api/join", methods=["POST"])
def api_join():
    try:
        d = request.get_json()
        khatma_id = d.get("khatma_id")
        name = d.get("name")
        pin = d.get("pin")
        hizb = d.get("hizb")
        
        if not name: return jsonify({"error": "الاسم مطلوب"}), 400
        if not khatma_id: khatma_id = None
        
        uid, s = db.register_web_user(name, pin, khatma_id)
        if s == "wrong_pin": return jsonify({"error": "الرمز السري غير صحيح"}), 403
        
        if db.assign_hizb(uid, int(hizb), khatma_id): 
            return jsonify({"success": True, "uid": uid})
        else:
            return jsonify({"error": "الحزب محجوز او حدث خطأ"}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Internal Server Error: {str(e)}"}), 500

@app.route("/api/join/batch", methods=["POST"])
def api_join_batch():
    try:
        d = request.get_json()
        khatma_id = d.get("khatma_id")
        name = d.get("name")
        pin = d.get("pin")
        hizbs = d.get("hizbs", [])

        if not name: return jsonify({"error": "الاسم مطلوب"}), 400
        if not hizbs or not isinstance(hizbs, list): return jsonify({"error": "لم يتم اختيار أحزاب"}), 400
        if not khatma_id: khatma_id = None

        uid, s = db.register_web_user(name, pin, khatma_id)
        if s == "wrong_pin": return jsonify({"error": "الرمز السري غير صحيح"}), 403
        if not uid: return jsonify({"error": "Could not register user"}), 500

        booked = db.assign_hizb_batch(uid, hizbs, khatma_id)
        failed = [int(h) for h in hizbs if int(h) not in booked]

        if booked:
            return jsonify({"success": True, "uid": uid, "booked": booked, "failed": failed})
        else:
            return jsonify({"error": "جميع الأحزاب محجوزة أو حدث خطأ"}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Internal Server Error: {str(e)}"}), 500

@app.route("/api/done", methods=["POST"])
def api_done():
    try:
        d = request.get_json()
        ur = d.get("uid")
        khatma_id = d.get("khatma_id")
        if not khatma_id: khatma_id = None
        uid = int(ur) if (ur and (str(ur).isdigit() or (str(ur).startswith('-') and str(ur)[1:].isdigit()))) else None
        if uid is None: return jsonify({"error": "User not identified"}), 400
        
        res = db.mark_done(uid, int(d.get("hizb")), khatma_id)
        if res == "completed":
            # Auto-increment total for this khatma and reset
            if khatma_id:
                db.reset(khatma_id)
            else:
                db.reset()  # Legacy bot behavior
            return jsonify({"success": True, "completed": True})
        if res: return jsonify({"success": True})
        return jsonify({"error": "فشل"}), 400
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/api/done_all", methods=["POST"])
def api_done_all():
    try:
        d = request.get_json(); ur = d.get("uid"); khatma_id = d.get("khatma_id")
        if not khatma_id: khatma_id = None
        uid = int(ur) if (ur and (str(ur).isdigit() or (str(ur).startswith('-') and str(ur)[1:].isdigit()))) else None
        if uid is None: return jsonify({"error": "User not identified"}), 400
        
        res = db.mark_all_done(uid, khatma_id)
        if res == "completed":
            db.reset(khatma_id)
            return jsonify({"success": True, "completed": True})
        if res: return jsonify({"success": True})
        return jsonify({"error": "لا يوجد أحزاب لإتمامها"}), 400
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route("/api/undo_complete", methods=["POST"])
def api_undo_complete():
    try:
        d = request.get_json()
        print(f"DEBUG UNDO PAYLOAD receive: {d}", flush=True)
        if not d: return jsonify({"error": "No data"}), 400
        
        ur = d.get("uid")
        hizb_val = d.get("hizb")
        
        uid = int(ur) if (ur and (str(ur).isdigit() or (str(ur).startswith('-') and str(ur)[1:].isdigit()))) else None
        
        if uid is None: return jsonify({"error": "User not identified"}), 400
        if hizb_val is None: return jsonify({"error": "Hizb number missing"}), 400
        
        khatma_id = d.get("khatma_id")
        if not khatma_id: khatma_id = None
        if db.undo_completion(uid, int(hizb_val), khatma_id): 
            return jsonify({"success": True})
        return jsonify({"error": "Failed to undo"}), 400
    except Exception as e:
        print(f"Undo Error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/return", methods=["POST"])
def api_return():
    try:
        d = request.get_json()
        if not d: return jsonify({"error": "No data"}), 400
        
        ur = d.get("uid")
        hizb_val = d.get("hizb")
        
        uid = int(ur) if (ur and (str(ur).isdigit() or (str(ur).startswith('-') and str(ur)[1:].isdigit()))) else None
        
        if uid is None: return jsonify({"error": "User not identified"}), 400
        if hizb_val is None: return jsonify({"error": "Hizb number missing"}), 400
        
        khatma_id = d.get("khatma_id")
        if not khatma_id: khatma_id = None
        if db.unassign_hizb(uid, int(hizb_val), khatma_id): 
            return jsonify({"success": True})
        return jsonify({"error": "Failed to return"}), 400
    except Exception as e:
        print(f"Return Error: {e}")
        return jsonify({"error": str(e)}), 500


# Only initialize bot on startup if it exists
if application:
    async def init_b(): await application.initialize(); await application.start()
    loop = asyncio.get_event_loop(); loop.run_until_complete(init_b())

@app.route("/api/download_card", methods=["POST"])
def download_card():
    try:
        # Support both JSON (fetch) and Form (hidden form) for maximum robustness
        if request.is_json:
            data = request.json
        else:
            data = request.form
            
        img_data = data.get("image")
        filename = data.get("filename", "khatma_card.png")
        
        if not img_data or "," not in img_data:
            return jsonify({"success": False, "error": "Invalid image data"}), 400
            
        header, encoded = img_data.split(",", 1)
        import base64, io
        from flask import send_file, make_response
        
        print(f"DEBUG: Received base64 length: {len(encoded)}")
        file_bytes = base64.b64decode(encoded)
        print(f"DEBUG: Decoded to {len(file_bytes)} bytes")
        
        buf = io.BytesIO(file_bytes)
        buf.seek(0)  # CRITICAL: Ensure we're at the start
        
        # Try Flask 2.0+ first, fallback to older versions
        try:
            response = make_response(send_file(
                buf,
                mimetype="image/png",
                as_attachment=True,
                download_name=filename
            ))
        except TypeError:
            # Older Flask - doesn't support download_name, will set via header
            buf.seek(0)
            response = make_response(send_file(
                buf,
                mimetype="image/png",
                as_attachment=True
            ))
        
        # For older Flask versions, set attachment_filename via header
        from urllib.parse import quote
        safe_filename = quote(filename)
        response.headers["Content-Disposition"] = f'attachment; filename="{filename}"; filename*=UTF-8\'\'{safe_filename}'
        response.headers["Content-Type"] = "image/png"
        response.headers["Cache-Control"] = "no-cache"
        
        print(f"DEBUG: Sending response with {len(file_bytes)} bytes")
        return response
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "error": str(e)}), 500

# --- Activity Log API ---
@app.route("/api/activity_log", methods=["GET"])
def api_get_activity_log():
    """Get activity logs for a khatma (admin endpoint)"""
    khatma_id = request.args.get("khatma_id")
    limit = int(request.args.get("limit", 100))
    
    if not khatma_id:
        return jsonify({"error": "khatma_id required"}), 400
    
    logs = db.get_activity_logs(khatma_id, limit)
    return jsonify({"success": True, "logs": logs})

@app.route("/api/dev/activity_log", methods=["GET"])
@require_dev_auth
def api_get_all_activity_logs():
    """Get all activity logs (developer endpoint)"""
    limit = int(request.args.get("limit", 100))
    logs = db.get_all_activity_logs(limit)
    return jsonify({"success": True, "logs": logs})

# --- Selection Type API ---
@app.route("/api/khatma/selection_type", methods=["GET"])
def api_get_selection_type():
    """Get the selection type for a khatma"""
    khatma_id = request.args.get("khatma_id")
    if not khatma_id:
        return jsonify({"error": "khatma_id required"}), 400
    
    selection_type = db.get_khatma_selection_type(khatma_id)
    return jsonify({"success": True, "selection_type": selection_type})

@app.route("/api/khatma/selection_type", methods=["POST"])
def api_set_selection_type():
    """Set the selection type for a khatma (admin endpoint)"""
    d = request.get_json()
    khatma_id = d.get("khatma_id")
    selection_type = d.get("selection_type")
    
    if not khatma_id:
        return jsonify({"error": "khatma_id required"}), 400
    if not selection_type:
        return jsonify({"error": "selection_type required"}), 400
    
    # Validate selection type
    valid_types = ['sequential', 'range', 'sahm', 'manual']
    if selection_type not in valid_types:
        return jsonify({"error": f"Invalid selection_type. Must be one of: {', '.join(valid_types)}"}), 400
    
    db.update_khatma(khatma_id, selection_type=selection_type)
    return jsonify({"success": True, "selection_type": selection_type})

# --- Sahm (Random) Selection API ---
@app.route("/api/join/sahm", methods=["POST"])
def api_join_sahm():
    """Join a khatma using Sahm (random) selection"""
    try:
        d = request.get_json()
        khatma_id = d.get("khatma_id")
        name = d.get("name")
        pin = d.get("pin")
        count = int(d.get("count", 1))  # Number of hizbs to randomly assign
        
        if not name: return jsonify({"error": "الاسم مطلوب"}), 400
        if not khatma_id: return jsonify({"error": "khatma_id مطلوب"}), 400
        
        # Check if this khatma supports sahm selection
        selection_type = db.get_khatma_selection_type(khatma_id)
        if selection_type != 'sahm':
            return jsonify({"error": "هذه الختمة لا تدعم الاختيار العشوائي (السهم)"}), 400
        
        uid, s = db.register_web_user(name, pin, khatma_id)
        if s == "wrong_pin": return jsonify({"error": "الرمز السري غير صحيح"}), 403
        
        # Get available hizbs
        avail = db.get_available(khatma_id)
        if not avail:
            return jsonify({"error": "لا توجد أحزاب متاحة"}), 400
        
        import random
        # Randomly select hizbs
        selected = random.sample(avail, min(count, len(avail)))
        
        booked = []
        failed = []
        for h in selected:
            if db.assign_hizb(uid, int(h), khatma_id):
                booked.append(int(h))
            else:
                failed.append(int(h))
        
        if booked:
            return jsonify({"success": True, "uid": uid, "hizbs": booked, "failed": failed})
        else:
            return jsonify({"error": "فشل في اختيار الأحزاب عشوائياً"}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Internal Server Error: {str(e)}"}), 500

# --- Auto Assignment API ---
@app.route("/api/join/auto", methods=["POST"])
def api_join_auto():
    """Automatically assign N available hizbs to a user"""
    try:
        d = request.get_json()
        khatma_id = d.get("khatma_id")
        name = d.get("name")
        pin = d.get("pin")
        count = int(d.get("count", 1))
        
        if not name: return jsonify({"error": "الاسم مطلوب"}), 400
        if not khatma_id: khatma_id = None
        
        uid, s = db.register_web_user(name, pin, khatma_id)
        if s == "wrong_pin": return jsonify({"error": "الرمز السري غير صحيح"}), 403
        if not uid: return jsonify({"error": "Could not register user"}), 500
        
        # Get available hizbs
        avail = db.get_available(khatma_id)
        if not avail:
            return jsonify({"error": "لا توجد أحزاب متاحة"}), 400
            
        # Get selection type to decide how to assign
        selection_type = db.get_khatma_selection_type(khatma_id) if khatma_id else 'sequential'
        
        if selection_type == 'sahm':
            # Random selection
            import random
            selected = random.sample(avail, min(count, len(avail)))
        else:
            # Sequential/default selection: take the first `count` available hizbs
            selected = sorted(avail)[:min(count, len(avail))]
            
        booked = []
        failed = []
        for h in selected:
            if db.assign_hizb(uid, int(h), khatma_id):
                booked.append(int(h))
            else:
                failed.append(int(h))
                
        if booked:
            return jsonify({"success": True, "uid": uid, "hizbs": booked, "failed": failed})
        else:
            return jsonify({"error": "فشل في تخصيص الأحزاب تلقائياً"}), 400
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"Internal Server Error: {str(e)}"}), 500

if __name__ == "__main__": 
    app.run(port=5000, debug=True)
