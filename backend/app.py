from flask import Flask, render_template, send_from_directory
import sqlite3, os

# Get ROOT directory (co2DigitalTwin-Main)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Path of admin.html OUTSIDE backend folder
TEMPLATE_DIR = BASE_DIR   # because admin.html is directly in main folder

# Path of static folder (works if static is also in main folder)
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)

# Database paths
ACTIVITIES_DB = os.path.join(BASE_DIR, "activities.db")

def get_db():
    conn = sqlite3.connect(ACTIVITIES_DB)
    conn.row_factory = sqlite3.Row
    return conn

@app.route("/")
def admin_dashboard():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM activities ORDER BY id DESC")
    rows = cur.fetchall()
    conn.close()
    return render_template("admin.html", activities=rows)

if __name__ == "__main__":
    print("Loading admin.html from:", TEMPLATE_DIR)
    print("Using activities database:", ACTIVITIES_DB)
    app.run(host="0.0.0.0", port=5001, debug=True)