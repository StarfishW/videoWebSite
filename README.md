# Video Library

A small self-hosted website for managing an educational video collection.

- Anyone who opens the site can browse, filter by category, search, watch and download.
- Logging in as **admin / admin** lets you upload, edit, delete and manage categories.
  Change the password right after the first login (**Password** in the header).

## Start with Docker (one click)

Requires [Docker Desktop](https://www.docker.com/products/docker-desktop/) (Windows/macOS) or Docker Engine (Linux).

- **Windows:** double-click `docker-start.bat`
- **Linux / macOS:** `./docker-start.sh`
- **Or anywhere:** `docker compose up -d --build`

Open http://localhost:5000. Your library is stored in the `data` folder next to
`docker-compose.yml`, so it survives restarts and rebuilds.
Stop with `docker compose down`; view logs with `docker compose logs -f`.

To change the port, password, etc., copy `.env.example` to `.env`, edit it, and start again.

## Start without Docker

Double-click `start.bat`, or run:

```
pip install -r requirements.txt
python app.py
```

Then open http://127.0.0.1:5000

To let other computers/phones on your network use it: `python app.py --host 0.0.0.0`
(open `http://<this-PC's-IP>:5000`; Windows Firewall may ask for permission).

## Features

- **Upload** with drag & drop and a progress bar. A thumbnail is captured from the video
  automatically in your browser, or you can choose your own image.
- **Thumbnails, upload date, category and duration** shown on every video card.
- **Filter** by category (chips at the top), **search** by title/description, **sort** by date or title.
- **Download** the original file with its original name. Playback supports seeking.
- **Share** without login: short links like `/v/3fa1c09b` (the first 8 characters of the file's
  MD5; a longer prefix is used if two different files clash, and identical files get `-2`, `-3`).
  Also an embed code, a QR code, and an optional start time (`?t=90`). Links always use the domain
  the visitor is on. Behind a reverse proxy, pass `X-Forwarded-Host` / `X-Forwarded-Proto`.
  Old `/video/<id>` links redirect to the short link.
- **Edit** title, description, category and thumbnail (including "use current frame").
- **Categories page** to add, rename and delete categories.

- **Languages:** English and 中文. Pick one from the 🌐 menu in the header; the choice is remembered.
  The first visit follows the browser's language. Translations live in `translations.py`.
  To add a language, add it to `LANGUAGES` and add a dictionary to `CATALOGS` there.
- **Upload API** for scripts and other programs (see below).

## Upload API

The API is **off** until you turn it on. Log in, open the **API** page and click
**Turn on API and generate URL**. That creates a random secret API URL such as
`http://localhost:5000/api/NHDtGjW7_px1V2qUq5_TzZQ0yA-QLYaA` and a separate token.
Any other `/api/...` address returns 404. On the same page you can make a new URL,
make a new token, or turn the API off. The old URL or token stops working right away.

```
curl -X POST <API URL>/videos \
  -H "Authorization: Bearer <token>" \
  -F "file=@lecture01.mp4" -F "title=Lecture 1" -F "category=Mathematics"
```

Paths below are relative to your API URL.

| Request | Token | What it does |
|---|---|---|
| `POST /videos` | yes | Upload (multipart): `file` (required), `title`, `description`, `category` (name, created if new) or `category_id`, `thumbnail` |
| `GET /videos` | no | List; optional `category=<id>\|none`, `q`, `sort=new\|old\|title`, `limit`, `offset` |
| `GET /videos/<id or slug>` | no | One video with stream/download/thumbnail URLs |
| `PATCH /videos/<id or slug>` | yes | JSON: `title`, `description`, `category` or `category_id` |
| `DELETE /videos/<id or slug>` | yes | Delete a video and its files |
| `GET /categories` | no | Categories with counts |
| `POST /categories` | yes | JSON: `{"name": "..."}` |

The token can also be sent as `X-API-Key: <token>`.
If ffmpeg is installed (it's included in the Docker image), uploads get a thumbnail and duration
generated on the server.

## Changing the password

Log in and click **Password** in the header. Changing it logs out every other device.
The new password is stored hashed in `data/library.db` and replaces `VIDEO_ADMIN_PASS`.

Forgot it? Reset to the `VIDEO_ADMIN_PASS` password (default `admin`):

- Docker: `docker compose exec video-library python app.py --reset-password`
- Without Docker: `python app.py --reset-password`

Formats: any file can be stored and downloaded; in-browser playback and automatic
thumbnails work for formats the browser understands (MP4/H.264, WebM, most MOV).
Other formats (AVI, WMV, some MKV) get a placeholder thumbnail - you can upload an image instead.

## Where data lives

Everything is in the `data` folder - back this up to back up your library:

- `data/videos/` - the video files
- `data/thumbs/` - thumbnail images
- `data/library.db` - titles, categories, dates, password and API settings (SQLite)

## Settings (optional environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `VIDEO_ADMIN_USER` | `admin` | Login username |
| `VIDEO_ADMIN_PASS` | `admin` | Starting password (used until you change it on the Password page) |
| `VIDEO_MAX_UPLOAD_GB` | `20` | Largest allowed upload |
| `SITE_NAME` | `Video Library` | Name shown in the header |
| `TRUST_PROXY` | `1` | Use `X-Forwarded-Host/Proto` headers for links; set `0` if not behind a proxy and you want to ignore them |

Example (PowerShell): `$env:VIDEO_ADMIN_PASS = "something-strong"; python app.py`
