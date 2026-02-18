import sqlite3
import time
import os

DB_FILE = "khatma.db"

def normalize_arabic(text):
    text = text or ""
    # Remove tashkeel
    tashkeel = ["\u064B", "\u064C", "\u064D", "\u064E", "\u064F", "\u0650", "\u0651", "\u0652"]
    for t in tashkeel: text = text.replace(t, "")
    
    # Unify Alef
    text = text.replace("\u0622", "\u0627") # Alif Madda
    text = text.replace("\u0623", "\u0627") # Alif Hamza Above
    text = text.replace("\u0625", "\u0627") # Alif Hamza Below
    
    # Unify Ya / Alif Maqsura
    text = text.replace("\u0649", "\u064A") # Alif Maqsura -> Ya
    
    # Unify Taa Marbuta
    text = text.replace("\u0629", "\u0647") # Taa Marbuta -> Ha
    
    # Remove invisible chars
    text = text.replace("\u200B", "").replace("\u200E", "").replace("\u200F", "")
    
    # Normalize spaces
    return " ".join(text.split())

def fix_duplicates():
    print(f"Connecting to {DB_FILE}...")
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    # Get all Khatmas
    khatmas = c.execute("SELECT id, name FROM khatmas").fetchall()
    print(f"Scanning {len(khatmas)} khatmas...")
    
    total_fixed = 0
    
    for kid, kname in khatmas:
        users = c.execute("SELECT id, full_name, web_pin FROM users WHERE khatma_id = ?", (kid,)).fetchall()
        
        name_map = {}
        for u in users:
            uid, raw_name, pin = u
            norm = normalize_arabic(raw_name)
            if norm not in name_map: name_map[norm] = []
            name_map[norm].append({'id': uid, 'name': raw_name, 'pin': pin})
            
        for norm, ulist in name_map.items():
            if len(ulist) > 1:
                print(f"Processing Duplicate in '{kname}' ({kid}): '{norm}' -> {len(ulist)} users")
                
                # Fetch progress for each
                for u in ulist:
                    u['active'] = [r[0] for r in c.execute("SELECT hizb_number FROM hizb_assignments WHERE user_id = ?", (u['id'],)).fetchall()]
                    u['completed'] = [r[0] for r in c.execute("SELECT hizb_number FROM completed_hizb WHERE user_id = ?", (u['id'],)).fetchall()]
                    u['score'] = len(u['completed']) * 10 + len(u['active'])
                    u['ts'] = abs(int(u['id'])) # Newer ID has higher absolute timestamp
                
                # Sort: Keep highest score, then newest TS (highest abs val)?
                # Actually, usually keeping the OLDEST ID is better for stability if references exist elsewhere (chats etc),
                # BUT here we want to keep the one with progress.
                # If equal progress, keep the one created EARLIER (smaller abs timestamp, assuming ID = -timestamp).
                # Wait: -177127... is NEWER (larger abs value) than -177099... (smaller abs value).
                # So we prefer the one with progress. If same, prefer NEWEST? Or OLDEST?
                # Let's prefer the one with progress. If tied, prefer the OLDEST (smaller ID magnitude) to be safe?
                # Actually, let's prefer the NEWEST if tied, assuming user just re-joined.
                
                ulist.sort(key=lambda x: (x['score'], x['ts']), reverse=True)
                
                keep = ulist[0]
                others = ulist[1:]
                
                print(f"  -> KEEP: {keep['id']} ({keep['name']}) [Score: {keep['score']}]")
                
                for other in others:
                    print(f"  -> MERGE/DELETE: {other['id']} ({other['name']}) [Score: {other['score']}]")
                    uid_to_remove = other['id']
                    
                    # Move any assignments to the kept user (if slot free)
                    for h in other['active']:
                        if h not in keep['active']:
                            print(f"     Moving active hizb {h}...")
                            try:
                                c.execute("UPDATE hizb_assignments SET user_id = ? WHERE user_id = ? AND hizb_number = ?", 
                                          (keep['id'], uid_to_remove, h))
                                keep['active'].append(h)
                            except: pass # Conflict, ignore
                            
                    # Move completions
                    for h in other['completed']:
                        if h not in keep['completed']:
                             print(f"     Moving completed hizb {h}...")
                             try:
                                 c.execute("UPDATE completed_hizb SET user_id = ? WHERE user_id = ? AND hizb_number = ?", 
                                           (keep['id'], uid_to_remove, h))
                                 keep['completed'].append(h)
                             except: pass

                    # Delete the duplicate user
                    c.execute("DELETE FROM users WHERE id = ?", (uid_to_remove,))
                    c.execute("DELETE FROM hizb_assignments WHERE user_id = ?", (uid_to_remove,)) # Cleanup remaining
                    c.execute("DELETE FROM completed_hizb WHERE user_id = ?", (uid_to_remove,)) # Cleanup remaining
                    total_fixed += 1
                    
    conn.commit()
    print(f"Done. Fixed {total_fixed} duplicate users.")
    
    # Try creating index now
    try:
        print("Creating Unique Index...")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_khatma_user_name ON users(khatma_id, full_name)")
        print("Index created successfully.")
    except Exception as e:
        print(f"Failed to create index: {e}")
        
    conn.close()

if __name__ == "__main__":
    if not os.path.exists(DB_FILE):
        print(f"Database {DB_FILE} not found.")
    else:
        fix_duplicates()
