/* offline_playlist/web/app.js -- UI controller for Offplayer. No framework, no CDN. */

'use strict';

const S = {
  state: null,
  view: 'home',
  playlistId: null,
  query: '',
  queue: [],
  index: -1,
  shuffle: false,
  repeat: 'off', // off | all | one
  pollTimer: null,
  viewSig: '',
  sideSig: '',
};

const audio = document.getElementById('audio');
const els = {
  view: document.getElementById('view'),
  sideNav: document.getElementById('playlist-nav'),
  sideCount: document.getElementById('side-count'),
  deps: document.getElementById('deps'),
  importUrl: document.getElementById('import-url'),
  importBtn: document.getElementById('import-btn'),
  search: document.getElementById('search'),
  npCover: document.getElementById('np-cover'),
  npTitle: document.getElementById('np-title'),
  npArtist: document.getElementById('np-artist'),
  btnPlay: document.getElementById('btn-play'),
  btnPrev: document.getElementById('btn-prev'),
  btnNext: document.getElementById('btn-next'),
  btnShuffle: document.getElementById('btn-shuffle'),
  btnRepeat: document.getElementById('btn-repeat'),
  seek: document.getElementById('seek'),
  timeNow: document.getElementById('time-now'),
  timeEnd: document.getElementById('time-end'),
  volume: document.getElementById('volume'),
  toast: document.getElementById('toast'),
};

/* ------------------------------------------------------------------ helpers */

function esc(value) {
  return String(value === null || value === undefined ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function fmtTime(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return m + ':' + String(s).padStart(2, '0');
}

function toast(message, isError) {
  els.toast.textContent = message;
  els.toast.className = 'toast show' + (isError ? ' error' : '');
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => { els.toast.className = 'toast'; }, 3600);
}

async function api(path, options) {
  const response = await fetch(path, Object.assign({
    headers: { 'Content-Type': 'application/json' }
  }, options || {}));
  let payload = {};
  try { payload = await response.json(); } catch (_) { payload = {}; }
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || ('HTTP ' + response.status));
  }
  return payload;
}

function coverUrl(kind, id) {
  return kind === 'playlist' ? '/api/cover/' + encodeURIComponent(id)
                              : '/api/track/' + encodeURIComponent(id) + '/cover';
}

/* --------------------------------------------------------------------- state */

async function refresh(silent) {
  try {
    S.state = await api('/api/state');
    const sig = viewSignature();
    if (sig !== S.viewSig) {
      S.viewSig = sig;
      renderAll();
    } else {
      patchLive();
    }
  } catch (err) {
    if (!silent) toast('sem conexão com o servidor: ' + err.message, true);
  }
}

/* Assinaturas: só redesenha quando a estrutura muda. Status/progresso são
   atualizados no lugar, para não arrancar o elemento sob o cursor do usuário. */
function viewSignature() {
  const st = S.state;
  if (!st) return '';
  const pl = (st.playlists || []).map(p =>
    `${p.id}|${p.title}|${p.done_tracks}|${p.total_tracks}`).join(',');
  const tr = (st.tracks || []).map(t =>
    `${t.id}|${t.title}|${t.artist}|${t.album}|${t.duration}`).join(',');
  return `${S.view}|${S.playlistId}|${S.query}|${pl}|${tr}`;
}

function patchLive() {
  const tracks = S.state.tracks || [];
  const labels = { pending: 'na fila', downloading: 'baixando', done: 'pronto', error: 'erro' };
  for (const track of tracks) {
    const row = document.querySelector('[data-row="' + track.id + '"]');
    if (!row) continue;
    const cell = row.querySelector('.t-state');
    if (cell) {
      const text = labels[track.status] || track.status;
      const pct = Math.round((track.progress || 0) * 100);
      cell.className = 't-state state-' + track.status;
      cell.innerHTML = text + (track.status === 'downloading'
        ? ' <div class="bar"><i style="width:' + pct + '%"></i></div>' : '');
    }
    const playBtn = row.querySelector('[data-play]');
    if (playBtn) playBtn.disabled = track.status !== 'done';
  }
  const stats = S.state.stats || {};
  const homeCount = document.getElementById('home-count');
  if (homeCount) homeCount.textContent = stats.ready || 0;
  const plCount = document.getElementById('pl-count');
  if (plCount) {
    const playlist = (S.state.playlists || []).find(p => p.id === S.playlistId);
    if (playlist) plCount.textContent = playlist.done_tracks || 0;
  }
  const sideSig = (S.state.playlists || []).map(p => `${p.id}:${p.title}`).join(',');
  if (sideSig !== S.sideSig) { S.sideSig = sideSig; renderSidebar(); }
}

function renderAll() {
  renderDeps();
  renderSidebar();
  renderView();
  renderPlayerBar();
}

/* ------------------------------------------------------------------- sidebar */

function renderDeps() {
  const deps = S.state.deps || {};
  const ready = deps.ready;
  const lines = [
    '<b>Motor</b> ' + (ready ? '<span class="ok">pronto</span>' : '<span class="bad">incompleto</span>'),
    'yt-dlp: ' + (deps.yt_dlp ? esc(deps.yt_dlp_version || 'ok') : '<span class="bad">faltando</span>'),
    'ffmpeg: ' + (deps.ffmpeg ? '<span class="ok">ok</span>' : '<span class="bad">faltando</span>'),
    'mutagen: ' + (deps.mutagen ? 'ok' : '<span class="bad">faltando</span>')
  ];
  if (!ready) {
    lines.push('<code>python bootstrap.py</code>');
  }
  els.deps.innerHTML = lines.join('<br>');
}

function renderSidebar() {
  const playlists = S.state.playlists || [];
  els.sideCount.textContent = playlists.length ? '(' + playlists.length + ')' : '';
  S.sideSig = playlists.map(p => `${p.id}:${p.title}`).join(',');
  els.sideNav.innerHTML = playlists.map(p =>
    '<button class="side-link' + (S.view === 'playlist' && S.playlistId === p.id ? ' active' : '') + '" title="' + esc(p.title) + '" data-playlist="' + esc(p.id) + '">' +
      esc(p.title) + '</button>'
  ).join('') || '<div class="hint">nenhuma playlist ainda</div>';
}

/* ---------------------------------------------------------------------- view */

function renderView() {
  document.querySelectorAll('.nav-item').forEach(btn =>
    btn.classList.toggle('active', btn.dataset.view === S.view));
  if (S.view === 'home') renderHome();
  else if (S.view === 'playlist') renderPlaylist(S.playlistId);
  else if (S.view === 'downloads') renderDownloads();
  else if (S.view === 'settings') renderSettings();
}

function matchesQuery(track) {
  if (!S.query) return true;
  const q = S.query.toLowerCase();
  return (track.title || '').toLowerCase().includes(q)
      || (track.artist || '').toLowerCase().includes(q)
      || (track.album || '').toLowerCase().includes(q);
}

function renderHome() {
  const playlists = S.state.playlists || [];
  const stats = S.state.stats || {};
  const jobs = (S.state.jobs || []).filter(j => j.state === 'running' || j.state === 'queued');

  const header =
    '<div class="view-head"><div><h1>Biblioteca offline</h1>' +
    '<p><span id="home-count">' + (stats.ready || 0) + '</span> faixas prontas · ' + (stats.tracks || 0) + ' na biblioteca · ' +
    (playlists.length) + ' playlists</p></div>' +
    '<div class="view-actions">' +
      (jobs.length ? '<span class="hint">' + jobs.length + ' job(s) em andamento</span>' : '') +
    '</div></div>';

  if (!playlists.length) {
    els.view.innerHTML = header +
      '<div class="empty">Cole o link de uma playlist no topo e clique em <b>Importar &amp; baixar</b>.<br>' +
      'A faixa é baixada uma vez e tocada localmente, sem anúncio e sem stream.</div>';
    return;
  }

  const cards = playlists.map(p => {
    const cover = p.cover_path ? '<img src="/api/cover/' + esc(p.id) + '" alt="">' : '♫';
    return '<div class="card" role="button" tabindex="0" aria-label="Abrir playlist ' + esc(p.title) + '" data-playlist="' + esc(p.id) + '">' +
      '<div class="card-art">' + cover + '</div>' +
      '<div class="card-title">' + esc(p.title) + '</div>' +
      '<div class="card-sub">' + (p.done_tracks || 0) + '/' + (p.total_tracks || 0) + ' baixadas</div>' +
      '</div>';
  }).join('');

  els.view.innerHTML = header + '<div class="grid">' + cards + '</div>';
}

function trackRow(track, index) {
  const stateClass = 'state-' + track.status;
  const stateText = {
    pending: 'na fila', downloading: 'baixando', done: 'pronto', error: 'erro'
  }[track.status] || track.status;

  const progress = track.status === 'downloading'
    ? '<div class="bar"><i style="width:' + Math.round((track.progress || 0) * 100) + '%"></i></div>'
    : '';

  const canPlay = track.status === 'done';
  const actions =
    '<button class="btn small" data-play="' + esc(track.id) + '"' + (canPlay ? '' : ' disabled') + '>Tocar</button>' +
    '<button class="btn small" data-retry="' + esc(track.id) + '">↻</button>' +
    '<button class="btn small danger" data-del="' + esc(track.id) + '">✕</button>';

  return '<div class="row' + (S.index >= 0 && S.queue[S.index] && S.queue[S.index].id === track.id ? ' playing' : '') + '" data-row="' + esc(track.id) + '">' +
    '<div class="idx">' + (index + 1) + '</div>' +
    '<div class="t-main"><div class="t-title">' + esc(track.title) + '</div>' +
      '<div class="t-artist">' + esc(track.artist || '—') + (track.error ? ' · ' + esc(track.error) : '') + '</div></div>' +
    '<div class="t-album">' + esc(track.album || '—') + '</div>' +
    '<div class="t-state ' + stateClass + '">' + stateText + progress + '</div>' +
    '<div class="t-dur">' + (track.duration ? fmtTime(track.duration) : '—') + '</div>' +
    '<div class="t-actions">' + actions + '</div>' +
    '</div>';
}

function renderPlaylist(playlistId) {
  const playlist = (S.state.playlists || []).find(p => p.id === playlistId);
  if (!playlist) { S.view = 'home'; renderView(); return; }
  const tracks = (S.state.tracks || []).filter(t => t.playlist_id === playlistId);
  const visible = tracks.filter(matchesQuery);

  const header =
    '<div class="view-head">' +
    '<div><h1>' + esc(playlist.title) + '</h1>' +
    '<p><span id="pl-count">' + (playlist.done_tracks || 0) + '</span> prontas · ' + tracks.length + ' faixas · ' +
      esc(playlist.source_url || '') + '</p></div>' +
    '<div class="view-actions">' +
      '<button class="btn" id="pl-shuffle">Embaralhar</button>' +
      '<button class="btn" id="pl-play">Tocar</button>' +
      '<button class="btn" id="pl-download">Baixar pendentes</button>' +
      '<button class="btn" id="pl-redownload">Baixar tudo de novo</button>' +
      '<button class="btn danger" id="pl-delete">Excluir</button>' +
    '</div></div>';

  const body = visible.length
    ? '<div class="tracklist">' + visible.map(trackRow).join('') + '</div>'
    : '<div class="empty">nada aqui ainda</div>';

  els.view.innerHTML = header + body;
  bindPlaylistActions(playlistId, tracks);
}

function renderDownloads() {
  const tracks = (S.state.tracks || []).filter(t => t.status === 'done' && matchesQuery(t));
  const header =
    '<div class="view-head"><div><h1>Baixados</h1>' +
    '<p>' + tracks.length + ' faixas prontas para tocar offline</p></div>' +
    '<div class="view-actions"><button class="btn primary" id="dl-play">Tocar tudo</button></div></div>';
  const body = tracks.length
    ? '<div class="tracklist">' + tracks.map(trackRow).join('') + '</div>'
    : '<div class="empty">nenhuma faixa baixada ainda</div>';
  els.view.innerHTML = header + body;
  const btn = document.getElementById('dl-play');
  if (btn) btn.onclick = () => { setQueue(tracks); playIndex(0); };
}

function renderSettings() {
  const cfg = S.state.config || {};
  const deps = S.state.deps || {};
  els.view.innerHTML =
    '<div class="view-head"><div><h1>Configurações</h1><p>onde as faixas caem, formato e motor</p></div></div>' +
    '<div class="panel"><h3>Biblioteca</h3>' +
    '<div class="form-row"><label>Pasta das músicas</label><input type="text" id="cfg-library" value="' + esc(cfg.library_dir) + '"></div>' +
    '<div class="form-row"><label>Formato</label><select id="cfg-format">' +
      ['mp3', 'm4a', 'opus', 'source', 'best'].map(f =>
        '<option value="' + f + '"' + (cfg.audio_format === f ? ' selected' : '') + '>' + f + '</option>').join('') +
    '</select></div>' +
    '<div class="form-row"><label>Qualidade</label><select id="cfg-quality">' +
      ['128', '192', '320', 'best'].map(q =>
        '<option value="' + q + '"' + (String(cfg.audio_quality) === q ? ' selected' : '') + '>' + q + '</option>').join('') +
    '</select></div>' +
    '<div class="hint">best = sem re-encode, mantém o container original (maior fidelidade). best na qualidade = VBR máximo.</div>' +
    '<div class="form-row"><label>Preferir canal "Topic" oficial</label>' +
      '<select id="cfg-topic"><option value="true"' + (cfg.prefer_topic ? ' selected' : '') + '>sim</option>' +
      '<option value="false"' + (!cfg.prefer_topic ? ' selected' : '') + '>não</option></select></div>' +
    '<button class="btn primary" id="cfg-save">Salvar</button></div>' +

    '<div class="panel"><h3>Spotify Web API (necessário para playlist inteira)</h3>' +
    '<div class="form-row"><label>client_id</label><input type="text" id="cfg-sid" value="' + esc(cfg.spotify_client_id || '') + '" placeholder="cole aqui"></div>' +
    '<div class="form-row"><label>client_secret</label><input type="text" id="cfg-secret" value="' + esc(cfg.spotify_client_secret || '') + '" placeholder="cole aqui"></div>' +
    '<div class="hint"><b>Como obter (2 minutos, grátis):</b><br>' +
    '1. Acesse <a href="https://developer.spotify.com/dashboard" target="_blank" style="color:var(--accent)">developer.spotify.com/dashboard</a> e faça login com sua conta Spotify.<br>' +
    '2. Clique em "Create app". Nome: qualquer (ex: Offplayer). Descrição: qualquer. Redirect URI: http://localhost:8722. Marque "Web API".<br>' +
    '3. Abra o app criado → "Settings" → copie o "Client ID" e o "Client Secret" (clique em "View client secret").<br>' +
    '4. Cole os dois campos acima e clique em "Salvar".<br>' +
    '5. Reimporte a playlist. Agora ela carrega inteira (sem limite de 50).<br><br>' +
    'Sem credenciais, o app usa o embed público do Spotify, que entrega no máximo ~50 faixas.</div></div>' +

    '<div class="panel"><h3>Motor</h3><div class="hint">' +
      'yt-dlp: ' + (deps.yt_dlp ? esc(deps.yt_dlp_version || 'instalado') : 'não instalado') + '<br>' +
      'ffmpeg: ' + (deps.ffmpeg ? esc(deps.ffmpeg_path || 'ok') : 'não encontrado') + '<br>' +
      'mutagen: ' + (deps.mutagen ? 'instalado' : 'não instalado (tags via .json ao lado)') + '<br>' +
      'python: ' + esc(deps.python) + '</div></div>' +

    '<div class="panel"><h3>Como funciona</h3><div class="hint">' +
      '1. Cole o link da playlist do Spotify (ou do YouTube) no topo.<br>' +
      '2. O app resolve as faixas e baixa o áudio para a pasta da biblioteca.<br>' +
      '3. O player toca direto do disco -- sem anúncio, sem stream, funciona com a internet desligada.<br>' +
      '4. Se uma faixa vier errada, use o ↻ para buscar de novo.</div></div>';

  const save = document.getElementById('cfg-save');
  if (save) save.onclick = async () => {
    try {
      await api('/api/settings', {
        method: 'POST',
        body: JSON.stringify({
          settings: {
            library_dir: document.getElementById('cfg-library').value,
            audio_format: document.getElementById('cfg-format').value,
            audio_quality: document.getElementById('cfg-quality').value,
            prefer_topic: document.getElementById('cfg-topic').value === 'true',
            spotify_client_id: document.getElementById('cfg-sid').value.trim(),
            spotify_client_secret: document.getElementById('cfg-secret').value.trim(),
          }
        })
      });
      toast('configuração salva');
      await refresh(true);
    } catch (err) { toast(err.message, true); }
  };
}

/* ------------------------------------------------------------------- actions */

function bindPlaylistActions(playlistId, tracks) {
  const download = document.getElementById('pl-download');
  if (download) download.onclick = async () => {
    try {
      await api('/api/playlist/' + encodeURIComponent(playlistId) + '/download', { method: 'POST', body: '{}' });
      toast('download enfileirado');
      await refresh(true);
    } catch (err) { toast(err.message, true); }
  };

  const redownload = document.getElementById('pl-redownload');
  if (redownload) redownload.onclick = async () => {
    try {
      await api('/api/playlist/' + encodeURIComponent(playlistId) + '/download',
        { method: 'POST', body: JSON.stringify({ redownload: true }) });
      toast('re-download enfileirado');
      await refresh(true);
    } catch (err) { toast(err.message, true); }
  };

  const play = document.getElementById('pl-play');
  if (play) play.onclick = () => {
    const playable = tracks.filter(t => t.status === 'done');
    if (!playable.length) { toast('nenhuma faixa pronta ainda', true); return; }
    setQueue(playable);
    playIndex(0);
  };

  const shf = document.getElementById('pl-shuffle');
  if (shf) shf.onclick = () => {
    const playable = tracks.filter(t => t.status === 'done');
    if (!playable.length) { toast('nenhuma faixa pronta ainda', true); return; }
    setQueue(shuffled(playable));
    S.shuffle = true;
    renderPlayerBar();
    playIndex(0);
    toast('fila embaralhada');
  };

  const del = document.getElementById('pl-delete');
  if (del) del.onclick = async () => {
    if (!confirm('Excluir a playlist da biblioteca?')) return;
    try {
      await api('/api/playlist/' + encodeURIComponent(playlistId), { method: 'DELETE' });
      S.view = 'home';
      S.playlistId = null;
      await refresh(true);
    } catch (err) { toast(err.message, true); }
  };
}

document.addEventListener('click', async (event) => {
  const target = event.target;

  const nav = target.closest('.nav-item');
  if (nav) { S.view = nav.dataset.view; renderView(); return; }

  const card = target.closest('.card');
  if (card) { S.view = 'playlist'; S.playlistId = card.dataset.playlist; renderView(); return; }

  const side = target.closest('.side-link');
  if (side) { S.view = 'playlist'; S.playlistId = side.dataset.playlist; renderView(); return; }

  const playBtn = target.closest('[data-play]');
  if (playBtn && !playBtn.disabled) {
    const id = playBtn.dataset.play;
    const list = (S.state.tracks || []).filter(t => t.status === 'done');
    setQueue(list);
    const at = list.findIndex(t => t.id === id);
    if (at >= 0) playIndex(at);
    return;
  }

  const retry = target.closest('[data-retry]');
  if (retry) {
    showCandidates(retry.dataset.retry);
    return;
  }

  const del = target.closest('[data-del]');
  if (del) {
    try {
      await api('/api/track/' + encodeURIComponent(del.dataset.del), { method: 'DELETE' });
      await refresh(true);
    } catch (err) { toast(err.message, true); }
    return;
  }

  const row = target.closest('.row');
  if (row && !target.closest('button')) {
    const id = row.dataset.row;
    const track = (S.state.tracks || []).find(t => t.id === id);
    if (track && track.status === 'done') {
      const list = (S.state.tracks || []).filter(t => t.status === 'done');
      setQueue(list);
      const at = list.findIndex(t => t.id === id);
      if (at >= 0) playIndex(at);
    }
  }
});

els.importBtn.onclick = async () => {
  const url = els.importUrl.value.trim();
  if (!url) { toast('cole o link da playlist', true); return; }
  els.importBtn.disabled = true;
  els.importBtn.textContent = 'Resolvendo...';
  try {
    const result = await api('/api/import', { method: 'POST', body: JSON.stringify({ url, auto_download: true }) });
    const label = result.playlist ? result.playlist.title : '';
    const via = result.resolver === 'api' ? 'API oficial (lista completa)' : (result.resolver === 'embed' ? 'embed público (~50 faixas)' : 'yt-dlp');
    let note = `playlist importada (${via}): ${label} — baixando`;
    if (result.truncated) {
      note += ' | Para carregar a playlist inteira, adicione suas credenciais em Configurações (guia na página)';
    }
    toast(note);
    els.importUrl.value = '';
    S.view = 'playlist';
    S.playlistId = result.playlist ? result.playlist.id : null;
    await refresh(true);
  } catch (err) {
    toast(err.message, true);
  } finally {
    els.importBtn.disabled = false;
    els.importBtn.textContent = 'Importar & baixar';
  }
};

els.importUrl.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') els.importBtn.click();
});

els.search.addEventListener('input', () => {
  S.query = els.search.value.trim();
  renderView();
});

/* -------------------------------------------------------------------- player */

function setQueue(tracks) {
  S.queue = tracks.slice();
  S.index = -1;
}

function shuffled(list) {
  const copy = list.slice();
  for (let i = copy.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [copy[i], copy[j]] = [copy[j], copy[i]];
  }
  return copy;
}

function playIndex(index) {
  if (!S.queue.length) { toast('fila vazia', true); return; }
  let target = index;
  if (target < 0 || target >= S.queue.length) target = 0;
  const track = S.queue[target];
  if (!track || track.status !== 'done') { toast('faixa indisponível', true); return; }
  S.index = target;
  audio.src = '/api/track/' + encodeURIComponent(track.id) + '/file';
  audio.play().catch(() => toast('o navegador bloqueou o autoplay: clique em ▶', true));
  renderPlayerBar();
  renderView();
}

function nextTrack(auto) {
  if (!S.queue.length) return;
  if (S.repeat === 'one' && auto) { audio.currentTime = 0; audio.play(); return; }
  let target;
  if (S.shuffle) {
    target = S.queue.length === 1 ? 0 : Math.floor(Math.random() * S.queue.length);
  } else {
    target = S.index + 1;
    if (target >= S.queue.length) {
      if (S.repeat === 'all') target = 0;
      else { audio.pause(); audio.currentTime = 0; renderPlayerBar(); return; }
    }
  }
  playIndex(target);
}

function prevTrack() {
  if (!S.queue.length) return;
  if (audio.currentTime > 3) { audio.currentTime = 0; return; }
  let target = S.index - 1;
  if (target < 0) target = S.repeat === 'all' ? S.queue.length - 1 : 0;
  playIndex(target);
}

function renderPlayerBar() {
  const track = S.index >= 0 ? S.queue[S.index] : null;
  els.npTitle.textContent = track ? track.title : 'Nada tocando';
  els.npArtist.textContent = track ? (track.artist || '') : '';
  if (track && track.playlist_id) {
    const playlist = (S.state.playlists || []).find(p => p.id === track.playlist_id);
    els.npCover.innerHTML = playlist && playlist.cover_path
      ? '<img src="/api/cover/' + encodeURIComponent(playlist.id) + '" alt="">' : '';
  } else {
    els.npCover.innerHTML = '';
  }
  els.btnPlay.textContent = audio.paused ? '▶' : '❚❚';
  els.btnShuffle.classList.toggle('on', S.shuffle);
  els.btnRepeat.classList.toggle('on', S.repeat !== 'off');
  els.btnRepeat.textContent = S.repeat === 'one' ? '⟳1' : '⟳';
}

els.btnPlay.onclick = () => {
  if (!audio.src) {
    const ready = (S.state.tracks || []).filter(t => t.status === 'done');
    if (!ready.length) { toast('nada baixado ainda', true); return; }
    setQueue(ready);
    playIndex(0);
    return;
  }
  if (audio.paused) audio.play(); else audio.pause();
};
els.btnNext.onclick = () => nextTrack(false);
els.btnPrev.onclick = () => prevTrack();
els.btnShuffle.onclick = () => { S.shuffle = !S.shuffle; renderPlayerBar(); };
els.btnRepeat.onclick = () => {
  S.repeat = S.repeat === 'off' ? 'all' : (S.repeat === 'all' ? 'one' : 'off');
  renderPlayerBar();
};

audio.addEventListener('timeupdate', () => {
  if (!audio.duration) return;
  els.seek.value = Math.round((audio.currentTime / audio.duration) * 1000);
  els.timeNow.textContent = fmtTime(audio.currentTime);
  els.timeEnd.textContent = fmtTime(audio.duration);
});
audio.addEventListener('ended', () => nextTrack(true));
audio.addEventListener('play', renderPlayerBar);
audio.addEventListener('pause', renderPlayerBar);
audio.addEventListener('error', () => {
  if (audio.src) toast('erro ao ler o arquivo local', true);
});
audio.addEventListener('loadedmetadata', () => {
  els.timeEnd.textContent = fmtTime(audio.duration);
});

els.seek.addEventListener('input', () => {
  if (audio.duration) audio.currentTime = (els.seek.value / 1000) * audio.duration;
});

els.volume.addEventListener('input', () => {
  audio.volume = els.volume.value / 100;
});
audio.volume = 1;

document.addEventListener('keydown', (event) => {
  if (event.target.tagName === 'INPUT') return;
  if (event.code === 'Space') { event.preventDefault(); els.btnPlay.click(); }
  else if (event.code === 'ArrowRight' && audio.src) audio.currentTime += 5;
  else if (event.code === 'ArrowLeft' && audio.src) audio.currentTime -= 5;
  else if (event.code === 'ArrowRight' && event.shiftKey) nextTrack(false);
  else if (event.code === 'ArrowLeft' && event.shiftKey) prevTrack();
});

/* ----------------------------------------------------------------- candidates */

function showCandidates(trackId) {
  const track = (S.state.tracks || []).find(t => t.id === trackId);
  const title = track ? track.title : 'faixa';
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.innerHTML =
    '<div class="modal">' +
    '<div class="modal-head"><h3>Escolher fonte — ' + esc(title) + '</h3>' +
    '<button class="btn small" id="modal-close">✕</button></div>' +
    '<div class="modal-body" id="modal-body"><div class="hint">buscando candidatos...</div></div>' +
    '</div>';
  document.body.appendChild(overlay);

  const close = () => overlay.remove();
  overlay.querySelector('#modal-close').onclick = close;
  overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });

  (async () => {
    try {
      const result = await api('/api/track/' + encodeURIComponent(trackId) + '/candidates', { method: 'POST', body: '{}' });
      const cands = result.candidates || [];
      const body = document.getElementById('modal-body');
      if (!cands.length) {
        body.innerHTML = '<div class="empty">nenhum candidato encontrado</div>';
        return;
      }
      body.innerHTML = cands.map((c, i) =>
        '<div class="cand-row' + (c.is_topic ? ' cand-topic' : '') + '">' +
        '<div class="cand-info">' +
        '<div class="cand-title">' + esc(c.title) + '</div>' +
        '<div class="cand-meta">' + esc(c.channel) + ' · ' + fmtTime(c.duration) +
        (c.is_topic ? ' · <span class="state-done">Topic oficial</span>' : '') +
        ' · score ' + c.score + '</div>' +
        '</div>' +
        '<button class="btn small primary" data-pick="' + esc(c.youtube_id) + '">Baixar esta</button>' +
        '</div>'
      ).join('');

      body.querySelectorAll('[data-pick]').forEach(btn => {
        btn.onclick = async () => {
          btn.disabled = true;
          btn.textContent = 'enfileirando...';
          try {
            await api('/api/track/' + encodeURIComponent(trackId) + '/download',
              { method: 'POST', body: JSON.stringify({ youtube_id: btn.dataset.pick }) });
            toast('download enfileirado com a fonte escolhida');
            close();
            await refresh(true);
          } catch (err) { toast(err.message, true); btn.disabled = false; btn.textContent = 'Baixar esta'; }
        };
      });
    } catch (err) {
      document.getElementById('modal-body').innerHTML =
        '<div class="empty">' + esc(err.message) + '</div>';
    }
  })();
}

/* --------------------------------------------------------------------- boot */

if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  });
}

refresh(false);
setInterval(() => {
  const busy = (S.state && S.state.jobs || []).some(j => j.state === 'running' || j.state === 'queued');
  if (busy || S.view !== 'settings') refresh(true);
}, 1500);
