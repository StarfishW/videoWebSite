"""Video Library - a small self-hosted site for managing an educational video collection.

Run:  python app.py            (then open http://127.0.0.1:5000)
Login: admin / admin           (override with VIDEO_ADMIN_USER / VIDEO_ADMIN_PASS)
API:  see /api-docs after logging in
"""
import argparse
import functools
import hmac
import mimetypes
import os
import secrets
import shutil
import sqlite3
import subprocess
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlparse

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, send_from_directory, session, url_for)

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
API_TOKEN_ENV = os.environ.get("VIDEO_API_TOKEN", "").strip()
API_TOKEN_FILE = os.path.join(DATA_DIR, "api_token")

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")

VIDEO_EXT = {"mp4", "m4v", "webm", "ogv", "ogg", "mov", "mkv", "avi", "wmv",
             "flv", "mpg", "mpeg", "3gp", "ts"}
IMAGE_EXT = {"jpg", "jpeg", "png", "webp", "gif"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
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
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_videos_category ON videos(category_id);
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


def init_db():
    with sqlite3.connect(DB_PATH) as db:
        db.executescript(SCHEMA)


init_db()


def find_video(vid):
    return get_db().execute(
        "SELECT v.*, c.name AS category_name FROM videos v "
        "LEFT JOIN categories c ON c.id = v.category_id WHERE v.id = ?", (vid,)
    ).fetchone()


def get_video(vid):
    row = find_video(vid)
    if row is None:
        abort(404)
    return row


def all_categories():
    return get_db().execute(
        "SELECT c.id, c.name, COUNT(v.id) AS n FROM categories c "
        "LEFT JOIN videos v ON v.category_id = c.id "
        "GROUP BY c.id ORDER BY c.name COLLATE NOCASE"
    ).fetchall()


def query_videos(cat="", q="", sort="new", limit=None, offset=0):
    sql = ("SELECT v.*, c.name AS category_name FROM videos v "
           "LEFT JOIN categories c ON c.id = v.category_id WHERE 1 = 1")
    params = []
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

def is_admin():
    return session.get("admin") is True


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


def api_token():
    return API_TOKEN_ENV or _read_or_create(API_TOKEN_FILE, lambda: secrets.token_urlsafe(32))


def token_required(view):
    """API auth: 'Authorization: Bearer <token>' or 'X-API-Key: <token>'."""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        sent = auth[7:].strip() if auth.lower().startswith("bearer ") else request.headers.get("X-API-Key", "")
        if not sent or not hmac.compare_digest(sent, api_token()):
            return api_error("Missing or invalid API token.", 401)
        return view(*args, **kwargs)
    return wrapped


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


@app.before_request
def _csrf_protect():
    if request.method == "POST" and not is_api():
        expected = session.get("csrf")
        sent = request.form.get("csrf") or request.headers.get("X-CSRF-Token")
        if not expected or not sent or not hmac.compare_digest(expected, sent):
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
    }


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


def category_by_name(name, create=True):
    name = " ".join((name or "").split())[:80]
    if not name:
        return None
    db = get_db()
    row = db.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    if not create:
        return None
    cur = db.execute("INSERT INTO categories (name) VALUES (?)", (name,))
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
        return category_by_name(form.get("new_category", ""))
    return category_by_id(choice)


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


def store_video(file, title, description, category_id, thumbnail_file=None, duration=None):
    """Save an uploaded video + metadata. Returns the new video id."""
    if not file or not file.filename:
        raise UploadError(_("Please choose a video file."))
    ext = ext_of(file.filename)
    if ext not in VIDEO_EXT:
        raise UploadError(_("'.{ext}' is not a supported video type.", ext=ext))

    original = os.path.basename(file.filename.replace("\\", "/"))
    title = (title or "").strip() or original.rsplit(".", 1)[0]
    stored = f"{uuid.uuid4().hex}.{ext}"
    path = os.path.join(VIDEO_DIR, stored)
    file.save(path)

    duration = parse_duration(duration) or probe_duration(path)
    thumb = save_thumbnail(thumbnail_file) or generate_thumbnail(path, duration)
    mime = file.mimetype if (file.mimetype or "").startswith("video/") else mimetypes.guess_type(original)[0]
    db = get_db()
    cur = db.execute(
        "INSERT INTO videos (title, description, category_id, filename, original_name, mime, "
        "size, duration, thumb, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (title[:200], (description or "").strip(), category_id, stored, original, mime,
         os.path.getsize(path), duration, thumb, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
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
    q = request.args.get("q", "").strip()
    cat = request.args.get("category", "")
    sort = request.args.get("sort", "new")
    db = get_db()
    totals = db.execute(
        "SELECT COUNT(*) AS total, SUM(category_id IS NULL) AS uncategorized FROM videos"
    ).fetchone()
    active_name = None
    if cat.isdigit():
        row = db.execute("SELECT name FROM categories WHERE id = ?", (int(cat),)).fetchone()
        active_name = row["name"] if row else None
    elif cat == "none":
        active_name = _("Uncategorized")
    return render_template(
        "index.html", videos=query_videos(cat, q, sort), categories=all_categories(),
        q=q, cat=cat, sort=sort, total=totals["total"] or 0,
        uncategorized=totals["uncategorized"] or 0, active_name=active_name,
    )


@app.route("/video/<int:vid>")
def video(vid):
    v = get_video(vid)
    related = []
    if v["category_id"] is not None:
        related = get_db().execute(
            "SELECT * FROM videos WHERE category_id = ? AND id != ? "
            "ORDER BY created_at DESC LIMIT 8", (v["category_id"], vid)).fetchall()
    return render_template("video.html", v=v, related=related)


@app.route("/media/<int:vid>")
def media(vid):
    v = get_video(vid)
    mime = v["mime"] or mimetypes.guess_type(v["original_name"])[0] or "application/octet-stream"
    return send_from_directory(VIDEO_DIR, v["filename"], mimetype=mime, conditional=True)


@app.route("/download/<int:vid>")
def download(vid):
    v = get_video(vid)
    return send_from_directory(VIDEO_DIR, v["filename"], as_attachment=True,
                               download_name=v["original_name"])


@app.route("/thumb/<int:vid>")
def thumb(vid):
    v = get_video(vid)
    if not v["thumb"]:
        abort(404)
    resp = send_from_directory(THUMB_DIR, v["thumb"])
    resp.cache_control.max_age = 86400
    return resp


# ---------------------------------------------------------------- login

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = request.form.get("username", "")
        pw = request.form.get("password", "")
        if hmac.compare_digest(user, ADMIN_USER) and hmac.compare_digest(pw, ADMIN_PASS):
            session.clear()
            session["admin"] = True
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
    return done(url_for("video", vid=vid))


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

    db = get_db()
    db.execute(
        "UPDATE videos SET title = ?, description = ?, category_id = ?, thumb = ?, duration = ? WHERE id = ?",
        (title[:200], request.form.get("description", "").strip(),
         resolve_category(request.form), thumb_name, duration, vid),
    )
    db.commit()
    flash(_("Changes saved."), "ok")
    return done(url_for("video", vid=vid))


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
        if not name:
            flash(_("Category name can't be empty."), "error")
        else:
            try:
                get_db().execute("INSERT INTO categories (name) VALUES (?)", (name,))
                get_db().commit()
                flash(_("Added category “{name}”.", name=name), "ok")
            except sqlite3.IntegrityError:
                flash(_("A category named “{name}” already exists.", name=name), "error")
        return redirect(url_for("categories"))
    return render_template("categories.html", categories=all_categories())


@app.route("/categories/<int:cid>/rename", methods=["POST"])
@admin_required
def rename_category(cid):
    name = _clean_name()
    if not name:
        flash(_("Category name can't be empty."), "error")
    else:
        try:
            get_db().execute("UPDATE categories SET name = ? WHERE id = ?", (name, cid))
            get_db().commit()
            flash(_("Category renamed."), "ok")
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


# ---------------------------------------------------------------- admin: API token page

@app.route("/api-docs")
@admin_required
def api_docs():
    return render_template("api.html", token=api_token(), token_from_env=bool(API_TOKEN_ENV),
                           base=request.host_url.rstrip("/"), ffmpeg=bool(FFMPEG),
                           max_gb=MAX_UPLOAD_GB)


@app.route("/api-docs/regenerate", methods=["POST"])
@admin_required
def regenerate_token():
    if API_TOKEN_ENV:
        flash(_("The API token is set by the VIDEO_API_TOKEN environment variable and can't be changed here."), "error")
    else:
        with open(API_TOKEN_FILE, "w", encoding="utf-8") as fh:
            fh.write(secrets.token_urlsafe(32))
        flash(_("New API token created. The old token no longer works."), "ok")
    return redirect(url_for("api_docs"))


# ---------------------------------------------------------------- REST API

def video_json(v):
    return {
        "id": v["id"],
        "title": v["title"],
        "description": v["description"],
        "category": ({"id": v["category_id"], "name": v["category_name"]}
                     if v["category_id"] is not None else None),
        "original_name": v["original_name"],
        "size": v["size"],
        "duration": v["duration"],
        "created_at": v["created_at"],
        "urls": {
            "page": url_for("video", vid=v["id"], _external=True),
            "stream": url_for("media", vid=v["id"], _external=True),
            "download": url_for("download", vid=v["id"], _external=True),
            "thumbnail": url_for("thumb", vid=v["id"], _external=True) if v["thumb"] else None,
        },
    }


@app.get("/api/categories")
def api_categories():
    return jsonify([{"id": c["id"], "name": c["name"], "count": c["n"]} for c in all_categories()])


@app.post("/api/categories")
@token_required
def api_create_category():
    data = request.get_json(silent=True) or request.form
    name = " ".join(str(data.get("name", "")).split())[:80]
    if not name:
        return api_error("Field 'name' is required.")
    existed = category_by_name(name, create=False)
    cid = existed or category_by_name(name)
    row = get_db().execute("SELECT name FROM categories WHERE id = ?", (cid,)).fetchone()
    return jsonify(id=cid, name=row["name"]), (200 if existed else 201)


@app.get("/api/videos")
def api_list_videos():
    try:
        limit = min(max(int(request.args.get("limit", 100)), 1), 1000)
        offset = max(int(request.args.get("offset", 0)), 0)
    except ValueError:
        return api_error("limit and offset must be integers.")
    rows = query_videos(request.args.get("category", ""), request.args.get("q", "").strip(),
                        request.args.get("sort", "new"), limit, offset)
    return jsonify([video_json(v) for v in rows])


@app.get("/api/videos/<int:vid>")
def api_get_video(vid):
    v = find_video(vid)
    return jsonify(video_json(v)) if v else api_error("Video not found.", 404)


@app.post("/api/videos")
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
    try:
        vid, _title = store_video(file, form.get("title"), form.get("description"), category_id,
                                  request.files.get("thumbnail"), form.get("duration"))
    except UploadError as e:
        return api_error(str(e))
    resp = jsonify(video_json(find_video(vid)))
    resp.status_code = 201
    resp.headers["Location"] = url_for("api_get_video", vid=vid, _external=True)
    return resp


@app.route("/api/videos/<int:vid>", methods=["PATCH"])
@token_required
def api_update_video(vid):
    v = find_video(vid)
    if not v:
        return api_error("Video not found.", 404)
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
    db = get_db()
    db.execute("UPDATE videos SET title = ?, description = ?, category_id = ? WHERE id = ?",
               (title[:200], str(data.get("description", v["description"])), category_id, vid))
    db.commit()
    return jsonify(video_json(find_video(vid)))


@app.delete("/api/videos/<int:vid>")
@token_required
def api_delete_video(vid):
    v = find_video(vid)
    if not v:
        return api_error("Video not found.", 404)
    delete_video_row(v)
    return jsonify(ok=True, deleted=vid)


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
    args = parser.parse_args()
    try:
        from waitress import serve  # optional, more robust server
        print(f"Serving on http://{args.host}:{args.port} (waitress)")
        serve(app, host=args.host, port=args.port, threads=8,
              max_request_body_size=app.config["MAX_CONTENT_LENGTH"])
    except ImportError:
        app.run(host=args.host, port=args.port, threaded=True)
