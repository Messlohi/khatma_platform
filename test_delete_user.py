
import requests
import json
import sqlite3

BASE_URL = "http://127.0.0.1:5000"
DB_PATH = "khatmas.db"

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def setup_test_data():
    conn = get_db_connection()
    c = conn.cursor()
    # Create a test khatma
    khatma_id = "test_del_khatma"
    c.execute("INSERT OR IGNORE INTO khatmas (id, name, created_at) VALUES (?, ?, ?)", (khatma_id, "Test Delete", "2024-01-01"))
    
    # Create admin user
    c.execute("INSERT OR IGNORE INTO users (name, khatma_id, is_admin) VALUES (?, ?, 1)", ("AdminUser", khatma_id))
    admin_id = c.execute("SELECT id FROM users WHERE name=? AND khatma_id=?", ("AdminUser", khatma_id)).fetchone()[0]
    
    # Create victim user
    c.execute("INSERT OR IGNORE INTO users (name, khatma_id) VALUES (?, ?)", ("VictimUser", khatma_id))
    victim_id = c.execute("SELECT id FROM users WHERE name=? AND khatma_id=?", ("VictimUser", khatma_id)).fetchone()[0]
    
    conn.commit()
    conn.close()
    return khatma_id, admin_id, victim_id

def test_delete():
    khatma_id, admin_id, victim_id = setup_test_data()
    print(f"Testing delete with Admin: {admin_id}, Victim: {victim_id}, Khatma: {khatma_id}")
    
    # 1. Try to delete as admin
    url = f"{BASE_URL}/api/khatma/delete_user"
    payload = {
        "uid": victim_id,
        "khatma_id": khatma_id,
        "requester_uid": admin_id
    }
    
    try:
        resp = requests.post(url, json=payload)
        print(f"Response: {resp.status_code}, {resp.text}")
        if resp.status_code == 200 and resp.json().get("success"):
            print("✅ Delete success")
        else:
            print("❌ Delete failed")
            
        # Verify DB
        conn = get_db_connection()
        exists = conn.execute("SELECT 1 FROM users WHERE id=?", (victim_id,)).fetchone()
        if not exists:
            print("✅ User removed from DB")
        else:
            print("❌ User still in DB")
            
    except Exception as e:
        print(f"❌ Exception: {e}")

if __name__ == "__main__":
    test_delete()
