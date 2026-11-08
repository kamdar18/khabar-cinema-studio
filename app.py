"""Khabar Cinema Studio: upload a reel, review it, approve it, post it to 3 platforms."""
import base64
import binascii
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from functools import wraps

from flask import (Flask, abort, flash, g, redirect, render_template, request, send_from_directory,
                   session, url_for)

import publishers as pub

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("DATA_DIR", os.path.join(BASE, "data"))
MEDIA = os.path.join(DATA, "media")
DB_PATH = os.path.join(DATA, "studio.db")
os.makedirs(MEDIA, exist_ok=True)

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
    MAX_CONTENT_LENGTH=int(os.environ.get("MAX_UPLOAD_MB", "300")) * 1024 * 1024,
    MAX_FORM_MEMORY_SIZE=4 * 1024 * 1024,  # room for the cover JPEG sent from the reel page
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 30,
)
PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
API_TOKEN = os.environ.get("API_TOKEN", "")
PLATFORMS = ("instagram", "facebook", "youtube")
LABEL = {"instagram": "Instagram", "facebook": "Facebook", "youtube": "YouTube"}

SECRET_KEYS = {"meta_page_token", "yt_client_secret", "yt_refresh_token"}
SETTING_KEYS = ["meta_page_id", "meta_page_token", "ig_user_id", "graph_version",
                "yt_client_id", "yt_client_secret", "yt_refresh_token", "yt_privacy",
                "caption_footer", "default_hashtags"]
DEFAULTS = {
    "graph_version": "v23.0",
    "yt_privacy": "private",
    "caption_footer": "ℹ️ इस रील में AI वॉइस और AI की मदद ली गई है।",
    "default_hashtags": "#KhabarCinema #NewsInHindi #ReelsIndia #TrendingNow",
}


# ---------------------------------------------------------------- database
def db():
    if "db" not in g:
        g.db = connect()
    return g.db


def connect():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


@app.teardown_appcontext
def _close(_):
    con = g.pop("db", None)
    if con:
        con.close()


def init_db():
    con = connect()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS reels(
        id TEXT PRIMARY KEY, created TEXT, title TEXT, caption TEXT, hashtags TEXT,
        yt_title TEXT, yt_description TEXT, video TEXT, size INTEGER,
        status TEXT DEFAULT 'pending', approved TEXT, platforms TEXT DEFAULT '{}');
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS log(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, reel TEXT, msg TEXT);
    """)
    cols = {r[1] for r in con.execute("PRAGMA table_info(reels)")}
    if "cover_sec" not in cols:  # added later: which frame becomes the cover
        con.execute("ALTER TABLE reels ADD COLUMN cover_sec REAL")
    con.commit()
    con.close()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_settings(con=None):
    con = con or db()
    s = dict(DEFAULTS)
    s.update({r["key"]: r["value"] for r in con.execute("SELECT key, value FROM settings")})
    return s


def set_setting(con, key, value):
    con.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value))


def log(con, reel, msg):
    con.execute("INSERT INTO log(at, reel, msg) VALUES(?, ?, ?)", (now(), reel, msg))


# ---------------------------------------------------------------- auth
_fails = {}


def login_required(f):
    @wraps(f)
    def wrap(*a, **kw):
        if session.get("ok"):
            return f(*a, **kw)
        auth = request.headers.get("Authorization", "")
        if API_TOKEN and hmac.compare_digest(auth, f"Bearer {API_TOKEN}"):
            return f(*a, **kw)
        return redirect(url_for("login", next=request.path))
    return wrap


@app.before_request
def _csrf():
    if request.method == "POST" and session.get("ok") and request.endpoint not in ("login", "static"):
        if not hmac.compare_digest(request.form.get("csrf", ""), session.get("csrf", "")):
            abort(400, "Form expired, page reload karo")


@app.context_processor
def _ctx():
    return {"csrf": session.get("csrf", ""), "LABEL": LABEL, "PLATFORMS": PLATFORMS}


@app.route("/login", methods=["GET", "POST"])
def login():
    if not PASSWORD:
        return "ADMIN_PASSWORD .env file ma set karo, pachhi service restart karo.", 500
    ip = request.headers.get("X-Real-IP", request.remote_addr)
    if request.method == "POST":
        fails = [t for t in _fails.get(ip, []) if t > time.time() - 900]
        if len(fails) >= 5:
            flash("Ghana khota prayatno. 15 minute pachhi try karo.")
            return render_template("login.html"), 429
        if hmac.compare_digest(request.form.get("password", ""), PASSWORD):
            session.clear()
            session.permanent = True
            session["ok"] = True
            session["csrf"] = secrets.token_hex(16)
            _fails.pop(ip, None)
            nxt = request.args.get("next", "/")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else "/")
        _fails[ip] = fails + [time.time()]
        flash("Password khoto che")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------- pages
def load(row):
    r = dict(row)
    r["platforms"] = json.loads(r.get("platforms") or "{}")
    return r


@app.route("/")
@login_required
def index():
    tab = request.args.get("tab", "pending")
    q = {"pending": "status='pending'", "done": "status IN ('posting','posted','partial','failed')",
         "rejected": "status='rejected'"}.get(tab, "status='pending'")
    reels = [load(r) for r in db().execute(f"SELECT * FROM reels WHERE {q} ORDER BY created DESC LIMIT 100")]
    counts = {r["status"]: r["n"] for r in db().execute("SELECT status, COUNT(*) n FROM reels GROUP BY status")}
    return render_template("index.html", reels=reels, tab=tab, counts=counts, settings=get_settings())


@app.route("/new", methods=["GET", "POST"])
@login_required
def new():
    s = get_settings()
    if request.method == "POST":
        f = request.files.get("video")
        if not f or not f.filename.lower().endswith((".mp4", ".mov")):
            flash("MP4 video pasand karo")
            return redirect(url_for("new"))
        rid = uuid.uuid4().hex[:12]
        name = f"{rid}.mp4"
        path = os.path.join(MEDIA, name)
        f.save(path)
        title = request.form.get("title", "").strip() or "Untitled"
        con = db()
        con.execute(
            "INSERT INTO reels(id, created, title, caption, hashtags, yt_title, yt_description, video, size) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (rid, now(), title, request.form.get("caption", ""), request.form.get("hashtags") or s["default_hashtags"],
             request.form.get("yt_title") or title, request.form.get("yt_description", ""), name, os.path.getsize(path)))
        log(con, rid, "uploaded")
        con.commit()
        if request.headers.get("Authorization"):
            return {"id": rid, "url": url_for("reel", rid=rid, _external=True)}
        return redirect(url_for("reel", rid=rid))
    return render_template("new.html", settings=s)


@app.route("/reel/<rid>", methods=["GET", "POST"])
@login_required
def reel(rid):
    con = db()
    row = con.execute("SELECT * FROM reels WHERE id=?", (rid,)).fetchone()
    if not row:
        abort(404)
    r = load(row)
    if request.method == "POST":
        action = request.form.get("action")
        fields = ("title", "caption", "hashtags", "yt_title", "yt_description")
        con.execute(f"UPDATE reels SET {', '.join(f + '=?' for f in fields)} WHERE id=?",
                    [request.form.get(f, r[f]) for f in fields] + [rid])
        save_cover(con, r, request.form)
        if action == "approve":
            chosen = [p for p in PLATFORMS if request.form.get("p_" + p)]
            s = get_settings(con)
            missing = {p: missing_setup(s, p) for p in chosen if missing_setup(s, p)}
            if not chosen:
                flash("Ochha ma ochhu ek platform pasand karo")
            elif missing:
                flash("Haju post nahi thay: " + " · ".join(f"{LABEL[p]}: {m}" for p, m in missing.items()))
            elif r["status"] == "posting":
                flash("Aa reel post thai rahi che")
            else:
                plat = {p: {"state": "queued"} for p in chosen}
                con.execute("UPDATE reels SET status='posting', approved=?, platforms=? WHERE id=?",
                            (now(), json.dumps(plat), rid))
                log(con, rid, "approved: " + ", ".join(chosen))
                con.commit()
                threading.Thread(target=run_publish, args=(rid, chosen), daemon=True).start()
                flash("Approve thayu! Posting chalu che…")
                return redirect(url_for("reel", rid=rid))
        elif action == "reject":
            con.execute("UPDATE reels SET status='rejected' WHERE id=?", (rid,))
            log(con, rid, "rejected")
            flash("Reel reject kari")
        elif action == "restore":
            con.execute("UPDATE reels SET status='pending' WHERE id=?", (rid,))
        elif action == "retry":
            failed = [p for p, v in r["platforms"].items() if v.get("state") == "failed"]
            if failed:
                for p in failed:
                    r["platforms"][p] = {"state": "queued"}
                con.execute("UPDATE reels SET status='posting', platforms=? WHERE id=?", (json.dumps(r["platforms"]), rid))
                con.commit()
                threading.Thread(target=run_publish, args=(rid, failed), daemon=True).start()
                flash("Fari try thai rahyu che…")
        elif action == "delete":
            for f in (os.path.join(MEDIA, r["video"]), cover_path(r)):
                try:
                    os.remove(f)
                except OSError:
                    pass
            con.execute("DELETE FROM reels WHERE id=?", (rid,))
            log(con, rid, "deleted")
            con.commit()
            flash("Reel delete thai")
            return redirect(url_for("index"))
        else:
            flash("Save thayu")
        con.commit()
        return redirect(url_for("reel", rid=rid))
    s = get_settings()
    logs = con.execute("SELECT * FROM log WHERE reel=? ORDER BY id DESC LIMIT 30", (rid,)).fetchall()
    has_cover = os.path.exists(cover_path(r))
    return render_template("reel.html", r=r, settings=s, preview=full_caption(r, s), logs=logs,
                           has_cover=has_cover, default_cover=DEFAULT_COVER_SEC)


DEFAULT_COVER_SEC = 2.0  # never the first frame: many reels open on black


def cover_path(r):
    return os.path.join(MEDIA, r["video"].rsplit(".", 1)[0] + ".jpg")


def save_cover(con, r, form):
    """Store the cover second and the JPEG the browser grabbed from that frame."""
    try:
        sec = float(form.get("cover_sec", ""))
    except ValueError:
        return
    sec = max(0.0, min(sec, 600.0))
    con.execute("UPDATE reels SET cover_sec=? WHERE id=?", (sec, r["id"]))
    jpg = form.get("cover_jpg", "")
    head = "data:image/jpeg;base64,"
    if jpg.startswith(head) and len(jpg) < 4_000_000:
        try:
            data = base64.b64decode(jpg[len(head):], validate=True)
        except (ValueError, binascii.Error):
            return
        if data[:3] == b"\xff\xd8\xff":
            with open(cover_path(r), "wb") as f:
                f.write(data)


@app.route("/media/<name>")
@login_required
def media(name):
    if not re.fullmatch(r"[0-9a-f]{12}\.(mp4|jpg)", name):
        abort(404)
    return send_from_directory(MEDIA, name, conditional=True,
                               as_attachment=request.args.get("dl") == "1")


@app.route("/status/<rid>")
@login_required
def status(rid):
    row = db().execute("SELECT status, platforms FROM reels WHERE id=?", (rid,)).fetchone()
    if not row:
        abort(404)
    return {"status": row["status"], "platforms": json.loads(row["platforms"] or "{}")}


# ---------------------------------------------------------------- publishing
def missing_setup(s, platform):
    """Return a message if this platform is not configured yet, else None."""
    if platform in ("instagram", "facebook"):
        if not s.get("meta_page_id") or not s.get("meta_page_token"):
            return "Settings ma Facebook Page ID ane Page token naakho"
    if platform == "youtube" and not s.get("yt_refresh_token"):
        return "Settings ma YouTube connect karo"
    return None


def full_caption(r, s):
    parts = [r["caption"].strip(), r["hashtags"].strip(), s.get("caption_footer", "").strip()]
    return "\n\n".join(p for p in parts if p)


_lock = threading.Lock()


def run_publish(rid, platforms):
    with _lock:  # one upload at a time: kind to a 1 GB server
        con = connect()
        try:
            s = get_settings(con)
            r = load(con.execute("SELECT * FROM reels WHERE id=?", (rid,)).fetchone())
            path = os.path.join(MEDIA, r["video"])
            caption = full_caption(r, s)
            cover_sec = r.get("cover_sec")
            cover_sec = DEFAULT_COVER_SEC if cover_sec is None else cover_sec
            tags = [t.lstrip("#") for t in r["hashtags"].split() if t.startswith("#")]
            yt_desc = "\n\n".join(p for p in [r["yt_description"] or r["caption"], r["hashtags"],
                                              s.get("caption_footer", "")] if p.strip())
            for p in platforms:
                state = {"state": "posting", "at": now()}
                _set_platform(con, rid, p, state)
                try:
                    if p == "instagram":
                        res = pub.publish_instagram(s, path, caption, int(cover_sec * 1000))
                    elif p == "facebook":
                        res = pub.publish_facebook(s, path, caption, cover_path(r))
                        if res.get("note"):
                            log(con, rid, "facebook: " + res["note"])
                    else:
                        title = r["yt_title"] or r["title"]
                        if "#shorts" not in title.lower():
                            title = (title[:90] + " #Shorts")
                        res = pub.publish_youtube(s, path, title, yt_desc, tags, s.get("yt_privacy") or "private")
                    state = {"state": "posted", "at": now(), "url": res.get("url"), "id": res.get("id")}
                    log(con, rid, f"{p}: posted {res.get('url') or res.get('id')}")
                except Exception as e:  # keep going with the next platform
                    state = {"state": "failed", "at": now(), "error": str(e)[:400]}
                    log(con, rid, f"{p}: FAILED {e}")
                _set_platform(con, rid, p, state)
            plat = json.loads(con.execute("SELECT platforms FROM reels WHERE id=?", (rid,)).fetchone()[0])
            states = {v.get("state") for v in plat.values()}
            final = "posted" if states == {"posted"} else "failed" if "posted" not in states else "partial"
            con.execute("UPDATE reels SET status=? WHERE id=?", (final, rid))
            con.commit()
        finally:
            con.close()


def _set_platform(con, rid, p, state):
    plat = json.loads(con.execute("SELECT platforms FROM reels WHERE id=?", (rid,)).fetchone()[0] or "{}")
    plat[p] = state
    con.execute("UPDATE reels SET platforms=? WHERE id=?", (json.dumps(plat), rid))
    con.commit()


# ---------------------------------------------------------------- settings
@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings_page():
    con = db()
    if request.method == "POST":
        action = request.form.get("action", "save")
        if action in ("save", "test_meta"):  # Test also saves what was typed
            for k in SETTING_KEYS:
                if k not in request.form:
                    continue
                v = request.form.get(k, "").strip()
                if k in SECRET_KEYS and not v:
                    continue  # empty secret field = keep the old value
                set_setting(con, k, v)
            con.commit()
            if action == "save":
                flash("Settings save thaya")
        if action == "test_meta":
            try:
                info = pub.meta_check(get_settings(con))
                if info.get("ig_id"):
                    set_setting(con, "ig_user_id", info["ig_id"])
                    con.commit()
                flash(f"✅ Page: {info['page_name']} · Instagram: @{info.get('ig_username') or 'link nathi'}")
            except Exception as e:
                flash(f"❌ {e}")
        elif action == "yt_code":
            try:
                token = pub.youtube_exchange_code(get_settings(con), request.form.get("code", ""))
                set_setting(con, "yt_refresh_token", token)
                con.commit()
                flash("✅ YouTube connect thai gayu")
            except Exception as e:
                flash(f"❌ {e}")
        elif action == "yt_disconnect":
            con.execute("DELETE FROM settings WHERE key='yt_refresh_token'")
            con.commit()
            flash("YouTube disconnect thayu")
        return redirect(url_for("settings_page"))
    s = get_settings(con)
    yt_url = pub.youtube_auth_url(s) if s.get("yt_client_id") else None
    disk = os.statvfs(MEDIA)
    free_gb = disk.f_bavail * disk.f_frsize / 1e9
    return render_template("settings.html", s=s, secret=SECRET_KEYS, yt_url=yt_url, free_gb=free_gb)


@app.route("/health")
def health():
    return {"ok": True}


init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=os.environ.get("DEBUG") == "1")
