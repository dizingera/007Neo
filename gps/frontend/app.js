/* AgriPilot – Kabinen-Oberfläche: der Blick über die Haube.
 *
 * Drei Dinge bestimmen den Aufbau:
 *
 * 1. Die Verbindung kann abreißen (WLAN im Feld, Master aus). Die Oberfläche
 *    verbindet sich deshalb selbst neu und zeigt in der Zwischenzeit weiter das
 *    letzte Bild, statt leer zu werden.
 * 2. Die bearbeitete Fläche wächst auf Hunderttausende Rasterzellen. Sie wird
 *    einmal in eine Hintergrund-Leinwand gezeichnet und danach nur noch als
 *    Bild verschoben – neue Zellen kommen als kleine Nachlieferung dazu.
 * 3. Die Perspektive ist keine 3D-Grafik. Die Karte wird flach und in
 *    Fahrtrichtung gezeichnet, wie bisher; das Kippen zum Horizont macht eine
 *    CSS-Transformation auf der Ebene darüber. Das kostet die Grafikkarte des
 *    Tablets fast nichts und läuft auf jedem Browser, der die Kachel kann.
 */

const state = {
  live: null,
  cellSize: 0.5,
  view: { scale: 0, rotate: true, mode: 'perspektive' },   // scale wird beim ersten resize gesetzt
  trail: [],
  xteVerlauf: [],
  fields: [], lines: [], jobs: [],
  connected: false,
};

const el = (id) => document.getElementById(id);
const canvas = el('map');
const ctx = canvas.getContext('2d');
const stage = el('stage');

/* Farben kommen aus dem Stylesheet, damit Tag und Nacht dieselbe Zeichnung
 * teilen und niemand in zwei Dateien dieselbe Farbe pflegen muss. */
let F = {};
function farbenLesen() {
  const cs = getComputedStyle(document.body);
  F = Object.fromEntries(['boden', 'raster', 'grenze', 'vg', 'pass', 'aktiv', 'flaeche', 'wende', 'spur', 'traktor']
    .map((k) => [k, cs.getPropertyValue('--f-' + k).trim()]));
  coverCtx.fillStyle = F.flaeche;
}

/* ---------------------------------------------------------------- Bedeckung */

const COVER_SIZE = 4096;            // Zellen; bei 0,5 m sind das gut 2 x 2 km
const coverLayer = document.createElement('canvas');
coverLayer.width = coverLayer.height = COVER_SIZE;
const coverCtx = coverLayer.getContext('2d');
const coverCells = [];              // alle Zellen, damit ein Farbwechsel neu malen kann

function paintCells(cells, merken = true) {
  const half = COVER_SIZE / 2;
  for (const [ix, iy] of cells) {
    const px = ix + half, py = half - iy;
    if (px < 0 || py < 0 || px >= COVER_SIZE || py >= COVER_SIZE) continue;
    coverCtx.fillRect(px, py, 1, 1);
    if (merken) coverCells.push([ix, iy]);
  }
}

function clearCells() {
  coverCtx.clearRect(0, 0, COVER_SIZE, COVER_SIZE);
  coverCells.length = 0;
}

function repaintCells() {
  coverCtx.clearRect(0, 0, COVER_SIZE, COVER_SIZE);
  paintCells(coverCells, false);
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

/* Ein Chip mit Leuchtpunkt: Text und Farbe, der Punkt bleibt. */
function chip(id, text, klasse) {
  const node = el(id);
  if (!node.dataset.aufgebaut) {
    // Einmal aufbauen: Leuchtpunkt und Textfeld. Der Platzhaltertext aus dem
    // HTML fliegt dabei raus, sonst stünde er neben dem echten.
    node.textContent = '';
    node.appendChild(document.createElement('i'));
    node.appendChild(document.createElement('span'));
    node.dataset.aufgebaut = '1';
  }
  node.lastElementChild.textContent = text;
  node.className = 'chip' + (node.classList.contains('titel') ? ' titel' : '') + (klasse ? ' ' + klasse : '');
}

function updateHud(s) {
  const guidance = s.guidance || {};
  const fix = s.fix;
  const cm = guidance.active ? guidance.cross_track_cm : null;

  // Die Abweichung: Zahl, Pfeil, Farbe. Der Pfeil zeigt wie der Balken die
  // Seite, auf der die Maschine steht - dorthin ist es hell, weg davon lenkt man.
  const xte = el('xte'), pfeil = el('xtePfeil');
  if (cm === null || cm === undefined) {
    xte.textContent = '--'; xte.className = ''; pfeil.textContent = '●'; pfeil.className = 'pfeil';
  } else {
    const acm = Math.abs(cm);
    xte.textContent = acm.toFixed(0);
    xte.className = acm < 5 ? 'ok' : acm < 20 ? 'warn' : 'bad';
    pfeil.textContent = acm < 3 ? '●' : cm > 0 ? '▶' : '◀';
    pfeil.className = 'pfeil' + (acm < 3 ? ' ok' : '');
  }
  el('speed').textContent = fix ? fix.speed_kmh.toFixed(1).replace('.', ',') : '--';
  const kontur = s.line && s.line.mode === 'contour';
  el('pass').textContent = guidance.active ? String(guidance.pass_number) : '--';
  el('passLabel').textContent = kontur ? 'Ring' : 'Spur';
  el('area').textContent = (s.job ? s.job.area_ha : (s.coverage ? s.coverage.area_ha : 0)).toFixed(2).replace('.', ',');

  // Hang und Ausgleich: die Zahl, die erklärt, warum die Spur am Hang sonst
  // wandert - der Ausgleich steht daneben, damit man ihm ansieht, dass er wirkt.
  const imu = s.imu;
  el('tiltBox').hidden = !imu;
  if (imu) {
    const correction = Math.abs(imu.terrain_offset_cm ? imu.terrain_offset_cm[0] : 0);
    el('tilt').textContent = imu.roll_deg.toFixed(1).replace('.', ',') + '°';
    el('tilt').className = imu.fresh && correction > 15 ? 'warn' : '';
    el('tiltLabel').textContent = `Hang · ${correction.toFixed(0)} cm`;
  }

  // Verlauf der Abweichung: sieht man, ob der Regler ruhig arbeitet oder pendelt.
  state.xteVerlauf.push(cm === null ? 0 : Math.max(-30, Math.min(30, cm)));
  if (state.xteVerlauf.length > 200) state.xteVerlauf.shift();
  const n = state.xteVerlauf.length;
  el('spark').setAttribute('d', state.xteVerlauf.map((v, i) =>
    (i ? 'L' : 'M') + (i * 200 / Math.max(1, n - 1)).toFixed(1) + ' ' + (20 - v / 30 * 18).toFixed(1)).join(''));

  // Restdistanz bis zum Vorgewende. Gezählt wird bis zum Beginn des
  // Vorgewendes, nicht bis zur Grenze – dort endet die Arbeit.
  const headland = s.headland;
  const turn = s.turn || {};
  const zeigen = !!(headland && headland.aktiv && headland.rest_m != null && headland.tiefe_m > 0) || !!turn.aktiv;
  el('restBox').hidden = !zeigen;
  const ruf = el('ruf');
  if (turn.aktiv) {
    const anteil = turn.punkte && turn.punkte.length > 1 && turn.index != null
      ? Math.min(1, turn.index / (turn.punkte.length - 1)) : 0;
    el('restLabel').textContent = 'Wende läuft';
    el('rest').textContent = Math.round(anteil * 100) + ' %'; el('rest').className = 'turn';
    el('restBar').style.transform = `scaleX(${anteil.toFixed(3)})`; el('restBar').className = 'turn';
    ruf.classList.remove('an');
  } else if (zeigen) {
    const rest = headland.rest_m;
    el('restLabel').textContent = headland.im_vorgewende ? 'im Vorgewende' : 'bis Vorgewende';
    el('rest').textContent = (rest >= 0 ? '' : '−') + Math.abs(rest).toFixed(0) + ' m';
    el('rest').className = headland.alarm ? 'warn' : '';
    // Der Balken zeigt die letzten 100 m - weiter voraus ist er voll.
    el('restBar').style.transform = `scaleX(${Math.max(0, Math.min(1, rest / 100)).toFixed(3)})`;
    el('restBar').className = headland.alarm ? 'warn' : '';
    ruf.textContent = `Vorgewende in ${Math.max(0, rest).toFixed(0)} m`;
    ruf.classList.toggle('an', !!headland.alarm && rest > 0);
  } else {
    ruf.classList.remove('an');
  }

  // Genauigkeit ist die Zahl, an der alles hängt – deshalb immer sichtbar.
  if (!fix) chip('fixChip', 'kein GPS', 'bad');
  else {
    const accuracy = fix.accuracy_m != null ? ` ±${(fix.accuracy_m * 100).toFixed(0)} cm` : '';
    chip('fixChip', `${fix.fix_label}${accuracy} · ${fix.satellites} Sat`,
         fix.rank >= 4 ? 'good' : fix.rank >= 2 ? 'warn' : 'bad');
  }

  const system = s.system;
  if (system) {
    const parts = [system.role === 'master' ? 'Master' : 'Client'];
    if (system.relay && system.relay.running) parts.push(`${system.relay.clients} Traktor(en)`);
    // Der Abgleichstatus sagt nur auf einem Client etwas aus - der Master
    // gleicht sich nicht mit sich selbst ab.
    if (system.role !== 'master' && system.sync && system.sync.status) parts.push(system.sync.status);
    if (system.gnss && system.gnss.source === 'replay') parts.push('Abspielen');
    chip('netChip', parts.join(' · '), state.connected ? '' : 'bad');
  }

  chip('fieldLabel', s.field ? s.field.name : 'Kein Feld', '');
  chip('lineLabel', s.line
    ? `${s.line.name} · ${s.line.spacing_m.toFixed(2).replace('.', ',')} m` +
      (s.line.nudge_m ? ` · Versatz ${(s.line.nudge_m * 100).toFixed(0)} cm` : '')
    : 'Keine Spur – A und B setzen', s.line ? 'good' : '');

  const hint = [];
  if (s.recording && s.recording.mode === 'boundary') {
    hint.push(`Grenze wird aufgezeichnet · ${s.recording.points} Punkte · ${s.recording.area_ha.toFixed(2)} ha`);
  }
  if (s.recording && s.recording.mode === 'curve') hint.push('Kurve wird aufgezeichnet');
  if (s.recording && s.recording.pending_a) hint.push('A gesetzt – bis zum Ende fahren, dann B');
  if (turn.aktiv) hint.push('Wende läuft – Hand am Lenkrad');
  else if (turn.geplant) {
    hint.push(turn.im_feld ? 'Wende geplant – Wende noch einmal drücken'
                           : 'Wende geplant, liegt aber nicht im Feld – Richtung oder Wendekreis ändern');
  }
  if (!state.connected) hint.push('Keine Verbindung zum Gerät');
  el('hint').textContent = hint.join('\n');
  el('hint').classList.toggle('alarm', !state.connected);

  drawLightbar(guidance);
  drawSections(s.sections || []);
  // Nur wenn jemand hinsieht: eine laufende Aufzeichnung soll mitzählen,
  // aber nicht zehnmal je Sekunde in ein verstecktes Feld schreiben.
  if (!el('sheet').hidden) rohdatenStatus();

  // Die beiden Schalter sagen, was ist - nicht, was man drücken kann.
  const job = el('btnJob');
  job.classList.toggle('an', !!s.job);
  job.setAttribute('aria-checked', String(!!s.job));
  el('jobText').textContent = s.job
    ? `an · ${s.job.operation || 'Arbeit'} · ${(s.job.area_ha || 0).toFixed(2).replace('.', ',')} ha`
    : 'aus · Fläche wird nicht gemalt';

  const steering = s.steering;
  const steer = el('btnSteer');
  steer.classList.remove('an', 'scharf');
  if (!steering || !steering.configured) {
    steer.setAttribute('aria-checked', 'false');
    el('steerText').textContent = 'Lenkhilfe · kein Motor freigegeben';
  } else if (steering.command.engaged) {
    steer.classList.add('an'); steer.setAttribute('aria-checked', 'true');
    el('steerText').textContent = 'aktiv · tippen schaltet ab';
  } else if (steering.armed) {
    steer.classList.add('scharf'); steer.setAttribute('aria-checked', 'true');
    el('steerText').textContent = `scharf · ${steering.command.reason}`;
  } else {
    steer.setAttribute('aria-checked', 'false');
    el('steerText').textContent = 'aus · tippen schaltet scharf';
  }

  // A/B ist ein Knopf mit zwei Schritten: erst A, dann B.
  el('btnAB').querySelector('span').textContent = s.recording && s.recording.pending_a ? 'B setzen' : 'A setzen';
  el('btnAB').classList.toggle('recording', !!(s.recording && s.recording.pending_a));
  el('btnBoundary').classList.toggle('recording', !!(s.recording && s.recording.mode === 'boundary'));
  el('btnCurve').classList.toggle('recording', !!(s.recording && s.recording.mode === 'curve'));

  // Die Wende: klein, weil sie Nebensache ist. Der Text sagt, was der nächste Druck tut.
  const turnBtn = el('btnTurn');
  turnBtn.querySelector('span:last-child').textContent = turn.aktiv ? 'Ω Stopp' : turn.geplant ? 'Ω los' : 'Wende';
  turnBtn.classList.toggle('aktiv', !!turn.aktiv);
  turnBtn.classList.toggle('geplant', !!turn.geplant && !turn.aktiv);
  el('turnFill').style.width = turn.aktiv && turn.punkte && turn.index != null
    ? `${Math.min(100, 100 * turn.index / Math.max(1, turn.punkte.length - 1)).toFixed(0)}%` : '0';
}

const LEDS = 21;
function drawLightbar(guidance) {
  const bar = el('lightbar');
  if (bar.children.length !== LEDS) {
    bar.innerHTML = '';
    for (let i = 0; i < LEDS; i++) {
      const led = document.createElement('div');
      led.className = 'led' + (i === (LEDS - 1) / 2 ? ' centre' : '');
      // Ein leichter Bogen: die Mitte sitzt etwas höher als die Ränder.
      led.style.setProperty('--h', (0.35 * (1 - Math.abs(i - (LEDS - 1) / 2) / ((LEDS - 1) / 2))).toFixed(2));
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

/* Die Leinwand ist größer als der Bildschirm (140 % der Breite, quadratisch),
 * damit sie gekippt den ganzen Blick füllt. Die Pixelzahl ist gedeckelt: ein
 * Tablet soll zehn Bilder je Sekunde schaffen, nicht Rekorde aufstellen. */
const MAX_PIXEL = 1600;
const ANKER_Y = 0.78;              // wo der Traktor auf der Leinwand sitzt

function resize() {
  const ratio = window.devicePixelRatio || 1;
  const px = Math.min(MAX_PIXEL, Math.round(canvas.clientWidth * ratio));
  canvas.width = canvas.height = px;
  const k = px / canvas.clientWidth;
  ctx.setTransform(k, 0, 0, k, 0, 0);
  // Grundmaßstab: 1 m ist knapp ein Prozent der Leinwandbreite - genug, um
  // die Nachbarspuren zu sehen, nah genug für Zentimeter auf der eigenen.
  if (!state.view.scale) state.view.scale = canvas.clientWidth / 155;
}
window.addEventListener('resize', resize);

function viewCentre() {
  const s = state.live;
  return (s && s.tool_position) ? s.tool_position : [0, 0];
}

function viewRotation() {
  const s = state.live;
  if (state.view.mode === 'nord' || !s || s.heading == null) return 0;
  return -s.heading * Math.PI / 180;
}

function ankerY() { return canvas.clientHeight * (state.view.mode === 'nord' ? 0.5 : ANKER_Y); }

function applyWorldTransform() {
  const width = canvas.clientWidth;
  const scale = state.view.scale;
  const [cx, cy] = viewCentre();
  ctx.translate(width / 2, ankerY());
  ctx.rotate(viewRotation());
  ctx.scale(scale, -scale);
  ctx.translate(-cx, -cy);
}

function screenToWorld(x, y) {
  const width = canvas.clientWidth;
  const scale = state.view.scale, rotation = viewRotation();
  const [cx, cy] = viewCentre();
  let dx = x - width / 2, dy = y - ankerY();
  const cos = Math.cos(-rotation), sin = Math.sin(-rotation);
  const rx = dx * cos - dy * sin, ry = dx * sin + dy * cos;
  return [cx + rx / scale, cy - ry / scale];
}

function render() {
  const width = canvas.clientWidth, height = canvas.clientHeight;
  ctx.save();
  ctx.fillStyle = F.boden;
  ctx.fillRect(0, 0, width, height);

  ctx.save();
  applyWorldTransform();
  drawGrid();
  drawCoverage();
  drawBoundary();
  drawHeadland();
  drawPasses();
  drawTrail();
  drawRecording();
  drawTurn();
  drawVehicle();
  ctx.restore();

  ctx.restore();
  requestAnimationFrame(render);
}

/* Ein Raster als Boden: ohne Bezug wirkt eine leere Fläche wie Stillstand. */
function drawGrid() {
  const [cx, cy] = viewCentre();
  const step = 10, reach = 400;
  const x0 = Math.floor((cx - reach) / step) * step, x1 = cx + reach;
  const y0 = Math.floor((cy - reach) / step) * step, y1 = cy + reach;
  ctx.beginPath();
  for (let x = x0; x <= x1; x += step) { ctx.moveTo(x, y0); ctx.lineTo(x, y1); }
  for (let y = y0; y <= y1; y += step) { ctx.moveTo(x0, y); ctx.lineTo(x1, y); }
  ctx.strokeStyle = F.raster;
  ctx.lineWidth = 1 / state.view.scale;
  ctx.stroke();
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

function polyline(points, close) {
  ctx.beginPath();
  points.forEach(([x, y], index) => index ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
  if (close) ctx.closePath();
}

function drawBoundary() {
  const field = state.live && state.live.field;
  if (!field || !field.boundary || field.boundary.length < 3) return;
  polyline(field.boundary, true);
  ctx.strokeStyle = F.grenze;
  ctx.lineWidth = 2.5 / state.view.scale;
  ctx.stroke();
}

/* Vorgewende: die Linie, an der die Arbeit endet und die Wende beginnt.
 * Zeichenhilfe – gerechnet wird gegen die echte Feldgrenze, nicht gegen sie. */
function drawHeadland() {
  const headland = state.live && state.live.headland;
  if (!headland || !headland.ring || headland.ring.length < 3) return;
  polyline(headland.ring, true);
  ctx.save();
  ctx.setLineDash([8 / state.view.scale, 6 / state.view.scale]);
  ctx.strokeStyle = F.vg;
  ctx.globalAlpha = headland.alarm ? 1 : 0.55;
  ctx.lineWidth = (headland.alarm ? 2.5 : 1.8) / state.view.scale;
  ctx.stroke();
  ctx.restore();
}

/* Die geplante oder laufende Wende. Geplant gestrichelt, gefahren durchgezogen –
 * damit auf einen Blick klar ist, ob die Maschine der Route schon folgt. */
function drawTurn() {
  const turn = state.live && state.live.turn;
  if (!turn || !turn.punkte || turn.punkte.length < 2) return;
  ctx.save();
  polyline(turn.punkte);
  if (turn.aktiv) {
    ctx.strokeStyle = F.wende;
    ctx.lineWidth = 3.2 / state.view.scale;
  } else {
    ctx.setLineDash([5 / state.view.scale, 5 / state.view.scale]);
    ctx.strokeStyle = turn.im_feld ? F.wende : F.vg;
    ctx.globalAlpha = 0.7;
    ctx.lineWidth = 2.2 / state.view.scale;
  }
  ctx.stroke();
  ctx.restore();
}

function drawPasses() {
  const s = state.live;
  if (!s || !s.line) return;
  const line = s.line;
  const current = s.guidance.active ? s.guidance.pass_number : 0;
  const ring = line.mode === 'contour';
  for (let offset = current - 6; offset <= current + 6; offset++) {
    // Ring -1 läge außerhalb der Feldgrenze – dort wird nicht gearbeitet.
    if (ring && offset < 0) continue;
    const points = shiftLine(line, offset * line.spacing_m + line.nudge_m);
    if (!points.length) continue;
    polyline(points, ring);
    const active = offset === current;
    if (active) {
      // Die aktive Spur leuchtet: ein breiter, blasser Schein und die Linie darin.
      ctx.strokeStyle = F.aktiv;
      ctx.globalAlpha = 0.22; ctx.lineWidth = 9 / state.view.scale; ctx.stroke();
      ctx.globalAlpha = 1; ctx.lineWidth = 2.8 / state.view.scale; ctx.stroke();
    } else {
      ctx.strokeStyle = F.pass;
      ctx.lineWidth = 1.4 / state.view.scale;
      ctx.stroke();
    }
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
  if (line.mode === 'contour') {
    // Geschlossener Ring, versetzt nach innen. Der Umlaufsinn der Grenze wird
    // herausgerechnet – sonst läge Ring 1 mal drinnen und mal draußen, je
    // nachdem, wie herum jemand das Feld abgefahren hat.
    const n = points.length;
    let area = 0;
    for (let i = 0; i < n; i++) {
      const [x1, y1] = points[i], [x2, y2] = points[(i + 1) % n];
      area += x1 * y2 - x2 * y1;
    }
    const inward = area > 0 ? -1 : 1;
    return points.map((p, index) => {
      const a = points[(index - 1 + n) % n], b = points[(index + 1) % n];
      const heading = Math.atan2(b[0] - a[0], b[1] - a[1]);
      return [p[0] + Math.cos(heading) * shift * inward,
              p[1] - Math.sin(heading) * shift * inward];
    });
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
  polyline(state.trail);
  ctx.strokeStyle = F.spur;
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
    ctx.fillStyle = F.vg;
    ctx.fill();
  }
}

/* Traktor und Gerät im Weltmaßstab, damit die Perspektive mitspielt: was drei
 * Meter breit ist, ist auch auf dem Bildschirm drei Meter breit - und wird
 * nach vorn hin kleiner wie alles andere. */
function drawVehicle() {
  const s = state.live;
  if (!s || !s.tool_position || s.heading == null) return;
  const profile = s.profile || { width_m: 3 };
  const sections = s.sections || [];
  const implement = s.implement || {};
  const h = s.heading * Math.PI / 180;
  // Nie kleiner als eine Fingerkuppe, egal wie weit herausgezoomt wird.
  const mind = 26 / state.view.scale;
  const laenge = Math.max(3.2, mind);

  ctx.save();
  ctx.translate(s.tool_position[0], s.tool_position[1]);
  ctx.rotate(-h);          // Welt ist y-oben: Kurs im Uhrzeigersinn = negative Drehung

  // Gerät: beim gezogenen Gerät in seiner eigenen Ausrichtung am Zugpunkt.
  ctx.save();
  if (implement.trailed && implement.heading != null) {
    const lag = (implement.heading - s.heading) * Math.PI / 180;
    const hitch = implement.hitch_length_m || 4;
    ctx.rotate(-lag);
    ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(0, -hitch);
    ctx.strokeStyle = F.traktor; ctx.globalAlpha = 0.6; ctx.lineWidth = 2 / state.view.scale; ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.translate(0, -hitch);
  } else {
    ctx.translate(0, -laenge * 0.55);
  }
  const bar = profile.width_m || 3, dicke = Math.max(0.6, mind * 0.35);
  if (sections.length) {
    sections.forEach((section) => {
      ctx.fillStyle = section.forced_off ? F.vg : section.enabled ? F.aktiv : F.pass;
      ctx.globalAlpha = section.enabled && !section.forced_off ? 0.9 : 0.6;
      ctx.fillRect(section.left_m, -dicke / 2, section.right_m - section.left_m, dicke);
    });
    ctx.globalAlpha = 1;
  } else {
    ctx.fillStyle = F.aktiv; ctx.globalAlpha = 0.9;
    ctx.fillRect(-bar / 2, -dicke / 2, bar, dicke);
    ctx.globalAlpha = 1;
  }
  ctx.restore();

  // Traktor: eine Pfeilspitze in Fahrtrichtung
  const w = laenge * 0.42;
  ctx.beginPath();
  ctx.moveTo(0, laenge * 0.55);
  ctx.lineTo(w, -laenge * 0.4);
  ctx.lineTo(0, -laenge * 0.22);
  ctx.lineTo(-w, -laenge * 0.4);
  ctx.closePath();
  ctx.fillStyle = F.traktor;
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

/* A/B in einem Knopf: erst A, bis zum Ende fahren, dann B. */
el('btnAB').onclick = async () => {
  const offen = state.live && state.live.recording && state.live.recording.pending_a;
  if (!offen) {
    if (await api('POST', '/api/guidance/a')) toast('Punkt A gesetzt – jetzt bis zum Ende fahren');
    return;
  }
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
/* Wende: planen, ansehen, starten. Bewusst zwei Druck – die Route liegt erst
 * sichtbar auf der Karte, bevor die Maschine ihr folgt. */
el('btnTurn').onclick = async () => {
  const turn = (state.live && state.live.turn) || {};
  if (turn.aktiv) {
    await api('POST', '/api/turn/stop');
    toast('Wende abgebrochen');
    return;
  }
  if (turn.geplant) {
    const result = await api('POST', '/api/turn/start');
    if (result) toast('Wende läuft – Hand am Lenkrad');
    return;
  }
  const plan = await api('POST', '/api/turn/plan', {});
  if (!plan) return;
  if (!plan.data.grenze_vorhanden) {
    toast('Ohne Feldgrenze wird die Route nicht geprüft', true);
  } else if (!plan.data.im_feld) {
    toast('Route liegt nicht vollständig im Feld', true);
  } else {
    toast(`${plan.data.muster === 'u' ? 'U' : 'Ω'}-Wende geplant, ` +
          `${plan.data.laenge_m.toFixed(0)} m – noch einmal drücken zum Starten`);
  }
};

el('btnContour').onclick = async () => {
  const result = await api('POST', '/api/guidance/contour');
  if (result) { toast('Kontur aktiv – Ring 0 ist die Feldgrenze'); refreshLists(); }
};

// Zehn Zentimeter je Druck: das ist der Schritt, den man im Feld braucht -
// die Nachbarspur liegt einen Meter daneben, nicht einen Zentimeter.
el('btnNudgeLeft').onclick = () => api('POST', '/api/guidance/nudge', { metres: -0.10 });
el('btnNudgeRight').onclick = () => api('POST', '/api/guidance/nudge', { metres: 0.10 });

/* Markieren an/aus - das ist der Auftrag. Die Bezeichnung (Grubbern, Säen...)
 * steht unter Menü → Aufträge und wird für den nächsten Start gemerkt. */
function arbeitBezeichnung() {
  try { return localStorage.getItem('agripilot.arbeit') || 'Arbeit'; } catch (e) { return 'Arbeit'; }
}
el('btnJob').onclick = async () => {
  if (state.live && state.live.job) {
    const result = await api('POST', '/api/job/stop');
    if (result) toast(`Markieren aus – ${result.data.area_ha.toFixed(2)} ha`);
  } else if (await api('POST', '/api/job/start', { operation: arbeitBezeichnung() })) {
    toast(`Markieren an – ${arbeitBezeichnung()}`);
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

/* ------------------------------------------------------------- Ansicht */

function merken(key, wert) { try { localStorage.setItem(key, wert); } catch (e) { /* privater Modus */ } }
function gemerkt(key) { try { return localStorage.getItem(key); } catch (e) { return null; } }

el('zoomIn').onclick = () => { state.view.scale = Math.min(state.view.scale * 1.4, canvas.clientWidth / 20); };
el('zoomOut').onclick = () => { state.view.scale = Math.max(state.view.scale / 1.4, canvas.clientWidth / 1200); };

const ANSICHTEN = ['perspektive', 'flach', 'nord'];
function ansichtSetzen(mode) {
  state.view.mode = ANSICHTEN.includes(mode) ? mode : 'perspektive';
  stage.classList.toggle('flach', state.view.mode === 'flach');
  stage.classList.toggle('nord', state.view.mode === 'nord');
  el('viewMode').title = { perspektive: 'Perspektive', flach: 'Flach, Fahrtrichtung oben', nord: 'Norden oben' }[state.view.mode];
  merken('agripilot.ansicht', state.view.mode);
}
el('viewMode').onclick = () => ansichtSetzen(ANSICHTEN[(ANSICHTEN.indexOf(state.view.mode) + 1) % ANSICHTEN.length]);

function tagSetzen(tag) {
  document.body.classList.toggle('tag', tag);
  el('tagNacht').classList.toggle('an', tag);
  document.querySelector('meta[name=theme-color]').setAttribute('content', tag ? '#dfe6dc' : '#05090c');
  merken('agripilot.tag', tag ? '1' : '0');
  farbenLesen();
  repaintCells();
}
el('tagNacht').onclick = () => tagSetzen(!document.body.classList.contains('tag'));

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
    state.view.scale = Math.max(canvas.clientWidth / 1200, Math.min(canvas.clientWidth / 20, pinchStart.scale * factor));
  }
}, { passive: false });
canvas.addEventListener('touchend', () => { pinchStart = null; });
canvas.addEventListener('wheel', (event) => {
  event.preventDefault();
  state.view.scale = Math.max(canvas.clientWidth / 1200, Math.min(canvas.clientWidth / 20,
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

el('arbeitName').value = arbeitBezeichnung();
el('arbeitName').onchange = (event) => {
  const name = event.target.value.trim() || 'Arbeit';
  merken('agripilot.arbeit', name);
  toast(`Nächste Arbeit: ${name}`);
};

el('btnSaveProfile').onclick = async () => {
  const payload = {};
  document.querySelectorAll('#profileForm input').forEach((input) => {
    payload[input.name] = input.type === 'checkbox' ? input.checked
      : input.type === 'number' ? parseFloat(input.value) : input.value;
  });
  if (await api('POST', '/api/profile', payload)) toast('Maschine übernommen');
};

/* ------------------------------------------------------------ Vorgewende */

function fillHeadland() {
  const headland = state.live && state.live.headland;
  if (!headland) return;
  document.querySelectorAll('#headlandForm input, #headlandForm select, ' +
                            '#headlandSwitches input').forEach((input) => {
    const wert = headland[input.name];
    if (wert === undefined) return;
    if (input.type === 'checkbox') input.checked = !!wert;
    else input.value = wert;
  });
  const profil = state.live.profile || {};
  el('headlandInfo').textContent =
    `Tiefe ${(headland.tiefe_m || 0).toFixed(1)} m` +
    ` · Arbeitsbreite ${(profil.width_m || 0).toFixed(2)} m` +
    ` · Spurabstand ${((profil.width_m || 0) - (profil.overlap_m || 0)).toFixed(2)} m`;
}

el('btnSaveHeadland').onclick = async () => {
  const payload = {};
  document.querySelectorAll('#headlandForm input, #headlandForm select, ' +
                            '#headlandSwitches input').forEach((input) => {
    payload[input.name] = input.type === 'checkbox' ? input.checked
      : input.type === 'number' ? parseFloat(input.value) : input.value;
  });
  if (await api('POST', '/api/headland', payload)) toast('Vorgewende übernommen');
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
  if (active === 'headland') fillHeadland();
  if (active === 'system') { renderSystem(); renderRohdaten(); }
  if (active === 'setup') renderSetup(await (await fetch('/api/checklist')).json());
  if (active === 'settings' && !settings.geladen) loadSettings();
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
    if (profile[input.name] === undefined) return;
    if (input.type === 'checkbox') input.checked = !!profile[input.name];
    else input.value = profile[input.name];
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

/* Einstellungen: die Oberfläche baut sich aus dem Schema des Servers auf, damit
   sie nicht davon abdriften kann. Gespeichert wird nur, was wirklich geändert
   wurde - so überschreibt ein Tablet nicht die Eingaben eines anderen. */
const settings = { geladen: false, werte: {}, gruppen: [] };

async function loadSettings() {
  const daten = await (await fetch('/api/settings')).json();
  settings.gruppen = daten.gruppen;
  settings.werte = daten.werte;
  settings.geladen = true;
  const host = el('settingsGroups');
  host.innerHTML = '';
  daten.gruppen.forEach((gruppe, index) => {
    const box = document.createElement('details');
    box.className = 'setting-group';
    box.open = index === 0;
    const kopf = document.createElement('summary');
    kopf.textContent = gruppe.titel;
    box.appendChild(kopf);
    if (gruppe.hinweis) {
      const note = document.createElement('div');
      note.className = 'group-note';
      note.textContent = gruppe.hinweis;
      box.appendChild(note);
    }
    gruppe.felder.forEach((feld) => box.appendChild(settingField(feld)));
    host.appendChild(box);
  });
  el('settingsInfo').textContent = daten.datei
    ? `Datei: ${daten.datei}` : 'Noch keine Konfigurationsdatei – beim Übernehmen wird eine angelegt.';
}

function settingField(feld) {
  const box = document.createElement('div');
  box.className = 'setting-field';
  const label = document.createElement('label');
  label.textContent = feld.label;
  if (feld.einheit) {
    const unit = document.createElement('span');
    unit.className = 'unit';
    unit.textContent = feld.einheit;
    label.appendChild(unit);
  }
  box.appendChild(label);

  let input;
  if (feld.typ === 'auswahl') {
    input = document.createElement('select');
    feld.auswahl.forEach((option) => {
      const eintrag = document.createElement('option');
      eintrag.value = option.wert;
      eintrag.textContent = option.label;
      input.appendChild(eintrag);
    });
    input.value = String(settings.werte[feld.schluessel]);
  } else if (feld.typ === 'schalter') {
    input = document.createElement('input');
    input.type = 'checkbox';
    input.checked = !!settings.werte[feld.schluessel];
    input.style.width = '28px';
    input.style.height = '28px';
  } else if (feld.typ === 'zahl' || feld.typ === 'ganzzahl') {
    input = document.createElement('input');
    input.type = 'number';
    input.step = feld.typ === 'ganzzahl' ? '1' : 'any';
    if (feld.minimum !== null) input.min = feld.minimum;
    if (feld.maximum !== null) input.max = feld.maximum;
    input.value = settings.werte[feld.schluessel];
  } else {
    input = document.createElement('input');
    input.type = feld.typ === 'passwort' ? 'password' : 'text';
    input.value = settings.werte[feld.schluessel] ?? '';
  }
  input.dataset.key = feld.schluessel;
  label.setAttribute('for', `set-${feld.schluessel}`);
  input.id = `set-${feld.schluessel}`;
  const markieren = () => box.classList.toggle('changed', feldGeaendert(feld, input));
  input.onchange = markieren;
  input.oninput = markieren;
  box.appendChild(input);

  if (feld.warnung) {
    const warn = document.createElement('div');
    warn.className = 'warn';
    warn.textContent = feld.warnung;
    box.appendChild(warn);
  }
  if (feld.hilfe) {
    const help = document.createElement('div');
    help.className = 'help';
    help.textContent = feld.hilfe;
    box.appendChild(help);
  }
  if (!feld.sofort) {
    // Ehrlich dranschreiben, was erst nach einem Neustart greift - sonst sucht
    // man den Fehler beim Empfänger statt beim Programm.
    const hint = document.createElement('div');
    hint.className = 'restart';
    hint.textContent = 'wirkt nach einem Neustart';
    box.appendChild(hint);
  }
  return box;
}

function feldWert(feld, input) {
  if (feld.typ === 'schalter') return input.checked;
  if (feld.typ === 'zahl' || feld.typ === 'ganzzahl') return parseFloat(input.value);
  return input.value;
}

function feldGeaendert(feld, input) {
  const jetzt = feldWert(feld, input);
  const vorher = settings.werte[feld.schluessel];
  if (feld.typ === 'auswahl') return String(jetzt) !== String(vorher);
  return jetzt !== vorher;
}

el('btnSaveSettings').onclick = async () => {
  const aenderungen = {};
  settings.gruppen.forEach((gruppe) => gruppe.felder.forEach((feld) => {
    const input = document.getElementById(`set-${feld.schluessel}`);
    if (input && feldGeaendert(feld, input)) aenderungen[feld.schluessel] = feldWert(feld, input);
  }));
  if (!Object.keys(aenderungen).length) { toast('Nichts geändert'); return; }
  const antwort = await api('POST', '/api/settings', { aenderungen });
  if (!antwort) return;
  settings.werte = antwort.data.werte;
  document.querySelectorAll('.setting-field.changed').forEach((box) =>
    box.classList.remove('changed'));
  const neustart = antwort.data.neustart_noetig || [];
  toast(neustart.length
    ? `Gespeichert – Neustart nötig für: ${neustart.join(', ')}`
    : 'Gespeichert und sofort wirksam');
  el('settingsInfo').textContent = neustart.length
    ? `Gespeichert in ${antwort.data.datei} · Neustart nötig für: ${neustart.join(', ')}`
    : `Gespeichert in ${antwort.data.datei} · sofort wirksam`;
};

function renderSetup(stand) {
  const host = el('setupList');
  el('setupBar').style.width =
    `${stand.gesamt ? (100 * stand.fertig) / stand.gesamt : 0}%`;
  el('setupCount').textContent = stand.abgeschlossen
    ? 'Alle Schritte erledigt'
    : `${stand.fertig} von ${stand.gesamt} · als Nächstes: ${stand.naechster}`;

  host.innerHTML = '';
  stand.schritte.forEach((schritt) => {
    const row = document.createElement('div');
    row.className = 'item step' + (schritt.fertig ? ' done' : '')
                  + (schritt.dran ? ' now' : '');

    const head = document.createElement('div');
    head.className = 'head';
    head.innerHTML = '<div class="nr"></div><div class="main">'
                   + '<div class="title"></div></div>';
    head.querySelector('.nr').textContent = schritt.fertig ? '✓' : schritt.nummer;
    head.querySelector('.title').textContent = schritt.titel;
    row.appendChild(head);

    const why = document.createElement('div');
    why.className = 'why';
    why.textContent = schritt.warum;
    row.appendChild(why);

    if (schritt.pruefung) {
      const check = document.createElement('div');
      // Grün nur, wo wirklich gemessen wurde. Wo das Programm nichts wissen
      // kann, bleibt es grau - eine grüne Zeile, die nichts geprüft hat,
      // wäre die gefährlichste Anzeige auf diesem Bildschirm.
      check.className = 'check ' + (schritt.erfuellt === true ? 'good'
                                  : schritt.erfuellt === false ? 'bad' : 'unknown');
      check.textContent = (schritt.erfuellt === true ? '✓ ' :
                           schritt.erfuellt === false ? '✗ ' : '· ') + schritt.pruefung;
      row.appendChild(check);
    }

    const foot = document.createElement('div');
    foot.className = 'foot';
    const text = document.createElement('div');
    text.className = 'quittung';
    if (schritt.fertig && schritt.bestaetigt_am) {
      const wann = new Date(schritt.bestaetigt_am * 1000);
      text.textContent = `abgehakt am ${wann.toLocaleDateString('de-DE')} `
        + `${wann.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' })}`
        + (schritt.bestaetigt_von ? ` · ${schritt.bestaetigt_von}` : '');
    } else {
      text.textContent = schritt.quittung || '';
    }
    foot.appendChild(text);

    // Schritte ohne Bestätigungstext hängen allein an der Messung. Dort einen
    // Knopf anzubieten wäre eine Einladung, an der Anlage vorbei abzuhaken.
    if (!schritt.quittung) {
      if (!schritt.fertig) text.textContent = 'erledigt sich, sobald die Prüfung trägt';
      row.appendChild(foot);
      host.appendChild(row);
      return;
    }

    const button = document.createElement('button');
    button.textContent = schritt.fertig ? 'Zurücknehmen' : 'Abhaken';
    if (!schritt.fertig) button.className = 'primary';
    button.onclick = async () => {
      const neu = !schritt.fertig;
      if (neu && !confirm(`${schritt.quittung}?`)) return;
      const antwort = await api('POST', `/api/checklist/${schritt.id}`, { erledigt: neu });
      if (antwort) {
        toast(neu ? 'Schritt abgehakt' : 'Bestätigung zurückgenommen');
        renderSetup(antwort.data);
      }
    };
    foot.appendChild(button);
    row.appendChild(foot);
    host.appendChild(row);
  });
}

el('btnSetupReset').onclick = async () => {
  if (!confirm('Alle Bestätigungen löschen? Nur nach einem Umbau sinnvoll.')) return;
  const antwort = await api('DELETE', '/api/checklist');
  if (antwort) { toast('Inbetriebnahme zurückgesetzt'); renderSetup(antwort.data); }
};

/* ----------------------------------------------------------- Rohdaten */

function mb(bytes) {
  return bytes < 1024 * 1024 ? `${Math.round(bytes / 1024)} kB`
                             : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function dauer(sekunden) {
  const s = Math.max(0, Math.round(sekunden));
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')} min`;
}

/* Der Zustand der laufenden Aufzeichnung kommt mit dem Livebild – so zählt die
 * Anzeige mit, statt beim Öffnen des Menüs einmal stehen zu bleiben. */
function rohdatenStatus() {
  const stand = state.live && state.live.system && state.live.system.rohdaten;
  if (!stand) return;
  const button = el('btnRohdaten');
  button.textContent = stand.laeuft ? 'Aufzeichnung beenden' : 'Aufzeichnung starten';
  button.classList.toggle('danger', !!stand.laeuft);
  if (stand.fehler) {
    el('rohdatenInfo').textContent = `Abgebrochen: ${stand.fehler}`;
  } else if (stand.laeuft) {
    el('rohdatenInfo').textContent =
      `läuft · ${dauer(stand.dauer_s)} · ${stand.zeilen} Zeilen · ${mb(stand.groesse_b)}`;
  } else {
    el('rohdatenInfo').textContent = '';
  }
}

async function renderRohdaten() {
  const antwort = await fetch('/api/rohdaten');
  if (!antwort.ok) return;
  const daten = await antwort.json();
  rohdatenStatus();
  const host = el('rohdatenList');
  host.innerHTML = '';
  daten.dateien.forEach((datei) => {
    const abgespielt = daten.abspielen.aktiv && daten.abspielen.datei === datei.datei;
    host.appendChild(item({
      active: abgespielt,
      title: datei.datei,
      sub: `${dauer(datei.dauer_s)} · ${mb(datei.groesse_b)}` +
           (abgespielt ? ' · wird gerade abgespielt' : ''),
      actions: [
        ['Herunterladen', () => { window.location = `/api/rohdaten/${datei.datei}`; }],
        ['Abspielen', async () => {
          if (!confirm(`"${datei.datei}" abspielen?\n\nDer Empfänger wird dafür ` +
                       `abgeschaltet – das System läuft danach auf der ` +
                       `Aufzeichnung, nicht auf der Wirklichkeit. Wirkt nach ` +
                       `einem Neustart.`)) return;
          const ok_ = await api('POST', '/api/settings', { aenderungen: {
            'gnss.source': 'replay', 'gnss.replay_file': datei.datei,
          }});
          if (ok_) toast('Abspielen eingestellt – wirkt nach einem Neustart');
        }],
        ['Löschen', async () => {
          if (!confirm(`"${datei.datei}" löschen?`)) return;
          if (await api('DELETE', `/api/rohdaten/${datei.datei}`)) renderRohdaten();
        }],
      ],
    }));
  });
  if (!daten.dateien.length) {
    host.innerHTML = '<p class="note">Noch nichts aufgezeichnet.</p>';
  }
}

el('btnRohdaten').onclick = async () => {
  const stand = state.live && state.live.system && state.live.system.rohdaten;
  const laeuft = stand && stand.laeuft;
  const antwort = await api('POST', laeuft ? '/api/rohdaten/stop' : '/api/rohdaten/start');
  if (!antwort) return;
  toast(laeuft ? `Aufzeichnung beendet: ${antwort.data.datei}`
               : 'Aufzeichnung läuft');
  renderRohdaten();
};

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

/* --------------------------------------------------- Bildschirm wachhalten */

/* Eine Kabinenanzeige, die nach zwei Minuten dunkel wird, ist keine Anzeige.
   Der Browser gibt die Sperre nur in sicherem Kontext her (HTTPS oder
   localhost) - über http://192.168.x.x passiert hier nichts, und das ist der
   Grund, warum auf dem Pi Zertifikate erzeugt werden (scripts/make_cert.sh).
   Android nimmt die Sperre beim Wegschalten zurück; deshalb wird sie beim
   Zurückkommen neu geholt. */
let wachSperre = null;

async function bildschirmWachhalten() {
  if (!('wakeLock' in navigator) || !window.isSecureContext) return;
  try {
    wachSperre = await navigator.wakeLock.request('screen');
    wachSperre.addEventListener('release', () => { wachSperre = null; });
  } catch (error) {
    wachSperre = null;   // z.B. wenn der Akku fast leer ist - kein Grund zu lärmen
  }
}

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && wachSperre === null) bildschirmWachhalten();
});

/* ------------------------------------------------------------------ Start */

farbenLesen();
tagSetzen(gemerkt('agripilot.tag') === '1');
ansichtSetzen(gemerkt('agripilot.ansicht') || 'perspektive');
resize();
connect();
bildschirmWachhalten();
requestAnimationFrame(render);
setInterval(() => { if (!el('sheet').hidden) refreshLists(); }, 5000);
