BATCH TAGGER — README
=====================
batch_tagger.py v1.0


WHAT IT DOES
------------
Scans a folder for MP3 files and for each one:
  1. Generates an audio fingerprint using Chromaprint (fpcalc)
  2. Identifies the track via the AcoustID database
  3. Fetches full metadata (title, artist, album, year) from MusicBrainz
  4. Searches iTunes for cover art and embeds it into the file
  5. Writes all tags to the MP3
  6. Renames the file to "Title - Artist.mp3"

All activity is logged to a timestamped file in the logs\ folder.
The log file opens with a summary table, followed by line-by-line detail
for every file processed.


REQUIREMENTS
------------
Python 3.9 or newer must be installed.

Required Python packages (install once):
  pip install mutagen pyacoustid requests

fpcalc (Chromaprint) must be present. Place fpcalc.exe in the same
folder as batch_tagger.py. Download from:
  https://acoustid.org/chromaprint

An AcoustID API key is required for fingerprint lookups.
The script reads this from batch_config.json (see CONFIGURATION below).
If you already have the web app's config.json with an acoustid_api_key,
the script will use that automatically — no extra setup needed.


FIRST RUN
---------
On the very first run, batch_tagger.py creates batch_config.json with
default values and exits. You must edit that file before running again.

  python batch_tagger.py


CONFIGURATION — batch_config.json
----------------------------------
Open batch_config.json in any text editor and set the values:

  {
    "scan_directory":     "C:\\Users\\Philip\\Music",
    "acoustid_api_key":  "your_key_here",
    "log_directory":     "logs",
    "recursive":         true,
    "skip_already_tagged": false,
    "embed_cover_art":   true
  }

scan_directory
  The folder to scan for MP3 files.
  Use double backslashes on Windows: "C:\\Users\\Philip\\Music"

acoustid_api_key
  Your AcoustID API key. Leave blank ("") to use the key from config.json
  (the web app config). Get a free key at https://acoustid.org

log_directory
  Where log files are written. Relative paths are resolved from the
  script folder. Default: "logs"

recursive
  true  — scan the folder and all subfolders
  false — scan only the top-level folder

skip_already_tagged
  true  — skip any MP3 that already has both a title and an artist tag
  false — re-tag and rename all files regardless of existing tags

embed_cover_art
  true  — download cover art from iTunes and embed it in the MP3
  false — update text tags only, leave cover art unchanged


RUNNING THE SCRIPT
------------------
Basic usage — uses batch_config.json in the same folder:

  python batch_tagger.py

Custom config file:

  python batch_tagger.py --config "C:\path\to\my_config.json"

You can create multiple config files for different folders and run
the script against each one separately.


WHAT HAPPENS TO YOUR FILES
---------------------------
- Tags are written directly to the original MP3 files.
- Each file is renamed to "Title - Artist.mp3" in the same folder.
  If a file with that name already exists, a counter is appended:
  "Title - Artist (1).mp3", "Title - Artist (2).mp3", etc.
- If a track cannot be identified (no fingerprint match), the file
  is left unchanged and logged as FAILED.
- If title or artist is missing from the match, renaming is skipped.
- Title case is applied to lowercase titles returned by MusicBrainz
  (e.g. "twilight zone" becomes "Twilight Zone").
  Already-mixed-case values are left as-is.


LOG FILES
---------
Each run produces a log file named:
  logs\tagger_YYYYMMDD_HHMMSS.log

Example: logs\tagger_20260319_151621.log

The log file is structured as follows:

  ════ SUMMARY BLOCK ════════════════════════════════
  Run date, directory, settings, totals, duration.

  FILE RESULTS TABLE
  One line per file: status, confidence score,
  original filename, renamed filename.

  ════ DETAILED LOG ══════════════════════════════════
  Timestamped lines showing every step taken for
  each file — fingerprint result, MusicBrainz data,
  cover art status, tag write result, rename result.

Status codes in the summary table:
  ✓ success  — fully tagged and renamed
  ✗ failed   — could not be identified or tagged
  — skipped  — skipped because already tagged


EXAMPLE OUTPUT (console)
------------------------
  Batch Tagger v1.0  —  2026-03-19 15:16:21
  Directory : C:\Users\Philip\Music
  Files found: 3
  Log file  : logs\tagger_20260319_151621.log

  ════════════════════════════════════════════════════════════════════════
  [15:16:21] (1/3) some song.mp3
    Existing tags: title="", artist=""
    Generating audio fingerprint...
    AcoustID match (96% confidence), recording: 7e1a8a85-...
    MusicBrainz: title="Twilight Zone", artist="Ariana Grande", ...
    Cover art found (44 KB)
    Cover art embedded
    Tags written successfully
    Renamed: Twilight Zone - Ariana Grande.mp3
    ✓ DONE

  ════════════════════════════════════════════════════════════════════════
    DONE  |  ✓ 3 succeeded  ✗ 0 failed  — 0 skipped  |  32.3s
    Log   : logs\tagger_20260319_151621.log
  ════════════════════════════════════════════════════════════════════════


ILLEGAL CHARACTERS IN FILENAMES
--------------------------------
Windows does not allow these characters in file or folder names:
  / \ : * ? " < > |

If a title or artist contains any of these, the script handles them
automatically during rename:

  Replacement rules:
    /  \  :   →  replaced with  -
    *  ?  "   →  removed entirely
    <  >  |   →  removed entirely

  Leading/trailing spaces, dots, and dashes are also stripped.

  Examples:
    "AC/DC"           →  "AC-DC"
    "What?"           →  "What"
    "Artist: Name"    →  "Artist- Name"
    "Title <Remix>"   →  "Title -Remix"

  Full rename examples:
    title="AC/DC",  artist="Back in Black?"
    → "AC-DC - Back in Black.mp3"

    title="What?: The Remix",  artist="Artist/Name"
    → "What-- The Remix - Artist-Name.mp3"

The tags written into the MP3 file always keep the original unmodified
values — only the filename is sanitised.


TROUBLESHOOTING
---------------
"fpcalc not found"
  Place fpcalc.exe in the same folder as batch_tagger.py, or add
  its location to your system PATH.

"No AcoustID API key configured"
  Set acoustid_api_key in batch_config.json, or ensure config.json
  (the web app config) exists in the same folder with the key set.

"No AcoustID match found"
  The track may not be in the AcoustID database (common for rare,
  live, or local recordings). The file is left unchanged.

"ModuleNotFoundError: No module named 'mutagen'"
  Run: pip install mutagen pyacoustid requests

MusicBrainz lookups are slow (1 per second)
  MusicBrainz enforces a rate limit of 1 request per second.
  The script respects this automatically. For large batches,
  expect roughly 5-10 seconds per file.
