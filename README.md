# Video Library

A small self-hosted website for managing an educational video collection.

- Anyone who opens the site can browse, filter by category, search, watch and download.
- Logging in as **admin / admin** lets you upload, edit, delete and manage categories.

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
- **Edit** title, description, category and thumbnail (including "use current frame").
- **Categories page** to add, rename and delete categories.

Formats: any file can be stored and downloaded; in-browser playback and automatic
thumbnails work for formats the browser understands (MP4/H.264, WebM, most MOV).
Other formats (AVI, WMV, some MKV) get a placeholder thumbnail - you can upload an image instead.

## Where data lives

Everything is in the `data` folder - back this up to back up your library:

- `data/videos/` - the video files
- `data/thumbs/` - thumbnail images
- `data/library.db` - titles, categories, dates (SQLite)

## Settings (optional environment variables)

| Variable | Default | Purpose |
|---|---|---|
| `VIDEO_ADMIN_USER` | `admin` | Login username |
| `VIDEO_ADMIN_PASS` | `admin` | Login password - change this if the site is reachable by others |
| `VIDEO_MAX_UPLOAD_GB` | `20` | Largest allowed upload |
| `SITE_NAME` | `Video Library` | Name shown in the header |

Example (PowerShell): `$env:VIDEO_ADMIN_PASS = "something-strong"; python app.py`
