# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the app locally

```bash
# Start (Windows)
python app.py
# or double-click start.bat

# The app runs at http://localhost:5000
# Stop with Ctrl+C
```

Python path on this machine: `C:\Users\Philip\AppData\Local\Programs\Python\Python312\python.exe`

Install dependencies:
```bash
pip install flask mutagen pyacoustid requests gunicorn
```

## Architecture

Single-file Flask backend (`app.py`) with a vanilla JS frontend. No build step.

**Request flow:**
1. `POST /upload` — saves MP3 to `uploads/`, reads ID3 tags and audio info, returns a `file_id` (UUID)
2. `POST /fingerprint` — generates audio fingerprint via fpcalc/Chromaprint, queries AcoustID, enriches with MusicBrainz
3. `POST /search_cover` — searches iTunes Search API for cover art, returns a URL
4. `GET /proxy_image?url=` — proxies the iTunes image URL server-side (browser CORS workaround)
5. `POST /update_tags` — writes ID3 tags + embedded cover art, renames file to `title - artist.mp3`
6. `GET /download/<file_id>` — serves the tagged file with its renamed filename

**File identity:** The browser never sees server paths. Every upload gets a UUID (`file_id`). The server keeps a `file_store = {file_id: filepath}` dict in memory. All subsequent routes (`/fingerprint`, `/update_tags`, `/download`) accept `file_id` and look up the real path internally. Uploading a new file sends the old `file_id` so the server can delete the previous file.

**fpcalc detection:** At startup, `app.py` looks for `fpcalc.exe` (Windows) or `fpcalc` (Linux) next to itself and sets `os.environ['FPCALC']` if found. pyacoustid reads this env var.

**API key:** `get_acoustid_key()` checks `ACOUSTID_API_KEY` env var first (used on Render), then falls back to `config.json` → `acoustid_api_key` (used locally). `config.json` is gitignored.

## Deployment (Render)

- `render.yaml` configures the service — build command is `bash build.sh`
- `build.sh` runs `pip install` then downloads the Linux fpcalc binary from GitHub releases into the project root
- Set `ACOUSTID_API_KEY` as an environment variable in the Render dashboard (not in code)
- GitHub repo: https://github.com/phillosah/mp3tagger

## Key dependencies

| Package | Purpose |
|---|---|
| `mutagen` | Read/write ID3 tags and MP3 audio info |
| `pyacoustid` | Python wrapper for AcoustID fingerprint lookup |
| fpcalc (binary) | Chromaprint audio fingerprint generator — must be present separately |
| `requests` | MusicBrainz API, iTunes API, image proxy |
| `gunicorn` | Production WSGI server (Render only) |

## Version bumping

Version string appears in two places — update both together:
- `templates/index.html` — `<title>` tag and `<span class="header-version">`
