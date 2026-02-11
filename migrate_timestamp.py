#!/usr/bin/env python3
"""
Standalone migration script to add timestamp columns to hizb_assignments and completed_hizb tables.
Run this on PythonAnywhere if you get: "table hizb_assignments has no column named timestamp"

Usage:
    python3 migrate_timestamp.py
"""

import sqlite3
import sys

DB_PATH = '/home/khatma/khatma_platform/khatma.db'  # PythonAnywhere database path

def migrate():
    """Add timestamp columns to activity tracking tables"""
    try:
        print("Connecting to database:", DB_PATH)
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # Check if columns already exist
        print("\nChecking hizb_assignments table...")
        cursor = c.execute("PRAGMA table_info(hizb_assignments)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if 'timestamp' not in columns:
            print("  ✓ Adding timestamp column to hizb_assignments...")
            c.execute("ALTER TABLE hizb_assignments ADD COLUMN timestamp DATETIME DEFAULT CURRENT_TIMESTAMP")
            print("  ✅ Added successfully!")
        else:
            print("  ℹ️  timestamp column already exists")
        
        print("\nChecking completed_hizb table...")
        cursor = c.execute("PRAGMA table_info(completed_hizb)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if 'timestamp' not in columns:
            print("  ✓ Adding timestamp column to completed_hizb...")
            c.execute("ALTER TABLE completed_hizb ADD COLUMN timestamp DATETIME DEFAULT CURRENT_TIMESTAMP")
            print("  ✅ Added successfully!")
        else:
            print("  ℹ️  timestamp column already exists")
        
        conn.commit()
        conn.close()
        
        print("\n✅ Migration completed successfully!")
        print("Now reload your web app on PythonAnywhere.")
        return 0
        
    except Exception as e:
        print(f"\n❌ Migration failed: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(migrate())
