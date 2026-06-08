"""
=============================================================
  1. URL Shortener Service (TinyURL)
  Run: python 01_url_shortener.py
  Test: curl -X POST http://localhost:5001/api/v1/shorten -H "Content-Type: application/json" -d "{\"long_url\":\"https://example.com/very/long/path\"}"
        curl -v http://localhost:5001/<short_code>
        curl http://localhost:5001/api/v1/stats/<short_code>
=============================================================
"""
import time
import hashlib
import threading
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, redirect

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Storage (in-memory; production would use PostgreSQL + Redis)
# ---------------------------------------------------------------------------
url_store: dict[str, dict] = {}        # short_code -> {long_url, created_at, expires_at, user}
analytics_store: dict[str, list] = {}  # short_code -> [{timestamp, ip, user_agent}]
counter_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Base62 encoder
# ---------------------------------------------------------------------------
BASE62 = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

def encode_base62(num: int) -> str:
    if num == 0:
        return BASE62[0]
    result = []
    while num > 0:
        result.append(BASE62[num % 62])
        num //= 62
    return "".join(reversed(result))

# ---------------------------------------------------------------------------
# Snowflake-like ID generator
# ---------------------------------------------------------------------------
class SnowflakeIDGenerator:
    EPOCH = 1_700_000_000_000  # custom epoch (ms)

    def __init__(self, machine_id: int = 1):
        self.machine_id = machine_id & 0x3FF   # 10 bits
        self.sequence = 0
        self.last_ts = -1
        self._lock = threading.Lock()

    def _current_ms(self) -> int:
        return int(time.time() * 1000)

    def next_id(self) -> int:
        with self._lock:
            ts = self._current_ms()
            if ts == self.last_ts:
                self.sequence = (self.sequence + 1) & 0xFFF
                if self.sequence == 0:
                    while ts <= self.last_ts:
                        ts = self._current_ms()
            else:
                self.sequence = 0
            self.last_ts = ts
            uid = ((ts - self.EPOCH) << 22) | (self.machine_id << 12) | self.sequence
            return uid

id_gen = SnowflakeIDGenerator(machine_id=1)

# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------
def generate_short_code(long_url: str) -> str:
    uid = id_gen.next_id()
    return encode_base62(uid)[:7]

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/api/v1/shorten", methods=["POST"])
def shorten():
    data = request.get_json(force=True)
    long_url = data.get("long_url")
    if not long_url:
        return jsonify({"error": "long_url is required"}), 400

    custom_alias = data.get("custom_alias")
    ttl_days = data.get("ttl_days", 365 * 5)

    if custom_alias:
        if custom_alias in url_store:
            return jsonify({"error": "Alias already taken"}), 409
        short_code = custom_alias
    else:
        short_code = generate_short_code(long_url)

    url_store[short_code] = {
        "long_url": long_url,
        "created_at": datetime.utcnow().isoformat(),
        "expires_at": (datetime.utcnow() + timedelta(days=ttl_days)).isoformat(),
        "user": data.get("user", "anonymous"),
    }
    analytics_store[short_code] = []

    return jsonify({
        "short_url": f"http://localhost:5001/{short_code}",
        "short_code": short_code,
        "expires_at": url_store[short_code]["expires_at"],
    }), 201


@app.route("/<short_code>")
def redirect_url(short_code: str):
    entry = url_store.get(short_code)
    if not entry:
        return jsonify({"error": "URL not found"}), 404

    if datetime.fromisoformat(entry["expires_at"]) < datetime.utcnow():
        return jsonify({"error": "URL expired"}), 410

    # Record analytics
    analytics_store.setdefault(short_code, []).append({
        "timestamp": datetime.utcnow().isoformat(),
        "ip": request.remote_addr,
        "user_agent": request.headers.get("User-Agent", ""),
        "referrer": request.headers.get("Referer", ""),
    })

    return redirect(entry["long_url"], code=302)


@app.route("/api/v1/stats/<short_code>")
def stats(short_code: str):
    entry = url_store.get(short_code)
    if not entry:
        return jsonify({"error": "URL not found"}), 404

    clicks = analytics_store.get(short_code, [])
    return jsonify({
        "short_code": short_code,
        "long_url": entry["long_url"],
        "created_at": entry["created_at"],
        "total_clicks": len(clicks),
        "recent_clicks": clicks[-10:],
    })


@app.route("/api/v1/urls")
def list_urls():
    return jsonify({
        code: {"long_url": v["long_url"], "clicks": len(analytics_store.get(code, []))}
        for code, v in url_store.items()
    })


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("  URL Shortener Service")
    print("  http://localhost:5001")
    print()
    print("  POST /api/v1/shorten  {\"long_url\": \"...\"}  → short link")
    print("  GET  /<code>                                → redirect")
    print("  GET  /api/v1/stats/<code>                   → analytics")
    print("=" * 60)
    app.run(port=5001, debug=True)
