import sqlite3

db_path = "activities.db"
print(f"Initializing {db_path}...")
conn = sqlite3.connect(db_path)
cur = conn.cursor()
cur.execute("""
    CREATE TABLE IF NOT EXISTS activities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        city TEXT,
        station TEXT,
        intervention TEXT,
        efficiency REAL,
        base_co2 REAL,
        after_co2 REAL
    );
""")
conn.commit()
conn.close()
print("Done.")
