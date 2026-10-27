"""Video Library - a small self-hosted site for managing an educational video collection.

Run:  python app.py            (then open http://127.0.0.1:5000)
Login: admin / admin           (override with VIDEO_ADMIN_USER / VIDEO_ADMIN_PASS)
API:  see /api-docs after logging in
"""
import argparse
import functools
import hashlib
import hmac
import io
import mimetypes
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlparse

from flask import (Flask, Response, abort, flash, g, jsonify, redirect, render_template,
                   request, send_from_directory, session, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

from translations import DEFAULT_LANG, JS_STRINGS, LANGUAGES, translate

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("VIDEO_DATA_DIR", os.path.join(BASE_DIR, "data"))
VIDEO_DIR = os.path.join(DATA_DIR, "videos")
THUMB_DIR = os.path.join(DATA_DIR, "thumbs")
DB_PATH = os.path.join(DATA_DIR, "library.db")
for _d in (VIDEO_DIR, THUMB_DIR):
    os.makedirs(_d, exist_ok=True)

SITE_NAME = os.environ.get("SITE_NAME", "Video Library")
ADMIN_USER = os.environ.get("VIDEO_ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("VIDEO_ADMIN_PASS", "admin")
MAX_UPLOAD_GB = float(os.environ.get("VIDEO_MAX_UPLOAD_GB", "20"))
MIN_PASSWORD_LEN = 6

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

VIDEO_EXT = {"mp4", "m4v", "webm", "ogv", "ogg", "mov", "mkv", "avi", "wmv",
             "flv", "mpg", "mpeg", "3gp", "ts"}
IMAGE_EXT = {"jpg", "jpeg", "png", "webp", "gif"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL UNIQUE COLLATE NOCASE,
    section TEXT NOT NULL DEFAULT 'science'
);
CREATE TABLE IF NOT EXISTS videos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    category_id   INTEGER REFERENCES categories(id),
    filename      TEXT NOT NULL,
    original_name TEXT NOT NULL,
    mime          TEXT,
    size          INTEGER NOT NULL DEFAULT 0,
    duration      REAL,
    thumb         TEXT,
    created_at    TEXT NOT NULL,
    md5           TEXT,
    slug          TEXT,
    series        TEXT,
    problem_no    INTEGER,
    problem       TEXT
);
CREATE INDEX IF NOT EXISTS idx_videos_category ON videos(category_id);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def _read_or_create(path, factory):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            value = fh.read().strip()
        if value:
            return value
    value = factory()
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(value)
    return value


app = Flask(__name__)
app.config.update(
    SECRET_KEY=_read_or_create(os.path.join(DATA_DIR, "secret_key"), lambda: secrets.token_hex(32)),
    MAX_CONTENT_LENGTH=int(MAX_UPLOAD_GB * 1024 ** 3),
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)
app.json.sort_keys = False
app.json.ensure_ascii = False
# Behind nginx/Caddy/etc., build links from the domain and scheme the visitor used
# (X-Forwarded-Host / X-Forwarded-Proto). Without a proxy the Host header is used as-is.
if os.environ.get("TRUST_PROXY", "1") != "0":
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)


# ---------------------------------------------------------------- database

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def _close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


SLUG_LEN = 8  # short link: /v/<first 8 hex chars of the file's MD5>


def file_md5(path):
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def unique_slug(db, md5, exclude_id=None):
    """Shortest free MD5 prefix (8, 10, ... chars); identical files get '-2', '-3'..."""
    def taken(s):
        row = db.execute("SELECT id FROM videos WHERE slug = ?", (s,)).fetchone()
        return row is not None and row[0] != exclude_id
    same_file = db.execute("SELECT 1 FROM videos WHERE md5 = ? AND id IS NOT ?",
                           (md5, exclude_id)).fetchone()
    if not same_file:  # a different file sharing the prefix: use a longer prefix
        for n in range(SLUG_LEN, 33, 2):
            if not taken(md5[:n]):
                return md5[:n]
    if not taken(md5[:SLUG_LEN]):
        return md5[:SLUG_LEN]
    i = 2
    while taken(f"{md5[:SLUG_LEN]}-{i}"):
        i += 1
    return f"{md5[:SLUG_LEN]}-{i}"


# The site is split into three sections; every category belongs to one of them.
SECTIONS = ("algorithms", "ai", "science")
DEFAULT_SECTION = "science"   # uncategorized videos show up here too
SECTION_LABELS = {"algorithms": "Algorithms", "ai": "AI Tutorials", "science": "Popular Science"}
SECTION_ICONS = {"algorithms": "&#129513;", "ai": "&#129302;", "science": "&#128301;"}


def guess_section(category_name):
    """Default section for a new category, from its name."""
    n = (category_name or "").lower()
    if any(k in n for k in ("leetcode", "algorithm", "算法", "数据结构", "刷题")):
        return "algorithms"
    return DEFAULT_SECTION


# "LeetCode 第42题：接雨水。…"  /  "算法思维 · 倒着学 第1课：滑动窗口。…"
_LC_RE = re.compile(r"(LeetCode)\s*第\s*(\d+)\s*题\s*[：:]\s*([^。\n]+)", re.I)
_LESSON_RE = re.compile(r"^\s*(.+?)\s*第\s*(\d+)\s*课\s*[：:]\s*([^。\n]+)")


def parse_problem(title, description):
    """(series, number, name) of the algorithm problem a video is about, or (None, None, None)."""
    for rx in (_LC_RE, _LESSON_RE):
        m = rx.search(description or "")
        if m:
            series = "LeetCode" if rx is _LC_RE else m.group(1).strip()
            return series, int(m.group(2)), m.group(3).strip()
    return None, None, None


def detect_problem(db, title, description, category_id):
    """Like parse_problem, but for algorithm videos without the usual description it matches
    the title prefix ("两数之和 · 午夜数学") against problems already in the library."""
    series, no, name = parse_problem(title, description)
    if series or category_id is None:
        return series, no, name
    row = db.execute("SELECT name, section FROM categories WHERE id = ?", (category_id,)).fetchone()
    if not row or row[1] != "algorithms":
        return None, None, None
    head = (title or "").split(" · ")[0].strip()
    head = re.sub(r"^第\s*\d+\s*[集课]\s*[：:]\s*", "", head)
    known = db.execute("SELECT series, problem_no, problem FROM videos WHERE problem IS NOT NULL "
                       "AND (problem = ? OR ? LIKE '%' || problem || '%') "
                       "ORDER BY (problem = ?) DESC, LENGTH(problem) DESC LIMIT 1",
                       (head, title, head)).fetchone()
    if known:
        return known[0], known[1], known[2]
    return row[0], None, head or title


def init_db():
    with sqlite3.connect(DB_PATH) as db:
        db.executescript(SCHEMA)
        # Upgrade older databases: add new columns and fill them in for existing rows.
        cols = {r[1] for r in db.execute("PRAGMA table_info(videos)")}
        for col, kind in (("md5", "TEXT"), ("slug", "TEXT"), ("series", "TEXT"),
                          ("problem_no", "INTEGER"), ("problem", "TEXT")):
            if col not in cols:
                db.execute(f"ALTER TABLE videos ADD COLUMN {col} {kind}")
        if "section" not in {r[1] for r in db.execute("PRAGMA table_info(categories)")}:
            db.execute(f"ALTER TABLE categories ADD COLUMN section TEXT NOT NULL DEFAULT '{DEFAULT_SECTION}'")
            for cid, name in db.execute("SELECT id, name FROM categories").fetchall():
                db.execute("UPDATE categories SET section = ? WHERE id = ?", (guess_section(name), cid))
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_videos_slug ON videos(slug)")
        for vid, filename in db.execute(
                "SELECT id, filename FROM videos WHERE slug IS NULL OR slug = '' ORDER BY id").fetchall():
            path = os.path.join(VIDEO_DIR, filename)
            md5 = file_md5(path) if os.path.exists(path) else hashlib.md5(f"missing-{vid}".encode()).hexdigest()
            db.execute("UPDATE videos SET md5 = ?, slug = ? WHERE id = ?", (md5, unique_slug(db, md5, vid), vid))
        # One-time: work out which algorithm problem each existing video belongs to.
        if not db.execute("SELECT 1 FROM settings WHERE key = 'problems_backfilled'").fetchone():
            rows = db.execute("SELECT id, title, description, category_id FROM videos ORDER BY id").fetchall()
            for vid, title, desc, cid in rows:          # pass 1: videos that name their problem
                s, n, p = parse_problem(title, desc)
                if s:
                    db.execute("UPDATE videos SET series=?, problem_no=?, problem=? WHERE id=?", (s, n, p, vid))
            for vid, title, desc, cid in rows:          # pass 2: match the rest by title
                if not parse_problem(title, desc)[0]:
                    s, n, p = detect_problem(db, title, desc, cid)
                    if p:
                        db.execute("UPDATE videos SET series=?, problem_no=?, problem=? WHERE id=?", (s, n, p, vid))
            db.execute("INSERT INTO settings (key, value) VALUES ('problems_backfilled', '1')")
        db.commit()


init_db()


def get_setting(key, default=None):
    row = get_db().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row and row["value"] is not None else default


def set_settings(**values):
    """Set (or with None, delete) several settings in one transaction."""
    db = get_db()
    for key, value in values.items():
        if value is None:
            db.execute("DELETE FROM settings WHERE key = ?", (key,))
        else:
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                       "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))
    db.commit()


def find_video(vid):
    return get_db().execute(
        "SELECT v.*, c.name AS category_name, " + SECTION_OF + " AS section FROM videos v "
        "LEFT JOIN categories c ON c.id = v.category_id WHERE v.id = ?", (vid,)
    ).fetchone()


def get_video(vid):
    row = find_video(vid)
    if row is None:
        abort(404)
    return row


def all_categories(section=None):
    sql = ("SELECT c.id, c.name, c.section, COUNT(v.id) AS n FROM categories c "
           "LEFT JOIN videos v ON v.category_id = c.id ")
    params = []
    if section:
        sql += "WHERE c.section = ? "
        params.append(section)
    return get_db().execute(sql + "GROUP BY c.id ORDER BY c.name COLLATE NOCASE", params).fetchall()


SECTION_OF = f"COALESCE(c.section, '{DEFAULT_SECTION}')"   # uncategorized -> default section


def section_counts():
    rows = get_db().execute(f"SELECT {SECTION_OF} AS s, COUNT(*) AS n FROM videos v "
                            "LEFT JOIN categories c ON c.id = v.category_id GROUP BY s").fetchall()
    counts = {s: 0 for s in SECTIONS}
    counts.update({r["s"]: r["n"] for r in rows if r["s"] in counts})
    return counts


def query_videos(cat="", q="", sort="new", limit=None, offset=0, section=None):
    sql = ("SELECT v.*, c.name AS category_name, " + SECTION_OF + " AS section FROM videos v "
           "LEFT JOIN categories c ON c.id = v.category_id WHERE 1 = 1")
    params = []
    if section:
        sql += f" AND {SECTION_OF} = ?"
        params.append(section)
    if cat == "none":
        sql += " AND v.category_id IS NULL"
    elif str(cat).isdigit():
        sql += " AND v.category_id = ?"
        params.append(int(cat))
    if q:
        sql += " AND (v.title LIKE ? OR v.description LIKE ? OR v.original_name LIKE ?)"
        params += [f"%{q}%"] * 3
    sql += {
        "old": " ORDER BY v.created_at ASC, v.id ASC",
        "title": " ORDER BY v.title COLLATE NOCASE ASC",
    }.get(sort, " ORDER BY v.created_at DESC, v.id DESC")
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params += [limit, offset]
    return get_db().execute(sql, params).fetchall()


# ---------------------------------------------------------------- i18n

def current_lang():
    return getattr(g, "lang", DEFAULT_LANG)


def _(text, **kwargs):
    return translate(text, current_lang(), **kwargs)


@app.before_request
def _pick_language():
    lang = request.cookies.get("lang")
    if lang not in LANGUAGES:
        lang = request.accept_languages.best_match(list(LANGUAGES)) or DEFAULT_LANG
    g.lang = lang


@app.route("/lang/<code>")
def set_language(code):
    resp = redirect(safe_next(request.args.get("next")))
    if code in LANGUAGES:
        resp.set_cookie("lang", code, max_age=365 * 86400, samesite="Lax")
    return resp


# ---------------------------------------------------------------- auth / csrf

def safe_equal(a, b):
    """Constant-time comparison that also works for non-ASCII text."""
    return hmac.compare_digest(str(a).encode("utf-8"), str(b).encode("utf-8"))


def password_version():
    return get_setting("password_version", "0")


def check_login(user, pw):
    if not safe_equal(user, ADMIN_USER):
        return False
    stored = get_setting("password_hash")
    if stored:  # password changed in the web interface
        return check_password_hash(stored, pw)
    return safe_equal(pw, ADMIN_PASS)


def is_admin():
    # Changing the password bumps the version, which logs out every other session.
    return session.get("admin") is True and session.get("pwv") == password_version()


def is_api():
    return request.path.startswith("/api/")


def wants_json():
    return is_api() or request.headers.get("X-Requested-With") == "XMLHttpRequest"


def admin_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not is_admin():
            if wants_json():
                return jsonify(ok=False, error=_("Please log in again.")), 401
            return redirect(url_for("login", next=request.full_path))
        return view(*args, **kwargs)
    return wrapped


def api_key():
    """Random secret part of the API URL; None while the API is off."""
    return get_setting("api_key")


def api_token():
    return get_setting("api_token")


def api_enabled(view):
    """Every API route lives under /api/<random key>/. Wrong key or API off -> 404."""
    @functools.wraps(view)
    def wrapped(key, *args, **kwargs):
        expected = api_key()
        if not expected or not safe_equal(key, expected):
            return api_error("Not found.", 404)
        return view(*args, **kwargs)
    return wrapped


def token_required(view):
    """Write access also needs 'Authorization: Bearer <token>' or 'X-API-Key: <token>'."""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        sent = auth[7:].strip() if auth.lower().startswith("bearer ") else request.headers.get("X-API-Key", "")
        token = api_token()
        if not sent or not token or not safe_equal(sent, token):
            return api_error("Missing or invalid API token.", 401)
        return view(*args, **kwargs)
    return wrapped


def api_url(endpoint, **values):
    return url_for(endpoint, key=api_key(), _external=True, **values)


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


@app.before_request
def _csrf_protect():
    if request.method == "POST" and not is_api():
        expected = session.get("csrf")
        sent = request.form.get("csrf") or request.headers.get("X-CSRF-Token")
        if not expected or not sent or not safe_equal(expected, sent):
            msg = _("Form expired - reload the page and try again.")
            if wants_json():
                return jsonify(ok=False, error=msg), 400
            flash(msg, "error")
            return redirect(request.referrer or url_for("index"))


@app.context_processor
def _template_globals():
    lang = current_lang()
    return {
        "is_admin": is_admin(), "csrf_token": csrf_token, "site_name": SITE_NAME,
        "_": _, "lang": lang, "languages": LANGUAGES,
        "js_strings": {s: translate(s, lang) for s in JS_STRINGS},
        "sections": SECTIONS, "section_labels": SECTION_LABELS, "section_icons": SECTION_ICONS,
        "section_url": section_url, "active_section": _active_section(),
    }


def _active_section():
    """Which top-nav tab to highlight."""
    ep = request.endpoint or ""
    if ep == "algorithms":
        return "algorithms"
    if ep == "section_page":
        return (request.view_args or {}).get("section")
    return None


# ---------------------------------------------------------------- template filters

@app.template_filter("filesize")
def _filesize(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


@app.template_filter("duration")
def _duration(sec):
    if not sec:
        return ""
    sec = int(round(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


@app.template_filter("date")
def _date(value):
    try:
        d = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return value or ""
    if current_lang() == "zh":
        return f"{d.year}年{d.month}月{d.day}日"
    return d.strftime("%b {day}, %Y").format(day=d.day)


@app.template_filter("ext")
def _ext_filter(name):
    return ext_of(name).upper() or "VIDEO"


# ---------------------------------------------------------------- helpers

def ext_of(filename):
    return filename.rsplit(".", 1)[-1].lower() if filename and "." in filename else ""


def done(target):
    return jsonify(ok=True, redirect=target) if wants_json() else redirect(target)


def failed(message, back):
    if wants_json():
        return jsonify(ok=False, error=message), 400
    flash(message, "error")
    return redirect(back)


def api_error(message, status=400):
    return jsonify(ok=False, error=message), status


def remove_file(folder, name):
    if name:
        try:
            os.remove(os.path.join(folder, name))
        except OSError:
            pass


def save_thumbnail(file):
    """Save an uploaded thumbnail image; returns the stored filename or None."""
    if not file or not file.filename:
        return None
    ext = ext_of(file.filename)
    if ext not in IMAGE_EXT:
        ext = (mimetypes.guess_extension(file.mimetype or "") or "").lstrip(".")
        ext = "jpg" if ext == "jpe" else ext
        if ext not in IMAGE_EXT:
            return None
    name = f"{uuid.uuid4().hex}.{ext}"
    file.save(os.path.join(THUMB_DIR, name))
    return name


def probe_duration(path):
    """Video length in seconds using ffprobe, if installed."""
    if not FFPROBE:
        return None
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=60).stdout
        return parse_duration(out.strip())
    except (OSError, subprocess.SubprocessError):
        return None


def generate_thumbnail(path, duration):
    """Grab a frame with ffmpeg, if installed; returns the stored filename or None."""
    if not FFMPEG:
        return None
    at = min(max(1.0, duration * 0.1), duration / 2) if duration else 0
    name = f"{uuid.uuid4().hex}.jpg"
    out = os.path.join(THUMB_DIR, name)
    try:
        subprocess.run(
            [FFMPEG, "-y", "-v", "error", "-ss", f"{at:.2f}", "-i", path, "-frames:v", "1",
             "-vf", "scale='min(640,iw)':-2", "-q:v", "4", out],
            capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        pass
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return name
    remove_file(THUMB_DIR, name)
    return None


def category_by_name(name, create=True, section=None):
    name = " ".join((name or "").split())[:80]
    if not name:
        return None
    db = get_db()
    row = db.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    if not create:
        return None
    section = section if section in SECTIONS else guess_section(name)
    cur = db.execute("INSERT INTO categories (name, section) VALUES (?, ?)", (name, section))
    db.commit()
    return cur.lastrowid


def category_by_id(value):
    if str(value).isdigit():
        row = get_db().execute("SELECT id FROM categories WHERE id = ?", (int(value),)).fetchone()
        return row["id"] if row else None
    return None


def resolve_category(form):
    """Web form: an existing category id, '__new__' + new_category, or none."""
    choice = form.get("category", "")
    if choice == "__new__":
        return category_by_name(form.get("new_category", ""), section=form.get("new_section"))
    return category_by_id(choice)


def problem_fields(form, title, description, category_id, current=None):
    """Series / number / problem name from a form or JSON body. Fields that aren't sent keep
    their current value (or are detected automatically for a new upload)."""
    if not any(k in form for k in ("series", "problem_no", "problem")):
        if current is not None:
            return current["series"], current["problem_no"], current["problem"]
        return detect_problem(get_db(), title, description, category_id)
    series = " ".join(str(form.get("series") or "").split())[:80] or None
    name = " ".join(str(form.get("problem") or "").split())[:120] or None
    raw_no = str(form.get("problem_no") or "").strip()
    no = int(raw_no) if raw_no.isdigit() else None
    return series, no, name


def parse_duration(value):
    try:
        d = float(value)
        return d if 0 < d < 10 ** 7 else None
    except (TypeError, ValueError):
        return None


def safe_next(target):
    if target:
        parts = urlparse(target)
        if not parts.scheme and not parts.netloc and target.startswith("/") and not target.startswith("//"):
            return target
    return url_for("index")


class UploadError(ValueError):
    pass


def store_video(file, title, description, category_id, thumbnail_file=None, duration=None,
                problem=None):
    """Save an uploaded video + metadata. Returns the new video id.
    `problem` is (series, number, name); None = detect it from the title/description."""
    if not file or not file.filename:
        raise UploadError(_("Please choose a video file."))
    ext = ext_of(file.filename)
    if ext not in VIDEO_EXT:
        raise UploadError(_("'.{ext}' is not a supported video type.", ext=ext))

    original = os.path.basename(file.filename.replace("\\", "/"))
    title = (title or "").strip() or original.rsplit(".", 1)[0]
    stored = f"{uuid.uuid4().hex}.{ext}"
    path = os.path.join(VIDEO_DIR, stored)
    md5 = hashlib.md5()  # hash while saving, so big files are read only once
    with open(path, "wb") as out:
        for chunk in iter(lambda: file.stream.read(1024 * 1024), b""):
            md5.update(chunk)
            out.write(chunk)
    md5 = md5.hexdigest()

    duration = parse_duration(duration) or probe_duration(path)
    thumb = save_thumbnail(thumbnail_file) or generate_thumbnail(path, duration)
    mime = file.mimetype if (file.mimetype or "").startswith("video/") else mimetypes.guess_type(original)[0]
    db = get_db()
    description = (description or "").strip()
    series, problem_no, problem_name = problem or detect_problem(db, title, description, category_id)
    cur = db.execute(
        "INSERT INTO videos (title, description, category_id, filename, original_name, mime, "
        "size, duration, thumb, created_at, md5, slug, series, problem_no, problem) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (title[:200], description, category_id, stored, original, mime,
         os.path.getsize(path), duration, thumb, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
         md5, unique_slug(db, md5), series, problem_no, problem_name),
    )
    db.commit()
    return cur.lastrowid, title


def delete_video_row(v):
    db = get_db()
    db.execute("DELETE FROM videos WHERE id = ?", (v["id"],))
    db.commit()
    remove_file(VIDEO_DIR, v["filename"])
    remove_file(THUMB_DIR, v["thumb"])


# ---------------------------------------------------------------- public pages

@app.route("/")
def index():
    # The library is private: visitors only see videos whose share link they were given.
    if not is_admin():
        return render_template("private.html")
    q = request.args.get("q", "").strip()
    cat = request.args.get("category", "")
    if cat:  # old ?category= links: open that category inside its section
        row = get_db().execute("SELECT section FROM categories WHERE id = ?",
                               (int(cat),)).fetchone() if cat.isdigit() else None
        section = row["section"] if row else DEFAULT_SECTION
        return redirect(section_url(section, category=cat, q=q or None))
    if q:  # search across every section
        return render_template("grid.html", section=None, q=q, cat="", sort=request.args.get("sort", "new"),
                               videos=query_videos("", q, request.args.get("sort", "new")),
                               categories=[], total=0, uncategorized=0, active_name=None)
    counts = section_counts()
    recent = {s: query_videos(section=s, limit=4) for s in SECTIONS}
    return render_template("home.html", counts=counts, recent=recent,
                           latest=query_videos(limit=8), total=sum(counts.values()))


def section_url(section, **args):
    if section == "algorithms":
        return url_for("algorithms", **{k: v for k, v in args.items() if k == "v"})
    return url_for("section_page", section=section, **args)


@app.route("/<any(ai, science):section>")
@admin_required
def section_page(section):
    """AI Tutorials / Popular Science: a grid of videos, filterable by category."""
    q = request.args.get("q", "").strip()
    cat = request.args.get("category", "")
    sort = request.args.get("sort", "new")
    cats = all_categories(section)
    uncategorized = 0
    if section == DEFAULT_SECTION:
        uncategorized = get_db().execute("SELECT COUNT(*) FROM videos WHERE category_id IS NULL").fetchone()[0]
    active_name = None
    if cat.isdigit():
        active_name = next((c["name"] for c in cats if str(c["id"]) == cat), None)
    elif cat == "none":
        active_name = _("Uncategorized")
    return render_template(
        "grid.html", section=section, videos=query_videos(cat, q, sort, section=section),
        categories=cats, q=q, cat=cat, sort=sort, total=section_counts()[section],
        uncategorized=uncategorized, active_name=active_name)


def variant_label(title, problem):
    """Tab label inside a problem: '两数之和 · 午夜数学' -> '午夜数学'."""
    if " · " in title:
        return title.rsplit(" · ", 1)[1]
    if problem and problem in title:
        m = re.match(r"^\s*(第\s*\d+\s*[集课])", title)
        if m:
            return m.group(1).replace(" ", "")
    return title


def algorithm_groups():
    """Algorithm videos grouped into problems, grouped into series (LeetCode first)."""
    rows = query_videos(section="algorithms", sort="title")
    problems = {}
    for v in rows:
        name = v["problem"] or v["title"]
        series = v["series"] or v["category_name"] or _("Other")
        key = (series, v["problem_no"] if v["problem_no"] is not None else name)
        p = problems.setdefault(key, {"series": series, "no": v["problem_no"], "name": name, "videos": []})
        p["videos"].append(v)
    for p in problems.values():
        # style versions in file order (a_, b_, c_, d_); full episodes ("第1集…") last
        p["videos"].sort(key=lambda v: (v["original_name"].startswith("第"), v["original_name"].lower()))
    series_names = sorted({p["series"] for p in problems.values()},
                          key=lambda s: (s.lower() != "leetcode", s.lower()))
    groups = []
    for s in series_names:
        items = sorted((p for p in problems.values() if p["series"] == s),
                       key=lambda p: (p["no"] is None, p["no"] or 0, p["name"]))
        groups.append({"series": s, "problems": items})
    return groups


def video_card(v):
    """The data the algorithm page's player needs for one video."""
    return {
        "slug": v["slug"], "title": v["title"], "label": variant_label(v["title"], v["problem"]),
        "description": v["description"], "category": v["category_name"],
        "date": _date(v["created_at"]), "duration": _duration(v["duration"]), "size": _filesize(v["size"]),
        "file": v["original_name"],
        "media": url_for("media", slug=v["slug"]), "mime": v["mime"] or "video/mp4",
        "thumb": url_for("thumb", slug=v["slug"]) if v["thumb"] else None,
        "page": url_for("video", slug=v["slug"]), "download": url_for("download", slug=v["slug"]),
        "edit": url_for("edit", vid=v["id"]),
    }


@app.route("/algorithms")
@admin_required
def algorithms():
    """Problem list on the left; the chosen problem's video (and its style versions) on the right."""
    groups = algorithm_groups()
    data, problems = [], []
    for gi, grp in enumerate(groups):
        for pi, p in enumerate(grp["problems"]):
            p["key"] = f"{gi}-{pi}"
            p["cards"] = [video_card(v) for v in p["videos"]]
            problems.append(p)
            data.append({"key": p["key"], "series": grp["series"], "no": p["no"], "name": p["name"],
                         "videos": p["cards"]})
    # ?v=<slug> picks the video (and so its problem); default = first problem, first version
    want = request.args.get("v", "")
    current = next(({"problem": p, "video": c} for p in problems for c in p["cards"] if c["slug"] == want),
                   {"problem": problems[0], "video": problems[0]["cards"][0]} if problems else None)
    return render_template("algorithms.html", groups=groups, data=data, current=current,
                           count=section_counts()["algorithms"])


def find_by_ref(ref):
    """Find a video by short link (MD5 prefix). Numeric ids work only for the admin,
    so visitors can't step through /video/1, /video/2, ... to find unshared videos."""
    db = get_db()
    sql = ("SELECT v.*, c.name AS category_name, " + SECTION_OF + " AS section FROM videos v "
           "LEFT JOIN categories c ON c.id = v.category_id WHERE ")
    row = db.execute(sql + "v.slug = ?", (ref,)).fetchone()
    if row is None and ref.isdigit() and len(ref) < SLUG_LEN and is_admin():
        row = db.execute(sql + "v.id = ?", (int(ref),)).fetchone()
    return row


def video_by_ref(ref):
    row = find_by_ref(ref)
    if row is None:
        abort(404)
    return row


def canonical(endpoint, v, ref):
    """Old /xxx/<id> links redirect to the short MD5 link, keeping ?t= etc."""
    if ref != v["slug"]:
        return redirect(url_for(endpoint, slug=v["slug"], **request.args), 301)
    return None


@app.route("/v/<slug>")
def video(slug):
    v = video_by_ref(slug)
    resp = canonical("video", v, slug)
    if resp:
        return resp
    related = []
    if v["category_id"] is not None and is_admin():  # don't reveal other videos to visitors
        related = get_db().execute(
            "SELECT * FROM videos WHERE category_id = ? AND id != ? "
            "ORDER BY created_at DESC LIMIT 8", (v["category_id"], v["id"])).fetchall()
    return render_template("video.html", v=v, related=related)


@app.route("/video/<int:vid>")
def legacy_video(vid):
    if not is_admin():
        abort(404)
    v = get_video(vid)
    return redirect(url_for("video", slug=v["slug"], **request.args), 301)


@app.route("/embed/<slug>")
def embed(slug):
    """Bare player page for <iframe> embedding on other websites."""
    v = video_by_ref(slug)
    return canonical("embed", v, slug) or render_template("embed.html", v=v)


@app.route("/qr/<slug>.svg")
def qr(slug):
    """QR code of the video's public link on the domain being visited (for phones / WeChat)."""
    v = video_by_ref(slug)
    try:
        import segno
    except ImportError:
        abort(404)
    buf = io.BytesIO()
    segno.make(url_for("video", slug=v["slug"], _external=True), error="m").save(
        buf, kind="svg", scale=6, border=2, dark="#000", light="#fff")
    resp = Response(buf.getvalue(), mimetype="image/svg+xml")
    resp.cache_control.max_age = 3600
    resp.vary.add("Host")
    resp.vary.add("X-Forwarded-Host")
    return resp


@app.route("/media/<slug>")
def media(slug):
    v = video_by_ref(slug)
    mime = v["mime"] or mimetypes.guess_type(v["original_name"])[0] or "application/octet-stream"
    return send_from_directory(VIDEO_DIR, v["filename"], mimetype=mime, conditional=True)


@app.route("/download/<slug>")
def download(slug):
    v = video_by_ref(slug)
    return send_from_directory(VIDEO_DIR, v["filename"], as_attachment=True,
                               download_name=v["original_name"])


@app.route("/thumb/<slug>")
def thumb(slug):
    v = video_by_ref(slug)
    if not v["thumb"]:
        abort(404)
    resp = send_from_directory(THUMB_DIR, v["thumb"])
    resp.cache_control.max_age = 86400
    return resp


def video_link(vid):
    row = get_db().execute("SELECT slug FROM videos WHERE id = ?", (vid,)).fetchone()
    return url_for("video", slug=row["slug"])


@app.route("/robots.txt")
def robots():
    # Shared links are unlisted: keep search engines out.
    return Response("User-agent: *\nDisallow: /\n", mimetype="text/plain")


@app.after_request
def _no_index(resp):
    resp.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
    return resp


# ---------------------------------------------------------------- login

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = request.form.get("username", "")
        pw = request.form.get("password", "")
        if check_login(user, pw):
            session.clear()
            session["admin"] = True
            session["pwv"] = password_version()
            session.permanent = True
            flash(_("Logged in. You can now upload and edit videos."), "ok")
            return redirect(safe_next(request.form.get("next")))
        flash(_("Wrong username or password."), "error")
    return render_template("login.html", next=request.args.get("next", ""))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash(_("Logged out."), "ok")
    return redirect(url_for("index"))


# ---------------------------------------------------------------- admin: videos

@app.route("/upload", methods=["GET", "POST"])
@admin_required
def upload():
    if request.method == "GET":
        return render_template("upload.html", categories=all_categories(),
                               preselect=request.args.get("category", ""),
                               max_gb=MAX_UPLOAD_GB)
    try:
        vid, title = store_video(
            request.files.get("video"), request.form.get("title"), request.form.get("description"),
            resolve_category(request.form), request.files.get("thumbnail"), request.form.get("duration"))
    except UploadError as e:
        return failed(str(e), url_for("upload"))
    flash(_("Uploaded “{title}”.", title=title), "ok")
    return done(video_link(vid))


@app.route("/video/<int:vid>/edit", methods=["GET", "POST"])
@admin_required
def edit(vid):
    v = get_video(vid)
    if request.method == "GET":
        return render_template("edit.html", v=v, categories=all_categories())

    title = request.form.get("title", "").strip()
    if not title:
        return failed(_("Title can't be empty."), url_for("edit", vid=vid))
    thumb_name = v["thumb"]
    new_thumb = save_thumbnail(request.files.get("thumbnail"))
    if new_thumb or request.form.get("remove_thumb"):
        remove_file(THUMB_DIR, thumb_name)
        thumb_name = new_thumb
    duration = parse_duration(request.form.get("duration")) or v["duration"]
    description = request.form.get("description", "").strip()
    category_id = resolve_category(request.form)
    series, problem_no, problem_name = problem_fields(request.form, title, description, category_id, v)

    db = get_db()
    db.execute(
        "UPDATE videos SET title = ?, description = ?, category_id = ?, thumb = ?, duration = ?, "
        "series = ?, problem_no = ?, problem = ? WHERE id = ?",
        (title[:200], description, category_id, thumb_name, duration,
         series, problem_no, problem_name, vid),
    )
    db.commit()
    flash(_("Changes saved."), "ok")
    return done(video_link(vid))


@app.route("/video/<int:vid>/delete", methods=["POST"])
@admin_required
def delete(vid):
    v = get_video(vid)
    delete_video_row(v)
    flash(_("Deleted “{title}”.", title=v["title"]), "ok")
    return redirect(url_for("index"))


# ---------------------------------------------------------------- admin: categories

def _clean_name():
    return " ".join(request.form.get("name", "").split())[:80]


@app.route("/categories", methods=["GET", "POST"])
@admin_required
def categories():
    if request.method == "POST":
        name = _clean_name()
        section = request.form.get("section")
        if not name:
            flash(_("Category name can't be empty."), "error")
        else:
            try:
                get_db().execute("INSERT INTO categories (name, section) VALUES (?, ?)",
                                 (name, section if section in SECTIONS else guess_section(name)))
                get_db().commit()
                flash(_("Added category “{name}”.", name=name), "ok")
            except sqlite3.IntegrityError:
                flash(_("A category named “{name}” already exists.", name=name), "error")
        return redirect(url_for("categories"))
    return render_template("categories.html", categories=all_categories())


@app.route("/categories/<int:cid>/rename", methods=["POST"])
@admin_required
def rename_category(cid):
    """Save a category's name and section."""
    name = _clean_name()
    section = request.form.get("section")
    if not name:
        flash(_("Category name can't be empty."), "error")
    else:
        try:
            db = get_db()
            db.execute("UPDATE categories SET name = ? WHERE id = ?", (name, cid))
            if section in SECTIONS:
                db.execute("UPDATE categories SET section = ? WHERE id = ?", (section, cid))
            db.commit()
            flash(_("Category saved."), "ok")
        except sqlite3.IntegrityError:
            flash(_("A category named “{name}” already exists.", name=name), "error")
    return redirect(url_for("categories"))


@app.route("/categories/<int:cid>/delete", methods=["POST"])
@admin_required
def delete_category(cid):
    db = get_db()
    db.execute("UPDATE videos SET category_id = NULL WHERE category_id = ?", (cid,))
    db.execute("DELETE FROM categories WHERE id = ?", (cid,))
    db.commit()
    flash(_("Category deleted. Its videos are now uncategorized."), "ok")
    return redirect(url_for("categories"))


# ---------------------------------------------------------------- admin: API settings

@app.route("/api-docs")
@admin_required
def api_docs():
    key = api_key()
    return render_template("api.html", enabled=bool(key), token=api_token() or "",
                           base=(request.host_url.rstrip("/") + f"/api/{key}") if key else "",
                           ffmpeg=bool(FFMPEG), max_gb=MAX_UPLOAD_GB)


@app.route("/api-docs/<action>", methods=["POST"])
@admin_required
def api_settings(action):
    new_key, new_token = secrets.token_urlsafe(24), secrets.token_urlsafe(32)
    if action == "enable":
        set_settings(api_key=new_key, api_token=new_token)
        flash(_("API turned on with a new random URL and token."), "ok")
    elif action == "new-url" and api_key():
        set_settings(api_key=new_key)
        flash(_("New API URL created. The old URL no longer works."), "ok")
    elif action == "new-token" and api_key():
        set_settings(api_token=new_token)
        flash(_("New API token created. The old token no longer works."), "ok")
    elif action == "disable":
        set_settings(api_key=None, api_token=None)
        flash(_("API turned off."), "ok")
    return redirect(url_for("api_docs"))


# ---------------------------------------------------------------- admin: password

@app.route("/account", methods=["GET", "POST"])
@admin_required
def account():
    if request.method == "POST":
        current = request.form.get("current", "")
        new = request.form.get("new", "")
        if not check_login(ADMIN_USER, current):
            flash(_("Current password is wrong."), "error")
        elif len(new) < MIN_PASSWORD_LEN:
            flash(_("The new password must be at least {n} characters.", n=MIN_PASSWORD_LEN), "error")
        elif new != request.form.get("confirm", ""):
            flash(_("The two new passwords don't match."), "error")
        elif new == current:
            flash(_("The new password is the same as the current one."), "error")
        else:
            version = str(int(password_version()) + 1)
            set_settings(password_hash=generate_password_hash(new), password_version=version)
            session["pwv"] = version  # stay logged in here; other sessions are logged out
            flash(_("Password changed. Other devices have been logged out."), "ok")
            return redirect(url_for("account"))
        return redirect(url_for("account"))
    return render_template("account.html", user=ADMIN_USER, min_len=MIN_PASSWORD_LEN,
                           is_default=not get_setting("password_hash") and ADMIN_PASS == "admin")


# ---------------------------------------------------------------- REST API

def video_json(v):
    return {
        "id": v["id"],
        "slug": v["slug"],
        "md5": v["md5"],
        "title": v["title"],
        "description": v["description"],
        "category": ({"id": v["category_id"], "name": v["category_name"]}
                     if v["category_id"] is not None else None),
        "section": v["section"] if "section" in v.keys() else None,
        "problem": ({"series": v["series"], "number": v["problem_no"], "name": v["problem"]}
                    if v["problem"] else None),
        "original_name": v["original_name"],
        "size": v["size"],
        "duration": v["duration"],
        "created_at": v["created_at"],
        "urls": {
            "page": url_for("video", slug=v["slug"], _external=True),
            "embed": url_for("embed", slug=v["slug"], _external=True),
            "stream": url_for("media", slug=v["slug"], _external=True),
            "download": url_for("download", slug=v["slug"], _external=True),
            "thumbnail": url_for("thumb", slug=v["slug"], _external=True) if v["thumb"] else None,
        },
    }


@app.get("/api/<key>/categories")
@api_enabled
@token_required
def api_categories():
    return jsonify([{"id": c["id"], "name": c["name"], "section": c["section"], "count": c["n"]}
                    for c in all_categories()])


@app.post("/api/<key>/categories")
@api_enabled
@token_required
def api_create_category():
    data = request.get_json(silent=True) or request.form
    name = " ".join(str(data.get("name", "")).split())[:80]
    if not name:
        return api_error("Field 'name' is required.")
    section = data.get("section")
    if section and section not in SECTIONS:
        return api_error(f"section must be one of: {', '.join(SECTIONS)}.")
    existed = category_by_name(name, create=False)
    cid = existed or category_by_name(name, section=section)
    if existed and section:
        get_db().execute("UPDATE categories SET section = ? WHERE id = ?", (section, cid))
        get_db().commit()
    row = get_db().execute("SELECT name, section FROM categories WHERE id = ?", (cid,)).fetchone()
    return jsonify(id=cid, name=row["name"], section=row["section"]), (200 if existed else 201)


@app.get("/api/<key>/videos")
@api_enabled
@token_required
def api_list_videos():
    try:
        limit = min(max(int(request.args.get("limit", 100)), 1), 1000)
        offset = max(int(request.args.get("offset", 0)), 0)
    except ValueError:
        return api_error("limit and offset must be integers.")
    rows = query_videos(request.args.get("category", ""), request.args.get("q", "").strip(),
                        request.args.get("sort", "new"), limit, offset)
    return jsonify([video_json(v) for v in rows])


def api_find(ref):
    """API accepts either the numeric id or the short link (slug)."""
    row = get_db().execute("SELECT v.*, c.name AS category_name, " + SECTION_OF + " AS section FROM videos v "
                           "LEFT JOIN categories c ON c.id = v.category_id WHERE v.slug = ?", (ref,)).fetchone()
    return row or (find_video(int(ref)) if ref.isdigit() else None)


@app.get("/api/<key>/videos/<ref>")
@api_enabled
@token_required
def api_get_video(ref):
    v = api_find(ref)
    return jsonify(video_json(v)) if v else api_error("Video not found.", 404)


@app.post("/api/<key>/videos")
@api_enabled
@token_required
def api_upload_video():
    """multipart/form-data: file (required), title, description,
    category (name, created if new) or category_id, thumbnail (image)."""
    form = request.form
    if form.get("category_id"):
        category_id = category_by_id(form["category_id"])
        if category_id is None:
            return api_error("category_id does not exist.")
    else:
        category_id = category_by_name(form.get("category", ""))
    file = request.files.get("file") or request.files.get("video")
    g.lang = "en"  # API messages are always English
    problem = None
    if any(k in form for k in ("series", "problem_no", "problem")):
        problem = problem_fields(form, form.get("title"), form.get("description"), category_id)
    try:
        vid, _title = store_video(file, form.get("title"), form.get("description"), category_id,
                                  request.files.get("thumbnail"), form.get("duration"), problem)
    except UploadError as e:
        return api_error(str(e))
    resp = jsonify(video_json(find_video(vid)))
    resp.status_code = 201
    resp.headers["Location"] = api_url("api_get_video", ref=find_video(vid)["slug"])
    return resp


@app.route("/api/<key>/videos/<ref>", methods=["PATCH"])
@api_enabled
@token_required
def api_update_video(ref):
    v = api_find(ref)
    if not v:
        return api_error("Video not found.", 404)
    vid = v["id"]
    data = request.get_json(silent=True) or request.form
    title = str(data.get("title", v["title"])).strip()
    if not title:
        return api_error("title can't be empty.")
    category_id = v["category_id"]
    if "category_id" in data:
        category_id = None if data["category_id"] in (None, "") else category_by_id(data["category_id"])
        if data["category_id"] not in (None, "") and category_id is None:
            return api_error("category_id does not exist.")
    elif "category" in data:
        category_id = category_by_name(data["category"]) if data["category"] else None
    description = str(data.get("description", v["description"]))
    series, problem_no, problem_name = problem_fields(data, title, description, category_id, v)
    db = get_db()
    db.execute("UPDATE videos SET title = ?, description = ?, category_id = ?, "
               "series = ?, problem_no = ?, problem = ? WHERE id = ?",
               (title[:200], description, category_id, series, problem_no, problem_name, vid))
    db.commit()
    return jsonify(video_json(find_video(vid)))


@app.delete("/api/<key>/videos/<ref>")
@api_enabled
@token_required
def api_delete_video(ref):
    v = api_find(ref)
    if not v:
        return api_error("Video not found.", 404)
    delete_video_row(v)
    return jsonify(ok=True, deleted=v["id"], slug=v["slug"])


# ---------------------------------------------------------------- errors

@app.errorhandler(413)
def _too_large(_e):
    msg = _("File is too large (limit is {gb} GB).", gb=f"{MAX_UPLOAD_GB:g}")
    if wants_json():
        return jsonify(ok=False, error=msg), 413
    flash(msg, "error")
    return redirect(url_for("upload"))


@app.errorhandler(404)
def _not_found(_e):
    if is_api():
        return api_error("Not found.", 404)
    return render_template("message.html", title=_("Not found"),
                           message=_("That page or video doesn't exist.")), 404


@app.errorhandler(405)
def _not_allowed(e):
    if is_api():
        return api_error("Method not allowed.", 405)
    return e


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the video library server.")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"),
                        help="use 0.0.0.0 to allow other devices on your network")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "5000")))
    parser.add_argument("--reset-password", action="store_true",
                        help="forget the password set in the web interface and go back to "
                             "VIDEO_ADMIN_PASS (default: admin), then exit")
    args = parser.parse_args()
    if args.reset_password:
        with app.app_context():
            set_settings(password_hash=None,
                         password_version=str(int(password_version()) + 1))
        print("Password reset. Log in with the VIDEO_ADMIN_PASS password (default: admin).")
        raise SystemExit(0)
    try:
        from waitress import serve  # optional, more robust server
        print(f"Serving on http://{args.host}:{args.port} (waitress)")
        serve(app, host=args.host, port=args.port, threads=8,
              max_request_body_size=app.config["MAX_CONTENT_LENGTH"])
    except ImportError:
        app.run(host=args.host, port=args.port, threaded=True)
