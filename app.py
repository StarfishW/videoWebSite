"""Video Library - a small self-hosted site for managing an educational video collection.

Run:  python app.py            (then open http://127.0.0.1:5000)
Login: admin / admin           (override with VIDEO_ADMIN_USER / VIDEO_ADMIN_PASS)
"""
import argparse
import functools
import hmac
import mimetypes
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlparse

from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, send_from_directory, session, url_for)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
VIDEO_DIR = os.path.join(DATA_DIR, "videos")
THUMB_DIR = os.path.join(DATA_DIR, "thumbs")
DB_PATH = os.path.join(DATA_DIR, "library.db")
for _d in (VIDEO_DIR, THUMB_DIR):
    os.makedirs(_d, exist_ok=True)

SITE_NAME = os.environ.get("SITE_NAME", "Video Library")
ADMIN_USER = os.environ.get("VIDEO_ADMIN_USER", "admin")
ADMIN_PASS = os.environ.get("VIDEO_ADMIN_PASS", "admin")
MAX_UPLOAD_GB = float(os.environ.get("VIDEO_MAX_UPLOAD_GB", "20"))

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


def _load_secret_key():
    path = os.path.join(DATA_DIR, "secret_key")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    key = secrets.token_hex(32)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(key)
    return key


app = Flask(__name__)
app.config.update(
    SECRET_KEY=_load_secret_key(),
    MAX_CONTENT_LENGTH=int(MAX_UPLOAD_GB * 1024 ** 3),
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)


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


def get_video(vid):
    row = get_db().execute(
        "SELECT v.*, c.name AS category_name FROM videos v "
        "LEFT JOIN categories c ON c.id = v.category_id WHERE v.id = ?", (vid,)
    ).fetchone()
    if row is None:
        abort(404)
    return row


def all_categories():
    return get_db().execute(
        "SELECT c.id, c.name, COUNT(v.id) AS n FROM categories c "
        "LEFT JOIN videos v ON v.category_id = c.id "
        "GROUP BY c.id ORDER BY c.name COLLATE NOCASE"
    ).fetchall()


# ---------------------------------------------------------------- auth / csrf

def is_admin():
    return session.get("admin") is True


def wants_json():
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def admin_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not is_admin():
            if wants_json():
                return jsonify(ok=False, error="Please log in again."), 401
            return redirect(url_for("login", next=request.full_path))
        return view(*args, **kwargs)
    return wrapped


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


@app.before_request
def _csrf_protect():
    if request.method == "POST":
        expected = session.get("csrf")
        sent = request.form.get("csrf") or request.headers.get("X-CSRF-Token")
        if not expected or not sent or not hmac.compare_digest(expected, sent):
            if wants_json():
                return jsonify(ok=False, error="Form expired - reload the page and try again."), 400
            flash("Form expired - please try again.", "error")
            return redirect(request.referrer or url_for("index"))


@app.context_processor
def _template_globals():
    return {"is_admin": is_admin(), "csrf_token": csrf_token, "site_name": SITE_NAME}


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
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").strftime("%b %d, %Y").replace(" 0", " ")
    except (TypeError, ValueError):
        return value or ""


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


def resolve_category(form):
    """Return a category id (creating a new category if requested) or None."""
    choice = form.get("category", "")
    if choice == "__new__":
        name = " ".join(form.get("new_category", "").split())[:80]
        if not name:
            return None
        db = get_db()
        row = db.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()
        if row:
            return row["id"]
        cur = db.execute("INSERT INTO categories (name) VALUES (?)", (name,))
        db.commit()
        return cur.lastrowid
    if choice.isdigit():
        row = get_db().execute("SELECT id FROM categories WHERE id = ?", (int(choice),)).fetchone()
        return row["id"] if row else None
    return None


def parse_duration(value):
    try:
        d = float(value)
        return d if 0 < d < 10 ** 7 else None
    except (TypeError, ValueError):
        return None


def safe_next(target):
    if target:
        parts = urlparse(target)
        if not parts.scheme and not parts.netloc and target.startswith("/"):
            return target
    return url_for("index")


# ---------------------------------------------------------------- public pages

@app.route("/")
def index():
    q = request.args.get("q", "").strip()
    cat = request.args.get("category", "")
    sort = request.args.get("sort", "new")

    sql = ("SELECT v.*, c.name AS category_name FROM videos v "
           "LEFT JOIN categories c ON c.id = v.category_id WHERE 1 = 1")
    params = []
    if cat == "none":
        sql += " AND v.category_id IS NULL"
    elif cat.isdigit():
        sql += " AND v.category_id = ?"
        params.append(int(cat))
    if q:
        sql += " AND (v.title LIKE ? OR v.description LIKE ? OR v.original_name LIKE ?)"
        params += [f"%{q}%"] * 3
    sql += {
        "old": " ORDER BY v.created_at ASC, v.id ASC",
        "title": " ORDER BY v.title COLLATE NOCASE ASC",
    }.get(sort, " ORDER BY v.created_at DESC, v.id DESC")

    db = get_db()
    videos = db.execute(sql, params).fetchall()
    totals = db.execute(
        "SELECT COUNT(*) AS total, SUM(category_id IS NULL) AS uncategorized FROM videos"
    ).fetchone()
    active_name = None
    if cat.isdigit():
        row = db.execute("SELECT name FROM categories WHERE id = ?", (int(cat),)).fetchone()
        active_name = row["name"] if row else None
    elif cat == "none":
        active_name = "Uncategorized"
    return render_template(
        "index.html", videos=videos, categories=all_categories(), q=q, cat=cat,
        sort=sort, total=totals["total"] or 0, uncategorized=totals["uncategorized"] or 0,
        active_name=active_name,
    )


@app.route("/video/<int:vid>")
def video(vid):
    v = get_video(vid)
    if v["category_id"] is not None:
        related = get_db().execute(
            "SELECT * FROM videos WHERE category_id = ? AND id != ? "
            "ORDER BY created_at DESC LIMIT 8", (v["category_id"], vid)).fetchall()
    else:
        related = []
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
            flash("Logged in. You can now upload and edit videos.", "ok")
            return redirect(safe_next(request.form.get("next")))
        flash("Wrong username or password.", "error")
    return render_template("login.html", next=request.args.get("next", ""))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Logged out.", "ok")
    return redirect(url_for("index"))


# ---------------------------------------------------------------- admin: videos

@app.route("/upload", methods=["GET", "POST"])
@admin_required
def upload():
    if request.method == "GET":
        return render_template("upload.html", categories=all_categories(),
                               preselect=request.args.get("category", ""),
                               max_gb=MAX_UPLOAD_GB)

    back = url_for("upload")
    file = request.files.get("video")
    if not file or not file.filename:
        return failed("Please choose a video file.", back)
    ext = ext_of(file.filename)
    if ext not in VIDEO_EXT:
        return failed(f"'.{ext}' is not a supported video type.", back)

    original = os.path.basename(file.filename.replace("\\", "/"))
    title = request.form.get("title", "").strip() or original.rsplit(".", 1)[0]
    stored = f"{uuid.uuid4().hex}.{ext}"
    path = os.path.join(VIDEO_DIR, stored)
    file.save(path)

    mime = file.mimetype if (file.mimetype or "").startswith("video/") else mimetypes.guess_type(original)[0]
    db = get_db()
    cur = db.execute(
        "INSERT INTO videos (title, description, category_id, filename, original_name, mime, "
        "size, duration, thumb, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (title[:200], request.form.get("description", "").strip(), resolve_category(request.form),
         stored, original, mime, os.path.getsize(path),
         parse_duration(request.form.get("duration")), save_thumbnail(request.files.get("thumbnail")),
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    db.commit()
    flash(f"Uploaded “{title}”.", "ok")
    return done(url_for("video", vid=cur.lastrowid))


@app.route("/video/<int:vid>/edit", methods=["GET", "POST"])
@admin_required
def edit(vid):
    v = get_video(vid)
    if request.method == "GET":
        return render_template("edit.html", v=v, categories=all_categories())

    title = request.form.get("title", "").strip()
    if not title:
        return failed("Title can't be empty.", url_for("edit", vid=vid))
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
    flash("Changes saved.", "ok")
    return done(url_for("video", vid=vid))


@app.route("/video/<int:vid>/delete", methods=["POST"])
@admin_required
def delete(vid):
    v = get_video(vid)
    db = get_db()
    db.execute("DELETE FROM videos WHERE id = ?", (vid,))
    db.commit()
    remove_file(VIDEO_DIR, v["filename"])
    remove_file(THUMB_DIR, v["thumb"])
    flash(f"Deleted “{v['title']}”.", "ok")
    return redirect(url_for("index"))


# ---------------------------------------------------------------- admin: categories

@app.route("/categories", methods=["GET", "POST"])
@admin_required
def categories():
    if request.method == "POST":
        name = " ".join(request.form.get("name", "").split())[:80]
        if not name:
            flash("Category name can't be empty.", "error")
        else:
            try:
                get_db().execute("INSERT INTO categories (name) VALUES (?)", (name,))
                get_db().commit()
                flash(f"Added category “{name}”.", "ok")
            except sqlite3.IntegrityError:
                flash(f"A category named “{name}” already exists.", "error")
        return redirect(url_for("categories"))
    return render_template("categories.html", categories=all_categories())


@app.route("/categories/<int:cid>/rename", methods=["POST"])
@admin_required
def rename_category(cid):
    name = " ".join(request.form.get("name", "").split())[:80]
    if not name:
        flash("Category name can't be empty.", "error")
    else:
        try:
            get_db().execute("UPDATE categories SET name = ? WHERE id = ?", (name, cid))
            get_db().commit()
            flash("Category renamed.", "ok")
        except sqlite3.IntegrityError:
            flash(f"A category named “{name}” already exists.", "error")
    return redirect(url_for("categories"))


@app.route("/categories/<int:cid>/delete", methods=["POST"])
@admin_required
def delete_category(cid):
    db = get_db()
    db.execute("UPDATE videos SET category_id = NULL WHERE category_id = ?", (cid,))
    db.execute("DELETE FROM categories WHERE id = ?", (cid,))
    db.commit()
    flash("Category deleted. Its videos are now uncategorized.", "ok")
    return redirect(url_for("categories"))


# ---------------------------------------------------------------- errors

@app.errorhandler(413)
def _too_large(_e):
    msg = f"File is too large (limit is {MAX_UPLOAD_GB:g} GB)."
    if wants_json():
        return jsonify(ok=False, error=msg), 413
    flash(msg, "error")
    return redirect(url_for("upload"))


@app.errorhandler(404)
def _not_found(_e):
    return render_template("message.html", title="Not found",
                           message="That page or video doesn't exist."), 404


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
