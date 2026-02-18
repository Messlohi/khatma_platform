import requests
import json

BASE_URL = "https://khatma.pythonanywhere.com"
DEV_KEY = "CCkr_gYmyKBUup2gqKWMskJ1h8bVc9l4"
KHATMA_ID = "iwu8zv"

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

def delete_user(uid):
    print(f"Deleting user {uid}...")
    try:
        res = requests.post(
            f"{BASE_URL}/api/dev/khatma/remove_user",
            json={"uid": uid, "khatma_id": KHATMA_ID},
            headers={"X-Dev-Key": DEV_KEY}
        )
        print(f"Delete result: {res.status_code} - {res.text}")
    except Exception as e:
        print(f"Error deleting: {e}")

def inspect_remote_khatma():
    print(f"Fetching details for Khatma {KHATMA_ID} from {BASE_URL}...")
    try:
        res = requests.get(
            f"{BASE_URL}/api/dev/khatma/details", 
            params={"khatma_id": KHATMA_ID},
            headers={"X-Dev-Key": DEV_KEY}
        )
        
        if res.status_code != 200:
            print(f"Error: {res.status_code} - {res.text}")
            return

        data = res.json()
        users = data.get("users", [])
        print(f"Found {len(users)} users.")
        
        # Analyze for duplicates
        name_map = {}
        for u in users:
            raw_name = u.get("name")
            norm = normalize_arabic(raw_name)
            
            if norm not in name_map:
                name_map[norm] = []
            name_map[norm].append(u)

        print("\n--- Duplicate Analysis ---")
        found_dups = False
        for norm, user_list in name_map.items():
            if len(user_list) > 1:
                found_dups = True
                print(f"\nNormalized Name: '{norm}'")
                
                # Identify which one to keep (the one with progress)
                keep_user = None
                delete_list = []
                
                # Simple logic: Keep the one with most completions/activity
                # If equal, keep the oldest (lowest ID magnitude usually, but IDs are random negative ts)
                # Actually IDs are -timestamp. So smaller absolute value is older?
                # ID = -int(time.time() * 1000000...)
                # So larger absolute value = newer.
                # -177127... is NEWER than -177099...
                # -177099... is OLDER.
                
                # Sort by activity first, then age (older first)
                for u in user_list:
                    score = len(u.get('completed', [])) * 10 + len(u.get('active', []))
                    u['score'] = score
                    # Parse ID to get rough timestamp?
                    # abs(id) is roughly microseconds timestamp
                    u['ts'] = abs(int(u['id']))

                # Sort: detailed sort.
                # We want mainly to keep the one with data.
                user_list.sort(key=lambda x: (x['score'], -x['ts']), reverse=True)
                
                keep_user = user_list[0]
                delete_list = user_list[1:]

                print(f" -> KEEP: ID {keep_user['id']} (Score: {keep_user['score']})")
                for d in delete_list:
                    print(f" -> DELETE: ID {d['id']} (Score: {d['score']})")
                    # Auto-delete if score is 0
                    if d['score'] == 0:
                         delete_user(d['id'])
                    else:
                         print("OBS: Skipping delete of user with progress. Manual merge needed.")

        if not found_dups:
            print("\nNo duplicates found based on normalization rules.")
            
    except Exception as e:
        print(f"Exception: {e}")

if __name__ == "__main__":
    inspect_remote_khatma()
