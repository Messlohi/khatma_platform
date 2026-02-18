import requests
import time
import json

BASE_URL = "http://127.0.0.1:5000"

def create_khatma():
    print("Creating Khatma...")
    res = requests.post(f"{BASE_URL}/api/khatma/create", json={
        "name": "Test Duplicate",
        "admin_name": "Admin",
        "admin_pin": "1234",
        "deadline": "2027-12-31"
    })
    print("Create:", res.status_code, res.text)
    return res.json().get("khatma_id")

def join_user(khatma_id, name, hizb=None):
    print(f"Joining '{name}' to hizb {hizb}...")
    res = requests.post(f"{BASE_URL}/api/join", json={
        "khatma_id": khatma_id,
        "name": name,
        "hizb": hizb
    })
    print(f"Join '{name}':", res.status_code, res.text)
    return res.json().get("uid")

def check_users(khatma_id):
    # This requires using the dev key or admin pin. Let's use dev key if possible or admin pin.
    # We can use /api/admin/users with X-Admin-Pin if we know the admin uid?
    # Or just use the dev route assuming I have the key.
    # Let's inspect the database directly or use dev route.
    # Developer route requires X-Dev-Key.
    DEV_KEY = "dev1234" # Default
    res = requests.get(f"{BASE_URL}/api/dev/khatma/details?khatma_id={khatma_id}", headers={"X-Dev-Key": DEV_KEY})
    if res.status_code == 200:
        d = res.json()
        print("Users:", len(d['users']))
        for u in d['users']:
            print(f" - ID: {u['id']}, Name: '{u['name']}', Active: {u['active']}, Completed: {u['completed']}")
    else:
        print("Failed to fetch users:", res.status_code)

import threading

def run_concurrent_join(kid, name, count=5):
    print(f"Concurrent join for '{name}' with {count} threads...")
    threads = []
    
    def worker(i):
        join_user(kid, name, i+10)

    for i in range(count):
        t = threading.Thread(target=worker, args=(i,))
        threads.append(t)
        t.start()
    
    for t in threads:
        t.join()

def run():
    kid = create_khatma()
    if not kid: return

    # 1. Test Arabic Name Normalization
    name = "سمية مصلوحي"
    print(f"Testing Arabic: {name}")
    uid1 = join_user(kid, name, 1)
    uid2 = join_user(kid, name, 2)
    
    if uid1 != uid2:
        print("FAIL: Duplicated user for Arabic name!")
    else:
        print("SUCCESS: Arabic name merged correctly.")

    # 2. Test Race Condition
    print("Testing Race Condition...")
    run_concurrent_join(kid, "RaceUser", 5)
    
    # 3. Test Invisible Characters & Normalization
    print("Testing Invisible Characters & Normalization...")
    
    # Base name
    base = "سمية مصلوحي"
    
    # Validation 1: Zero-width space (u200B)
    name_zwsp = "سمية\u200B مصلوحي"
    print(f"Testing ZWSP: {name_zwsp}")
    uid_zwsp = join_user(kid, name_zwsp, 3)
    if uid1 != uid_zwsp:
        print("FAIL: Duplicated user for ZWSP!")
    else:
        print("SUCCESS: ZWSP merged correctly.")
        
    # Validation 2: Different Alef (Old: Alif Hamza, New: Alif)
    name_alef = "سمية مصلوحى" # Alif Maqsura at end instead of Ya
    print(f"Testing Alif Maqsura: {name_alef}")
    uid_alef = join_user(kid, name_alef, 4)
    if uid1 != uid_alef:
        print("FAIL: Duplicated user for Alif Maqsura!")
    else:
        print("SUCCESS: Alif Maqsura merged correctly.")

    check_users(kid)

if __name__ == "__main__":
    run()
