import sqlite3
import os

db_path = 'khatma.db'
if not os.path.exists(db_path):
    print(f"Error: {db_path} not found")
    exit(1)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

def add_column(table, column, definition):
    try:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        print(f"Successfully added {column} to {table}")
    except sqlite3.OperationalError as e:
        if "duplicate column name" in str(e).lower():
            print(f"Column {column} already exists in {table}")
        else:
            print(f"Error adding {column} to {table}: {e}")

add_column('hizb_assignments', 'timestamp', "DATETIME")
add_column('completed_hizb', 'timestamp', "DATETIME")

conn.commit()
conn.close()
print("Migration finished.")
