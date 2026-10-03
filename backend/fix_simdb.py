import sqlite3
import os

db_path = os.path.join(os.path.dirname(__file__), 'data', 'trips.db')
conn = sqlite3.connect(db_path)

print("Fixing database...")
# 1. Expire all bad simulations (where target_chat_id is not an integer or is suspiciously small)
# Since sqlite typeof on target_chat_id might be tricky if it was saved as a string, let's just do it directly.
# Wait, let's just expire everything to be safe, or just the ones we know are bad.
conn.execute("UPDATE simulations SET status='expired' WHERE typeof(target_chat_id) != 'integer'")
try:
    conn.execute("UPDATE simulations SET status='expired' WHERE CAST(target_chat_id AS INTEGER) < 1000")
except:
    pass

# Expire anything that was targeted at strings
conn.execute("UPDATE simulations SET status='expired' WHERE target_chat_id = 'Technical fault' OR target_chat_id = 'Air traffic congestion' OR target_chat_id = '30'")

# 2. Force ALL trips to active status so the scheduler monitors them
conn.execute("UPDATE trips_multi SET status='active' WHERE status='upcoming'")
conn.commit()

print("Active trips now:")
for row in conn.execute("SELECT id, chat_id, destination, status FROM trips_multi WHERE status='active'").fetchall():
    print(f"  ID: {row[0]}, Chat ID: {row[1]}, Dest: {row[2]}, Status: {row[3]}")

conn.close()
print("Done.")
