# flawforge — INTENTIONALLY VULNERABLE TEST FIXTURE. DO NOT DEPLOY.
# Each route plants exactly one labeled vulnerability for pipeline testing.
import os
import sqlite3
import pickle
import base64
import urllib.request
import yaml
import re
import hmac
from flask import Flask, request, jsonify, g

DB_PATH = "flawforge.db"

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY")


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
    sql = "SELECT id, name, description, price FROM products WHERE name LIKE ?"
    rows = db.execute(sql, ('%' + q + '%',)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/admin/users")
def admin_users():
    # VULN: BROKEN AUTHZ — trusts a client-supplied header for the access decision.
    token = request.headers.get("X-Auth-Token", "")
    expected_token = os.environ.get("ADMIN_TOKEN", "")
    if not expected_token or not hmac.compare_digest(token, expected_token):
        return jsonify({"error": "forbidden"}), 403
    db = get_db()
    rows = db.execute("SELECT id, username, role, email FROM users").fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/load", methods=["POST"])
def load_blob():
    # VULN: INSECURE DESERIALIZATION — pickle.loads on attacker-controlled bytes.
    blob = request.get_data()
    return jsonify({"error": "deserialization disabled"}), 400


@app.route("/config", methods=["POST"])
def load_config():
    # VULN: INSECURE DESERIALIZATION — yaml.load with unsafe Loader on user input.
    data = request.get_data(as_text=True)
    cfg = yaml.safe_load(data)  # VULN: unsafe yaml.load
    return jsonify({"config": str(cfg)})


@app.route("/fetch")
def fetch_url():
    # VULN: SSRF — fetches any user-supplied URL with no destination validation.
    url = request.args.get("url", "")
    
    # Validate URL scheme
    if not url.startswith(("http://", "https://")):
        return jsonify({"error": "invalid scheme"}), 400
    
    # Parse URL to check for private/loopback addresses
    import urllib.parse
    parsed = urllib.parse.urlparse(url)
    
    # Check for loopback/private ranges
    if parsed.hostname:
        try:
            import ipaddress
            import socket
            
            # Resolve hostname and check all resolved addresses
            try:
                addr_info = socket.getaddrinfo(parsed.hostname, None)
                for addr_tuple in addr_info:
                    addr = addr_tuple[4][0]
                    ip_addr = ipaddress.ip_address(addr)
                    if (ip_addr.is_loopback or 
                        ip_addr.is_private or 
                        ip_addr.is_link_local or 
                        ip_addr.is_multicast or
                        ip_addr.is_reserved):
                        return jsonify({"error": "invalid host"}), 400
            except Exception:
                # If DNS resolution fails, reject the request
                return jsonify({"error": "invalid host"}), 400
                
        except ValueError:
            # If hostname is not an IP and DNS resolution fails, reject it
            return jsonify({"error": "invalid host"}), 400
    
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:  # nosemgrep
            body = resp.read(4096)
        return jsonify({"url": url, "body": body.decode("utf-8", "replace")})
    except Exception:
        return jsonify({"error": "fetch failed"}), 400


@app.route("/")
def index():
    return jsonify({"app": "flawforge", "status": "intentionally vulnerable"})


if __name__ == "__main__":
    seed()
    # Bind to loopback only. Never expose this.
    app.run(host="127.0.0.1", port=5000, debug=False)
