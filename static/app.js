'use strict';

const state = {
  file_id: null,
  coverUrl: null,
  filename: null,
};

// ── Logging ──────────────────────────────────────────────────────────────────

function log(message, type = 'default') {
  const out = document.getElementById('log-output');
  const entry = document.createElement('div');
  entry.className = 'log-entry' + (type !== 'default' ? ` log-${type}` : '');

  const now = new Date();
  const ts = now.toTimeString().slice(0, 8);

  entry.innerHTML =
    `<span class="ts">[${ts}]</span>` +
    `<span class="msg">${escHtml(message)}</span>`;

  out.appendChild(entry);
  out.scrollTop = out.scrollHeight;
}

function logMany(messages) {
  if (!messages) return;
  messages.forEach(msg => {
    const m = msg.toLowerCase();
    let type = 'default';
    if (m.startsWith('error') || m.includes('failed') || m.includes('not found'))
      type = 'error';
    else if (m.includes('success') || m.includes('saved') || m.includes('embedded'))
      type = 'success';
    else if (m.startsWith('start') || m.startsWith('searching') ||
             m.startsWith('downloading') || m.startsWith('looking') ||
             m.startsWith('generating') || m.startsWith('uploading'))
      type = 'info';
    else if (m.startsWith('warning') || m.startsWith('warn'))
      type = 'warn';
    log(msg, type);
  });
}

function escHtml(text) {
  const d = document.createElement('div');
  d.appendChild(document.createTextNode(String(text)));
  return d.innerHTML;
}

// ── Bootstrap ─────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
  const dropZone  = document.getElementById('drop-zone');
  const fileInput = document.getElementById('file-input');

  dropZone.addEventListener('click', () => fileInput.click());

  fileInput.addEventListener('change', e => {
    const file = e.target.files[0];
    if (file) uploadFile(file);
    fileInput.value = '';
  });

  dropZone.addEventListener('dragover', e => {
    e.preventDefault();
    dropZone.classList.add('drag-over');
  });
  dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
  dropZone.addEventListener('drop', e => {
    e.preventDefault();
    dropZone.classList.remove('drag-over');
    const file = e.dataTransfer.files[0];
    if (file && file.name.toLowerCase().endsWith('.mp3')) {
      uploadFile(file);
    } else {
      log('Please drop an MP3 file', 'error');
    }
  });

  document.getElementById('update-btn').addEventListener('click', updateTags);
  document.getElementById('retry-fp-btn').addEventListener('click', doFingerprint);
  document.getElementById('retry-cover-btn').addEventListener('click', () => {
    const artist = document.getElementById('edit-artist').value.trim();
    const title  = document.getElementById('edit-title').value.trim();
    doSearchCover(artist, title);
  });
});

// ── Upload ────────────────────────────────────────────────────────────────────

async function uploadFile(file) {
  log(`Uploading: ${file.name}`, 'info');

  setHidden('info-section', true);
  setHidden('fingerprint-section', true);
  setHidden('update-result', true);
  document.getElementById('download-link').classList.add('hidden');

  const fd = new FormData();
  fd.append('file', file);
  if (state.file_id) fd.append('old_file_id', state.file_id);

  try {
    const res  = await fetch('/upload', { method: 'POST', body: fd });
    const data = await res.json();
    logMany(data.logs);

    if (data.error) { log('Upload failed: ' + data.error, 'error'); return; }

    state.file_id  = data.file_id;
    state.filename = data.info.filename;
    displayInfo(data.info);
    await doFingerprint();

  } catch (err) {
    log('Upload error: ' + err.message, 'error');
  }
}

// ── Display Info ──────────────────────────────────────────────────────────────

function displayInfo(info) {
  setHidden('info-section', false);

  setText('info-filename', info.filename || '—');
  setTagText('info-title',    info.title);
  setTagText('info-artist',   info.artist);
  setTagText('info-album',    info.album);
  setTagText('info-date',     info.date);
  setText('info-codec',    info.codec    || '—');
  setText('info-bitrate',  info.bitrate  || '—');
  setText('info-duration', info.duration || '—');

  const img   = document.getElementById('info-cover-img');
  const noArt = document.getElementById('info-no-cover');
  if (info.cover_art) {
    img.src = info.cover_art;
    img.classList.remove('hidden');
    noArt.classList.add('hidden');
  } else {
    img.classList.add('hidden');
    noArt.classList.remove('hidden');
  }
}

function setTagText(id, value) {
  const el = document.getElementById(id);
  if (value) {
    el.textContent = value;
    el.classList.remove('empty');
  } else {
    el.textContent = '—';
    el.classList.add('empty');
  }
}

function setText(id, value) {
  document.getElementById(id).textContent = value;
}

function setHidden(id, hidden) {
  document.getElementById(id).classList.toggle('hidden', hidden);
}

// ── Fingerprint ───────────────────────────────────────────────────────────────

async function doFingerprint() {
  if (!state.file_id) return;

  setHidden('fingerprint-section', false);
  setHidden('fp-status', false);
  setHidden('fp-results', true);

  const spinner  = document.getElementById('fp-spinner');
  const statusTx = document.getElementById('fp-status-text');
  spinner.style.display = '';
  statusTx.textContent  = 'Generating audio fingerprint…';
  statusTx.style.color  = '';

  log('Starting fingerprinting…', 'info');

  try {
    const res  = await fetch('/fingerprint', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_id: state.file_id }),
    });
    const data = await res.json();
    logMany(data.logs);

    setHidden('fp-status', true);

    if (data.error) {
      setHidden('fp-status', false);
      spinner.style.display = 'none';
      statusTx.textContent  = '⚠ ' + data.error;
      statusTx.style.color  = 'var(--warn)';
      return;
    }

    if (data.match) {
      showFpResults(data.match);
      await doSearchCover(data.match.artist, data.match.title);
    } else {
      setHidden('fp-status', false);
      spinner.style.display = 'none';
      statusTx.textContent  = 'No match found for this audio';
    }

  } catch (err) {
    log('Fingerprint error: ' + err.message, 'error');
    document.getElementById('fp-status-text').textContent = 'Error: ' + err.message;
    document.getElementById('fp-spinner').style.display = 'none';
  }
}

function showFpResults(match) {
  setHidden('fp-results', false);

  const pct = Math.round(match.score * 100);
  document.getElementById('fp-score').textContent = `Match ${pct}%`;
  document.getElementById('fp-recording-id').textContent =
    match.recording_id ? `MusicBrainz: ${match.recording_id}` : '';

  document.getElementById('edit-title').value  = match.title  || '';
  document.getElementById('edit-artist').value = match.artist || '';
  document.getElementById('edit-album').value  = match.album  || '';
  document.getElementById('edit-date').value   = match.date   || '';
}

// ── Cover Art ─────────────────────────────────────────────────────────────────

async function doSearchCover(artist, title) {
  if (!artist && !title) return;

  const covImg  = document.getElementById('found-cover-img');
  const noFound = document.getElementById('no-cover-found');
  const status  = document.getElementById('cover-status');

  covImg.classList.add('hidden');
  noFound.classList.remove('hidden');
  noFound.textContent = 'Searching…';
  status.textContent  = '';
  state.coverUrl      = null;

  log(`Searching cover art: "${title}" by ${artist}`, 'info');

  try {
    const res  = await fetch('/search_cover', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ artist, title }),
    });
    const data = await res.json();
    logMany(data.logs);

    if (data.cover_url) {
      state.coverUrl = data.cover_url;
      loadFoundCover(data.cover_url);
    } else {
      noFound.textContent = 'No cover art found';
      status.textContent  = '';
    }

  } catch (err) {
    log('Cover search error: ' + err.message, 'error');
    noFound.textContent = 'Search failed';
  }
}

function loadFoundCover(url) {
  const img     = document.getElementById('found-cover-img');
  const noFound = document.getElementById('no-cover-found');
  const status  = document.getElementById('cover-status');

  img.onload = () => {
    img.classList.remove('hidden');
    noFound.classList.add('hidden');
    status.textContent = 'Cover art found ✓';
  };
  img.onerror = () => {
    noFound.textContent = 'Could not load image';
    status.textContent  = '';
  };
  img.src = `/proxy_image?url=${encodeURIComponent(url)}`;
}

// ── Update Tags ───────────────────────────────────────────────────────────────

async function updateTags() {
  if (!state.file_id) return;

  const btn      = document.getElementById('update-btn');
  const resultEl = document.getElementById('update-result');

  btn.disabled = true;
  setHidden('update-result', true);

  const tags = {
    title:  document.getElementById('edit-title').value.trim(),
    artist: document.getElementById('edit-artist').value.trim(),
    album:  document.getElementById('edit-album').value.trim(),
    date:   document.getElementById('edit-date').value.trim(),
  };

  const useCover = document.getElementById('use-cover-checkbox').checked;
  const coverUrl = useCover ? state.coverUrl : null;

  log('Updating tags…', 'info');

  try {
    const res  = await fetch('/update_tags', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_id: state.file_id, tags, cover_url: coverUrl }),
    });
    const data = await res.json();
    logMany(data.logs);

    if (data.success) {
      log('Tags updated successfully!', 'success');

      resultEl.className   = 'success';
      resultEl.textContent = '✓ Tags updated successfully';
      setHidden('update-result', false);

      if (data.updated_info) displayInfo(data.updated_info);

      const dl = document.getElementById('download-link');
      dl.href = `/download/${state.file_id}`;
      dl.classList.remove('hidden');

    } else {
      resultEl.className   = 'error';
      resultEl.textContent = '✗ ' + (data.error || 'Update failed');
      setHidden('update-result', false);
      log('Update failed: ' + data.error, 'error');
    }

  } catch (err) {
    log('Update error: ' + err.message, 'error');
    resultEl.className   = 'error';
    resultEl.textContent = '✗ ' + err.message;
    setHidden('update-result', false);
  } finally {
    btn.disabled = false;
  }
}
