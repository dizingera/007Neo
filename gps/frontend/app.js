/* AgriPilot – Kabinen-Oberfläche.
 *
 * Zwei Dinge bestimmen den Aufbau:
 *
 * 1. Die Verbindung kann abreißen (WLAN im Feld, Master aus). Die Oberfläche
 *    verbindet sich deshalb selbst neu und zeigt in der Zwischenzeit weiter das
 *    letzte Bild, statt leer zu werden.
 * 2. Die bearbeitete Fläche wächst auf Hunderttausende Rasterzellen. Sie wird
 *    einmal in eine Hintergrund-Leinwand gezeichnet und danach nur noch als
 *    Bild verschoben – neue Zellen kommen als kleine Nachlieferung dazu.
 */

const state = {
  live: null,
  cellSize: 0.5,
  view: { scale: 6, rotate: true, follow: true },
  trail: [],
  fields: [], lines: [], jobs: [],
  connected: false,
};

const el = (id) => document.getElementById(id);
const canvas = el('map');
const ctx = canvas.getContext('2d');

/* ---------------------------------------------------------------- Bedeckung */

const COVER_SIZE = 4096;            // Zellen; bei 0,5 m sind das gut 2 x 2 km
const coverLayer = document.createElement('canvas');
coverLayer.width = coverLayer.height = COVER_SIZE;
const coverCtx = coverLayer.getContext('2d');
coverCtx.fillStyle = '#1d6b3a';

function paintCells(cells) {
  const half = COVER_SIZE / 2;
  for (const [ix, iy] of cells) {
    const px = ix + half, py = half - iy;
    if (px < 0 || py < 0 || px >= COVER_SIZE || py >= COVER_SIZE) continue;
    coverCtx.fillRect(px, py, 1, 1);
  }
}

function clearCells() {
  coverCtx.clearRect(0, 0, COVER_SIZE, COVER_SIZE);
}

/* ------------------------------------------------------------- Verbindung */

let socket = null;

function connect() {
  const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
  socket = new WebSocket(`${protocol}://${location.host}/ws`);

  socket.onopen = () => { state.connected = true; };
  socket.onclose = () => {
    state.connected = false;
    setTimeout(connect, 1500);          // im Feld ist ein Abriss normal
  };
  socket.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.type === 'init') {
      state.cellSize = data.coverage ? data.coverage.cell_size : 0.5;
      clearCells();
      paintCells(data.cells || []);
      state.trail = [];
    } else if (data.new_cells && data.new_cells.length) {
      paintCells(data.new_cells);
    }
    onState(data);
  };
}

function onState(data) {
  const previous = state.live;
  state.live = data;
  if (data.coverage) state.cellSize = data.coverage.cell_size;

  if (data.tool_position) {
    const last = state.trail[state.trail.length - 1];
    if (!last || Math.hypot(last[0] - data.tool_position[0],
                            last[1] - data.tool_position[1]) > 1.0) {
      state.trail.push(data.tool_position);
      if (state.trail.length > 900) state.trail.shift();
    }
  }
  // Feldwechsel: alte Bedeckung gehört nicht auf das neue Feld
  const before = previous && previous.field ? previous.field.id : null;
  const now = data.field ? data.field.id : null;
  if (before !== now) { clearCells(); state.trail = []; refreshLists(); }

  updateHud(data);
}

/* -------------------------------------------------------------------- HUD */

function updateHud(s) {
  const guidance = s.guidance || {};
  const fix = s.fix;
  const cm = guidance.active ? guidance.cross_track_cm : null;

  const xte = el('xte');
  if (cm === null || cm === undefined) {
    xte.textContent = '--'; xte.className = 'readout-value';
  } else {
    xte.textContent = Math.abs(cm).toFixed(0);
    xte.className = 'readout-value ' +
      (Math.abs(cm) < 5 ? 'centre' : (cm > 0 ? 'right' : 'left'));
  }
  el('speed').textContent = fix ? (fix.speed_kmh).toFixed(1) : '--';
  el('pass').textContent = guidance.active ? guidance.pass_number : '--';
  el('area').textContent = s.job ? s.job.area_ha.toFixed(2)
                                 : (s.coverage ? s.coverage.area_ha.toFixed(2) : '--');

  // Hang und Ausgleich: die Zahl, die erklärt, warum die Spur am Hang sonst
  // wandert - der Ausgleich steht daneben, damit man ihm ansieht, dass er wirkt.
  const imu = s.imu;
  el('tiltBox').hidden = !imu;
  if (imu) {
    const correction = Math.abs(imu.terrain_offset_cm ? imu.terrain_offset_cm[0] : 0);
    el('tilt').textContent = imu.roll_deg.toFixed(1);
    el('tilt').className = 'readout-value' +
      (!imu.fresh ? '' : correction > 15 ? ' left' : ' centre');
    el('tiltBox').title = `Hangausgleich ${correction.toFixed(0)} cm`;
  }

  // Genauigkeit ist die Zahl, an der alles hängt – deshalb immer sichtbar.
  const fixChip = el('fixChip');
  if (!fix) { fixChip.textContent = 'kein GPS'; fixChip.className = 'chip bad'; }
  else {
    const accuracy = fix.accuracy_m != null ? ` ±${(fix.accuracy_m * 100).toFixed(0)} cm` : '';
    fixChip.textContent = `${fix.fix_label}${accuracy} · ${fix.satellites} Sat`;
    fixChip.className = 'chip ' + (fix.rank >= 4 ? 'good' : fix.rank >= 2 ? 'warn' : 'bad');
  }

  const steering = s.steering;
  const steerChip = el('steerChip');
  if (!steering || !steering.configured) {
    steerChip.textContent = 'Lenkhilfe'; steerChip.className = 'chip';
  } else if (steering.command.engaged) {
    steerChip.textContent = 'Lenkung aktiv'; steerChip.className = 'chip good';
  } else {
    steerChip.textContent = steering.command.reason; steerChip.className = 'chip warn';
  }

  const system = s.system;
  const netChip = el('netChip');
  if (system) {
    const parts = [system.role === 'master' ? 'Master' : 'Client'];
    if (system.relay && system.relay.running) {
      parts.push(`${system.relay.clients} Traktor(en)`);
    }
    // Der Abgleichstatus sagt nur auf einem Client etwas aus - der Master
    // gleicht sich nicht mit sich selbst ab.
    if (system.role !== 'master' && system.sync && system.sync.status) {
      parts.push(system.sync.status);
    }
    netChip.textContent = parts.join(' · ');
    netChip.className = 'chip ' + (state.connected ? '' : 'bad');
  }

  el('fieldLabel').textContent = s.field ? s.field.name : 'Kein Feld';
  el('lineLabel').textContent = s.line
    ? `${s.line.name} · ${s.line.spacing_m.toFixed(2)} m` +
      (s.line.nudge_m ? ` · Versatz ${(s.line.nudge_m * 100).toFixed(0)} cm` : '')
    : 'Keine Spur – A und B setzen';

  const hint = [];
  if (s.recording && s.recording.mode === 'boundary') {
    hint.push(`Grenze wird aufgezeichnet · ${s.recording.points} Punkte · ` +
              `${s.recording.area_ha.toFixed(2)} ha`);
  }
  if (s.recording && s.recording.mode === 'curve') hint.push('Kurve wird aufgezeichnet');
  if (s.recording && s.recording.pending_a) hint.push('A gesetzt – jetzt B setzen');
  if (!state.connected) hint.push('Keine Verbindung zum Gerät');
  el('hint').textContent = hint.join('\n');

  drawLightbar(guidance);
  drawSections(s.sections || []);

  el('btnJob').classList.toggle('active', !!s.job);
  el('btnJob').querySelector('span').textContent = s.job ? 'Arbeit beenden' : 'Arbeit starten';
  el('btnBoundary').classList.toggle('recording',
      s.recording && s.recording.mode === 'boundary');
  el('btnCurve').classList.toggle('recording', s.recording && s.recording.mode === 'curve');
  el('btnSteer').classList.toggle('armed', steering && steering.armed);
}

const LEDS = 21;
function drawLightbar(guidance) {
  const bar = el('lightbar');
  if (bar.children.length !== LEDS) {
    bar.innerHTML = '';
    for (let i = 0; i < LEDS; i++) {
      const led = document.createElement('div');
      led.className = 'led' + (i === (LEDS - 1) / 2 ? ' centre' : '');
      bar.appendChild(led);
    }
  }
  const middle = (LEDS - 1) / 2;
  const offset = guidance.active ? Math.max(-middle, Math.min(middle, guidance.lightbar)) : null;
  for (let i = 0; i < LEDS; i++) {
    const led = bar.children[i];
    led.className = 'led' + (i === middle ? ' centre' : '');
    if (offset === null) continue;
    const position = i - middle;
    // Die Lampen zwischen Mitte und Abweichung leuchten: man fährt dorthin,
    // wo es dunkel ist.
    const lit = offset === 0 ? position === 0
      : (offset > 0 ? position > 0 && position <= offset
                    : position < 0 && position >= offset);
    if (!lit) continue;
    const magnitude = Math.abs(guidance.cross_track_cm);
    led.classList.add(magnitude < 5 ? 'on-green' : magnitude < 20 ? 'on-amber' : 'on-red');
  }
}

function drawSections(sections) {
  const host = el('sections');
  if (sections.length <= 1) { host.hidden = true; return; }
  host.hidden = false;
  if (host.children.length !== sections.length) {
    host.innerHTML = '';
    sections.forEach((section, index) => {
      const box = document.createElement('div');
      box.className = 'sec';
      box.textContent = index + 1;
      box.onclick = () => api('POST', `/api/sections/${index}`,
        { forced_off: !state.live.sections[index].forced_off });
      host.appendChild(box);
    });
  }
  sections.forEach((section, index) => {
    const box = host.children[index];
    box.className = 'sec' + (section.forced_off ? ' forced' : (section.enabled ? ' on' : ''));
  });
}

/* ----------------------------------------------------------------- Karte */

function resize() {
  const ratio = window.devicePixelRatio || 1;
  canvas.width = canvas.clientWidth * ratio;
  canvas.height = canvas.clientHeight * ratio;
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
}
window.addEventListener('resize', resize);

function viewCentre() {
  const s = state.live;
  return (s && s.tool_position) ? s.tool_position : [0, 0];
}

function viewRotation() {
  const s = state.live;
  if (!state.view.rotate || !s || s.heading == null) return 0;
  return -s.heading * Math.PI / 180;
}

function applyWorldTransform() {
  const width = canvas.clientWidth, height = canvas.clientHeight;
  const scale = state.view.scale;
  const [cx, cy] = viewCentre();
  ctx.translate(width / 2, height * 0.62);   // Traktor sitzt im unteren Drittel
  ctx.rotate(viewRotation());
  ctx.scale(scale, -scale);
  ctx.translate(-cx, -cy);
}

function screenToWorld(x, y) {
  const width = canvas.clientWidth, height = canvas.clientHeight;
  const scale = state.view.scale, rotation = viewRotation();
  const [cx, cy] = viewCentre();
  let dx = x - width / 2, dy = y - height * 0.62;
  const cos = Math.cos(-rotation), sin = Math.sin(-rotation);
  const rx = dx * cos - dy * sin, ry = dx * sin + dy * cos;
  return [cx + rx / scale, cy - ry / scale];
}

function render() {
  const width = canvas.clientWidth, height = canvas.clientHeight;
  ctx.save();
  ctx.fillStyle = '#0d1117';
  ctx.fillRect(0, 0, width, height);

  ctx.save();
  applyWorldTransform();
  drawCoverage();
  drawBoundary();
  drawPasses();
  drawTrail();
  drawRecording();
  ctx.restore();

  drawVehicle();
  ctx.restore();
  requestAnimationFrame(render);
}

function drawCoverage() {
  const cell = state.cellSize || 0.5;
  const half = COVER_SIZE / 2;
  // Nur den sichtbaren Ausschnitt der Hintergrund-Leinwand zeichnen.
  const corners = [
    screenToWorld(0, 0), screenToWorld(canvas.clientWidth, 0),
    screenToWorld(0, canvas.clientHeight),
    screenToWorld(canvas.clientWidth, canvas.clientHeight),
  ];
  const xs = corners.map((p) => p[0]), ys = corners.map((p) => p[1]);
  const sx0 = Math.max(0, Math.floor(Math.min(...xs) / cell) + half - 1);
  const sx1 = Math.min(COVER_SIZE, Math.ceil(Math.max(...xs) / cell) + half + 1);
  const sy0 = Math.max(0, half - Math.ceil(Math.max(...ys) / cell) - 1);
  const sy1 = Math.min(COVER_SIZE, half - Math.floor(Math.min(...ys) / cell) + 1);
  if (sx1 <= sx0 || sy1 <= sy0) return;

  const wx = (sx0 - half) * cell;
  const wy = (half - sy1) * cell;
  ctx.save();
  ctx.scale(1, -1);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(coverLayer, sx0, sy0, sx1 - sx0, sy1 - sy0,
                wx, -(wy + (sy1 - sy0) * cell), (sx1 - sx0) * cell, (sy1 - sy0) * cell);
  ctx.restore();
}

function drawBoundary() {
  const field = state.live && state.live.field;
  if (!field || !field.boundary || field.boundary.length < 3) return;
  ctx.beginPath();
  field.boundary.forEach(([x, y], index) =>
    index ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
  ctx.closePath();
  ctx.strokeStyle = '#e3b341';
  ctx.lineWidth = 2 / state.view.scale;
  ctx.stroke();
}

function drawPasses() {
  const s = state.live;
  if (!s || !s.line) return;
  const line = s.line;
  const current = s.guidance.active ? s.guidance.pass_number : 0;
  for (let offset = current - 6; offset <= current + 6; offset++) {
    const points = shiftLine(line, offset * line.spacing_m + line.nudge_m);
    if (!points.length) continue;
    ctx.beginPath();
    points.forEach(([x, y], index) => index ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
    const active = offset === current;
    ctx.strokeStyle = active ? '#3fb950' : '#3d4b5c';
    ctx.lineWidth = (active ? 3 : 1.4) / state.view.scale;
    ctx.stroke();
  }
}

/* Parallele Spur berechnen – dieselbe Rechnung wie im Backend, damit die
 * Anzeige und die Führung nicht auseinanderlaufen. */
function shiftLine(line, shift) {
  const points = line.points;
  if (points.length < 2) return [];
  if (line.mode === 'ab') {
    const [a, b] = [points[0], points[points.length - 1]];
    const heading = Math.atan2(b[0] - a[0], b[1] - a[1]);
    const forward = [Math.sin(heading), Math.cos(heading)];
    const right = [Math.cos(heading), -Math.sin(heading)];
    const mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
    const half = Math.max(600, Math.hypot(b[0] - a[0], b[1] - a[1])) / 2;
    return [-half, half].map((t) => [
      mid[0] + forward[0] * t + right[0] * shift,
      mid[1] + forward[1] * t + right[1] * shift,
    ]);
  }
  return points.map((p, index) => {
    const a = points[Math.max(0, index - 1)];
    const b = points[Math.min(points.length - 1, index + 1)];
    const heading = Math.atan2(b[0] - a[0], b[1] - a[1]);
    return [p[0] + Math.cos(heading) * shift, p[1] - Math.sin(heading) * shift];
  });
}

function drawTrail() {
  if (state.trail.length < 2) return;
  ctx.beginPath();
  state.trail.forEach(([x, y], index) => index ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
  ctx.strokeStyle = '#58a6ff88';
  ctx.lineWidth = 1.5 / state.view.scale;
  ctx.stroke();
}

function drawRecording() {
  const s = state.live;
  if (!s || !s.recording) return;
  if (s.recording.pending_a) {
    const [x, y] = s.recording.pending_a;
    ctx.beginPath();
    ctx.arc(x, y, 6 / state.view.scale, 0, Math.PI * 2);
    ctx.fillStyle = '#f85149';
    ctx.fill();
  }
}

function drawVehicle() {
  const s = state.live;
  if (!s || !s.tool_position) return;
  const width = canvas.clientWidth, height = canvas.clientHeight;
  const scale = state.view.scale;
  const profile = s.profile || { width_m: 3 };
  const sections = s.sections || [];

  ctx.save();
  ctx.translate(width / 2, height * 0.62);
  if (!state.view.rotate && s.heading != null) {
    ctx.rotate(s.heading * Math.PI / 180);
  }

  // Arbeitsbreite mit dem Schaltzustand jeder Sektion
  const bar = (profile.width_m || 3) * scale;
  ctx.save();
  ctx.translate(0, 12);
  if (sections.length) {
    sections.forEach((section) => {
      const x0 = section.left_m * scale, x1 = section.right_m * scale;
      ctx.fillStyle = section.forced_off ? '#f8514966'
        : section.enabled ? '#3fb950cc' : '#6e7681aa';
      ctx.fillRect(x0, -5, x1 - x0, 10);
    });
  } else {
    ctx.fillStyle = '#3fb950cc';
    ctx.fillRect(-bar / 2, -5, bar, 10);
  }
  ctx.strokeStyle = '#0d1117';
  ctx.lineWidth = 1;
  ctx.strokeRect(-bar / 2, -5, bar, 10);
  ctx.restore();

  // Traktor
  ctx.beginPath();
  ctx.moveTo(0, -22);
  ctx.lineTo(13, 14);
  ctx.lineTo(0, 7);
  ctx.lineTo(-13, 14);
  ctx.closePath();
  ctx.fillStyle = '#e6edf3';
  ctx.fill();
  ctx.restore();
}

/* ------------------------------------------------------------- Bedienung */

async function api(method, url, body) {
  try {
    const response = await fetch(url, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      toast(data.detail || `Fehler ${response.status}`, true);
      return null;
    }
    return data;
  } catch (error) {
    toast('Gerät nicht erreichbar', true);
    return null;
  }
}

let toastTimer = null;
function toast(message, isError) {
  const box = el('toast');
  box.textContent = message;
  box.className = 'toast' + (isError ? ' error' : '');
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { box.hidden = true; }, 3500);
}

el('btnA').onclick = async () => {
  if (await api('POST', '/api/guidance/a')) toast('Punkt A gesetzt');
};
el('btnB').onclick = async () => {
  const result = await api('POST', '/api/guidance/b', {});
  if (result) { toast(`Spur "${result.data.name}" angelegt`); refreshLists(); }
};
el('btnCurve').onclick = async () => {
  const recording = state.live && state.live.recording.mode === 'curve';
  if (recording) {
    const result = await api('POST', '/api/record/stop', {});
    if (result) { toast('Kurve gespeichert'); refreshLists(); }
  } else if (await api('POST', '/api/record/start', { mode: 'curve' })) {
    toast('Kurve aufzeichnen – jetzt die Linie abfahren');
  }
};
el('btnBoundary').onclick = async () => {
  const recording = state.live && state.live.recording.mode === 'boundary';
  if (recording) {
    const result = await api('POST', '/api/record/stop', {});
    if (result) toast(`Feldgrenze: ${result.data.area_ha.toFixed(2)} ha`);
  } else if (await api('POST', '/api/record/start', { mode: 'boundary' })) {
    toast('Grenze aufzeichnen – einmal um das Feld fahren');
  }
};
el('btnNudgeLeft').onclick = () => api('POST', '/api/guidance/nudge', { metres: -0.01 });
el('btnNudgeRight').onclick = () => api('POST', '/api/guidance/nudge', { metres: 0.01 });

el('btnJob').onclick = async () => {
  if (state.live && state.live.job) {
    const result = await api('POST', '/api/job/stop');
    if (result) toast(`Fertig: ${result.data.area_ha.toFixed(2)} ha`);
  } else {
    const operation = prompt('Welche Arbeit? (z.B. Grubbern, Säen, Spritzen)', '');
    if (operation === null) return;
    if (await api('POST', '/api/job/start', { operation })) toast('Arbeit läuft');
  }
};

el('btnSteer').onclick = async () => {
  const steering = state.live && state.live.steering;
  if (steering && steering.armed) {
    await api('POST', '/api/steering/disarm');
    toast('Lenkung aus');
  } else {
    const result = await api('POST', '/api/steering/arm');
    if (result) toast(result.data.armed ? 'Lenkung scharf – Hände ans Lenkrad'
                                        : result.data.message, !result.data.armed);
  }
};

el('zoomIn').onclick = () => { state.view.scale = Math.min(40, state.view.scale * 1.4); };
el('zoomOut').onclick = () => { state.view.scale = Math.max(0.4, state.view.scale / 1.4); };
el('viewMode').onclick = () => {
  state.view.rotate = !state.view.rotate;
  el('viewMode').textContent = state.view.rotate ? '↑' : 'N';
};

// Zwei-Finger-Zoom, weil in der Kabine nicht immer eine Maus liegt
let pinchStart = null;
canvas.addEventListener('touchstart', (event) => {
  if (event.touches.length === 2) {
    pinchStart = { distance: touchDistance(event), scale: state.view.scale };
  }
});
canvas.addEventListener('touchmove', (event) => {
  if (pinchStart && event.touches.length === 2) {
    event.preventDefault();
    const factor = touchDistance(event) / pinchStart.distance;
    state.view.scale = Math.max(0.4, Math.min(40, pinchStart.scale * factor));
  }
}, { passive: false });
canvas.addEventListener('touchend', () => { pinchStart = null; });
canvas.addEventListener('wheel', (event) => {
  event.preventDefault();
  state.view.scale = Math.max(0.4, Math.min(40,
    state.view.scale * (event.deltaY < 0 ? 1.15 : 1 / 1.15)));
}, { passive: false });

function touchDistance(event) {
  const [a, b] = event.touches;
  return Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
}

/* -------------------------------------------------------------- Menü */

el('btnMenu').onclick = () => { el('sheet').hidden = false; refreshLists(); };
el('sheetClose').onclick = () => { el('sheet').hidden = true; };
document.querySelectorAll('.tab').forEach((tab) => {
  tab.onclick = () => {
    document.querySelectorAll('.tab').forEach((t) => t.classList.remove('active'));
    tab.classList.add('active');
    document.querySelectorAll('.tab-panel').forEach((panel) => {
      panel.hidden = panel.dataset.panel !== tab.dataset.tab;
    });
    refreshLists();
  };
});

el('btnNewField').onclick = async () => {
  const name = el('newFieldName').value.trim() || `Feld ${new Date().toLocaleDateString('de-DE')}`;
  const result = await api('POST', '/api/fields', { name });
  if (result) { el('newFieldName').value = ''; toast(`Feld "${name}" angelegt`); refreshLists(); }
};

el('btnAPlus').onclick = async () => {
  const result = await api('POST', '/api/guidance/a-plus', {});
  if (result) { toast(`Spur "${result.data.name}" angelegt`); refreshLists(); }
};

el('btnSaveProfile').onclick = async () => {
  const payload = {};
  document.querySelectorAll('#profileForm input').forEach((input) => {
    payload[input.name] = input.type === 'number' ? parseFloat(input.value) : input.value;
  });
  if (await api('POST', '/api/profile', payload)) toast('Maschine übernommen');
};

el('autoSections').onchange = (event) =>
  api('POST', '/api/sections/auto', { enabled: event.target.checked });

el('btnCentre').onclick = async () => {
  if (!confirm('Stehen die Räder gerade? Diese Stellung wird als Mitte gemerkt.')) return;
  const result = await api('POST', '/api/steering/centre');
  if (result) toast('Mitte gelernt');
};

el('btnLevel').onclick = async () => {
  const result = await api('POST', '/api/imu/level');
  if (result) toast('Neigungssensor genullt');
};
el('compensation').onchange = (event) =>
  api('POST', '/api/imu/compensation', { enabled: event.target.checked });

el('simSpeed').oninput = (event) =>
  api('POST', '/api/simulator', { speed_kmh: parseFloat(event.target.value) });
el('simSteer').oninput = (event) =>
  api('POST', '/api/simulator', { steer_deg: parseFloat(event.target.value) });

async function refreshLists() {
  if (el('sheet').hidden) return;
  const active = document.querySelector('.tab.active').dataset.tab;
  if (active === 'fields') renderFields(await (await fetch('/api/fields')).json());
  if (active === 'lines') {
    const fieldId = state.live && state.live.field ? state.live.field.id : '';
    renderLines(await (await fetch(`/api/lines?field_id=${fieldId}`)).json());
  }
  if (active === 'jobs') renderJobs(await (await fetch('/api/jobs')).json());
  if (active === 'machine') fillProfile();
  if (active === 'system') renderSystem();
}

function renderFields(fields) {
  const host = el('fieldList');
  const currentId = state.live && state.live.field ? state.live.field.id : null;
  host.innerHTML = '';
  fields.forEach((field) => {
    host.appendChild(item({
      active: field.id === currentId,
      title: field.name,
      sub: `${field.area_ha.toFixed(2)} ha` +
           (field.boundary.length ? ` · ${field.boundary.length} Grenzpunkte` : ' · keine Grenze'),
      actions: [
        ['Laden', async () => {
          if (await api('POST', `/api/fields/${field.id}/load`)) {
            toast(`${field.name} geladen`); el('sheet').hidden = true;
          }
        }],
        ['Löschen', async () => {
          if (confirm(`Feld "${field.name}" löschen?`)) {
            await api('DELETE', `/api/fields/${field.id}`); refreshLists();
          }
        }],
      ],
    }));
  });
  if (!fields.length) host.innerHTML = '<p class="note">Noch keine Felder angelegt.</p>';
}

function renderLines(lines) {
  const host = el('lineList');
  const currentId = state.live && state.live.line ? state.live.line.id : null;
  host.innerHTML = '';
  lines.forEach((line) => {
    host.appendChild(item({
      active: line.id === currentId,
      title: line.name,
      sub: `${line.mode === 'ab' ? 'AB-Linie' : 'Kurve'} · Abstand ${line.spacing_m.toFixed(2)} m`,
      actions: [
        ['Laden', async () => {
          if (await api('POST', `/api/lines/${line.id}/load`)) {
            toast(`Spur "${line.name}" aktiv`); el('sheet').hidden = true;
          }
        }],
        ['Löschen', async () => {
          await api('DELETE', `/api/lines/${line.id}`); refreshLists();
        }],
      ],
    }));
  });
  if (!lines.length) host.innerHTML =
    '<p class="note">Für dieses Feld gibt es noch keine Spur.</p>';
}

function renderJobs(jobs) {
  const host = el('jobList');
  host.innerHTML = '';
  jobs.forEach((job) => {
    const started = new Date(job.started_at * 1000);
    const minutes = job.ended_at ? (job.ended_at - job.started_at) / 60 : null;
    const row = item({
      title: `${job.operation || 'Arbeit'} · ${job.area_ha.toFixed(2)} ha`,
      sub: `${started.toLocaleString('de-DE')} · ${job.vehicle || 'Traktor'}` +
           (minutes ? ` · ${minutes.toFixed(0)} min` : ' · läuft') +
           ` · ${(job.distance_m / 1000).toFixed(1)} km` +
           (job.overlap_ha ? ` · ${job.overlap_ha.toFixed(2)} ha doppelt` : ''),
      actions: [],
    });
    const actions = row.querySelector('.actions');
    ['gpx', 'geojson', 'csv'].forEach((format) => {
      const link = document.createElement('a');
      link.className = 'button';
      link.href = `/api/jobs/${job.id}/${format}`;
      link.textContent = format.toUpperCase();
      actions.appendChild(link);
    });
    host.appendChild(row);
  });
  if (!jobs.length) host.innerHTML = '<p class="note">Noch keine Arbeiten aufgezeichnet.</p>';
}

function fillProfile() {
  const profile = state.live && state.live.profile;
  if (!profile) return;
  document.querySelectorAll('#profileForm input').forEach((input) => {
    if (profile[input.name] !== undefined) input.value = profile[input.name];
  });
  el('autoSections').checked = !!(state.live && state.live.auto_sections);
}

function renderSystem() {
  const system = state.live && state.live.system;
  const host = el('systemList');
  if (!system) { host.innerHTML = '<p class="note">Keine Daten.</p>'; return; }
  const imu = state.live.imu;
  el('imuRow').hidden = !imu;
  el('imuNote').hidden = !imu;
  if (imu) el('compensation').checked = !!imu.compensation;

  const steering = state.live.steering;
  const output = system.steering_output;
  // Die Mitte lässt sich nur lernen, wo ein Drehgeber zählt.
  el('steerRow').hidden = !(output && output.mitte_gelernt !== undefined);
  if (!el('steerRow').hidden) {
    el('steerInfo').textContent =
      `${output.zaehlwerte_je_grad} Zählwerte je Grad · ` +
      (output.mitte_gelernt ? `Ist ${(output.radwinkel || 0).toFixed(1)}° · ` +
        `Soll ${(output.soll_grad || 0).toFixed(1)}°` : 'Mitte noch nicht gelernt');
  }

  const rows = [
    ['Rolle', system.role === 'master' ? 'Master' : 'Client', true],
    ['Version', system.version, true],
    ['GPS-Quelle', `${system.gnss.source} · ${system.gnss.status}`, system.gnss.healthy],
    ['Neigungssensor', system.imu.source === 'aus' ? 'nicht eingerichtet'
      : `${system.imu.status}` + (imu ? ` · Hang ${imu.roll_deg.toFixed(1)}° · ` +
        `Ausgleich ${Math.abs(imu.terrain_offset_cm[0]).toFixed(0)} cm` : ''),
      system.imu.source === 'aus' ? true : system.imu.healthy],
    ['Lenkausgang', `${system.steering_output.typ} · ${system.steering_output.status}` +
      (steering && steering.duty ? ` · ${(steering.duty * 100).toFixed(0)} %` : ''),
      system.steering_output.bereit || system.steering_output.typ === 'none'],
    ['Korrekturdaten (RTK)', system.corrections.status +
      (system.corrections.bytes ? ` · ${(system.corrections.bytes / 1024).toFixed(0)} kB` : '') +
      // Das Alter kommt vom Empfänger und sagt mehr als die Byte-Zahl: es
      // steigt, sobald der Weg von der Basis abreißt, auch wenn die
      // Verbindung noch zu stehen scheint.
      (system.corrections.age_s != null
        ? ` · ${system.corrections.age_s.toFixed(0)} s alt` : ''),
      system.corrections.healthy &&
      (system.corrections.age_s == null || system.corrections.age_s < 30)],
    ['Korrektur-Weitergabe', system.relay.running
      ? `aktiv · ${system.relay.clients} Traktor(en)` : 'aus', system.relay.running],
    ['Abgleich', system.sync.status + (system.sync.age_s != null
      ? ` · vor ${system.sync.age_s.toFixed(0)} s` : ''), true],
  ];
  host.innerHTML = '';
  rows.forEach(([label, value, good]) => {
    const row = document.createElement('div');
    row.className = 'item';
    row.innerHTML = `<div class="main"><div class="title">${label}</div>
      <div class="sub">${value}</div></div>
      <div class="chip ${good ? 'good' : 'warn'}">${good ? 'OK' : 'prüfen'}</div>`;
    host.appendChild(row);
  });
  (system.devices || []).forEach((device) => {
    const age = (Date.now() / 1000 - device.last_seen);
    const row = document.createElement('div');
    row.className = 'item';
    row.innerHTML = `<div class="main"><div class="title">${device.name}</div>
      <div class="sub">${device.role} · zuletzt vor ${age < 90 ? age.toFixed(0) + ' s'
        : (age / 60).toFixed(0) + ' min'}</div></div>`;
    host.appendChild(row);
  });
  el('simRow').hidden = system.gnss.source !== 'simulator';
}

function item({ active, title, sub, actions }) {
  const row = document.createElement('div');
  row.className = 'item' + (active ? ' active' : '');
  const main = document.createElement('div');
  main.className = 'main';
  main.innerHTML = `<div class="title"></div><div class="sub"></div>`;
  main.querySelector('.title').textContent = title;
  main.querySelector('.sub').textContent = sub;
  const box = document.createElement('div');
  box.className = 'actions';
  (actions || []).forEach(([label, handler]) => {
    const button = document.createElement('button');
    button.textContent = label;
    button.onclick = handler;
    box.appendChild(button);
  });
  row.append(main, box);
  return row;
}

/* ------------------------------------------------------------------ Start */

resize();
connect();
requestAnimationFrame(render);
setInterval(() => { if (!el('sheet').hidden) refreshLists(); }, 5000);
