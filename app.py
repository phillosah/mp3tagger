import os
import uuid
import platform
import base64
import requests
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, TALB, TDRC, APIC, ID3NoHeaderError
from flask import Flask, request, jsonify, render_template, send_file
from werkzeug.utils import secure_filename

try:
    import acoustid
    ACOUSTID_AVAILABLE = True
except ImportError:
    ACOUSTID_AVAILABLE = False

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# fpcalc: look for local binary first (works on both Windows and Linux)
_base = os.path.dirname(os.path.abspath(__file__))
FPCALC = os.path.join(_base, 'fpcalc.exe' if platform.system() == 'Windows' else 'fpcalc')
if os.path.exists(FPCALC):
    os.environ['FPCALC'] = FPCALC

# In-memory map of file_id -> filepath (lives for the duration of the process)
file_store = {}

CONFIG_FILE = os.path.join(_base, 'config.json')


def get_acoustid_key():
    # Environment variable takes priority (used on Render)
    env_key = os.environ.get('ACOUSTID_API_KEY', '').strip()
    if env_key:
        return env_key
    # Fall back to config.json (used locally)
    if os.path.exists(CONFIG_FILE):
        import json
        with open(CONFIG_FILE) as f:
            return json.load(f).get('acoustid_api_key', '')
    return ''


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/upload', methods=['POST'])
def upload():
    logs = []
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided', 'logs': ['Error: No file provided']})

    file = request.files['file']
    if not file.filename:
        return jsonify({'error': 'No file selected', 'logs': ['Error: No file selected']})
    if not file.filename.lower().endswith('.mp3'):
        return jsonify({'error': 'Only MP3 files are supported', 'logs': ['Error: Only MP3 files are supported']})

    filename = secure_filename(file.filename)
    # Prefix with a uuid so two users uploading the same filename don't collide
    file_id = str(uuid.uuid4())
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], file_id + '_' + filename)
    file.save(filepath)
    file_store[file_id] = filepath
    logs.append(f'File uploaded: {filename}')

    info = {'filename': filename}

    try:
        audio = MP3(filepath)
        info['codec'] = 'MP3'
        info['bitrate'] = f"{int(audio.info.bitrate / 1000)} kbps"
        total_secs = int(audio.info.length)
        info['duration'] = f"{total_secs // 60}:{total_secs % 60:02d}"
        logs.append(f'Audio info: {info["bitrate"]}, duration {info["duration"]}')
    except Exception as e:
        logs.append(f'Warning: Could not read audio info: {e}')
        info.update({'codec': 'MP3', 'bitrate': 'Unknown', 'duration': 'Unknown'})

    try:
        tags = ID3(filepath)
        info['title'] = str(tags.get('TIT2', ''))
        info['artist'] = str(tags.get('TPE1', ''))
        info['album'] = str(tags.get('TALB', ''))
        info['date'] = str(tags.get('TDRC', ''))

        apic = None
        for key in tags.keys():
            if key.startswith('APIC'):
                apic = tags[key]
                break

        if apic:
            cover_b64 = base64.b64encode(apic.data).decode('utf-8')
            info['cover_art'] = f"data:{apic.mime};base64,{cover_b64}"
            logs.append('Found existing cover art in tags')
        else:
            info['cover_art'] = None
            logs.append('No cover art in existing tags')

        logs.append(f'Read tags — title: "{info["title"]}", artist: "{info["artist"]}", album: "{info["album"]}", date: "{info["date"]}"')
    except ID3NoHeaderError:
        info.update({'title': '', 'artist': '', 'album': '', 'date': '', 'cover_art': None})
        logs.append('No ID3 tags found in file')
    except Exception as e:
        info.update({'title': '', 'artist': '', 'album': '', 'date': '', 'cover_art': None})
        logs.append(f'Error reading tags: {e}')

    return jsonify({'info': info, 'file_id': file_id, 'logs': logs})


@app.route('/fingerprint', methods=['POST'])
def fingerprint():
    logs = []
    data = request.get_json()
    file_id = data.get('file_id', '')
    filepath = file_store.get(file_id)

    if not filepath or not os.path.exists(filepath):
        return jsonify({'error': 'File not found', 'logs': ['Error: File not found on server']})

    if not ACOUSTID_AVAILABLE:
        return jsonify({'error': 'pyacoustid not installed', 'logs': ['Error: pyacoustid not available']})

    api_key = get_acoustid_key()
    if not api_key:
        return jsonify({'error': 'no_api_key', 'logs': ['Error: No AcoustID API key configured']})

    logs.append('Generating audio fingerprint with Chromaprint...')

    try:
        best_score = 0
        best_rid = None
        best_title = None
        best_artist = None

        for score, rid, title, artist in acoustid.match(api_key, filepath, force_fpcalc=True):
            if score > best_score:
                best_score = score
                best_rid = rid
                best_title = title
                best_artist = artist
            if best_score >= 0.8:
                break

        if not best_rid:
            logs.append('No AcoustID match found for this audio')
            return jsonify({'match': None, 'logs': logs})

        logs.append(f'AcoustID match (confidence {best_score:.0%}), recording ID: {best_rid}')

        album = ''
        date = ''
        try:
            logs.append(f'Looking up MusicBrainz recording {best_rid}...')
            mb_url = f'https://musicbrainz.org/ws/2/recording/{best_rid}?inc=releases+artists&fmt=json'
            headers = {'User-Agent': 'MP3Tagger/1.0 (local; github.com/user/mp3tagger)'}
            mb_resp = requests.get(mb_url, headers=headers, timeout=10)

            if mb_resp.status_code == 200:
                mb = mb_resp.json()
                best_title = mb.get('title', best_title)
                artist_credits = mb.get('artist-credit', [])
                if artist_credits:
                    best_artist = artist_credits[0].get('artist', {}).get('name', best_artist)
                releases = mb.get('releases', [])
                if releases:
                    album = releases[0].get('title', '')
                    raw_date = releases[0].get('date', '')
                    date = raw_date[:4] if raw_date else ''
                logs.append(f'MusicBrainz: album="{album}", date="{date}"')
            else:
                logs.append(f'MusicBrainz returned HTTP {mb_resp.status_code}')
        except Exception as e:
            logs.append(f'MusicBrainz lookup failed: {e}')

        match = {
            'title': best_title or '',
            'artist': best_artist or '',
            'album': album,
            'date': date,
            'score': best_score,
            'recording_id': best_rid,
        }
        return jsonify({'match': match, 'logs': logs})

    except acoustid.FingerprintGenerationError as e:
        msg = str(e)
        if 'fpcalc' in msg.lower() or 'not found' in msg.lower():
            logs.append('fpcalc (Chromaprint) not found')
            return jsonify({'error': 'fpcalc not found', 'logs': logs})
        return jsonify({'error': msg, 'logs': logs})
    except acoustid.WebServiceError as e:
        logs.append(f'AcoustID web service error: {e}')
        return jsonify({'error': str(e), 'logs': logs})
    except Exception as e:
        logs.append(f'Fingerprinting error: {e}')
        return jsonify({'error': str(e), 'logs': logs})


@app.route('/search_cover', methods=['POST'])
def search_cover():
    logs = []
    data = request.get_json()
    artist = (data.get('artist') or '').strip()
    title = (data.get('title') or '').strip()

    if not artist and not title:
        return jsonify({'cover_url': None, 'logs': ['No artist or title to search']})

    term = f"{artist} {title}".strip()
    logs.append(f'Searching iTunes for cover art: "{term}"')

    try:
        url = 'https://itunes.apple.com/search'
        params = {'term': term, 'media': 'music', 'entity': 'musicTrack', 'limit': 5}
        resp = requests.get(url, params=params, timeout=10)

        if resp.status_code == 200:
            results = resp.json().get('results', [])
            if results:
                artwork = results[0].get('artworkUrl100', '')
                artwork = artwork.replace('100x100bb', '600x600bb').replace('100x100', '600x600')
                logs.append(f'Found cover art on iTunes: {results[0].get("trackName", "")} — {results[0].get("artistName", "")}')
                return jsonify({'cover_url': artwork, 'logs': logs})

        logs.append('No cover art found on iTunes')
        return jsonify({'cover_url': None, 'logs': logs})

    except Exception as e:
        logs.append(f'Cover art search error: {e}')
        return jsonify({'error': str(e), 'logs': logs})


@app.route('/proxy_image')
def proxy_image():
    url = request.args.get('url', '')
    if not url.startswith('https://'):
        return 'Invalid URL', 400
    try:
        resp = requests.get(url, timeout=15)
        from flask import Response
        return Response(resp.content, content_type=resp.headers.get('content-type', 'image/jpeg'))
    except Exception as e:
        return str(e), 500


@app.route('/update_tags', methods=['POST'])
def update_tags():
    logs = []
    data = request.get_json()
    file_id = data.get('file_id', '')
    tags_data = data.get('tags', {})
    cover_url = data.get('cover_url')

    filepath = file_store.get(file_id)
    if not filepath or not os.path.exists(filepath):
        return jsonify({'error': 'File not found', 'logs': ['Error: File not found on server']})

    filename = os.path.basename(filepath)
    logs.append(f'Updating tags for: {filename}')

    try:
        try:
            tags = ID3(filepath)
        except ID3NoHeaderError:
            tags = ID3()

        if tags_data.get('title') is not None:
            tags['TIT2'] = TIT2(encoding=3, text=tags_data['title'])
            logs.append(f'Set title: "{tags_data["title"]}"')

        if tags_data.get('artist') is not None:
            tags['TPE1'] = TPE1(encoding=3, text=tags_data['artist'])
            logs.append(f'Set artist: "{tags_data["artist"]}"')

        if tags_data.get('album') is not None:
            tags['TALB'] = TALB(encoding=3, text=tags_data['album'])
            logs.append(f'Set album: "{tags_data["album"]}"')

        if tags_data.get('date') is not None:
            tags['TDRC'] = TDRC(encoding=3, text=tags_data['date'])
            logs.append(f'Set date: "{tags_data["date"]}"')

        if cover_url:
            logs.append('Downloading cover art...')
            try:
                if cover_url.startswith('data:'):
                    header, b64data = cover_url.split(',', 1)
                    mime = header.split(';')[0].split(':')[1]
                    img_data = base64.b64decode(b64data)
                else:
                    img_resp = requests.get(cover_url, timeout=15)
                    img_data = img_resp.content
                    mime = img_resp.headers.get('content-type', 'image/jpeg').split(';')[0]

                for key in list(tags.keys()):
                    if key.startswith('APIC'):
                        del tags[key]

                tags['APIC:'] = APIC(
                    encoding=3, mime=mime, type=3, desc='Cover', data=img_data
                )
                logs.append(f'Cover art embedded ({len(img_data) // 1024} KB)')
            except Exception as e:
                logs.append(f'Failed to embed cover art: {e}')

        tags.save(filepath)
        logs.append('Tags saved successfully!')

        updated_info = _read_file_info(filepath)
        return jsonify({'success': True, 'logs': logs, 'updated_info': updated_info})

    except Exception as e:
        logs.append(f'Error updating tags: {e}')
        return jsonify({'error': str(e), 'logs': logs})


@app.route('/download/<file_id>')
def download(file_id):
    filepath = file_store.get(file_id)
    if not filepath or not os.path.exists(filepath):
        return 'File not found', 404
    # Serve with the original filename (strip the uuid prefix)
    original_name = os.path.basename(filepath).split('_', 1)[-1]
    return send_file(filepath, as_attachment=True, download_name=original_name)


def _read_file_info(filepath):
    info = {'filename': os.path.basename(filepath).split('_', 1)[-1]}
    try:
        audio = MP3(filepath)
        info['codec'] = 'MP3'
        info['bitrate'] = f"{int(audio.info.bitrate / 1000)} kbps"
        total_secs = int(audio.info.length)
        info['duration'] = f"{total_secs // 60}:{total_secs % 60:02d}"
    except Exception:
        info.update({'codec': 'MP3', 'bitrate': 'Unknown', 'duration': 'Unknown'})

    try:
        tags = ID3(filepath)
        info['title'] = str(tags.get('TIT2', ''))
        info['artist'] = str(tags.get('TPE1', ''))
        info['album'] = str(tags.get('TALB', ''))
        info['date'] = str(tags.get('TDRC', ''))
        apic = None
        for key in tags.keys():
            if key.startswith('APIC'):
                apic = tags[key]
                break
        if apic:
            cover_b64 = base64.b64encode(apic.data).decode('utf-8')
            info['cover_art'] = f"data:{apic.mime};base64,{cover_b64}"
        else:
            info['cover_art'] = None
    except Exception:
        info.update({'title': '', 'artist': '', 'album': '', 'date': '', 'cover_art': None})

    return info


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV') != 'production'
    print(f"MP3 Tagger running at http://localhost:{port}")
    app.run(debug=debug, port=port)
