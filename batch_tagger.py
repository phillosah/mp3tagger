#!/usr/bin/env python3
# =============================================================================
# batch_tagger.py — Batch MP3 Tagger v1.0
#
# Scans a directory for MP3 files, fingerprints each one via AcoustID,
# fetches metadata from MusicBrainz and cover art from iTunes, writes the
# tags back to the file, and renames it to "title - artist.mp3".
#
# Config file : batch_config.json  (created automatically on first run)
# Log files   : logs/tagger_YYYYMMDD_HHMMSS.log
#               Each log starts with a summary block followed by per-file detail.
#
# Usage:
#   python batch_tagger.py
#   python batch_tagger.py --config path\to\other_config.json
# =============================================================================

import os
import sys
import json
import time
import platform
import base64
import argparse
import requests
import datetime
from pathlib import Path

# Allow Unicode output (✓ ✗ etc.) on Windows consoles
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

# ── Dependency checks ─────────────────────────────────────────────────────────

try:
    from mutagen.mp3 import MP3
    from mutagen.id3 import ID3, TIT2, TPE1, TALB, TDRC, APIC, ID3NoHeaderError
except ImportError:
    print("ERROR: mutagen not installed. Run: pip install mutagen")
    sys.exit(1)

try:
    import acoustid
except ImportError:
    print("ERROR: pyacoustid not installed. Run: pip install pyacoustid")
    sys.exit(1)

# ── Constants ─────────────────────────────────────────────────────────────────

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
VERSION      = "1.0"

# Default values written to batch_config.json on first run
DEFAULT_CONFIG = {
    "scan_directory":    "",
    "acoustid_api_key":  "",
    "log_directory":     "logs",
    "recursive":         True,
    "skip_already_tagged": False,
    "embed_cover_art":   True
}

# Characters illegal in Windows/Linux filenames
_ILLEGAL = r'/\:*?"<>|'


# ── Helpers ───────────────────────────────────────────────────────────────────

def sanitise(name: str) -> str:
    """Remove or replace characters that are illegal in file/folder names."""
    for ch in _ILLEGAL:
        name = name.replace(ch, '-')
    return name.strip(' .')


def safe_rename(title: str, artist: str) -> str:
    """Build a safe 'title - artist.mp3' filename."""
    return f"{sanitise(title)} - {sanitise(artist)}.mp3"


def load_config(path: str) -> dict:
    """Load the config file, creating a default one if it does not exist."""
    if not os.path.exists(path):
        with open(path, 'w') as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        print(f"Config file created: {path}")
        print("Edit it to set scan_directory and acoustid_api_key, then re-run.")
        sys.exit(0)

    with open(path) as f:
        cfg = {**DEFAULT_CONFIG, **json.load(f)}

    # Fall back to the web app's config.json for the API key if not set here
    if not cfg['acoustid_api_key']:
        main_cfg_path = os.path.join(SCRIPT_DIR, 'config.json')
        if os.path.exists(main_cfg_path):
            with open(main_cfg_path) as f:
                cfg['acoustid_api_key'] = json.load(f).get('acoustid_api_key', '')

    # Also accept the environment variable (same as the web app)
    cfg['acoustid_api_key'] = os.environ.get('ACOUSTID_API_KEY', cfg['acoustid_api_key'])

    return cfg


def find_fpcalc() -> str:
    """Return the path to the fpcalc binary, or 'fpcalc' to rely on PATH."""
    name  = 'fpcalc.exe' if platform.system() == 'Windows' else 'fpcalc'
    local = os.path.join(SCRIPT_DIR, name)
    if os.path.exists(local):
        os.environ['FPCALC'] = local
        return local
    return name   # let pyacoustid search PATH


def collect_mp3s(directory: str, recursive: bool) -> list:
    """Return a sorted list of MP3 file paths found in directory."""
    p = Path(directory)
    pattern = '**/*.mp3' if recursive else '*.mp3'
    return sorted(str(f) for f in p.glob(pattern) if f.is_file())


# ── Per-file processing ───────────────────────────────────────────────────────

def read_existing_tags(filepath: str) -> dict:
    """Read current ID3 tags from an MP3. Returns a dict with string values."""
    try:
        tags = ID3(filepath)
        return {
            'title':  str(tags.get('TIT2', '')),
            'artist': str(tags.get('TPE1', '')),
            'album':  str(tags.get('TALB', '')),
            'date':   str(tags.get('TDRC', '')),
        }
    except ID3NoHeaderError:
        return {'title': '', 'artist': '', 'album': '', 'date': ''}


def fingerprint_file(filepath: str, api_key: str) -> tuple:
    """Fingerprint an MP3 and return (match_dict, logs_list).

    match_dict contains: title, artist, album, date, score, recording_id
    Returns (None, logs) if no match is found.
    """
    logs = []
    logs.append("  Generating audio fingerprint...")

    try:
        best_score  = 0
        best_rid    = None
        best_title  = None
        best_artist = None

        for score, rid, title, artist in acoustid.match(api_key, filepath, force_fpcalc=True):
            if score > best_score:
                best_score, best_rid, best_title, best_artist = score, rid, title, artist
            if best_score >= 0.8:
                break

        if not best_rid:
            logs.append("  No AcoustID match found")
            return None, logs

        logs.append(f"  AcoustID match ({best_score:.0%} confidence), recording: {best_rid}")

        # Enrich with MusicBrainz (album + year)
        album = ''
        date  = ''
        try:
            mb_url  = f'https://musicbrainz.org/ws/2/recording/{best_rid}?inc=releases+artists&fmt=json'
            headers = {'User-Agent': f'BatchTagger/{VERSION} (github.com/phillosah/mp3tagger)'}
            resp    = requests.get(mb_url, headers=headers, timeout=10)

            if resp.status_code == 200:
                mb = resp.json()
                best_title  = mb.get('title', best_title) or best_title
                ac = mb.get('artist-credit', [])
                if ac:
                    best_artist = ac[0].get('artist', {}).get('name', best_artist) or best_artist
                releases = mb.get('releases', [])
                if releases:
                    album    = releases[0].get('title', '')
                    raw_date = releases[0].get('date', '')
                    date     = raw_date[:4] if raw_date else ''
                logs.append(f"  MusicBrainz: title=\"{best_title}\", artist=\"{best_artist}\", album=\"{album}\", date=\"{date}\"")
            else:
                logs.append(f"  MusicBrainz HTTP {resp.status_code} — skipping album/date")

            # Respect MusicBrainz rate limit (1 req/sec)
            time.sleep(1.1)

        except Exception as e:
            logs.append(f"  MusicBrainz lookup failed: {e}")

        match = {
            'title':        best_title  or '',
            'artist':       best_artist or '',
            'album':        album,
            'date':         date,
            'score':        best_score,
            'recording_id': best_rid,
        }
        return match, logs

    except acoustid.FingerprintGenerationError as e:
        msg = str(e)
        if 'fpcalc' in msg.lower() or 'not found' in msg.lower():
            logs.append("  ERROR: fpcalc not found — install Chromaprint and add to PATH")
        else:
            logs.append(f"  ERROR: fingerprint generation failed: {msg}")
        return None, logs
    except acoustid.WebServiceError as e:
        logs.append(f"  ERROR: AcoustID web service error: {e}")
        return None, logs
    except Exception as e:
        logs.append(f"  ERROR: fingerprinting failed: {e}")
        return None, logs


def fetch_cover_art(artist: str, title: str) -> tuple:
    """Search iTunes for cover art. Returns (image_bytes, mime, logs)."""
    logs = []
    term = f"{artist} {title}".strip()
    logs.append(f"  Searching iTunes cover art: \"{term}\"")

    try:
        params = {'term': term, 'media': 'music', 'entity': 'musicTrack', 'limit': 5}
        resp   = requests.get('https://itunes.apple.com/search', params=params, timeout=10)

        if resp.status_code == 200:
            results = resp.json().get('results', [])
            if results:
                url = results[0].get('artworkUrl100', '')
                url = url.replace('100x100bb', '600x600bb').replace('100x100', '600x600')
                img_resp = requests.get(url, timeout=15)
                mime     = img_resp.headers.get('content-type', 'image/jpeg').split(';')[0]
                logs.append(f"  Cover art found ({len(img_resp.content) // 1024} KB)")
                return img_resp.content, mime, logs

        logs.append("  No cover art found on iTunes")
        return None, None, logs

    except Exception as e:
        logs.append(f"  Cover art search failed: {e}")
        return None, None, logs


def write_tags(filepath: str, match: dict, cover_bytes: bytes, mime: str) -> list:
    """Write ID3 tags to the file. Returns a list of log lines."""
    logs = []
    try:
        try:
            tags = ID3(filepath)
        except ID3NoHeaderError:
            tags = ID3()

        if match.get('title'):
            tags['TIT2'] = TIT2(encoding=3, text=match['title'])
        if match.get('artist'):
            tags['TPE1'] = TPE1(encoding=3, text=match['artist'])
        if match.get('album'):
            tags['TALB'] = TALB(encoding=3, text=match['album'])
        if match.get('date'):
            tags['TDRC'] = TDRC(encoding=3, text=match['date'])

        if cover_bytes:
            for key in list(tags.keys()):
                if key.startswith('APIC'):
                    del tags[key]
            tags['APIC:'] = APIC(encoding=3, mime=mime, type=3, desc='Cover', data=cover_bytes)
            logs.append(f"  Cover art embedded")

        tags.save(filepath)
        logs.append("  Tags written successfully")

    except Exception as e:
        logs.append(f"  ERROR: failed to write tags: {e}")

    return logs


def rename_file(filepath: str, title: str, artist: str) -> tuple:
    """Rename the file to 'title - artist.mp3'. Returns (new_path, log_line)."""
    if not title or not artist:
        return filepath, "  Rename skipped (title or artist missing)"

    new_name = safe_rename(title, artist)
    new_path = os.path.join(os.path.dirname(filepath), new_name)

    # If target name already exists and is a different file, add a counter
    counter = 1
    base_new = new_path
    while os.path.exists(new_path) and os.path.abspath(new_path) != os.path.abspath(filepath):
        stem = Path(base_new).stem
        new_path = os.path.join(os.path.dirname(filepath), f"{stem} ({counter}).mp3")
        counter += 1

    try:
        os.rename(filepath, new_path)
        return new_path, f"  Renamed: {os.path.basename(new_path)}"
    except Exception as e:
        return filepath, f"  Rename failed: {e}"


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Batch MP3 Tagger')
    parser.add_argument('--config', default=os.path.join(SCRIPT_DIR, 'batch_config.json'),
                        help='Path to config file (default: batch_config.json)')
    args = parser.parse_args()

    cfg = load_config(args.config)

    # Validate config
    scan_dir = cfg.get('scan_directory', '').strip()
    if not scan_dir:
        print("ERROR: scan_directory is not set in batch_config.json")
        sys.exit(1)
    if not os.path.isdir(scan_dir):
        print(f"ERROR: scan_directory does not exist: {scan_dir}")
        sys.exit(1)
    if not cfg['acoustid_api_key']:
        print("ERROR: acoustid_api_key is not set in batch_config.json or config.json")
        sys.exit(1)

    find_fpcalc()

    # Prepare log directory
    log_dir = cfg['log_directory']
    if not os.path.isabs(log_dir):
        log_dir = os.path.join(SCRIPT_DIR, log_dir)
    os.makedirs(log_dir, exist_ok=True)

    run_dt    = datetime.datetime.now()
    log_name  = run_dt.strftime('tagger_%Y%m%d_%H%M%S.log')
    log_path  = os.path.join(log_dir, log_name)

    # Discover files
    mp3_files = collect_mp3s(scan_dir, cfg['recursive'])
    total     = len(mp3_files)

    if total == 0:
        print(f"No MP3 files found in: {scan_dir}")
        sys.exit(0)

    print(f"\nBatch Tagger v{VERSION}  —  {run_dt.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Directory : {scan_dir}")
    print(f"Files found: {total}")
    print(f"Log file  : {log_path}\n")
    print("=" * 72)

    # ── Process each file ────────────────────────────────────────────────────

    results   = []   # list of dicts, one per file
    all_lines = []   # all log lines, written to file after run

    start_time = time.time()

    for idx, filepath in enumerate(mp3_files, 1):
        filename   = os.path.basename(filepath)
        file_lines = []   # log lines for this file
        status     = 'success'
        note       = ''

        ts = datetime.datetime.now().strftime('%H:%M:%S')
        header = f"[{ts}] ({idx}/{total}) {filename}"
        print(header)
        file_lines.append(header)

        # Read existing tags
        existing = read_existing_tags(filepath)
        tag_summary = f"  Existing tags: title=\"{existing['title']}\", artist=\"{existing['artist']}\""
        print(tag_summary)
        file_lines.append(tag_summary)

        # Skip if already tagged and config says so
        if cfg['skip_already_tagged'] and existing['title'] and existing['artist']:
            skip_msg = "  SKIPPED (already tagged)"
            print(skip_msg)
            file_lines.append(skip_msg)
            file_lines.append("")
            all_lines.extend(file_lines)
            results.append({'file': filename, 'status': 'skipped', 'note': 'already tagged'})
            continue

        # Fingerprint
        match, fp_logs = fingerprint_file(filepath, cfg['acoustid_api_key'])
        for line in fp_logs:
            print(line)
        file_lines.extend(fp_logs)

        if match is None:
            status = 'failed'
            note   = 'no fingerprint match'
            result_line = f"  ✗ FAILED — no match found"
            print(result_line)
            file_lines.append(result_line)
            file_lines.append("")
            all_lines.extend(file_lines)
            results.append({'file': filename, 'status': status, 'note': note})
            continue

        # Cover art
        cover_bytes, mime, cover_logs = None, None, []
        if cfg['embed_cover_art'] and match['title'] and match['artist']:
            cover_bytes, mime, cover_logs = fetch_cover_art(match['artist'], match['title'])
        for line in cover_logs:
            print(line)
        file_lines.extend(cover_logs)

        # Write tags
        tag_logs = write_tags(filepath, match, cover_bytes, mime)
        for line in tag_logs:
            print(line)
        file_lines.extend(tag_logs)

        # Check if writing actually failed
        if any('ERROR' in l for l in tag_logs):
            status = 'failed'
            note   = 'tag write error'

        # Rename file
        new_path, rename_log = rename_file(filepath, match['title'], match['artist'])
        print(rename_log)
        file_lines.append(rename_log)

        result_icon = "✓" if status == 'success' else "✗"
        result_line = f"  {result_icon} {'DONE' if status == 'success' else 'FAILED'}"
        print(result_line)
        file_lines.append(result_line)
        file_lines.append("")   # blank line between files

        all_lines.extend(file_lines)
        results.append({
            'file':     filename,
            'renamed':  os.path.basename(new_path),
            'title':    match['title'],
            'artist':   match['artist'],
            'album':    match['album'],
            'date':     match['date'],
            'score':    match['score'],
            'status':   status,
            'note':     note,
        })

    # ── Build log file ───────────────────────────────────────────────────────

    elapsed  = time.time() - start_time
    n_ok     = sum(1 for r in results if r['status'] == 'success')
    n_fail   = sum(1 for r in results if r['status'] == 'failed')
    n_skip   = sum(1 for r in results if r['status'] == 'skipped')

    sep   = "=" * 72
    summary_lines = [
        sep,
        f"  BATCH TAGGING SUMMARY — {run_dt.strftime('%Y-%m-%d %H:%M:%S')}",
        sep,
        f"  Script version  : v{VERSION}",
        f"  Directory       : {scan_dir}",
        f"  Recursive       : {'Yes' if cfg['recursive'] else 'No'}",
        f"  Skip if tagged  : {'Yes' if cfg['skip_already_tagged'] else 'No'}",
        f"  Embed cover art : {'Yes' if cfg['embed_cover_art'] else 'No'}",
        f"  Total files     : {total}",
        f"  ✓ Successful    : {n_ok}",
        f"  ✗ Failed        : {n_fail}",
        f"  — Skipped       : {n_skip}",
        f"  Duration        : {elapsed:.1f}s",
        sep,
    ]

    # Results table
    if results:
        summary_lines.append("")
        summary_lines.append("  FILE RESULTS:")
        summary_lines.append(f"  {'STATUS':<10} {'SCORE':<8} {'ORIGINAL FILE':<40} {'RENAMED TO'}")
        summary_lines.append("  " + "-" * 100)
        for r in results:
            score_str  = f"{r['score']:.0%}" if r.get('score') else '—'
            renamed_to = r.get('renamed', '—') if r['status'] == 'success' else r.get('note', '—')
            original   = r['file'][:38] + '..' if len(r['file']) > 40 else r['file']
            icon       = '✓' if r['status'] == 'success' else ('—' if r['status'] == 'skipped' else '✗')
            summary_lines.append(f"  {icon} {r['status']:<8} {score_str:<8} {original:<40} {renamed_to}")

    summary_lines += ["", sep, "", "DETAILED LOG", sep, ""]

    # Write log: summary at top, then per-file detail
    with open(log_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(summary_lines) + '\n')
        f.write('\n'.join(all_lines) + '\n')

    # Print final summary to console
    print("\n" + sep)
    print(f"  DONE  |  ✓ {n_ok} succeeded  ✗ {n_fail} failed  — {n_skip} skipped  |  {elapsed:.1f}s")
    print(f"  Log   : {log_path}")
    print(sep)


if __name__ == '__main__':
    main()
