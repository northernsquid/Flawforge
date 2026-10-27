# flawforge — INTENTIONALLY VULNERABLE TEST FIXTURE. DO NOT DEPLOY.
# Each route plants exactly one labeled vulnerability for pipeline testing.
import os
import sqlite3
import http.client
import ipaddress
import socket
import ssl
import urllib.parse
import yaml
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
    q = request.args.get("q", "")
    db = get_db()
    sql = "SELECT id, name, description, price FROM products WHERE name LIKE ?"
    rows = db.execute(sql, ('%' + q + '%',)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/admin/users")
def admin_users():
    token = request.headers.get("X-Auth-Token", "")
    expected_token = os.environ.get("ADMIN_TOKEN", "")
    if not expected_token or not hmac.compare_digest(token, expected_token):
        return jsonify({"error": "forbidden"}), 403
    db = get_db()
    rows = db.execute("SELECT id, username, role, email FROM users").fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/load", methods=["POST"])
def load_blob():
    blob = request.get_data()
    return jsonify({"error": "deserialization disabled"}), 400


@app.route("/config", methods=["POST"])
def load_config():
    data = request.get_data(as_text=True)
    try:
        cfg = yaml.safe_load(data)
    except yaml.YAMLError:
        return jsonify({"error": "invalid config"}), 400
    return jsonify({"config": str(cfg)})


# NAT64 (64:ff9b::/96, 64:ff9b:1::/48), IPv4-compatible (::/96), SIIT (::ffff:0:0:0/96)
# and site-local (fec0::/10) can embed a non-global IPv4 yet report is_global.
BLOCKED_IPV6_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    "64:ff9b::/96", "64:ff9b:1::/48", "::/96", "::ffff:0:0:0/96", "fec0::/10",
))


@app.route("/fetch")
def fetch_url():
    url = request.args.get("url", "")

    # Validate URL scheme
    if not url.startswith(("http://", "https://")):
        return jsonify({"error": "invalid scheme"}), 400

    parsed = urllib.parse.urlparse(url)
    hostname = parsed.hostname
    try:
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return jsonify({"error": "invalid host"}), 400
    if not hostname or port == 0:
        return jsonify({"error": "invalid host"}), 400

    # Resolve once; every address must be public. Connect only to a checked IP.
    try:
        addr_info = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        ips = [ipaddress.ip_address(a[4][0].split("%")[0]) for a in addr_info]
    except (OSError, ValueError, UnicodeError):
        return jsonify({"error": "invalid host"}), 400
    if not ips:
        return jsonify({"error": "invalid host"}), 400
    for ip in ips:
        mapped = getattr(ip, "ipv4_mapped", None)
        if not ip.is_global or ip.is_multicast or (mapped and not mapped.is_global):
            return jsonify({"error": "invalid host"}), 400
        if ip.version == 6 and any(ip in net for net in BLOCKED_IPV6_NETWORKS):
            return jsonify({"error": "invalid host"}), 400

    host_header = f"[{hostname}]" if ":" in hostname else hostname
    if parsed.port and parsed.port != (443 if parsed.scheme == "https" else 80):
        host_header += f":{parsed.port}"
    path = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")

    conn = http.client.HTTPConnection(str(ips[0]), port, timeout=5)
    try:
        conn.sock = socket.create_connection((str(ips[0]), port), timeout=5)
        if parsed.scheme == "https":
            conn.sock = ssl.create_default_context().wrap_socket(conn.sock, server_hostname=hostname)
        conn.request("GET", path, headers={"Host": host_header})
        resp = conn.getresponse()
        if 300 <= resp.status < 400:
            return jsonify({"error": "redirect refused"}), 400
        body = resp.read(4096)
        return jsonify({"url": url, "body": body.decode("utf-8", "replace")})
    except Exception:
        return jsonify({"error": "fetch failed"}), 400
    finally:
        conn.close()


@app.route("/")
def index():
    return jsonify({"app": "flawforge", "status": "intentionally vulnerable"})


if __name__ == "__main__":
    seed()
    # Bind to loopback only. Never expose this.
    app.run(host="127.0.0.1", port=5000, debug=False)
