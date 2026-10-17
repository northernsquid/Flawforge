# flawforge — INTENTIONALLY VULNERABLE TEST FIXTURE. DO NOT DEPLOY.
# Each route plants exactly one labeled vulnerability for pipeline testing.
import os
import sqlite3
import pickle
import base64
import urllib.request
import yaml
from flask import Flask, request, jsonify, g

DB_PATH = "flawforge.db"

# VULN: HARDCODED SECRET — obvious placeholder, not a real credential.
API_SECRET = "sk-flawforge-FAKE-DO-NOT-USE-0000000000000000"  # VULN: hardcoded secret

app = Flask(__name__)
app.config["SECRET_KEY"] = API_SECRET


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def seed():
    """Inline seeding so the app runs with one command."""
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    db = sqlite3.connect(DB_PATH)
    db.executescript(
        """
        CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            username TEXT,
            role TEXT,
            email TEXT
        );
        CREATE TABLE products (
            id INTEGER PRIMARY KEY,
            name TEXT,
            description TEXT,
            price REAL
        );
        INSERT INTO users (username, role, email) VALUES
            ('alice', 'user',  'alice@example.com'),
            ('bob',   'user',  'bob@example.com'),
            ('root',  'admin', 'root@example.com');
        INSERT INTO products (name, description, price) VALUES
            ('Widget', 'A basic widget', 9.99),
            ('Gadget', 'A fancy gadget', 19.99),
            ('Gizmo',  'A mysterious gizmo', 4.99);
        """
    )
    db.commit()
    db.close()


@app.route("/search")
def search():
    # VULN: SQLi — raw string-formatted query, user input concatenated directly.
    q = request.args.get("q", "")
    db = get_db()
    sql = "SELECT id, name, description, price FROM products WHERE name LIKE '%%%s%%'" % q
    rows = db.execute(sql).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/admin/users")
def admin_users():
    # VULN: BROKEN AUTHZ — trusts a client-supplied header for the access decision.
    role = request.headers.get("X-Role", "user")
    if role != "admin":
        return jsonify({"error": "forbidden"}), 403
    db = get_db()
    rows = db.execute("SELECT id, username, role, email FROM users").fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/load", methods=["POST"])
def load_blob():
    # VULN: INSECURE DESERIALIZATION — pickle.loads on attacker-controlled bytes.
    blob = request.get_data()
    obj = pickle.loads(base64.b64decode(blob))  # VULN: pickle.loads of user input
    return jsonify({"loaded": str(obj)})


@app.route("/config", methods=["POST"])
def load_config():
    # VULN: INSECURE DESERIALIZATION — yaml.load with unsafe Loader on user input.
    data = request.get_data(as_text=True)
    cfg = yaml.load(data, Loader=yaml.Loader)  # VULN: unsafe yaml.load
    return jsonify({"config": str(cfg)})


@app.route("/fetch")
def fetch_url():
    # VULN: SSRF — fetches any user-supplied URL with no destination validation.
    url = request.args.get("url", "")
    with urllib.request.urlopen(url, timeout=5) as resp:  # VULN: SSRF
        body = resp.read(4096)
    return jsonify({"url": url, "body": body.decode("utf-8", "replace")})


@app.route("/")
def index():
    return jsonify({"app": "flawforge", "status": "intentionally vulnerable"})


if __name__ == "__main__":
    seed()
    # Bind to loopback only. Never expose this.
    app.run(host="127.0.0.1", port=5000, debug=False)
