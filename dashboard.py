"""
WondeX Dashboard
A lightweight Flask web dashboard that runs alongside the Discord bot,
providing real-time stats and a commands reference page.
"""

import time
import threading
from flask import Flask, render_template, jsonify

# ──────────────────────────────────────────────
# Shared state — updated live by the bot
# ──────────────────────────────────────────────
bot_stats = {
    "bot_name": "WondeX",
    "bot_avatar": None,
    "guild_count": 0,
    "member_count": 0,
    "command_count": 0,
    "start_time": time.time(),
    "status": "offline",
    "prefix": "Wa!",
    "latency_ms": None,
    "last_updated": None,
}

# ──────────────────────────────────────────────
# Flask application
# ──────────────────────────────────────────────
app = Flask(__name__, template_folder="templates", static_folder="static")


@app.route("/")
def index():
    return render_template("index.html", prefix=bot_stats.get("prefix", "Wa!"))


@app.route("/commands")
def commands_page():
    return render_template("commands.html", prefix=bot_stats.get("prefix", "Wa!"))


@app.route("/api/stats")
def api_stats():
    stats = dict(bot_stats)
    uptime_seconds = int(time.time() - stats.get("start_time", time.time()))
    hours, remainder = divmod(uptime_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    stats["uptime"] = f"{hours}h {minutes}m {seconds}s"
    stats["last_updated"] = stats.get("last_updated") or int(time.time())
    return jsonify(stats)


# ──────────────────────────────────────────────
# Dashboard thread helper
# ──────────────────────────────────────────────

def _run(host: str, port: int) -> None:
    app.run(host=host, port=port, debug=False, use_reloader=False)


def start_dashboard_thread(host: str = "0.0.0.0", port: int = 5000) -> None:
    """Start the Flask dashboard in a background daemon thread."""
    thread = threading.Thread(target=_run, args=(host, port), daemon=True)
    thread.start()
