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
let kartenfarben = null;
function farbenLesen() {
  const bisher = F;
  const cs = getComputedStyle(document.body);
  F = Object.fromEntries(['boden', 'raster', 'grenze', 'vg', 'pass', 'aktiv', 'flaeche', 'doppelt', 'wende', 'spur',
    'traktor', 'traktor-rand', 'geraet', 'fest', 'fahrgasse', 'konflikt', 'lack', 'lack-hell', 'lack-dunkel', 'akzent', 'reifen', 'kabine']
    .map((k) => [k, cs.getPropertyValue('--f-' + k).trim()]));
  if (bisher.flaeche !== F.flaeche || bisher.doppelt !== F.doppelt)
    flaeche.farbenSetzen(F.flaeche, F.doppelt);
}

/* ---------------------------------------------------------------- Bedeckung */

/* Die bearbeitete Fläche zeichnet flaeche.js: die eigene Fahrt als glatter
 * Streifen hinter dem Gerät, Fremdes und Früheres aus den Zellen des Servers. */
const flaeche = new window.FlaecheModul.Flaeche((px) => {
  const leinwand = document.createElement('canvas');
  leinwand.width = leinwand.height = px;
  return leinwand;
});

function feldKennung(daten) { return daten && daten.field ? daten.field.id : null; }

/* Wo das Gerät gerade arbeitet - die Lage, an der der Streifen weiterwächst. */
function geraeteLage(s, versatz) {
  if (!s || s.heading == null) return null;
  const implement = s.implement || {};
  const pos = implement.position || s.tool_position;
  if (!pos) return null;
  const kurs = implement.trailed && implement.heading != null ? implement.heading : s.heading;
  const [dx, dy, dh] = versatz || [0, 0, 0];
  return {
    pos: [pos[0] + dx, pos[1] + dy],
    kurs: kurs + dh,
    stuecke: window.FlaecheModul.querStuecke(s.sections, (s.profile || {}).width_m || 3),
    aktiv: Boolean(s.job),
    auftrag: s.job ? s.job.id : null,
  };
}

/* ------------------------------------------------------------- Verbindung */

let socket = null;

function connect() {
  const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
  socket = new WebSocket(`${protocol}://${location.host}/ws`);

  socket.onopen = () => { state.connected = true; };
  socket.onclose = () => {
    // Nur beim Abriss selbst, nicht bei jedem Wiederholungsversuch.
    if (state.connected && window.Warnungen && state.live) window.Warnungen.pruefen(state.live, state.live, false);
    state.connected = false;
    setTimeout(connect, 1500);          // im Feld ist ein Abriss normal
  };
  socket.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.type === 'init') {
      state.cellSize = data.coverage ? data.coverage.cell_size : 0.5;
      flaeche.leeren();
      flaeche.zellenMalen(data.cells || [], state.cellSize);
      state.trail = [];
    } else {
      // Feldwechsel vor dem Malen: was mit dem neuen Feld schon mitkommt
      // (Arbeit eines anderen Traktors), gehört bereits auf das neue Feld.
      if (state.live && feldKennung(state.live) !== feldKennung(data)) { flaeche.leeren(); state.trail = []; }
      if (data.fremde_zellen && data.fremde_zellen.length) flaeche.zellenMalen(data.fremde_zellen, state.cellSize);
    }
    onState(data);
  };
}

function onState(data) {
  const previous = state.live;
  lenkAufzeichnung.aufnehmen(data);
  glaettungNachfuehren(previous, data);
  state.live = data;
  // Erst den Streifen bis zur neuen Lage malen, dann die Zellen dieser Fahrt
  // merken - sonst hielte er seine eigene Spur für eine Überlappung.
  const jetzt = performance.now();
  const lage = geraeteLage(data);
  if (lage) flaeche.fahrt(lage, jetzt);
  else flaeche.letzte = null;
  if (data.new_cells && data.new_cells.length) flaeche.zellenMerken(data.new_cells, jetzt);
  zeichnenAnfordern();
  // Hörbar machen, was der Fahrer nicht selbst ausgelöst hat (alerts.js).
  if (previous && window.Warnungen) {
    window.Warnungen.pruefen(previous, data, state.connected).forEach((e) => toast(e.text, e.stufe === 'alarm'));
  }
  if (data.coverage) state.cellSize = data.coverage.cell_size;

  if (data.tool_position) {
    const last = state.trail[state.trail.length - 1];
    if (!last || Math.hypot(last[0] - data.tool_position[0],
                            last[1] - data.tool_position[1]) > 1.0) {
      state.trail.push(data.tool_position);
      if (state.trail.length > 900) state.trail.shift();
    }
  }
  // Feldwechsel: alte Bedeckung gehört nicht auf das neue Feld. Aber erst ab
  // dem zweiten Bild - das erste ist kein Wechsel, sondern der Anfang, und
  // die eben im 'init' gemalte Fläche wäre sonst sofort wieder weg.
  const before = previous && previous.field ? previous.field.id : null;
  const now = data.field ? data.field.id : null;
  if (previous && before !== now) refreshLists();
  // Neue Grenze desselben Feldes (Editor): der gerechnete Saatplan gehörte zur alten Form -
  // neu laden, statt ihn auf der Karte weiter zu zeigen.
  if (previous && before === now && now && JSON.stringify(previous.field.boundary) !== JSON.stringify(data.field.boundary)) {
    saat.feld = null;
    refreshLists();
  }
  // Erstes Bild bei offenem Menü (Aufruf über #menu=…): die Listen wurden vor
  // dem Zustand gefüllt und wussten noch nichts vom geladenen Feld.
  if (!previous && !el('sheet').hidden) refreshLists();
  reglerLiveZeigen(data);

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
  // Feld erkannt, aber nicht geladen: anbieten, nie von selbst wechseln.
  const vorschlag = el('feldVorschlag');
  if (s.feld_vorschlag) {
    vorschlag.textContent = `Feld „${s.feld_vorschlag.name}“ laden`;
    vorschlag.dataset.id = s.feld_vorschlag.id;
    vorschlag.hidden = false;
  } else {
    vorschlag.hidden = true;
  }
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
    // Der Pfeil ist ein Befehl, kein Befund: er zeigt, wohin zu lenken ist.
    // cm > 0 heißt rechts der Spur - also nach links. Andersherum las es
    // jeder als „er geht in die falsche Richtung", sobald er 10 cm versetzte.
    pfeil.textContent = acm < 3 ? '●' : cm > 0 ? '◀' : '▶';
    pfeil.className = 'pfeil' + (acm < 3 ? ' ok' : '');
  }
  el('speed').textContent = fix ? fix.speed_kmh.toFixed(1).replace('.', ',') : '--';
  const kontur = s.line && s.line.mode === 'contour';
  // Saatplan: die Spur mit ihrem Namen (A 12, B 3, Runde 4) statt der Listennummer.
  const spurName = s.line && s.line.spur_name;
  el('pass').textContent = guidance.active ? (spurName || String(guidance.pass_number)) : '--';
  el('passLabel').textContent = kontur ? 'Ring' : 'Spur';
  el('markLegende').hidden = !(s.job || (s.coverage && s.coverage.area_ha > 0));
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
    el('restLabel').textContent = (headland.im_vorgewende ? 'im Vorgewende' : 'bis Vorgewende') +
      (headland.abdeckung != null ? ` · ${Math.round(headland.abdeckung * 100)} % bearbeitet` : '');
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
  else if (s.rtk_bruecke && s.rtk_bruecke.aktiv) {
    chip('fixChip', `RTK überbrückt · noch ${Math.round(s.rtk_bruecke.rest_s)} s · ${fix.fix_label}`, 'warn');
  } else {
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
      (s.line.nudge_m ? ` · Versatz ${(s.line.nudge_m * 100).toFixed(0)} cm` : '') +
      (s.line.fahrgasse_m ? ` · Fahrgassen ${s.line.fahrgasse_m.toFixed(0)} m` : '')
    : 'Keine Spur – A und B setzen', s.line ? 'good' : '');

  const hint = [];
  if (s.recording && s.recording.mode === 'boundary') {
    hint.push(`Grenze wird aufgezeichnet · ${s.recording.points} Punkte · ${s.recording.area_ha.toFixed(2)} ha`);
  }
  if (s.recording && s.recording.mode === 'curve') hint.push('Kurve wird aufgezeichnet');
  if (s.recording && s.recording.pending_a) hint.push('A gesetzt – bis zum Ende fahren, dann B');
  if (turn.aktiv) hint.push('Wende läuft – Hand am Lenkrad');
  else if (turn.geplant && el('wendeFenster').hidden) {
    // Bei offenem Wende-Fenster steht das Ergebnis dort - nicht doppelt.
    hint.push(turn.fahrbar === false ? 'Wende zu eng für die Maschine – Wendekreis größer stellen'
            : turn.im_feld ? 'Wende geplant – unter „Wende“ starten'
                           : 'Wende geplant, liegt aber nicht im Feld – Richtung oder Wendekreis ändern');
  }
  if (headland && headland.hinweis) hint.push(headland.hinweis);
  // Neue Version: der grüne Knopf „Update“ in der Leiste, statt eines Hinweises im Menü.
  const neueVersion = s.system && s.system.update_online;
  el('btnUpdateHaupt').hidden = !neueVersion;
  if (neueVersion) {
    el('btnUpdateHauptText').textContent = `Update ${neueVersion}`;
    el('btnUpdateHaupt').title = `Version ${neueVersion} einspielen`;
  }
  if (s.positionsfilter?.aktiv && !s.positionsfilter.akzeptiert) hint.push('GNSS-Ausreißer verworfen – Lenkung ausgeschaltet');
  if (s.steering && s.steering.tempo_begrenzt) {
    hint.push(`Einschlag wegen Tempo auf ${Math.round(s.steering.tempo_grenze_deg)}° begrenzt – für engere Bögen langsamer fahren`);
  }
  const teilbreiten = s.teilbreiten_hardware;
  if (teilbreiten?.ausgang === 'udp' && (!teilbreiten.verbunden || teilbreiten.angefordert !== teilbreiten.bestaetigt)) {
    hint.push('Teilbreiten warten auf Hardwarebestätigung – unbestätigte Teilbreiten werden nicht markiert');
  }
  if (s.saisonspur_neu) hint.push(`Neue Fahrgassen für Erntejahr ${s.saisonspur_neu.saison} vorhanden – nach der Arbeit Feld neu laden`);
  if (s.guidance.active && s.guidance.message) hint.push(s.guidance.message);
  // Markieren an, aber jede Sektion automatisch aus: die Maschine steht
  // außerhalb der Grenze (oder auf schon bearbeiteter Fläche). Ohne den Satz
  // sieht es aus, als würde das Markieren nicht funktionieren.
  if (s.job && s.field && s.field.boundary.length >= 3 && s.sections.length
      && s.sections.every((sec) => sec.auto && !sec.enabled && !sec.forced_off)) {
    hint.push(s.field.im_feld === false
      ? 'Außerhalb der Feldgrenze – Sektionen aus, es wird nichts markiert'
      : 'Alle Sektionen aus – außerhalb der Grenze oder Fläche schon bearbeitet');
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
  // Neben dem Symbol ist wenig Platz: dort nur der Zustand, der ganze Satz im Tooltip.
  const ha = s.job ? (s.job.area_ha || 0).toFixed(2).replace('.', ',') : '';
  el('jobText').textContent = s.job ? `an · ${ha} ha` : 'aus';
  job.title = s.job ? `Markieren an · ${s.job.operation || 'Arbeit'} · ${ha} ha` : 'Markieren aus · Fläche wird nicht gemalt';

  const steering = s.steering;
  // Nimmt die Anlage gerade Rückwärtsfahrt an, soll man das sehen - stimmt es
  // nicht, dreht derselbe Knopf es um.
  const rueck = Boolean(s.kurs_kopplung && s.kurs_kopplung.rueckwaerts);
  el('btnRichtung').classList.toggle('rueck', rueck);
  el('richtungText').textContent = rueck ? 'Rückwärts' : 'Richtung';
  el('btnSpur').classList.toggle('recording', spurAufnahmeLaeuft());
  el('fahrgasseHinweis').hidden = !s.fahrgasse_aktiv;
  const hd = s.hindernis_warnung;
  el('hindernisRuf').hidden = !hd;
  if (hd) {
    el('hindernisRuf').textContent = `⚠ ${hd.name} in ${Math.round(hd.abstand_m)} m` +
      (hd.seite === 'mitte' ? '' : ` · ${hd.seite}`);
  }
  if (s.fahrgasse_aktiv) el('fahrgasseSpur').textContent = (s.line && s.line.spur_name) || s.guidance.pass_number;

  const steer = el('btnSteer');
  steer.classList.remove('an', 'scharf');
  // Dasselbe am Lichtbalken, wo der Blick ist: Rahmen und Plakette.
  const lenkt = !!(steering && steering.configured && steering.command.engaged);
  const scharf = !lenkt && !!(steering && steering.configured && steering.armed);
  el('balken').classList.toggle('lenkt', lenkt);
  el('balken').classList.toggle('scharf', scharf);
  const plakette = el('lenkStatus');
  plakette.hidden = !(lenkt || scharf);
  plakette.className = 'lenkstatus' + (lenkt ? ' lenkt' : scharf ? ' scharf' : '');
  plakette.textContent = lenkt ? 'Lenkt' : scharf ? 'Scharf' : '';
  const lenkText = (kurz, lang) => { el('steerText').textContent = kurz; steer.title = `Lenkung ${lang}`; };
  if (!steering || !steering.configured) {
    steer.setAttribute('aria-checked', 'false');
    lenkText('kein Motor', 'Lenkhilfe · kein Motor freigegeben');
  } else if (steering.command.engaged) {
    steer.classList.add('an'); steer.setAttribute('aria-checked', 'true');
    lenkText('aktiv', 'aktiv · tippen schaltet ab');
  } else if (steering.armed) {
    steer.classList.add('scharf'); steer.setAttribute('aria-checked', 'true');
    // Scharf, lenkt aber gerade nicht: sie wartet und greift von selbst wieder.
    const warten = steering.command.reason && steering.command.reason !== 'nicht scharf';
    const rueck = steering.command.reason === 'Rückwärtsfahrt';
    lenkText(warten ? `wartet · ${steering.command.reason}` : 'scharf',
      !warten ? 'scharf' : rueck ? 'wartet · Rückwärtsfahrt · fährst du vorwärts: tippen'
        : `wartet · ${steering.command.reason} · greift von selbst wieder`);
  } else {
    steer.setAttribute('aria-checked', 'false');
    lenkText('aus', 'aus · tippen schaltet scharf');
  }

  // A/B ist ein Knopf mit zwei Schritten: erst A, dann B.
  el('btnAB').querySelector('span').textContent = s.recording && s.recording.pending_a ? 'B setzen' : 'A setzen';
  el('btnAB').classList.toggle('recording', !!(s.recording && s.recording.pending_a));
  const grenzeLaeuft = !!(s.recording && s.recording.mode === 'boundary');
  el('btnContour').classList.toggle('recording', grenzeLaeuft);
  el('btnContour').querySelector('span').textContent = grenzeLaeuft ? 'Kontur fertig' : 'Kontur';
  el('btnCurve').classList.toggle('recording', !!(s.recording && s.recording.mode === 'curve'));

  // Die Wende: klein, weil sie Nebensache ist. Der Text sagt, was der nächste Druck tut.
  const turnBtn = el('btnTurn');
  turnBtn.querySelector('span:last-child').textContent = turn.aktiv ? 'Stopp' : 'Wende';
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
  // Zum Licht hin lenken: die Lampen leuchten auf der Seite, auf der die Spur
  // liegt. guidance.lightbar ist positiv, wenn die Maschine rechts der Spur
  // steht - dann muss es links leuchten.
  const offset = guidance.active ? -Math.max(-middle, Math.min(middle, guidance.lightbar)) : null;
  for (let i = 0; i < LEDS; i++) {
    const led = bar.children[i];
    led.className = 'led' + (i === middle ? ' centre' : '');
    if (offset === null) continue;
    const position = i - middle;
    // Die Lampen zwischen Mitte und Spur leuchten: man fährt dorthin, wo es
    // leuchtet, bis nur noch die Mitte brennt.
    const lit = offset === 0 ? position === 0
      : (offset > 0 ? position > 0 && position <= offset
                    : position < 0 && position >= offset);
    if (!lit) continue;
    const magnitude = Math.abs(guidance.cross_track_cm);
    led.classList.add(magnitude < 5 ? 'on-green' : magnitude < 20 ? 'on-amber' : 'on-red');
  }
}

/* Teilbreiten wie in AgOpenGPS: vorn der Hauptschalter (AUTO → MAN = alle an → AUS),
 * dahinter je Teilbreite ein Knopf (Auto → fest an → fest aus). Farben: grün offen,
 * grau von der Automatik zu, gelb fest an, rot fest aus. */
const TB_MODUS_NAECHST = { auto: 'manuell', manuell: 'aus', aus: 'auto' };
const TB_MODUS_TEXT = { auto: 'AUTO', manuell: 'MAN', aus: 'AUS' };

function teilbreitenZustand(section) {
  return section.forced_off ? 'aus' : section.auto ? 'auto' : 'an';
}

function drawSections(sections) {
  const host = el('sections');
  if (sections.length <= 1) { host.hidden = true; return; }
  host.hidden = false;
  if (host.children.length !== sections.length + 1) {
    host.innerHTML = '';
    const master = document.createElement('div');
    master.className = 'sec master';
    master.title = 'Hauptschalter: AUTO, MAN (alle an), AUS';
    master.onclick = () => api('POST', '/api/sections/modus',
      { modus: TB_MODUS_NAECHST[(state.live && state.live.teilbreiten_modus) || 'auto'] });
    host.appendChild(master);
    sections.forEach((section, index) => {
      const box = document.createElement('div');
      box.className = 'sec';
      box.textContent = index + 1;
      box.title = 'Tippen: Auto → fest an → fest aus';
      box.onclick = () => {
        const jetzt = teilbreitenZustand(state.live.sections[index]);
        api('POST', `/api/sections/${index}`, { zustand: { auto: 'an', an: 'aus', aus: 'auto' }[jetzt] });
      };
      host.appendChild(box);
    });
  }
  const modus = (state.live && state.live.teilbreiten_modus) || 'auto';
  const master = host.children[0];
  master.textContent = TB_MODUS_TEXT[modus];
  master.className = 'sec master ' + modus;
  sections.forEach((section, index) => {
    const box = host.children[index + 1];
    const zustand = teilbreitenZustand(section);
    box.className = 'sec' + (zustand === 'aus' ? ' forced'
      : (section.enabled ? (zustand === 'an' ? ' manuell' : ' on') : ''));
  });
}

/* ----------------------------------------------------------------- Karte */

/* Die Leinwand ist größer als der Bildschirm (140 % der Breite, quadratisch),
 * damit sie gekippt den ganzen Blick füllt. Die Pixelzahl ist gedeckelt: ein
 * Tablet soll zehn Bilder je Sekunde schaffen, nicht Rekorde aufstellen. */
const MAX_PIXEL = 1600;
const ANKER_Y = 0.78;              // wo der Traktor auf der Leinwand sitzt

/* Hinweise können mehrzeilig werden. Nur bei Größenänderung messen, damit
 * das Livefenster darunter sitzt, ohne bei jedem Datenpaket Layout abzufragen. */
function statusAbstandMessen() {
  const unten = Math.max(...Array.from(document.querySelectorAll('.chips'),
    (gruppe) => gruppe.getBoundingClientRect().bottom));
  stage.style.setProperty('--status-unterkante', (unten + 10) + 'px');
}
if ('ResizeObserver' in window) {
  const statusBeobachter = new ResizeObserver(statusAbstandMessen);
  document.querySelectorAll('.chips').forEach((gruppe) => statusBeobachter.observe(gruppe));
}

function resize() {
  statusAbstandMessen();
  const ratio = window.devicePixelRatio || 1;
  const px = Math.min(MAX_PIXEL, Math.round(canvas.clientWidth * ratio));
  canvas.width = canvas.height = px;
  const k = px / canvas.clientWidth;
  ctx.setTransform(k, 0, 0, k, 0, 0);
  // Grundmaßstab: 1 m ist knapp ein Prozent der Leinwandbreite - genug, um
  // die Nachbarspuren zu sehen, nah genug für Zentimeter auf der eigenen.
  if (!state.view.scale) state.view.scale = canvas.clientWidth / 155;
  zeichnenAnfordern();
}
window.addEventListener('resize', resize);
// Jede Bedienung der Karte zeichnet sofort - Verschieben und Zoomen bleiben flüssig.
['pointerdown', 'pointermove', 'pointerup', 'wheel', 'touchstart', 'touchmove', 'touchend']
  .forEach((art) => canvas.addEventListener(art, zeichnenAnfordern, { passive: true }));
document.addEventListener('click', zeichnenAnfordern, { passive: true });

/* ------------------------------------------------- Glatte Bewegung

   Die Lage kommt zehnmal je Sekunde, aber nicht im Takt des Bildschirms -
   mal zwei Pakete kurz hintereinander, mal eine Lücke. Gezeichnet stur nach
   Paket ruckelt das Bild. Deshalb wird zwischen zwei Paketen mit Tempo und
   Kurs weitergerechnet, und ein neues Paket zieht die Anzeige in 180 ms weich
   auf die gemeldete Lage, statt sie hinüberspringen zu lassen. */
const glatt = { empfangen: 0, korrektur: [0, 0, 0], korrekturZeit: 0 };
let rahmen = [0, 0, 0];             // Versatz dieses Bildes: Ost, Nord, Kurs
const GLATT_VORAUS_S = 0.25;
const GLATT_ZIEHEN_MS = 180;

/* Die Hinterachse - dort steht der Traktor, dort ist die Mitte der Ansicht. */
function traktorPose(s) {
  if (!s || !s.position || s.heading == null) return null;
  const profil = s.profile || {};
  const h = s.heading * Math.PI / 180;
  const vor = profil.antenna_forward_m || 0, rechts = profil.antenna_right_m || 0;
  return [s.position[0] - Math.sin(h) * vor - Math.cos(h) * rechts,
          s.position[1] - Math.cos(h) * vor + Math.sin(h) * rechts, s.heading];
}

function fahrtTempo(s) {
  const v = s && s.fix ? (s.fix.speed_ms || 0) : 0;
  return s && s.kurs_kopplung && s.kurs_kopplung.rueckwaerts ? -v : v;
}

function winkelDifferenz(a, b) { return ((a - b + 540) % 360) - 180; }

function anzeigeVersatz(jetzt) {
  const s = state.live;
  if (!s || s.heading == null) return [0, 0, 0];
  const dt = Math.min(GLATT_VORAUS_S, Math.max(0, (jetzt - glatt.empfangen) / 1000));
  const v = fahrtTempo(s), h = s.heading * Math.PI / 180;
  const rest = Math.max(0, 1 - (jetzt - glatt.korrekturZeit) / GLATT_ZIEHEN_MS);
  return [Math.sin(h) * v * dt + glatt.korrektur[0] * rest,
          Math.cos(h) * v * dt + glatt.korrektur[1] * rest,
          glatt.korrektur[2] * rest];
}

function glaettungNachfuehren(vorher, neu) {
  const jetzt = performance.now();
  const alt = traktorPose(vorher), neuPose = traktorPose(neu);
  let korrektur = [0, 0, 0];
  if (alt && neuPose) {
    const v = anzeigeVersatz(jetzt);
    const k = [alt[0] + v[0] - neuPose[0], alt[1] + v[1] - neuPose[1],
               winkelDifferenz(alt[2] + v[2], neuPose[2])];
    // Ein Sprung (neues Feld, Simulator versetzt) wird nicht weichgezeichnet.
    if (Math.hypot(k[0], k[1]) < 3 && Math.abs(k[2]) < 30) korrektur = k;
  }
  glatt.korrektur = korrektur;
  glatt.empfangen = jetzt;
  glatt.korrekturZeit = jetzt;
}

function inBewegung() {
  const s = state.live;
  return Boolean(s && s.heading != null && (Math.abs(fahrtTempo(s)) > 0.05
    || performance.now() - glatt.korrekturZeit < GLATT_ZIEHEN_MS));
}

function viewCentre() {
  const s = state.live;
  const pose = traktorPose(s);
  if (pose) return [pose[0] + rahmen[0], pose[1] + rahmen[1]];
  return (s && s.tool_position) ? s.tool_position : [0, 0];
}

function viewRotation() {
  const s = state.live;
  if (state.view.mode === 'nord' || !s || s.heading == null) return 0;
  return -(s.heading + rahmen[2]) * Math.PI / 180;
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

/* Gezeichnet wird nur, wenn sich etwas geändert hat: neue Daten (10-mal je
   Sekunde), Verschieben/Zoomen, Größe, Ansicht. Vorher lief jedes Bild des
   Bildschirms durch (60-144 je Sekunde), obwohl zwischen zwei Datenpaketen
   dasselbe Bild herauskam - das hielt Grafikkarte und Prozessor ständig wach. */
let neuZeichnen = true;
function zeichnenAnfordern() { neuZeichnen = true; }
setInterval(zeichnenAnfordern, 1000);   // falls irgendwo eine Änderung nicht Bescheid sagt

/* In Fahrt wird zwischen den Paketen weitergezeichnet - gedeckelt auf 30 Bilder
   je Sekunde, das reicht für eine ruhige Bewegung und schont das Tablet. */
const BILD_ABSTAND_MS = 33;
let letztesBild = 0;
function render(zeit) {
  if (inBewegung() && zeit - letztesBild >= BILD_ABSTAND_MS) neuZeichnen = true;
  if (neuZeichnen && document.visibilityState === 'visible') {
    neuZeichnen = false;
    letztesBild = zeit;
    zeichnen();
  }
  requestAnimationFrame(render);
}

function zeichnen() {
  rahmen = anzeigeVersatz(performance.now());
  const width = canvas.clientWidth, height = canvas.clientHeight;
  ctx.save();
  ctx.fillStyle = F.boden;
  ctx.fillRect(0, 0, width, height);

  ctx.save();
  applyWorldTransform();
  drawGrid();
  drawLuftbild();
  drawCoverage();
  drawBoundary();
  drawHeadland();
  drawSaatplan();
  drawPasses();
  drawTrail();
  drawRecording();
  drawTurn();
  drawHindernisse();
  drawFlotte();
  drawVehicle();
  ctx.restore();

  ctx.restore();
}

/* Ein Raster als Boden: ohne Bezug wirkt eine leere Fläche wie Stillstand. */
/* Luftbild unter der Karte (luftbild.py): ein Bild je Feld, mit dem Knopf ein- und ausgeblendet. */
const luftbild = { an: gemerkt('luftbild') === 'an', feld: null, bild: null, rechteck: null, laedt: false };

function luftbildLaden() {
  const feld = state.live && state.live.field;
  if (!luftbild.an || !feld || luftbild.laedt || luftbild.feld === feld.id) return;
  luftbild.laedt = true;
  luftbild.feld = feld.id;
  luftbild.bild = null;
  fetch(`/api/luftbild/${feld.id}`)
    .then((r) => r.json().then((d) => { if (!r.ok) throw new Error(d.detail || r.status); return d; }))
    .then((info) => {
      const bild = new Image();
      bild.onload = () => {
        if (luftbild.feld !== info.feld_id) return;
        luftbild.bild = bild;
        luftbild.rechteck = info.rechteck;
        zeichnenAnfordern();
      };
      bild.src = info.bild;
      el('luftbildQuelle').textContent = info.quelle || '';
      el('luftbildQuelle').hidden = !luftbild.an;
      if (info.grob) {
        toast('Luftbild gerade nicht erreichbar – grobes Satellitenbild, später wird es nachgeholt');
        // In zehn Minuten noch einmal fragen: hat der Master oder der Dienst das scharfe
        // Bild, kommt es unter einer neuen Adresse (?v=…) und ersetzt das grobe.
        setTimeout(() => { if (luftbild.feld === info.feld_id) luftbild.feld = null; }, 10 * 60 * 1000);
      }
    })
    .catch((fehler) => toast(`Luftbild: ${fehler.message}`))
    .finally(() => { luftbild.laedt = false; });
}

function luftbildSchalten(an) {
  luftbild.an = an;
  merken('luftbild', an ? 'an' : 'aus');
  if (an) luftbild.feld = null;            // neu versuchen, auch nach einem Fehler
  el('luftbildKnopf').classList.toggle('an', an);
  el('luftbildKnopf').setAttribute('aria-pressed', String(an));
  el('luftbildQuelle').hidden = !an || !el('luftbildQuelle').textContent;
  zeichnenAnfordern();
}
el('luftbildKnopf').onclick = () => luftbildSchalten(!luftbild.an);
luftbildSchalten(luftbild.an);

function drawLuftbild() {
  luftbildLaden();
  const feld = state.live && state.live.field;
  if (!luftbild.an || !luftbild.bild || !feld || luftbild.feld !== feld.id) return;
  const [x0, y0, x1, y1] = luftbild.rechteck;
  ctx.save();
  // Nachts gedämpft, sonst überstrahlt das Bild Spuren und Fläche.
  ctx.globalAlpha = document.body.classList.contains('tag') ? 0.95 : 0.55;
  ctx.translate(x0, y1);
  ctx.scale((x1 - x0) / luftbild.bild.width, -(y1 - y0) / luftbild.bild.height);
  ctx.drawImage(luftbild.bild, 0, 0);
  ctx.restore();
}

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
  const ecken = [
    screenToWorld(0, 0), screenToWorld(canvas.clientWidth, 0),
    screenToWorld(0, canvas.clientHeight), screenToWorld(canvas.clientWidth, canvas.clientHeight),
  ];
  const xs = ecken.map((p) => p[0]), ys = ecken.map((p) => p[1]);
  flaeche.zeichnen(ctx, [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)],
    geraeteLage(state.live, rahmen));
}

function polyline(points, close) {
  ctx.beginPath();
  points.forEach(([x, y], index) => index ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
  if (close) ctx.closePath();
}

function drawBoundary() {
  const field = state.live && state.live.field;
  if (!field || !field.boundary || field.boundary.length < 3) return;
  ctx.save();
  if(field.working_boundary?.length)ctx.setLineDash([5/state.view.scale,5/state.view.scale]);
  polyline(field.boundary, true);
  ctx.strokeStyle = F.grenze;
  ctx.lineWidth = 2.5 / state.view.scale;
  ctx.stroke();ctx.setLineDash([]);
  if(field.working_boundary?.length){polyline(field.working_boundary,true);ctx.stroke();}
  ctx.restore();
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

/* Saatplan: der vorab geplante Schlag. Säspuren dünn, Fahrgassen rot mit ihrem
 * Abstand, Vorgewende-Runden gestrichelt, Ränder mit Konflikt bernsteinfarben.
 * Der Plan gehört zum Feld und wird nachgeladen, sobald ein anderes Feld kommt. */
const saat = { feld: null, plan: null, zeigen: true, laedt: false };

async function saatplanLaden(feldId) {
  saat.laedt = true;
  try {
    const antwort = await (await fetch(`/api/fields/${feldId}/saatplan`)).json();
    saat.feld = feldId;
    saat.plan = antwort.data ? antwort.data.plan : null;
    zeichnenAnfordern();
    return antwort.data;
  } catch (fehler) {
    saat.feld = feldId; saat.plan = null;
    return null;
  } finally {
    saat.laedt = false;
  }
}

/* Schrift an einem Weltpunkt. Mit ``belegt`` (Liste der schon beschrifteten Kästen in
 * Bildpunkten) bleibt weg, was eine andere Schrift überdecken würde - übereinander
 * liegende Fahrgassen-Beschriftungen rutschten bei jeder Bewegung gegeneinander. */
function weltText(text, x, y, farbe, belegt = null) {
  const p = ctx.getTransform().transformPoint(new DOMPoint(x, y));
  const k = window.devicePixelRatio || 1;
  ctx.save();
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.font = `700 ${Math.round(13 * k)}px system-ui, sans-serif`;
  if (belegt) {
    const w = ctx.measureText(text).width + 8 * k, h = 18 * k;
    const kasten = [p.x - w / 2, p.y - h / 2, p.x + w / 2, p.y + h / 2];
    if (belegt.some((b) => kasten[0] < b[2] && kasten[2] > b[0] && kasten[1] < b[3] && kasten[3] > b[1])) {
      ctx.restore();
      return;
    }
    belegt.push(kasten);
  }
  ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  ctx.lineWidth = 3 * k; ctx.strokeStyle = F.boden; ctx.strokeText(text, p.x, p.y);
  ctx.fillStyle = farbe; ctx.fillText(text, p.x, p.y);
  ctx.restore();
}

function drawSaatplan() {
  const feld = state.live && state.live.field;
  if (!feld) return;
  if (saat.feld !== feld.id) { if (!saat.laedt) saatplanLaden(feld.id); return; }
  const plan = saat.plan;
  if (!plan || !saat.zeigen) return;
  const px = 1 / state.view.scale;
  ctx.save();
  plan.ringe.forEach((runde) => {
    polyline(runde.punkte, true);
    ctx.setLineDash(runde.fahrgasse ? [10 * px, 5 * px] : [4 * px, 5 * px]);
    ctx.strokeStyle = runde.fahrgasse ? F.fahrgasse : F.pass;
    ctx.lineWidth = (runde.fahrgasse ? 2.6 : 1.2) * px;
    ctx.stroke();
  });
  ctx.setLineDash([]);
  plan.spuren.forEach((spur) => {
    ctx.beginPath();
    spur.stuecke.forEach(([a, b]) => { ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); });
    // Füllstreifen bernsteinfarben, die Restfahrgasse darin gestrichelt rot.
    ctx.strokeStyle = spur.fahrgasse ? F.fahrgasse : spur.fuell ? F.konflikt : F.pass;
    ctx.globalAlpha = spur.fuell && !spur.fahrgasse ? 0.6 : 1;
    ctx.setLineDash(spur.rest ? [10 * px, 6 * px] : []);
    ctx.lineWidth = (spur.fahrgasse ? 3 : 1.2) * px;
    ctx.stroke();
  });
  ctx.globalAlpha = 1; ctx.setLineDash([]);
  plan.konflikte.forEach((k) => {
    if (!k.punkte) return;
    polyline(k.punkte);
    ctx.strokeStyle = F.konflikt; ctx.globalAlpha = 0.85;
    ctx.lineWidth = 6 * px; ctx.stroke(); ctx.globalAlpha = 1;
  });
  // Abstand zwischen zwei Fahrgassen - auf halbem Weg, in der Mitte der Spur.
  // Weit herausgezoomt lägen die Schriften übereinander: dann nur die Linien.
  const gassen = plan.spuren.filter((s) => s.fahrgasse && !s.rest);
  if (plan.fahrgasse_m * state.view.scale < 44) { ctx.restore(); return; }
  const belegt = [];
  gassen.forEach((gasse, i) => {
    const naechste = gassen[i + 1];
    const [a, b] = gasse.stuecke.reduce((l, s) =>
      Math.hypot(s[1][0] - s[0][0], s[1][1] - s[0][1]) > Math.hypot(l[1][0] - l[0][0], l[1][1] - l[0][1]) ? s : l);
    const mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2;
    weltText(`FG ${i + 1}`, mx, my, F.fahrgasse, belegt);
    if (naechste) {
      const d = naechste.versatz_m - gasse.versatz_m;
      const ex = b[0] - a[0], ey = b[1] - a[1], l = Math.hypot(ex, ey) || 1;
      // Normale zur Spur, in Richtung der nächsten Fahrgasse.
      const nx = -ey / l, ny = ex / l;
      const vz = (naechste.stuecke[0][0][0] - a[0]) * nx + (naechste.stuecke[0][0][1] - a[1]) * ny > 0 ? 1 : -1;
      weltText(`${d.toLocaleString('de-DE')} m`, mx + nx * vz * d / 2 + ex / l * 25, my + ny * vz * d / 2 + ey / l * 25, F.fahrgasse, belegt);
    }
  });
  ctx.restore();
}

/* Hindernisse: roter Kreis in ihrer Größe, Name daneben. */
const HINDERNIS_ROT = '#ff5c5c';
function drawHindernisse() {
  const liste = (state.live && state.live.hindernisse) || [];
  if (!liste.length) return;
  const px = 1 / state.view.scale;
  const warnId = state.live.hindernis_warnung && state.live.hindernis_warnung.id;
  ctx.save();
  liste.forEach((h) => {
    const r = Math.max(h.radius_m, 6 * px);
    ctx.beginPath();
    ctx.arc(h.x, h.y, r, 0, Math.PI * 2);
    ctx.fillStyle = HINDERNIS_ROT;
    ctx.globalAlpha = h.id === warnId ? 0.55 : 0.3;
    ctx.fill();
    ctx.globalAlpha = 1;
    ctx.lineWidth = 2.5 * px;
    ctx.strokeStyle = HINDERNIS_ROT;
    ctx.stroke();
    weltText(`⚠ ${h.name}`, h.x, h.y + r + 10 * px, HINDERNIS_ROT);
  });
  ctx.restore();
}

/* Die anderen Maschinen (Fernansicht): blauer Pfeil mit Namen, grau wenn die Meldung alt ist. */
function drawFlotte() {
  const liste = ((state.live && state.live.flotte) || []).filter((m) => m.x != null);
  if (!liste.length) return;
  const px = 1 / state.view.scale;
  ctx.save();
  liste.forEach((m) => {
    const alt = window.Fernansicht.veraltet(m);
    const farbe = alt ? F.raster : '#4b9dff';
    const h = ((m.kurs || 0) * Math.PI) / 180;
    const vor = [Math.sin(h), Math.cos(h)], re = [Math.cos(h), -Math.sin(h)];
    const g = Math.max(3, 14 * px);
    ctx.beginPath();
    ctx.moveTo(m.x + vor[0] * g * 1.4, m.y + vor[1] * g * 1.4);
    ctx.lineTo(m.x - vor[0] * g + re[0] * g, m.y - vor[1] * g + re[1] * g);
    ctx.lineTo(m.x - vor[0] * g * 0.4, m.y - vor[1] * g * 0.4);
    ctx.lineTo(m.x - vor[0] * g - re[0] * g, m.y - vor[1] * g - re[1] * g);
    ctx.closePath();
    ctx.fillStyle = farbe;
    ctx.globalAlpha = 0.9;
    ctx.fill();
    ctx.globalAlpha = 1;
    weltText(m.name + (alt ? ` (${window.Fernansicht.alterText(m.alter_s)})` : ''), m.x, m.y - g * 2, farbe);
  });
  ctx.restore();
}

/* Die geplante oder laufende Wende. Geplant gestrichelt, gefahren durchgezogen –
 * damit auf einen Blick klar ist, ob die Maschine der Route schon folgt. */
function drawTurn() {
  const turn = state.live && state.live.turn;
  if (!turn || !turn.punkte || turn.punkte.length < 2) return;
  ctx.save();
  const huellen=turn.gespann?.huellen||[];
  if(huellen.length){ctx.beginPath();for(const polygon of huellen){polygon.forEach(([x,y],i)=>i?ctx.lineTo(x,y):ctx.moveTo(x,y));ctx.closePath();}ctx.fillStyle=turn.im_feld?F.wende:F.vg;ctx.globalAlpha=.14;ctx.fill();ctx.globalAlpha=1;}
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

/* Wie GuidanceLine.ist_fahrgasse: Fahrgassen liegen fest alle fahrgasse_m ab
 * der Bezugslinie (am Rand ab halber Fahrgassenweite, Spur 0 bei halber
 * Arbeitsbreite), Fahrgasse ist die Spur, in deren Streifen eine fällt. So
 * liegen sie bei jeder Arbeitsbreite an derselben Stelle. */
function istFahrgasse(line, spur) {
  const weite = line.fahrgasse_m, breite = line.spacing_m;
  if (!(weite > 0) || !(breite > 0)) return false;
  if (line.fahrgassen && line.fahrgassen.length) {
    // Saatplan: genau diese Fahrgassen (im Füllstreifen keine, dafür die Restfahrgasse).
    const mitte = (line.raster_m || 0) + spur * breite;
    return line.fahrgassen.some((x) => mitte - x > -breite / 2 && mitte - x <= breite / 2 + 1e-6);
  }
  if (weite <= breite) return true;
  const raster = line.raster_m || 0, erste = line.fahrgassen_versatz_m || 0;
  const mitte = raster + spur * breite;
  const gasse = erste + Math.floor((mitte - erste) / weite + 0.5) * weite;
  return Math.floor((gasse - raster) / breite + 0.5) === spur;
}

const spurPuffer = new window.SpurenModul.SpurPuffer();

/* Saatplan mit allen Spuren: die Spuren selbst kommen einmal über /api/line/spuren,
 * nicht zehnmal je Sekunde mit dem Zustand. Der Schlüssel sagt, wann es neue gibt. */
const planSpuren = { schluessel: '', spuren: [], laedt: false, zuletzt: 0 };

function planSpurenHolen(line) {
  if (planSpuren.laedt || planSpuren.schluessel === line.spuren_schluessel) return;
  if (Date.now() - planSpuren.zuletzt < 2000) return;   // nicht bei jedem Bild neu fragen
  planSpuren.laedt = true;
  planSpuren.zuletzt = Date.now();
  fetch('/api/line/spuren').then((r) => r.json()).then((d) => {
    planSpuren.schluessel = d.schluessel || '';
    planSpuren.spuren = d.spuren || [];
    zeichnenAnfordern();
  }).catch(() => {}).finally(() => { planSpuren.laedt = false; });
}

function planSpurenMuster(line) {
  planSpurenHolen(line);
  if (planSpuren.schluessel !== line.spuren_schluessel) return [];
  const nudge = line.nudge_m || 0;
  return planSpuren.spuren.map((spur, nummer) => ({
    nummer,
    ring: spur.geschlossen,
    gasse: spur.fahrgasse,
    punkte: nudge ? window.SpurenModul.versetzen({ mode: 'curve', points: spur.punkte }, nudge) : spur.punkte,
  }));
}

function drawPasses() {
  const s = state.live;
  if (!s || !s.line) { spurPuffer.spuren(null, 0); return; }
  const line = s.line;
  const current = s.guidance.active ? s.guidance.pass_number : 0;
  // Saatplan: alle Spuren einzeln (Hauptfeld, Gegenseite, Vorgewende-Runden).
  const plan = line.mode === 'plan';
  const muster = plan ? planSpurenMuster(line) : spurPuffer.spuren(line, current);
  for (const { nummer: offset, punkte: points, ring, gasse } of muster) {
    if (!points.length) continue;
    polyline(points, plan ? ring : line.mode === 'contour');
    const active = offset === current;
    // Fahrgassen rot – dort wird nicht gesät, dort fährt man später.
    const fahrgasse = plan ? gasse : istFahrgasse(line, offset);
    if (active) {
      // Die aktive Spur leuchtet: ein breiter, blasser Schein und die Linie darin.
      ctx.strokeStyle = fahrgasse ? F.fahrgasse : F.aktiv;
      ctx.globalAlpha = 0.22; ctx.lineWidth = 9 / state.view.scale; ctx.stroke();
      ctx.globalAlpha = 1; ctx.lineWidth = 2.8 / state.view.scale; ctx.stroke();
    } else if (fahrgasse) {
      ctx.strokeStyle = F.fahrgasse;
      ctx.setLineDash([6 / state.view.scale, 4 / state.view.scale]);
      ctx.lineWidth = 2 / state.view.scale;
      ctx.stroke();
      ctx.setLineDash([]);
    } else {
      ctx.strokeStyle = F.pass;
      ctx.lineWidth = 1.4 / state.view.scale;
      ctx.stroke();
    }
  }
}

function drawTrail() {
  // Bei laufender Arbeit zeigt der Streifen, wo gefahren wurde.
  if (state.trail.length < 2 || (state.live && state.live.job)) return;
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
 * Meter breit ist, ist auch auf dem Bildschirm drei Meter breit.
 *
 * Der Traktor steht an der Hinterachse, das Gerät dort, wo der Server markiert
 * (implement.position) - beim gezogenen Gerät in seiner eigenen Ausrichtung.
 * So beginnt der Streifen sichtbar am Gerät und nicht unter dem Traktor. */
/* Traktor und Gerät durchsichtig stellen, damit Spur und Fläche darunter sichtbar bleiben. */
let fahrzeugDeckkraft = Math.max(0.3, Math.min(1, parseFloat(gemerkt('fahrzeugDeckkraft') || '1') || 1));

/* Auswahllisten für Bauform, Marke und Geräteart aus fahrzeugbild.js füllen. */
function fahrzeugWahlFuellen() {
  const fuellen = (id, eintraege) => {
    const wahl = el(id);
    if (!wahl || wahl.options.length) return;
    Object.entries(eintraege).forEach(([wert, text]) => {
      const o = document.createElement('option');
      o.value = wert;
      o.textContent = typeof text === 'string' ? text : text.name;
      wahl.appendChild(o);
    });
  };
  fuellen('wahlBauform', window.Fahrzeugbild.BAUFORMEN);
  fuellen('wahlMarke', window.Fahrzeugbild.MARKEN);
  fuellen('wahlGeraetArt', window.Fahrzeugbild.GERAETE);
  el('fahrzeugDeckkraft').value = String(fahrzeugDeckkraft);
}

function drawVehicle() {
  const s = state.live;
  const pose = traktorPose(s);
  if (!pose) return;
  const profil = s.profile || {};
  const implement = s.implement || {};
  const roh = pose[2] * Math.PI / 180;
  const kurs = (pose[2] + rahmen[2]) * Math.PI / 180;

  // Lage des Geräts relativ zur Achse, im Fahrzeugsystem (rechts, vorwärts).
  const ip = implement.position || s.tool_position || pose;
  const ox = ip[0] - pose[0], oy = ip[1] - pose[1];
  const geraetRechts = ox * Math.cos(roh) - oy * Math.sin(roh);
  const geraetVor = ox * Math.sin(roh) + oy * Math.cos(roh);

  ctx.save();
  ctx.globalAlpha = fahrzeugDeckkraft;
  ctx.translate(pose[0] + rahmen[0], pose[1] + rahmen[1]);
  ctx.rotate(-kurs);           // Welt ist y-oben: +y ist vorwärts, +x rechts
  // Das Schneidwerk liegt vorn: über den Mähdrescher, den das Herauszoomen vergrößert.
  if (profil.geraet_art === 'schneidwerk') {
    traktorZeichnen(profil);
    geraetZeichnen(s, profil, implement, geraetRechts, geraetVor);
  } else {
    geraetZeichnen(s, profil, implement, geraetRechts, geraetVor);
    traktorZeichnen(profil);
  }
  ctx.restore();
}

function geraetZeichnen(s, profil, implement, rechts, vor) {
  const px = 1 / state.view.scale;
  const tiefe = Math.max(0.9, 7 * px);
  const sections = s.sections || [];
  const breite = profil.width_m || 3;
  const art = profil.geraet_art || 'balken';

  // Verbindung von der Hinterachse zum Gerät - das Schneidwerk hängt vorn am Schrägförderer.
  if (art !== 'schneidwerk') {
    ctx.beginPath();
    ctx.moveTo(0, -0.6);
    ctx.lineTo(rechts, vor + tiefe / 2);
    ctx.save();
    ctx.strokeStyle = F.traktor;
    ctx.globalAlpha = 0.7;
    ctx.lineWidth = Math.max(0.12, 2.5 * px);
    ctx.stroke();
    ctx.restore();
  }

  ctx.save();
  ctx.translate(rechts, vor);
  if (implement.trailed && implement.heading != null) {
    ctx.rotate(-winkelDifferenz(implement.heading, s.heading) * Math.PI / 180);
  }
  // Der Aufbau reicht vom Arbeitspunkt nach vorn - höchstens bis ans Heck des Traktors;
  // das Schneidwerk vom Messerbalken zurück bis an den Mähdrescher.
  const platz = art === 'schneidwerk' ? vor - window.Fahrzeugbild.DRESCHER_VORN : Math.max(0.5, -1.0 - vor);
  if (art !== 'balken') {
    window.Fahrzeugbild.geraet(ctx, art, breite, px, F, platz, window.Fahrzeugbild.farben(profil.marke, F));
  }
  const leiste = art === 'balken' ? tiefe : Math.max(0.3, 5 * px);
  const teile = sections.length ? sections : [{ left_m: -breite / 2, right_m: breite / 2, enabled: true }];
  teile.forEach((t) => {
    const an = t.enabled && !t.forced_off;
    ctx.fillStyle = t.forced_off ? F.vg : an ? (t.auto === false ? F.fest : F.aktiv) : F.geraet;
    ctx.fillRect(t.left_m, -leiste / 2, t.right_m - t.left_m, leiste);
  });
  // Vorlauflinien der Automatik wie in AgOpenGPS: grün wird eingeschaltet, rot ausgeschaltet.
  const vorlauf = s.teilbreiten_vorlauf;
  if (vorlauf && s.job && s.teilbreiten_modus === 'auto' && sections.length > 1
      && s.fix && s.fix.speed_ms > 0.3) {
    const links = teile[0].left_m, ganz = teile[teile.length - 1].right_m - teile[0].left_m;
    ctx.save();
    ctx.lineWidth = Math.max(0.06, 1.6 * px);
    ctx.setLineDash([4 * px, 3 * px]);
    [[vorlauf.an_m, F.aktiv], [vorlauf.aus_m, F.vg]].forEach(([y, farbe]) => {
      ctx.strokeStyle = farbe;
      ctx.beginPath();
      ctx.moveTo(links, y);
      ctx.lineTo(links + ganz, y);
      ctx.stroke();
    });
    ctx.restore();
  }
  // Fugen zwischen den Teilbreiten und ein Rand ums ganze Gerät
  ctx.strokeStyle = F['traktor-rand'];
  ctx.lineWidth = Math.max(0.05, 1.2 * px);
  teile.forEach((t) => ctx.strokeRect(t.left_m, -leiste / 2, t.right_m - t.left_m, leiste));
  ctx.restore();
}

/* Ein Schlepper von oben, in Metern ab Mitte Hinterachse (+y vorn, +x rechts):
 * Reifen mit Stollen, Kotflügel, Haube mit Glanzlicht, Kabine mit Glasrand,
 * Dach, Frontgewicht. Unter einer Fingerkuppe Größe wird er vergrößert - das
 * Gerät nicht, dessen Breite zählt. */
function rundRechteck(x, y, b, h, r) {
  const rr = Math.min(r, b / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.arcTo(x + b, y, x + b, y + h, rr);
  ctx.arcTo(x + b, y + h, x, y + h, rr);
  ctx.arcTo(x, y + h, x, y, rr);
  ctx.arcTo(x, y, x + b, y, rr);
  ctx.closePath();
}

function reifen(mx, my, breite, laenge, px) {
  rundRechteck(mx - breite / 2, my - laenge / 2, breite, laenge, breite * 0.28);
  ctx.fillStyle = F.reifen;
  ctx.fill();
  // Stollen: schräge Rippen, wie sie von oben auf der Lauffläche zu sehen sind
  ctx.save();
  ctx.clip();
  ctx.strokeStyle = 'rgba(255,255,255,.13)';
  ctx.lineWidth = Math.max(0.05, 1.2 * px);
  const schritt = laenge / 9;
  for (let s = -laenge / 2 - schritt; s <= laenge / 2 + schritt; s += schritt) {
    ctx.beginPath();
    ctx.moveTo(mx - breite / 2, my + s);
    ctx.lineTo(mx, my + s + schritt * 0.45);
    ctx.lineTo(mx + breite / 2, my + s);
    ctx.stroke();
  }
  ctx.restore();
}

/* Schatten in Bildschirmpixeln - er bleibt gleich weich, egal wie nah gezoomt ist. */
function schattenAn() { ctx.shadowColor = 'rgba(0,0,0,.5)'; ctx.shadowBlur = 10; }
function schattenAus() { ctx.shadowColor = 'transparent'; ctx.shadowBlur = 0; }

function traktorZeichnen(profil) {
  // Unter einer Fingerkuppe Größe wird die Maschine vergrößert - das Gerät nicht,
  // dessen Breite zählt. Bauform, Farbe und mitlenkende Räder: fahrzeugbild.js.
  const radstand = Math.max(2.2, Math.min(3.4, profil.wheelbase_m || 2.6));
  const laenge = profil.bauform === 'maehdrescher' || profil.bauform === 'knicklenker' ? 5 : radstand + 2.4;
  const k = Math.max(1, (40 / state.view.scale) / laenge);
  ctx.save();
  ctx.scale(k, k);
  window.Fahrzeugbild.fahrzeug(ctx, {
    profil, px: 1 / (state.view.scale * k), F,
    p: window.Fahrzeugbild.farben(profil.marke, F),
    winkel: window.Fahrzeugbild.radwinkel(state.live),
  });
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
  // Fehler länger: wer nur kurz hinschaut, soll sie noch lesen können.
  toastTimer = setTimeout(() => { box.hidden = true; }, isError ? 7000 : 4000);
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
/* Saatplan ab hier: an der Startecke stehen, in Richtung der ersten Spur
 * zeigen, drücken. Der Plan liegt sofort auf der Karte, das Menü zeigt ihn
 * mit Reihenfolge und Füllstreifen und bietet die Übernahme an. */
el('btnSaatplanHier').onclick = async () => {
  const antwort = await api('POST', '/api/saatplan/hier', {});
  if (!antwort) return;
  const plan = antwort.data;
  saat.feld = state.live.field.id; saat.plan = plan; saat.zeigen = true;
  zeichnenAnfordern();
  toast(`Saatplan ${plan.startwahl}: ${plan.anzahl_spuren} Spuren, ${plan.anzahl_fahrgassen} Fahrgassen` +
        (plan.fuellstreifen ? `, A–B ${plan.fuellstreifen.abstand_m.toFixed(1).replace('.', ',')} m` : ''));
  el('sheet').hidden = false;
  document.querySelector('.tab[data-tab="saatplan"]').onclick();
};
/* Wende: planen, ansehen, starten. Bewusst zwei Druck – die Route liegt erst
 * sichtbar auf der Karte, bevor die Maschine ihr folgt. */
/* Wende: läuft eine, beendet der Knopf sie. Sonst öffnet er das Wende-Fenster -
 * Richtung, ausgelassene Spuren und Form wählen; jede Wahl plant sofort neu,
 * das Ergebnis steht im Fenster, gestartet wird mit "Wende starten". */
el('btnTurn').onclick = async () => {
  const turn = (state.live && state.live.turn) || {};
  if (turn.aktiv) {
    await api('POST', '/api/turn/stop');
    toast('Wende abgebrochen');
    return;
  }
  if (!el('wendeFenster').hidden) { wendeFensterZu(); return; }
  await wendeFensterAuf();
};

async function apiRoh(method, url, body) {
  try {
    const antwort = await fetch(url, {
      method, headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    const daten = await antwort.json().catch(() => ({}));
    return { ok: antwort.ok, data: daten.data, detail: daten.detail };
  } catch (fehler) {
    return { ok: false, detail: 'Gerät nicht erreichbar' };
  }
}

function wahlSetzen(gruppe, wert) {
  el(gruppe).querySelectorAll('button').forEach((b) => b.classList.toggle('an', b.dataset.wert === String(wert)));
}
function wahlLesen(gruppe) {
  const an = el(gruppe).querySelector('button.an');
  return an ? an.dataset.wert : null;
}

function wendeErgebnis(text, gut) {
  const feld = el('wfErgebnis');
  feld.textContent = text;
  feld.className = 'wf-ergebnis ' + (gut ? 'gut' : 'schlecht');
  el('wfStarten').disabled = !gut;
}

let wendeInArbeit = false, wendeNachholen = false;
async function wendePlanen(speichern = true) {
  if (wendeInArbeit) {
    if (speichern) { wendeNachholen = true; wendeErgebnis('Neue Auswahl wird anschließend geplant …', false); }
    return;
  }
  wendeInArbeit = true;
  try { await wendeBerechnen(speichern); }
  finally {
    wendeInArbeit = false;
    if (wendeNachholen) {
      wendeNachholen = false;
      if (!el('wendeFenster').hidden) await wendePlanen(true);
    }
  }
}

async function wendeBerechnen(speichern) {
  wendeErgebnis('Plane …', false);
  const auslassen = parseInt(wahlLesen('wfAuslassen') || '0', 10);
  if (speichern) {
    wendeErgebnis('Plane …', false);
    const gespeichert = await apiRoh('POST', '/api/headland', {
      richtung: wahlLesen('wfRichtung'), muster: wahlLesen('wfForm'), ueberspringen: auslassen + 1,
    });
    if (!gespeichert.ok) { wendeErgebnis(gespeichert.detail || 'Einstellung nicht übernommen', false); return; }
  }
  const plan = await apiRoh('POST', '/api/turn/plan', {});
  if (wendeNachholen || el('wendeFenster').hidden) return;
  if (!plan.ok) { wendeErgebnis(plan.detail || 'Keine Wende möglich', false); return; }
  const d = plan.data;
  if(d.gespann&&!d.gespann.vollstaendig){wendeErgebnis(d.gespann.hinweis,false);
  } else if (!d.fahrbar) {
    wendeErgebnis(`Zu eng: engster Bogen ${d.engster_radius_m} m, die Lenkung schafft ${d.kleinster_radius_m} m – ` +
                  'mehr Spuren auslassen oder U-Wende.', false);
  } else if (!d.grenze_vorhanden) {
    wendeErgebnis('Das Feld hat keine Grenze – die Route kann nicht geprüft werden.', false);
  } else if (!d.im_feld) {
    wendeErgebnis(d.vorschlag || 'Die Route liegt nicht vollständig im Feld.', false);
  } else {
    const form = d.muster === 'dubins' ? 'Dubins-Wende' : d.muster === 'u' ? 'U-Wende' : 'Ω-Wende';
    const spuren = auslassen === 0 ? 'auf die Nachbarspur' : `${auslassen} ${auslassen === 1 ? 'Spur' : 'Spuren'} ausgelassen`;
    const wo = d.anlauf_m > 1 ? `wendet in ${d.anlauf_m.toFixed(0)} m am Vorgewende` : 'wendet hier';
    wendeErgebnis(`${form} nach ${d.richtung}, ${spuren} – ${wo}.`, true);
  }
}

async function wendeFensterAuf() {
  // /api/headland antwortet ohne Umschlag - deshalb hier direkt gelesen.
  const einstellung = await fetch('/api/headland').then((r) => r.json()).catch(() => ({}));
  wahlSetzen('wfRichtung', einstellung.richtung || 'links');
  wahlSetzen('wfAuslassen', Math.max(0, (einstellung.ueberspringen || 1) - 1));
  wahlSetzen('wfForm', einstellung.muster || 'omega');
  el('wendeFenster').hidden = false;
  el('btnTurn').setAttribute('aria-expanded', 'true');
  await wendePlanen();
  // Solange das Fenster offen ist, alle 2 s neu planen: der Abstand zum
  // Vorgewende stimmt, und ist es noch über 80 m entfernt, sieht der Fahrer,
  // sobald es passt - ohne erneut zu wählen.
  clearInterval(wendeTakt);
  wendeTakt = setInterval(() => {
    if (el('wendeFenster').hidden) { clearInterval(wendeTakt); return; }
    wendePlanen(false);
  }, 2000);
}
let wendeTakt = null;

function wendeFensterZu() {
  el('wendeFenster').hidden = true;
  el('btnTurn').setAttribute('aria-expanded', 'false');
  clearInterval(wendeTakt);
}

/* Hindernis markieren: Wo, Was, Größe wählen, "Markieren". Die Wahl bleibt für das nächste Mal. */
function hindernisFensterAuf() {
  werkzeugeZeigen(false);
  if (!el('wendeFenster').hidden) wendeFensterZu();
  if (!wahlLesen('hfSeite')) wahlSetzen('hfSeite', 'hier');
  if (!wahlLesen('hfArt')) wahlSetzen('hfArt', 'Mast');
  if (!wahlLesen('hfGroesse')) wahlSetzen('hfGroesse', '1.5');
  el('hfErgebnis').textContent = '„Hier“ = unter der Maschine. Kante = halbe Arbeitsbreite daneben.';
  el('hfErgebnis').className = 'wf-ergebnis';
  el('hindernisFenster').hidden = false;
  el('btnHindernis').setAttribute('aria-expanded', 'true');
}

function hindernisFensterZu() {
  el('hindernisFenster').hidden = true;
  el('btnHindernis').setAttribute('aria-expanded', 'false');
}

el('btnHindernis').onclick = () => (el('hindernisFenster').hidden ? hindernisFensterAuf() : hindernisFensterZu());
el('hfZu').onclick = hindernisFensterZu;
['hfSeite', 'hfArt', 'hfGroesse'].forEach((gruppe) => {
  el(gruppe).addEventListener('click', (ereignis) => {
    const knopf = ereignis.target.closest('button');
    if (knopf) wahlSetzen(gruppe, knopf.dataset.wert);
  });
});
el('hfSetzen').onclick = async () => {
  const antwort = await apiRoh('POST', '/api/hindernisse', {
    seite: wahlLesen('hfSeite'), name: wahlLesen('hfArt'), radius_m: parseFloat(wahlLesen('hfGroesse')),
  });
  if (!antwort.ok) {
    el('hfErgebnis').textContent = antwort.detail || 'Nicht markiert';
    el('hfErgebnis').className = 'wf-ergebnis schlecht';
    return;
  }
  toast(`${antwort.data.name} markiert – alle Tablets bekommen es beim Abgleich`);
  hindernisFensterZu();
};

['wfRichtung', 'wfAuslassen', 'wfForm'].forEach((gruppe) => {
  el(gruppe).addEventListener('click', (ereignis) => {
    const knopf = ereignis.target.closest('button');
    if (!knopf) return;
    wahlSetzen(gruppe, knopf.dataset.wert);
    wendePlanen();
  });
});
el('wfZu').onclick = wendeFensterZu;
el('wfStarten').onclick = async () => {
  const antwort = await apiRoh('POST', '/api/turn/start');
  if (!antwort.ok) { wendeErgebnis(antwort.detail || 'Start nicht möglich', false); return; }
  wendeFensterZu();
  toast('Wende läuft – fährt bis zum Vorgewende und wendet dort');
};

/* Kontur und Grenze sind dasselbe: die Grenze wird einmal abgefahren, und
 * genau diese Linie ist die Kontur, auf der danach geführt wird. Ein Knopf,
 * drei Zustände: Grenze aufzeichnen - Grenze abschließen (und sofort auf der
 * Kontur fahren) - Kontur wieder aktivieren, wenn gerade eine andere Spur
 * aktiv war. Neu abfahren geht über Menü → Felder → Grenze abfahren. */
/* Was die aktive Spur ersetzt, braucht einen zweiten Tipp: auf dem rüttelnden Sitz
   trifft der Handschuh leicht die Nachbartaste, und bei eingeschalteter Lenkung
   wechselte sonst das Lenkziel. Wie bei der Wende - erst fragen, dann tun. */
/* Spur-Werkzeuge: aufklappen, nach der Wahl wieder zu. Läuft eine Aufnahme
   (A gesetzt, Kurve, Grenze), bleiben sie offen - der zweite Druck folgt. */
function spurAufnahmeLaeuft() {
  const r = state.live && state.live.recording;
  return Boolean(r && (r.pending_a || r.mode));
}
function werkzeugeZeigen(offen) {
  el('werkzeuge').hidden = !offen;
  el('btnSpur').setAttribute('aria-expanded', String(offen));
}
function werkzeugeSchliessenWennFertig() {
  if (!spurAufnahmeLaeuft() && !document.querySelector('#werkzeuge .bestaetigen')) werkzeugeZeigen(false);
}
el('btnSpur').onclick = () => werkzeugeZeigen(el('werkzeuge').hidden);
el('werkzeuge').addEventListener('click', () => setTimeout(werkzeugeSchliessenWennFertig, 700));
document.addEventListener('pointerdown', (event) => {
  if (!el('werkzeuge').hidden && !event.target.closest('#werkzeuge, #btnSpur')) werkzeugeSchliessenWennFertig();
});

function nachfragen(knopf, frage) {
  if (knopf.classList.contains('bestaetigen')) {
    knopf.classList.remove('bestaetigen');
    clearTimeout(knopf._frageZeit);
    return true;
  }
  knopf.dataset.frage = frage;
  knopf.classList.add('bestaetigen');
  knopf._frageZeit = setTimeout(() => knopf.classList.remove('bestaetigen'), 4000);
  return false;
}

el('btnContour').onclick = async () => {
  const s = state.live || {};
  if (s.recording && s.recording.mode === 'boundary') {
    const result = await api('POST', '/api/record/stop', {});
    if (!result) return;
    const kontur = await api('POST', '/api/guidance/contour');
    toast(`Grenze gespeichert: ${result.data.area_ha.toFixed(2)} ha` +
          (kontur ? ' – Kontur aktiv, Ring 0 ist die Grenze' : ''));
    refreshLists();
    return;
  }
  if (!s.field) { toast('Erst ein Feld laden oder anlegen (Menü → Felder)', true); return; }
  if (s.field.boundary && s.field.boundary.length >= 3) {
    if (s.line && s.line.mode === 'contour') {
      toast('Kontur ist aktiv. Grenze neu abfahren: Menü → Felder → Grenze abfahren');
      return;
    }
    if (s.line && !nachfragen(el('btnContour'), 'Spur wechseln? nochmal')) return;
    const result = await api('POST', '/api/guidance/contour');
    if (result) { toast('Kontur aktiv – Ring 0 ist die Feldgrenze'); refreshLists(); }
    return;
  }
  if (await api('POST', '/api/record/start', { mode: 'boundary' })) {
    toast('Kontur aufzeichnen – einmal um das Feld fahren, dann wieder Kontur drücken');
  }
};

// Zehn Zentimeter je Druck: das ist der Schritt, den man im Feld braucht -
// die Nachbarspur liegt einen Meter daneben, nicht einen Zentimeter.
el('btnRichtung').onclick = async () => {
  const antwort = await api('POST', '/api/richtung/umkehren');
  if (antwort) toast(antwort.data.rueckwaerts ? 'Fahrtrichtung: rückwärts' : 'Fahrtrichtung: vorwärts');
};
/* "› 10 cm" schiebt die Spur dorthin, wo auf dem Bildschirm rechts ist - in
   jeder Ansicht, auch "Norden oben". Mitgeschickt wird deshalb die Richtung
   "rechts auf dem Bildschirm" in Weltkoordinaten (Ost, Nord). */
function bildschirmRechts() {
  const y = ankerY(), x = canvas.clientWidth / 2;
  const a = screenToWorld(x, y), b = screenToWorld(x + 100, y);
  const laenge = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
  return [(b[0] - a[0]) / laenge, (b[1] - a[1]) / laenge];
}
const feldwerkzeuge = new FeldwerkzeugeModul.Feldwerkzeuge({el,api:apiRoh,aktualisieren:refreshLists,profil:()=>state.live?.profile});
el('btnOriginalspur').onclick=async()=>{
  const s=state.live,linie=s?.line,feld=s?.field;if(!linie||!feld)return;
  const payload={field_id:feld.id,line_id:linie.id,nudge_m:linie.nudge_m};
  if(!confirm(`Seitlichen Versatz der Spur „${linie.name}“ von ${(linie.nudge_m*100).toFixed(0)} cm auf 0 zurücksetzen?`))return;
  if(await api('POST','/api/guidance/original',payload)){toast('Originalspur wiederhergestellt');refreshLists();}
};
el('btnNudgeLeft').onclick = () => api('POST', '/api/guidance/nudge', { metres: -0.10, bildschirm_rechts: bildschirmRechts() });
el('btnNudgeRight').onclick = () => api('POST', '/api/guidance/nudge', { metres: 0.10, bildschirm_rechts: bildschirmRechts() });

/* Markieren an/aus - das ist der Auftrag. Die Bezeichnung (Grubbern, Säen...)
 * steht unter Menü → Aufträge und wird für den nächsten Start gemerkt. */
function arbeitBezeichnung() {
  try { return localStorage.getItem('agripilot.arbeit') || 'Arbeit'; } catch (e) { return 'Arbeit'; }
}
el('btnJob').onclick = async () => {
  if (state.live && state.live.job) {
    const result = await api('POST', '/api/job/stop');
    if (result) {
      toast(result.data.verworfen ? 'Markieren aus – nichts gefahren, Arbeit verworfen'
                                  : `Markieren aus – ${result.data.area_ha.toFixed(2)} ha`);
    }
  } else if (await api('POST', '/api/job/start', { operation: arbeitBezeichnung() })) {
    toast(`Markieren an – ${arbeitBezeichnung()}`);
  }
};

el('feldVorschlag').onclick = async () => {
  const id = el('feldVorschlag').dataset.id;
  el('feldVorschlag').hidden = true;
  if (id && await api('POST', `/api/fields/${id}/load`)) toast('Feld geladen');
};

/* Ton: Browser erlauben ihn erst nach einer Berührung - die erste genügt. */
document.addEventListener('pointerdown', () => window.Warnungen && window.Warnungen.freischalten(), { once: true });
el('warnton').checked = gemerkt('warnton') !== 'aus';
if (window.Warnungen) window.Warnungen.einstellen({ an: el('warnton').checked });
el('warnton').onchange = () => {
  merken('warnton', el('warnton').checked ? 'an' : 'aus');
  if (window.Warnungen) window.Warnungen.einstellen({ an: el('warnton').checked });
};
el('sprache').checked = gemerkt('sprache') !== 'aus';
if (window.Warnungen) window.Warnungen.einstellen({ sprache: el('sprache').checked });

/* Sprachknopf auf der Karte und Häkchen im Menü sind dieselbe Einstellung. Aus heißt:
 * keine Ansagen mehr, der Piepton (Warnton) bleibt. */
function spracheSetzen(an) {
  el('sprache').checked = an;
  merken('sprache', an ? 'an' : 'aus');
  if (window.Warnungen) window.Warnungen.einstellen({ sprache: an });
  el('spracheKnopf').classList.toggle('stumm', !an);
  el('spracheKnopf').setAttribute('aria-pressed', String(an));
  el('spracheKnopf').title = an ? 'Sprachansagen an – tippen: nur noch Piepton'
    : 'Nur Piepton – tippen: Sprachansagen wieder an';
}
spracheSetzen(el('sprache').checked);
el('sprache').onchange = () => spracheSetzen(el('sprache').checked);
el('spracheKnopf').onclick = () => {
  spracheSetzen(!el('sprache').checked);
  toast(el('sprache').checked ? 'Sprachansagen an' : 'Sprachansagen aus – nur noch Piepton');
};
el('btnWarnProbe').onclick = () => {
  if (!window.Warnungen) return;
  const gesprochen = window.Warnungen.probe('alarm');
  el('spracheInfo').textContent = !el('sprache').checked ? 'Sprachansagen sind aus.'
    : gesprochen ? (window.Warnungen.hatStimme() ? '' : 'Keine deutsche Stimme gefunden – es spricht die Standardstimme.')
      : 'Dieses Gerät kann nicht sprechen (keine Sprachausgabe im Browser).';
};

el('btnSteer').onclick = async () => {
  const steering = state.live && state.live.steering;
  if (steering && steering.armed && !steering.command.engaged && steering.command.reason === 'Rückwärtsfahrt') {
    // Wartet, weil die Anlage Rückwärts vermutet - ohne Gyro auch nach einer Wende von
    // Hand. Tippen heißt hier: ich fahre vorwärts. Nicht neu scharf schalten: das lernte
    // die Radmitte neu, und nach einer Wende von Hand stehen die Räder selten gerade.
    if (await api('POST', '/api/steering/vorwaerts')) toast('Vorwärts bestätigt');
  } else if (steering && steering.armed) {
    await api('POST', '/api/steering/disarm');
    toast('Lenkung aus');
  } else {
    const result = await api('POST', '/api/steering/arm', { operation: arbeitBezeichnung() });
    if (result) {
      const zusatz = result.data.markieren_gestartet ? ' · Markieren an' : '';
      toast(result.data.armed ? `Lenkung scharf – Hände ans Lenkrad${zusatz}`
                              : result.data.message, !result.data.armed);
    }
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
  if (kartenfarben) kartenfarben.themaSetzen(tag ? 'tag' : 'nacht');
  farbenLesen();
  zeichnenAnfordern();
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

/* Seitenkopf: Zeichen, Titel und die Zeile darunter kommen vom gewählten Reiter. */
function seitenkopf(tab) {
  el('sheetTitel').textContent = tab.querySelector('.tab-name').textContent;
  el('sheetUntertitel').textContent = (tab.querySelector('small') || {}).textContent || '';
  const zeichen = tab.querySelector('.tab-symbol');
  el('sheetSymbol').replaceChildren(...(zeichen ? [zeichen.cloneNode(true)] : []));
  menueFuss();
}

/* Fuß der Leiste: Version und Rolle dieses Geräts. */
function menueFuss() {
  const system = state.live && state.live.system;
  if (!system) return;
  const rolle = system.role === 'master' ? 'Master' : 'Arbeiter';
  el('menueFuss').innerHTML = '<b></b><br><span></span>';
  el('menueFuss').querySelector('b').textContent = `AgriPilot ${system.version || ''}`;
  el('menueFuss').querySelector('span').textContent =
    `${rolle}${system.device_name ? ' · ' + system.device_name : ''}`;
}

function menueOeffnen() {
  el('sheet').hidden = false;
  seitenkopf(document.querySelector('.tab.active'));
  menueFuss();
  refreshLists();
  // Fokus ins Menü: wer mit der Tastatur bedient, landet beim aktiven Reiter.
  document.querySelector('.tab.active').focus({ preventScroll: true });
}

function menueSchliessen() {
  el('sheet').hidden = true;
  el('btnMenu').focus({ preventScroll: true });
}

el('btnMenu').onclick = menueOeffnen;
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && !(el('aussaatDialog').open||el('feldWerkzeug').open) && !el('sheet').hidden) menueSchliessen();
});

/* Ein Reiter lässt sich direkt ansteuern: /#menu=setup öffnet die Inbetriebnahme.
 * Für ein Lesezeichen auf dem Tablet - und für Bildschirmfotos der Doku. */
function menueAusAdresse() {
  const treffer = /menu=([a-z]+)/.exec(location.hash);
  if (!treffer) return;
  const tab = document.querySelector(`.tab[data-tab="${treffer[1]}"]`);
  if (!tab) return;
  el('sheet').hidden = false;
  tab.onclick();
}
window.addEventListener('hashchange', menueAusAdresse);
el('sheetClose').onclick = menueSchliessen;
const reiter = [...document.querySelectorAll('.tab')];
reiter.forEach((tab, index) => {
  tab.onclick = () => {
    reiter.forEach((t) => {
      t.classList.toggle('active', t === tab);
      t.setAttribute('aria-selected', String(t === tab));
      t.tabIndex = t === tab ? 0 : -1;
    });
    document.querySelectorAll('.tab-panel').forEach((panel) => {
      panel.hidden = panel.dataset.panel !== tab.dataset.tab;
    });
    seitenkopf(tab);
    // Auf schmalen Geräten scrollt die Leiste: den gewählten Reiter in Sicht holen.
    tab.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    refreshLists();
  };
  // Pfeiltasten wechseln den Reiter, wie bei jeder Reiterleiste.
  tab.onkeydown = (event) => {
    const schritt = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
    if (!schritt) return;
    event.preventDefault();
    const ziel = reiter[(index + schritt + reiter.length) % reiter.length];
    ziel.focus();
    ziel.onclick();
  };
});

/* Menüsuche (menuesuche.js): ein Treffer öffnet die Seite, klappt den Abschnitt auf
 * und hebt die Stelle kurz hervor. */
let menueTreffer = [];

function menueSucheLeeren() {
  el('menueSuche').value = '';
  el('menueTreffer').hidden = true;
  el('menueTreffer').innerHTML = '';
  menueTreffer = [];
}

function menueTrefferOeffnen(eintrag) {
  menueSucheLeeren();
  document.querySelector(`.tab[data-tab="${eintrag.panel}"]`).onclick();
  const ziel = eintrag.element;
  if (!ziel || !ziel.isConnected) return;
  for (let k = ziel; k; k = k.parentElement) if (k.tagName === 'DETAILS') k.open = true;
  ziel.scrollIntoView({ block: 'center' });
  ziel.classList.add('such-treffer');
  setTimeout(() => ziel.classList.remove('such-treffer'), 2500);
  const feld = ziel.tagName === 'LABEL' && ziel.control ? ziel.control : ziel;
  if (typeof feld.focus === 'function') feld.focus({ preventScroll: true });
}

function menueTrefferZeigen() {
  const host = el('menueTreffer');
  const frage = el('menueSuche').value;
  host.innerHTML = '';
  if (!frage.trim()) { host.hidden = true; menueTreffer = []; return; }
  menueTreffer = MenueSucheModul.suchen(
    MenueSucheModul.sammeln([...document.querySelectorAll('.tab-panel')], reiter), frage);
  host.hidden = false;
  if (!menueTreffer.length) {
    host.innerHTML = '<p class="note">Nichts gefunden. Ein anderes Wort probieren, z.B. „Spur“ oder „Update“.</p>';
    return;
  }
  menueTreffer.forEach((eintrag) => {
    const knopf = document.createElement('button');
    knopf.type = 'button';
    knopf.setAttribute('role', 'listitem');
    knopf.innerHTML = '<b></b><small></small>';
    knopf.querySelector('b').textContent = eintrag.text;
    knopf.querySelector('small').textContent = eintrag.element
      ? [eintrag.seite, eintrag.abschnitt].filter(Boolean).join(' › ') : `Seite · ${eintrag.abschnitt}`;
    knopf.onclick = () => menueTrefferOeffnen(eintrag);
    host.appendChild(knopf);
  });
}

// Die Einstellungen entstehen erst beim Laden - vorher fände die Suche sie nicht.
let menueEinstellungenLaden = null;
function menueSucheEingabe() {
  menueTrefferZeigen();
  if (settings.geladen || menueEinstellungenLaden) return;
  menueEinstellungenLaden = loadSettings().then(menueTrefferZeigen, () => {}).finally(() => { menueEinstellungenLaden = null; });
}
el('menueSuche').onfocus = () => { if (el('menueSuche').value) menueTrefferZeigen(); };
el('menueSuche').oninput = menueSucheEingabe;
el('menueSuche').onkeydown = (event) => {
  if (event.key === 'Enter' && menueTreffer.length) { event.preventDefault(); menueTrefferOeffnen(menueTreffer[0]); }
  if (event.key === 'ArrowDown' && menueTreffer.length) { event.preventDefault(); el('menueTreffer').querySelector('button').focus(); }
  // Escape leert erst die Suche; erst das zweite schließt das Menü.
  if (event.key === 'Escape' && el('menueSuche').value) { event.stopPropagation(); menueSucheLeeren(); }
};
el('menueTreffer').onkeydown = (event) => {
  const knoepfe = [...el('menueTreffer').querySelectorAll('button')];
  const i = knoepfe.indexOf(document.activeElement);
  if (event.key === 'ArrowDown' && i < knoepfe.length - 1) { event.preventDefault(); knoepfe[i + 1].focus(); }
  if (event.key === 'ArrowUp') { event.preventDefault(); (i > 0 ? knoepfe[i - 1] : el('menueSuche')).focus(); }
  if (event.key === 'Escape') { event.stopPropagation(); menueSucheLeeren(); el('menueSuche').focus(); }
};
document.addEventListener('pointerdown', (event) => {
  if (!event.target.closest('.menue-suche')) { el('menueTreffer').hidden = true; }
});

el('btnNewField').onclick = async () => {
  const name = el('newFieldName').value.trim() || `Feld ${new Date().toLocaleDateString('de-DE')}`;
  const result = await api('POST', '/api/fields', { name });
  if (result) { el('newFieldName').value = ''; toast(`Feld "${name}" angelegt`); refreshLists(); }
};

/* Shapefile: drei Dateien, als Text verpackt. Der Server bekommt sie
 * base64-kodiert im JSON - kein Multipart, kein weiteres Paket auf dem Pi. */
el('shpDateien').onchange = async (event) => {
  const dateien = [...event.target.files];
  event.target.value = '';
  const finde = (endung) => dateien.find((d) => d.name.toLowerCase().endsWith(endung));
  const shp = finde('.shp');
  if (!shp) { toast('Die .shp-Datei fehlt in der Auswahl', true); return; }
  const info = el('shpInfo');
  info.textContent = `${shp.name} wird gelesen …`;
  const b64 = dateiAlsBase64;
  const dbf = finde('.dbf'), prj = finde('.prj');
  const payload = {
    name: shp.name,
    shp: await b64(shp),
    dbf: dbf ? await b64(dbf) : null,
    prj: prj ? await prj.text() : null,
  };
  const antwort = await api('POST', '/api/fields/import', payload);
  if (!antwort) { info.textContent = ''; return; }
  const felder = antwort.data.felder;
  const ha = felder.reduce((summe, feld) => summe + feld.area_ha, 0);
  info.textContent = `${felder.length} Felder · ${ha.toFixed(2)} ha` +
    (prj ? '' : ' · ohne .prj, Koordinatensystem geraten') +
    (dbf ? '' : ' · ohne .dbf, Namen durchnummeriert');
  toast(`${felder.length} Felder aus ${shp.name} übernommen`);
  refreshLists();
};

el('btnAPlus').onclick = async () => {
  const s = state.live || {};
  if (s.line && !nachfragen(el('btnAPlus'), 'neue Spur? nochmal')) return;
  const result = await api('POST', '/api/guidance/a-plus', {});
  if (result) { toast(`Spur "${result.data.name}" angelegt`); refreshLists(); }
};

el('arbeitName').value = arbeitBezeichnung();
el('arbeitName').onchange = (event) => {
  const name = event.target.value.trim() || 'Arbeit';
  merken('agripilot.arbeit', name);
  toast(`Nächste Arbeit: ${name}`);
};

/* Ungespeicherte Eingaben: das Menü frischt sich alle 5 s auf. Ein Formular, in dem
   gerade getippt wurde, darf dabei nicht überschrieben werden - sonst ist die
   eingetippte Arbeitsbreite still weg, und man hält den alten Wert für gespeichert.
   Geänderte Felder bleiben markiert, bis übernommen oder neu gewählt wird. */
const FORMULARE = ['traktorForm', 'profileForm', 'headlandForm', 'headlandSwitches'];

function formularOffen(id) { return el(id).dataset.geaendert === '1'; }

function formularSauber(id) {
  const form = el(id);
  form.dataset.geaendert = '';
  form.querySelectorAll('.geaendert').forEach((feld) => feld.classList.remove('geaendert'));
}

FORMULARE.forEach((id) => {
  const form = el(id);
  const merken = (event) => {
    if (!event.target.matches('input, select')) return;
    form.dataset.geaendert = '1';
    event.target.classList.add('geaendert');
  };
  form.addEventListener('input', merken);
  form.addEventListener('change', merken);
});

/* Mehrere Maschinen: die Liste oben wählt, das Formular darunter gehört zur
 * gewählten. "Neue Maschine" kopiert die aktuellen Werte unter neuem Namen. */
/* Ein Mähdrescher fährt mit Schneidwerk, ein Traktor mit Gerät dahinter: die Liste
   zeigt nur, was zum gewählten Fahrzeug passt (das gewählte immer). */
let geraeteAlle = [];

function drescherGewaehlt() {
  const traktor = traktorenListe.find((t) => t.aktiv);
  return (traktor ? traktor.bauform : ((state.live && state.live.profile) || {}).bauform) === 'maehdrescher';
}

function renderProfiles(alle) {
  geraeteAlle = alle;
  const drescher = drescherGewaehlt();
  const profile = alle.filter((p) => p.aktiv || (p.geraet_art === 'schneidwerk') === drescher);
  el('kopfGeraet').textContent = drescher ? 'Schneidwerk' : 'Gerät';
  el('btnNewProfile').textContent = drescher ? 'Neues Schneidwerk' : 'Neues Gerät';
  const select = el('profileSelect');
  select.innerHTML = '';
  profile.forEach((p) => {
    const option = document.createElement('option');
    option.value = p.id;
    option.textContent = `${p.name} · ${p.width_m.toFixed(2)} m`;
    option.selected = !!p.aktiv;
    select.appendChild(option);
  });
  el('btnDeleteProfile').disabled = alle.length <= 1;
  myterraAuswahlLaden();
}

/* Traktoren und Geräte, wie MyTerra sie nennt - als Vorschlag beim Tippen, damit
   der Name genau stimmt und die Arbeit in MyTerra von selbst zugeordnet wird. */
async function myterraAuswahlLaden() {
  const antwort = await fetch('/api/myterra/maschinen').then((r) => r.json()).catch(() => null);
  if (!antwort) return;
  [['mtTraktoren', antwort.traktoren || []], ['mtGeraete', antwort.geraete || []]].forEach(([id, namen]) => {
    const liste = el(id);
    liste.replaceChildren(...namen.map((name) => Object.assign(document.createElement('option'), { value: name })));
  });
}

function fillProfileFrom(profile) {
  formularSauber('profileForm');
  document.querySelectorAll('#profileForm input, #profileForm select').forEach((input) => {
    if (profile[input.name] === undefined) return;
    if (input.type === 'checkbox') input.checked = !!profile[input.name];
    else input.value = profile[input.name];
  });
}

el('profileSelect').onchange = async (event) => {
  const antwort = await api('POST', `/api/profiles/${event.target.value}/select`);
  if (antwort) { fillProfileFrom(antwort.data); toast(`Gerät: ${antwort.data.name}`); }
};

el('btnNewProfile').onclick = async () => {
  const name = prompt('Bezeichnung des neuen Geräts, am besten wie in MyTerra (die aktuellen Werte werden übernommen)', '');
  if (name === null || !name.trim()) return;
  const antwort = await api('POST', '/api/profiles', { name: name.trim() });
  if (antwort) {
    fillProfileFrom(antwort.data);
    renderProfiles(await (await fetch('/api/profiles')).json());
    toast(`Gerät "${antwort.data.name}" angelegt und gewählt`);
  }
};

el('btnDeleteProfile').onclick = async () => {
  const select = el('profileSelect');
  const option = select.options[select.selectedIndex];
  if (!option || !confirm(`Gerät "${option.textContent}" löschen?`)) return;
  if (await api('DELETE', `/api/profiles/${option.value}`)) {
    renderProfiles(await (await fetch('/api/profiles')).json());
    setTimeout(fillProfile, 300);
    toast('Gerät gelöscht');
  }
};

fahrzeugWahlFuellen();
el('fahrzeugDeckkraft').onchange = () => {
  fahrzeugDeckkraft = parseFloat(el('fahrzeugDeckkraft').value) || 1;
  merken('fahrzeugDeckkraft', String(fahrzeugDeckkraft));
  zeichnenAnfordern();
};

el('btnSaveProfile').onclick = async () => {
  const payload = {};
  document.querySelectorAll('#profileForm input, #profileForm select').forEach((input) => {
    payload[input.name] = input.type === 'checkbox' ? input.checked
      : input.type === 'number' ? parseFloat(input.value) : input.value;
  });
  if (await api('POST', '/api/profile', payload)) {
    formularSauber('profileForm');
    toast('Gerät übernommen');
    renderProfiles(await (await fetch('/api/profiles')).json());
  }
};

/* Traktoren: getrennt vom Gerät gewählt. Antenne und Lenkung gehören dem Traktor -
   einmal eingestellt, gelten sie für jedes Gerät, das er zieht. */
let traktorenListe = [];

function fillTraktorForm(traktor, erzwingen = false) {
  if (!traktor || (formularOffen('traktorForm') && !erzwingen)) return;
  formularSauber('traktorForm');
  document.querySelectorAll('#traktorForm input, #traktorForm select').forEach((input) => {
    if (traktor[input.name] !== undefined) input.value = traktor[input.name];
  });
}

async function renderTraktoren() {
  const liste = await fetch('/api/traktoren').then((r) => r.json()).catch(() => null);
  if (!Array.isArray(liste)) return;
  traktorenListe = liste;
  const select = el('traktorSelect');
  select.replaceChildren(...liste.map((t) => Object.assign(document.createElement('option'), {
    value: t.id, textContent: t.name, selected: !!t.aktiv,
  })));
  el('btnDeleteTraktor').disabled = liste.length <= 1;
  fillTraktorForm(liste.find((t) => t.aktiv));
  // Mit dem Mähdrescher kommt sein Schneidwerk (und zurück das letzte Gerät).
  const vorher = (geraeteAlle.find((p) => p.aktiv) || {}).id;
  const geraete = await fetch('/api/profiles').then((r) => r.json()).catch(() => null);
  if (!Array.isArray(geraete)) return;
  renderProfiles(geraete);
  const jetzt = geraete.find((p) => p.aktiv);
  if (jetzt && vorher !== undefined && jetzt.id !== vorher) fillProfileFrom(jetzt);
}

el('traktorSelect').onchange = async (event) => {
  const antwort = await api('POST', `/api/traktoren/${event.target.value}/select`);
  if (antwort) { formularSauber('traktorForm'); await renderTraktoren(); toast(`Traktor: ${antwort.data.name}`); }
};

el('btnNewTraktor').onclick = async () => {
  const name = prompt('Name des Traktors, am besten wie in MyTerra (Antenne und Lenkung des gewählten werden übernommen)', '');
  if (name === null || !name.trim()) return;
  const antwort = await api('POST', '/api/traktoren', { name: name.trim() });
  if (antwort) { formularSauber('traktorForm'); await renderTraktoren(); toast(`Traktor "${antwort.data.name}" angelegt und gewählt`); }
};

el('btnDeleteTraktor').onclick = async () => {
  const option = el('traktorSelect').selectedOptions[0];
  if (!option || !confirm(`Traktor "${option.textContent}" löschen?`)) return;
  if (await api('DELETE', `/api/traktoren/${option.value}`)) {
    await renderTraktoren();
    toast('Traktor gelöscht');
  }
};

el('btnSaveTraktor').onclick = async () => {
  const payload = {};
  document.querySelectorAll('#traktorForm input, #traktorForm select').forEach((input) => {
    payload[input.name] = input.type === 'number' ? parseFloat(input.value) : input.value;
  });
  if (await api('POST', '/api/traktor', payload)) {
    formularSauber('traktorForm');
    await renderTraktoren();
    toast('Traktor übernommen');
  }
};

/* ------------------------------------------------------------ Lenkregler
 * Die Werte wie im Cerea-Dialog (cerearegler.py): − und + in festen Schritten,
 * die beiden Aggressivitäten zusätzlich als Schieber. Sie gehören zum gewählten
 * Traktor; „OK“ schaltet die Regelart Cerea ein. Oben live, wo der Punkt liegt. */
const CEREA_GRUNDWERTE = { cerea_lenk_aggr: 100, cerea_auffahr_aggr: 100, cerea_l: 0.10,
  cerea_i: 0.09, cerea_i2: 0.06, cerea_d: 0.45 };
let reglerGeaendert = false;
const reglerZeilen = [...document.querySelectorAll('#reglerWerte .regler-zeile')];

function reglerWert(zeile) { return parseFloat(zeile.dataset.wert); }

function reglerSetzen(zeile, wert) {
  const unten = parseFloat(zeile.dataset.min), oben = parseFloat(zeile.dataset.max);
  const stellen = parseInt(zeile.dataset.stellen, 10);
  const sauber = Math.min(oben, Math.max(unten, Number(wert.toFixed(stellen))));
  zeile.dataset.wert = String(sauber);
  zeile.querySelector('.regler-wert').textContent = sauber.toLocaleString('de-DE',
    { minimumFractionDigits: stellen, maximumFractionDigits: stellen });
  const schieber = zeile.querySelector('.regler-schieber');
  if (schieber) schieber.value = String(sauber);
  zeile.querySelector('.regler-minus').disabled = sauber <= unten;
  zeile.querySelector('.regler-plus').disabled = sauber >= oben;
}

function reglerAendern(zeile, wert) {
  reglerSetzen(zeile, wert);
  reglerGeaendert = true;
  zeile.classList.add('geaendert');
}

function renderLenkregler(erzwingen = false) {
  const traktor = traktorenListe.find((t) => t.aktiv) || (state.live && state.live.profile) || {};
  if (!reglerGeaendert || erzwingen) {
    reglerZeilen.forEach((zeile) => {
      const wert = traktor[zeile.dataset.feld];
      reglerSetzen(zeile, Number.isFinite(wert) ? wert : CEREA_GRUNDWERTE[zeile.dataset.feld]);
      zeile.classList.remove('geaendert');
    });
    reglerGeaendert = false;
  }
  const art = { cerea: 'Cerea', voraus: 'Vorausschauend', stanley: 'Stanley' }[traktor.regelart] || traktor.regelart || '–';
  // Ohne Traktorliste steht im Zustand das Gespann - dessen Name ist der des Geräts.
  const name = traktorenListe.some((t) => t.aktiv) ? traktor.name : 'Traktor';
  el('reglerArt').textContent = `${name} · Regelart jetzt: ${art}`;
  el('btnReglerStanley').hidden = traktor.regelart !== 'cerea';
  el('reglerHinweis').textContent = traktor.regelart === 'cerea' ? ''
    : 'Diese Werte wirken erst mit der Regelart Cerea – „OK“ schaltet sie für diesen Traktor ein.';
}

/* Die Zeile oben: Abweichung, Tempo, Punkt voraus, I + I2 - bei jedem Zustand neu. */
function reglerLiveZeigen(s) {
  if (el('sheet').hidden || el('reglerLive').offsetParent === null || !s) return;
  const g = s.guidance || {};
  const a = s.spurausgleich || {};
  const zahl = (x, n) => Number(x).toLocaleString('de-DE', { minimumFractionDigits: n, maximumFractionDigits: n });
  el('reglerAbw').textContent = g.active && Number.isFinite(g.cross_track_cm) ? `${zahl(g.cross_track_cm, 1)} cm` : '–';
  el('reglerTempo').textContent = s.fix && Number.isFinite(s.fix.speed_kmh) ? `${zahl(s.fix.speed_kmh, 1)} km/h` : '–';
  el('reglerPunkt').textContent = Number.isFinite(a.punkt_voraus_m) ? `${zahl(a.punkt_voraus_m, 2)} m` : '–';
  el('reglerIntegral').textContent = Number.isFinite(a.i_deg) ? `${zahl(a.i_deg + a.i2_deg, 2)}°` : '–';
}

reglerZeilen.forEach((zeile) => {
  const schritt = parseFloat(zeile.dataset.schritt);
  zeile.querySelector('.regler-minus').onclick = () => reglerAendern(zeile, reglerWert(zeile) - schritt);
  zeile.querySelector('.regler-plus').onclick = () => reglerAendern(zeile, reglerWert(zeile) + schritt);
  const schieber = zeile.querySelector('.regler-schieber');
  if (schieber) schieber.oninput = () => reglerAendern(zeile, parseFloat(schieber.value));
});

el('btnReglerGrund').onclick = () => {
  reglerZeilen.forEach((zeile) => reglerAendern(zeile, CEREA_GRUNDWERTE[zeile.dataset.feld]));
};

el('btnReglerOk').onclick = async () => {
  const payload = { regelart: 'cerea' };
  reglerZeilen.forEach((zeile) => { payload[zeile.dataset.feld] = reglerWert(zeile); });
  if (await api('POST', '/api/traktor', payload)) {
    reglerGeaendert = false;
    await renderTraktoren();
    renderLenkregler(true);
    toast('Lenkregler übernommen – Regelart Cerea');
  }
};

el('btnReglerStanley').onclick = async () => {
  if (await api('POST', '/api/traktor', { regelart: 'stanley' })) {
    await renderTraktoren();
    renderLenkregler(true);
    toast('Regelart wieder Stanley');
  }
};

/* ------------------------------------------------------------ Vorgewende */

function fillHeadland() {
  const headland = state.live && state.live.headland;
  if (!headland) return;
  const offen = formularOffen('headlandForm') || formularOffen('headlandSwitches');
  if (!offen) document.querySelectorAll('#headlandForm input, #headlandForm select, ' +
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
  if (await api('POST', '/api/headland', payload)) {
    formularSauber('headlandForm');
    formularSauber('headlandSwitches');
    toast('Vorgewende übernommen');
  }
};

/* Reiter Saatplan: Vorgabe wählen, planen, Ergebnis und Konflikte lesen. */
async function renderSaatplan() {
  const feld = state.live && state.live.field;
  saat.seiteFeld = feld ? feld.id : null;   // für welches Feld die Seite gefüllt ist
  el('saatplanErgebnis').hidden = true;
  if (!feld) { el('saatplanFeld').textContent = 'Kein Feld geladen – erst unter Felder ein Feld laden.'; return; }
  el('saatplanFeld').textContent = `Schlag: ${feld.name}`;
  const daten = await saatplanLaden(feld.id);
  if (!daten) { el('saatplanFeld').textContent = `${feld.name}: keine Grenze – erst umfahren oder Shapefile einlesen.`; return; }
  saat.optionen = daten.optionen;
  const wahl = el('saatplanStart');
  wahl.innerHTML = '';
  daten.optionen.forEach((o) => {
    const option = document.createElement('option');
    option.value = o.id; option.textContent = o.text;
    wahl.appendChild(option);
  });
  const vorgabe = daten.vorgabe || {};
  if (vorgabe.start) wahl.value = vorgabe.start;
  const form = el('saatplanForm');
  ['breite_m', 'fahrgasse_m', 'randspuren'].forEach((name) => {
    if (vorgabe[name] != null) form.querySelector(`[name="${name}"]`).value = vorgabe[name];
  });
  zeigeSaatplan(daten.plan);
}

/* Vorschau unter „Vorgabe“: der Schlag von oben, gedreht bis die Startkante waagrecht liegt
 * und die lange Seite die Breite füllt. Eigene, feste Farben - nicht die Kartenfarben: war die
 * Grenze dort blau, verschwand die blaue Startkante darin (24.10.2026). Die Startecke trägt
 * ein „A“, die Gegenseite ein „B“; das Vorgewende ist ein getöntes Band. */
const VORSCHAU = { start: '#1f6fff', a: 'rgba(214, 140, 20, 0.9)', b: '#2fae63', gasse: '#e5484d',
                   band: 'rgba(255, 176, 32, 0.16)', feld: 'rgba(72, 204, 119, 0.12)' };

function saatVorschau() {
  const leinwand = el('saatplanVorschau');
  const feld = state.live && state.live.field;
  const legende = el('saatplanLegende');
  // Dieselbe Grenze, mit der geplant wird: eine Arbeitsgrenze geht vor.
  const umriss = feld && (feld.working_boundary && feld.working_boundary.length >= 3 ? feld.working_boundary : feld.boundary);
  const grenze = umriss && umriss.length >= 3 ? umriss : null;
  const breite = leinwand.clientWidth;
  if (!breite) return;
  if (!grenze) { leinwand.style.height = '120px'; legende.textContent = 'Kein Feld mit Grenze geladen.'; return; }
  const wahl = (saat.optionen || []).find((o) => o.id === el('saatplanStart').value);
  const drehen = (p, w) => [p[0] * Math.cos(w) + p[1] * Math.sin(w), -p[0] * Math.sin(w) + p[1] * Math.cos(w)];
  const ausdehnung = (w) => {
    const q = grenze.map((p) => drehen(p, w));
    return [Math.max(...q.map((p) => p[0])) - Math.min(...q.map((p) => p[0])),
      Math.max(...q.map((p) => p[1])) - Math.min(...q.map((p) => p[1]))];
  };
  // Startkante waagrecht; liegt der Schlag dann hochkant, eine Vierteldrehung dazu. Dazu
  // die Drehung von Hand (↺ ↻, in Vierteln), „Auto“ setzt sie zurück.
  // Antippen und Drehen von Hand halten die Ansicht fest (saat.ansicht): sonst drehte sich
  // das Bild nach jeder Wahl, und das zweite Antippen träfe eine andere Kante.
  let winkel = wahl && wahl.von && wahl.nach ? Math.atan2(wahl.nach[1] - wahl.von[1], wahl.nach[0] - wahl.von[0]) : 0;
  let [w0, h0] = ausdehnung(winkel);
  if (h0 > w0 * 1.1) winkel += Math.PI / 2;
  if (saat.ansicht != null) winkel = saat.ansicht;
  [w0, h0] = ausdehnung(winkel);
  el('btnVorschauAuto').classList.toggle('an', saat.ansicht == null);
  // Die Höhe folgt der Form: ein langer Schlag bekommt kein leeres Feld darüber und darunter.
  // Oben mehr Rand: dort liegen die Knöpfe.
  const rand = 26, oben = 58;
  const hoehe = Math.round(Math.max(190, Math.min(340, (breite - 2 * rand) * (h0 / Math.max(1, w0)) + rand + oben)));
  leinwand.style.height = `${hoehe}px`;
  const k = window.devicePixelRatio || 1;
  leinwand.width = breite * k; leinwand.height = hoehe * k;
  const c = leinwand.getContext('2d');
  c.setTransform(k, 0, 0, k, 0, 0);
  c.clearRect(0, 0, breite, hoehe);
  const gedreht = grenze.map((p) => drehen(p, winkel));
  const xs = gedreht.map((p) => p[0]), ys = gedreht.map((p) => p[1]);
  const massstab = Math.min((breite - 2 * rand) / Math.max(1, w0), (hoehe - rand - oben) / Math.max(1, h0));
  const mx = (Math.max(...xs) + Math.min(...xs)) / 2, my = (Math.max(...ys) + Math.min(...ys)) / 2;
  const mitteY = oben + (hoehe - rand - oben) / 2;
  const px = (p) => { const [u, v] = drehen(p, winkel); return [breite / 2 + (u - mx) * massstab, mitteY - (v - my) * massstab]; };
  // Für das Antippen: jede wählbare Kante als Linie in Bildpunkten, mit ihren zwei Startecken.
  const kanten = new Map();
  (saat.optionen || []).forEach((o) => {
    if (!kanten.has(o.kante)) kanten.set(o.kante, { kante: o.kante, linie: (o.punkte || [o.von, o.nach]).map(px), optionen: [] });
    kanten.get(o.kante).optionen.push(o);
  });
  saat.vorschau = { px, kanten: [...kanten.values()], winkel };
  const pfad = (punkte, zu) => {
    punkte.forEach((p, i) => { const [x, y] = px(p); i ? c.lineTo(x, y) : c.moveTo(x, y); });
    if (zu) c.closePath();
  };
  const tinte = getComputedStyle(document.body).getPropertyValue('--text').trim() || '#e8efe9';
  c.lineJoin = 'round'; c.lineCap = 'round';
  c.beginPath(); pfad(grenze, true); c.fillStyle = VORSCHAU.feld; c.fill();
  const plan = saat.plan;
  const teile = [];
  if (plan) {
    // Vorgewende als Band: zwischen Grenze und innerster Runde getönt.
    const innerste = Math.max(0, ...plan.ringe.map((r) => r.runde));
    c.beginPath(); pfad(grenze, true);
    plan.ringe.filter((r) => r.runde === innerste).forEach((r) => pfad(r.punkte, true));
    c.fillStyle = VORSCHAU.band; c.fill('evenodd');
    plan.ringe.filter((r) => r.fahrgasse).forEach((r) => {
      c.beginPath(); pfad(r.punkte, true);
      c.setLineDash([6, 4]); c.strokeStyle = VORSCHAU.gasse; c.lineWidth = 1.6; c.stroke(); c.setLineDash([]);
    });
    plan.spuren.forEach((sp) => sp.stuecke.forEach(([p0, p1]) => {
      const [x0, y0] = px(p0), [x1, y1] = px(p1);
      c.beginPath(); c.moveTo(x0, y0); c.lineTo(x1, y1);
      c.strokeStyle = sp.fahrgasse ? VORSCHAU.gasse : sp.von_b ? VORSCHAU.b : VORSCHAU.a;
      c.lineWidth = sp.fahrgasse ? 2.2 : 1; c.stroke();
    }));
    teile.push(['Säspuren', VORSCHAU.a], ['Fahrgassen', VORSCHAU.gasse]);
    if (plan.spuren.some((sp) => sp.von_b)) teile.push(['von B', VORSCHAU.b]);
    teile.push(['Vorgewende', 'rgba(255, 176, 32, 0.55)']);
  }
  c.beginPath(); pfad(grenze, true);
  c.strokeStyle = tinte; c.globalAlpha = 0.8; c.lineWidth = 2; c.stroke(); c.globalAlpha = 1;
  teile.unshift(['Grenze', tinte]);
  // Kante unter der Maus: hell hervorgehoben, damit man sieht, was ein Klick wählt.
  const unter = saat.vorschau.kanten.find((x) => x.kante === saat.hoverKante);
  if (unter) {
    c.beginPath(); unter.linie.forEach(([x, y], i) => (i ? c.lineTo(x, y) : c.moveTo(x, y)));
    c.strokeStyle = 'rgba(31, 111, 255, 0.45)'; c.lineWidth = 10; c.stroke();
  }
  const marke = (x, y, text, farbe) => {
    c.beginPath(); c.arc(x, y, 11, 0, Math.PI * 2);
    c.fillStyle = farbe; c.fill(); c.lineWidth = 2.5; c.strokeStyle = '#fff'; c.stroke();
    c.font = '700 12px "IBM Plex Sans", sans-serif'; c.textAlign = 'center'; c.textBaseline = 'middle';
    c.fillStyle = '#fff'; c.fillText(text, x, y + 0.5);
  };
  if (plan && plan.fuellstreifen && plan.fuellstreifen.punkte && plan.fuellstreifen.punkte.length >= 2) {
    const q = plan.fuellstreifen.punkte;
    const [x, y] = px([(q[0][0] + q[q.length - 1][0]) / 2, (q[0][1] + q[q.length - 1][1]) / 2]);
    marke(x, y, 'B', VORSCHAU.b);
  }
  // Startkante: hell unterlegt, kräftig blau, Pfeile in Fahrtrichtung, „A“ an der Startecke.
  if (wahl && wahl.von && wahl.nach) {
    const [x0, y0] = px(wahl.von), [x1, y1] = px(wahl.nach);
    c.beginPath(); c.moveTo(x0, y0); c.lineTo(x1, y1);
    c.strokeStyle = 'rgba(255, 255, 255, 0.9)'; c.lineWidth = 9; c.stroke();
    c.strokeStyle = VORSCHAU.start; c.lineWidth = 5; c.stroke();
    const l = Math.hypot(x1 - x0, y1 - y0) || 1, ux = (x1 - x0) / l, uy = (y1 - y0) / l;
    for (let t = 34; t < l - 14; t += 46) {
      const sx = x0 + ux * t, sy = y0 + uy * t;
      c.beginPath();
      c.moveTo(sx - ux * 4 - uy * 4, sy - uy * 4 + ux * 4); c.lineTo(sx + ux * 2, sy + uy * 2);
      c.lineTo(sx - ux * 4 + uy * 4, sy - uy * 4 - ux * 4);
      c.strokeStyle = '#fff'; c.lineWidth = 2; c.stroke();
    }
    marke(x0, y0, 'A', VORSCHAU.start);
    teile.unshift(['Startkante', VORSCHAU.start]);
  }
  // Nordpfeil unten rechts in einem kleinen Kreis - dort liegt selten eine Kante.
  const [nx, ny] = drehen([0, 1], winkel);
  c.save(); c.translate(breite - 20, hoehe - 20);
  c.beginPath(); c.arc(0, 0, 14, 0, Math.PI * 2);
  c.fillStyle = 'rgba(0, 0, 0, 0.18)'; c.fill();
  c.rotate(Math.atan2(nx, ny));
  c.beginPath(); c.moveTo(0, -10); c.lineTo(5, 4); c.lineTo(0, 1); c.lineTo(-5, 4); c.closePath();
  c.fillStyle = VORSCHAU.gasse; c.fill();
  c.restore();
  c.font = '700 10px "IBM Plex Sans", sans-serif'; c.textAlign = 'center'; c.textBaseline = 'middle';
  c.fillStyle = tinte; c.fillText('N', breite - 42, hoehe - 20);
  legende.replaceChildren(...teile.map(([text, farbe]) => {
    const eintrag = document.createElement('span');
    const strich = document.createElement('i');
    strich.style.background = farbe;
    eintrag.append(strich, text);
    return eintrag;
  }));
}
el('saatplanStart').addEventListener('change', saatVorschau);

/* Vorschau bedienen: Kante antippen wählt sie (Start am näheren Ende), nochmal antippen dreht
 * die Richtung; danach wird sofort geplant. ↺ ↻ drehen das Bild, „Auto“ richtet es wieder aus. */
function vorschauAbstand(p, linie) {
  let best = Infinity;
  for (let i = 0; i + 1 < linie.length; i++) {
    const [ax, ay] = linie[i], [bx, by] = linie[i + 1];
    const dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy;
    const t = l2 > 0 ? Math.max(0, Math.min(1, ((p[0] - ax) * dx + (p[1] - ay) * dy) / l2)) : 0;
    best = Math.min(best, Math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy)));
  }
  return best;
}

function vorschauKanteBei(p, fang) {
  const v = saat.vorschau;
  if (!v) return null;
  let treffer = null, best = fang;
  v.kanten.forEach((k) => { const d = vorschauAbstand(p, k.linie); if (d < best) { best = d; treffer = k; } });
  return treffer;
}

function vorschauStartSetzen(option) {
  if (!option || el('saatplanStart').value === option.id) return;
  if (saat.vorschau) saat.ansicht = saat.vorschau.winkel;   // Ansicht bleibt, wie sie ist
  el('saatplanStart').value = option.id;
  saatVorschau();
  clearTimeout(saat.planTakt);
  // Kurz warten: wer zweimal tippt (Richtung umdrehen), soll nicht zweimal rechnen lassen.
  saat.planTakt = setTimeout(() => el('btnSaatplanPlanen').click(), 350);
}

(function vorschauBedienung() {
  const leinwand = el('saatplanVorschau');
  const ort = (e) => { const r = leinwand.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
  leinwand.addEventListener('click', (e) => {
    const p = ort(e);
    const kante = vorschauKanteBei(p, 24);
    if (!kante) return;
    const jetzt = el('saatplanStart').value;
    const wahl = kante.optionen.some((o) => o.id === jetzt)
      ? kante.optionen.find((o) => o.id !== jetzt)
      : kante.optionen.reduce((a, b) => {
        const da = Math.hypot(saat.vorschau.px(a.von)[0] - p[0], saat.vorschau.px(a.von)[1] - p[1]);
        const db = Math.hypot(saat.vorschau.px(b.von)[0] - p[0], saat.vorschau.px(b.von)[1] - p[1]);
        return db < da ? b : a;
      });
    vorschauStartSetzen(wahl);
  });
  leinwand.addEventListener('pointermove', (e) => {
    if (e.pointerType !== 'mouse') return;
    const kante = vorschauKanteBei(ort(e), 18);
    const name = kante ? kante.kante : null;
    if (name !== saat.hoverKante) { saat.hoverKante = name; saatVorschau(); }
  });
  leinwand.addEventListener('pointerleave', () => { if (saat.hoverKante) { saat.hoverKante = null; saatVorschau(); } });
  const drehen = (viertel) => {
    if (!saat.vorschau) return;
    saat.ansicht = saat.vorschau.winkel + viertel * Math.PI / 2;
    saatVorschau();
  };
  el('btnVorschauLinks').onclick = () => drehen(1);
  el('btnVorschauRechts').onclick = () => drehen(-1);
  el('btnVorschauAuto').onclick = () => { saat.ansicht = null; saatVorschau(); };
  el('btnVorschauRichtung').onclick = () => {
    const jetzt = (saat.optionen || []).find((o) => o.id === el('saatplanStart').value);
    if (!jetzt) return;
    vorschauStartSetzen((saat.optionen || []).find((o) => o.kante === jetzt.kante && o.id !== jetzt.id));
  };
})();
// Neu zeichnen, sobald sich die Breite ändert - auch wenn rechts die Ergebnis-Karte
// erscheint und die Spalte schmaler wird; sonst würde das alte Bild gestaucht.
if (window.ResizeObserver) {
  let breiteVorher = 0;
  new ResizeObserver(() => {
    const b = el('saatplanVorschau').clientWidth;
    if (b && b !== breiteVorher) { breiteVorher = b; saatVorschau(); }
  }).observe(el('saatplanVorschau'));
} else {
  window.addEventListener('resize', () => { if (!el('saatplanVorschau').closest('[hidden]')) saatVorschau(); });
}

function zeigeSaatplan(plan) {
  el('saatplanErgebnis').hidden = !plan;
  saatVorschau();
  if (!plan) return;
  const zahl = (x) => Number(x).toLocaleString('de-DE');
  el('saatplanZusammenfassung').textContent =
    `${plan.startwahl}. Vorgewende ${plan.vorgewende_runden} Runden (${zahl(plan.vorgewende_m)} m), ` +
    `Hauptfeld ${plan.anzahl_spuren} Säspuren, davon ${plan.anzahl_fahrgassen} Fahrgassen im Abstand von ` +
    `${zahl(plan.fahrgasse_m)} m. Fahrstrecke rund ${zahl(plan.strecke_km)} km.`;
  const host = el('saatplanKonflikte');
  host.innerHTML = '';
  if (plan.reihenfolge && plan.reihenfolge.length) {
    const liste = document.createElement('ol');
    plan.reihenfolge.forEach((schritt) => {
      const li = document.createElement('li'); li.textContent = schritt; liste.appendChild(li);
    });
    host.appendChild(liste);
  }
  if (plan.fuellstreifen) {
    const box = document.createElement('div');
    box.className = 'saat-fuell';
    const text = document.createElement('p');
    text.textContent = plan.fuellstreifen.text;
    box.appendChild(text);
    const liste = document.createElement('ul');
    plan.fuellstreifen.anpassung.forEach((a) => {
      const li = document.createElement('li'); li.textContent = a; liste.appendChild(li);
    });
    box.appendChild(liste);
    host.appendChild(box);
  }
  if (!plan.konflikte.length) {
    const ok = document.createElement('p');
    ok.className = 'saat-ok';
    ok.textContent = `Die Randvorgabe passt an jeder Feldgrenze: ${plan.randspuren} Säspuren, dann die Fahrgasse.`;
    host.appendChild(ok);
  }
  plan.konflikte.forEach((k) => {
    const box = document.createElement('div');
    box.className = 'saat-konflikt';
    const text = document.createElement('p');
    text.textContent = k.text;
    box.appendChild(text);
    const liste = document.createElement('ul');
    (k.anpassung || []).forEach((a) => {
      const li = document.createElement('li'); li.textContent = a; liste.appendChild(li);
    });
    box.appendChild(liste);
    host.appendChild(box);
  });
  (plan.hinweise || []).forEach((h) => {
    const p = document.createElement('p'); p.className = 'note'; p.textContent = h; host.appendChild(p);
  });
}

el('btnSaatplanPlanen').onclick = async () => {
  const feld = state.live && state.live.field;
  if (!feld) { toast('Erst ein Feld laden', true); return; }
  const form = el('saatplanForm');
  const zahl = (name) => parseFloat(String(form.querySelector(`[name="${name}"]`).value).replace(',', '.'));
  const antwort = await api('POST', `/api/fields/${feld.id}/saatplan`, {
    start: el('saatplanStart').value, breite_m: zahl('breite_m'),
    fahrgasse_m: zahl('fahrgasse_m'), randspuren: Math.round(zahl('randspuren')),
  });
  if (!antwort) return;
  saat.feld = feld.id; saat.plan = antwort.data; saat.zeigen = true;
  el('saatplanZeigen').checked = true;
  zeigeSaatplan(antwort.data);
  zeichnenAnfordern();
  toast(antwort.data.konflikte.length
    ? `Geplant – ${antwort.data.konflikte.length} Konflikt${antwort.data.konflikte.length > 1 ? 'e' : ''}`
    : 'Geplant – passt an allen Rändern');
};

el('btnSaatplanAbHier').onclick = async () => {
  // Werte aus dem Formular mitgeben, damit z. B. 3 m Sämaschine gilt.
  const form = el('saatplanForm');
  const zahl = (name) => parseFloat(String(form.querySelector(`[name="${name}"]`).value).replace(',', '.'));
  const antwort = await api('POST', '/api/saatplan/hier', {
    breite_m: zahl('breite_m'), fahrgasse_m: zahl('fahrgasse_m'), randspuren: Math.round(zahl('randspuren')),
  });
  if (!antwort) return;
  saat.feld = state.live.field.id; saat.plan = antwort.data; saat.zeigen = true;
  el('saatplanZeigen').checked = true;
  zeigeSaatplan(antwort.data);
  zeichnenAnfordern();
  toast(`Geplant ab ${antwort.data.startwahl}`);
};

el('saatplanZeigen').onchange = (event) => { saat.zeigen = event.target.checked; zeichnenAnfordern(); };

el('btnSaatplanUebernehmen').onclick = async () => {
  const feld = state.live && state.live.field;
  if (!feld) return;
  const jahr = await erntejahrWaehlen();
  if (jahr === null) return;
  const antwort = await api('POST', `/api/fields/${feld.id}/saatplan/uebernehmen`, { saison: jahr });
  if (antwort) {
    toast(saat.plan && saat.plan.saisonspur_b
      ? `Saisonspur „${antwort.data.name}“ aktiv – an B die Spur „… B“ laden`
      : `Saisonspur „${antwort.data.name}“ aktiv`);
  }
};

el('btnSaatplanLoeschen').onclick = async () => {
  const feld = state.live && state.live.field;
  if (!feld) return;
  if (await api('DELETE', `/api/fields/${feld.id}/saatplan`)) {
    saat.plan = null; zeigeSaatplan(null); zeichnenAnfordern(); toast('Plan verworfen');
  }
};

el('autoSections').onchange = (event) =>
  api('POST', '/api/sections/auto', { enabled: event.target.checked });

/* Aktualisierung: Paket einspielen, aus dem Repo holen, neu starten, zurück. */
let updateVersion = '';
async function renderUpdate() {
  try {
    const stand = await (await fetch('/api/update')).json();
    updateVersion = stand.version;
    el('updateStand').textContent =
      `Version ${stand.version}` + (stand.commit ? ` · Stand ${stand.commit}` : '') +
      (stand.eingespielt ? ` · eingespielt ${stand.eingespielt}` : '') +
      (stand.git ? ' · aus Git-Klon' : '') + ` · ${stand.wurzel}`;
    el('btnUpdateGit').hidden = !stand.git;
    el('btnUpdateZurueck').hidden = !(stand.sicherungen && stand.sicherungen.length);
    // Im installierten Programm gibt es nur das Online-Update und die Setup-Datei.
    el('updatePaketZeile').hidden = !!stand.als_programm;
    el('updatePaketNote').hidden = !!stand.als_programm;
    if (stand.sicherungen && stand.sicherungen.length) {
      el('btnUpdateZurueck').textContent = `Vorigen Stand zurückholen (${stand.sicherungen[0].replace('sicherung-', '')})`;
    }
  } catch (error) {
    el('updateStand').textContent = 'Stand nicht abrufbar';
  }
}

/* Datenpaket: Master -> Arbeiter als Datei. */
function renderDatenRolle() {
  const system = state.live && state.live.system;
  if (!system) return;
  const adressen = (system.adressen || []).join('  ·  ');
  el('datenRolle').textContent = system.role === 'master'
    ? 'Dieses Gerät ist der Master.' + (adressen ? ` Die Arbeiter tragen als Adresse des Masters ein: ${adressen}` : '') +
      (system.arbeitercode ? `\nArbeitercode: ${system.arbeitercode}` : '\nNoch kein Arbeitercode – nötig für den Abgleich von unterwegs (Einstellungen → Netzwerk).')
    : `Dieses Gerät ist ein Arbeiter${system.sync && system.sync.status ? ' · Abgleich: ' + system.sync.status : ''}.`;
}

el('btnDatenExport').onclick = async () => {
  const mit = el('datenMitArbeiten').checked ? 1 : 0;
  const antwort = await fetch(`/api/daten/export?mit_arbeiten=${mit}`);
  if (!antwort.ok) { toast('Datenpaket konnte nicht erstellt werden', true); return; }
  const blob = await antwort.blob();
  const name = (antwort.headers.get('Content-Disposition') || '').match(/filename="([^"]+)"/);
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = name ? name[1] : 'agripilot-daten.zip';
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 10000);
  el('datenInfo').textContent = `${link.download} erstellt (${(blob.size / 1024).toFixed(0)} KB) – im Download-Ordner.`;
};

el('datenImport').onchange = async (event) => {
  const datei = event.target.files[0];
  event.target.value = '';
  if (!datei) return;
  const info = el('datenInfo');
  info.textContent = `${datei.name} wird eingespielt …`;
  const antwort = await api('POST', '/api/daten/import', { zip: await dateiAlsBase64(datei) });
  if (!antwort) { info.textContent = ''; return; }
  const e = antwort.data;
  const m = e.maschinen.neu + e.maschinen.aktualisiert;
  info.textContent = `Eingespielt${e.von ? ' von ' + e.von : ''}: ${e.felder} Felder, ${e.spuren} Spuren, ` +
    `${m} Maschinen${e.arbeiten ? ', ' + e.arbeiten + ' Arbeiten' : ''}.`;
  toast('Datenpaket eingespielt');
  refreshLists();
};

/* Online aktualisieren: suchen ist harmlos, einspielen verlangt Stand und ausgeschaltete Lenkung. */
let onlineNeu = null;

async function onlineSuchen(still) {
  const info = el('updateOnlineInfo');
  if (!still) info.textContent = 'Suche …';
  const antwort = await api('GET', '/api/update/online');
  if (!antwort) { info.textContent = ''; return; }
  onlineNeu = antwort.verfuegbar ? antwort.neu : null;
  el('btnUpdateOnline').hidden = !(antwort.verfuegbar && antwort.als_programm);
  if (antwort.fehler) info.textContent = antwort.fehler;
  else if (antwort.verfuegbar) {
    info.textContent = `Neu: Version ${antwort.neu} (jetzt ${antwort.aktuell}, ${(antwort.groesse / 1e6).toFixed(0)} MB).` +
      (antwort.notizen ? `\n${antwort.notizen}` : '') +
      (antwort.als_programm ? '' : '\nEingespielt wird in dieser Installation per Paket oder aus GitHub.');
  } else info.textContent = `Auf dem neuesten Stand (Version ${antwort.aktuell}).`;
}

el('btnUpdateSuchen').onclick = () => onlineSuchen(false);

async function onlineEinspielen(neu) {
  if (!neu) return;
  if (!confirm(`Auf Version ${neu} aktualisieren?\n\nDie Lenkung muss aus sein und der Traktor stehen. ` +
               `Eine laufende Arbeit wird gesichert und danach fortgesetzt. Das Programm startet ` +
               `dabei neu (eine Minute); geht es schief, wird der vorige Stand wieder eingesetzt.`)) return;
  const info = el('updateOnlineInfo');
  info.textContent = `Version ${neu} wird geladen und geprüft …`;
  toast(`Version ${neu} wird geladen und geprüft …`);
  const antwort = await api('POST', '/api/update/online');
  if (!antwort) { info.textContent = ''; return; }
  if (antwort.data.schon_aktuell) { info.textContent = 'Schon auf dem neuesten Stand.'; return; }
  info.textContent = `Version ${antwort.data.version} wird eingespielt. Das Programm startet neu - ` +
    'diese Seite verbindet sich von selbst wieder (etwa eine Minute).';
  el('btnUpdateHaupt').hidden = true;
  toast('Aktualisierung läuft – Programm startet neu');
}

el('btnUpdateOnline').onclick = () => onlineEinspielen(onlineNeu);
// Der Knopf auf dem Hauptbildschirm: dieselbe Rückfrage, ohne Umweg über das Menü.
el('btnUpdateHaupt').onclick = () => onlineEinspielen(state.live && state.live.system && state.live.system.update_online);

async function dateiAlsBase64(datei) {
  const puffer = await datei.arrayBuffer();
  const bytes = new Uint8Array(puffer);
  let text = '';
  for (let i = 0; i < bytes.length; i += 0x8000) {
    text += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  return btoa(text);
}

el('updatePaket').onchange = async (event) => {
  const datei = event.target.files[0];
  event.target.value = '';
  if (!datei) return;
  if (!confirm(`"${datei.name}" einspielen?\n\nDie Lenkung geht aus, eine laufende Arbeit wird ` +
               `gesichert. Danach ist ein Neustart nötig.`)) return;
  const info = el('updateInfo');
  info.textContent = `${datei.name} wird übertragen und geprüft …`;
  const antwort = await api('POST', '/api/update/paket', { name: datei.name, zip: await dateiAlsBase64(datei) });
  if (!antwort) { info.textContent = ''; return; }
  const e = antwort.data;
  info.textContent = `Eingespielt: Version ${e.version} · Stand ${e.commit} · ${e.dateien} Dateien · ` +
    `${e.requirements}` + (e.warnungen && e.warnungen.length ? ` · ${e.warnungen.join(' · ')}` : '') +
    ' – jetzt Neu starten.';
  toast('Update eingespielt – jetzt neu starten');
  renderUpdate();
};

el('btnUpdateGit').onclick = async () => {
  const info = el('updateInfo');
  info.textContent = 'Hole aus dem Repo …';
  const antwort = await api('POST', '/api/update/git');
  if (!antwort) { info.textContent = ''; return; }
  const e = antwort.data;
  info.textContent = e.geaendert
    ? `Geholt: ${e.vorher} → ${e.nachher} · ${e.requirements} – jetzt Neu starten.`
    : `Schon auf Stand (${e.nachher}).`;
  toast(e.geaendert ? 'Neuer Stand geholt – jetzt neu starten' : 'Schon auf dem neuesten Stand');
  renderUpdate();
};

el('btnUpdateZurueck').onclick = async () => {
  if (!confirm('Den vorigen Stand zurückholen? Der jetzige wird dabei gesichert.')) return;
  const antwort = await api('POST', '/api/update/zurueck', {});
  if (!antwort) return;
  el('updateInfo').textContent = `Zurückgeholt aus ${antwort.data.zurueck_aus} – jetzt Neu starten.`;
  toast('Voriger Stand zurückgeholt – jetzt neu starten');
  renderUpdate();
};

el('btnUpdateNeustart').onclick = async () => {
  if (!confirm('Programm jetzt neu starten? Die Lenkung geht aus, die Anzeige verbindet sich in ' +
               'einigen Sekunden von selbst neu.')) return;
  const antwort = await api('POST', '/api/update/neustart');
  if (!antwort) return;
  toast('Neustart läuft – die Anzeige verbindet sich gleich neu');
  el('sheet').hidden = true;
  // Nach dem Neustart die Seite frisch laden - so kommt auch neues app.js an.
  setTimeout(async () => {
    for (let versuch = 0; versuch < 60; versuch++) {
      await new Promise((r) => setTimeout(r, 1000));
      try {
        const antwort2 = await fetch('/api/update', { cache: 'no-store' });
        if (antwort2.ok) { location.reload(); return; }
      } catch (error) { /* noch nicht da */ }
    }
  }, 2000);
};

/* Gerätesuche: was steckt dran? Ergebnis ist eine Liste und ein Vorschlag für
 * die Einstellungen - derselbe Weg wie beim Tippen von Hand. */
let geraeteVorschlag = null;
el('btnGeraete').onclick = async () => {
  const knopf = el('btnGeraete');
  knopf.disabled = true;
  el('geraeteInfo').textContent = 'Suche läuft – einige Sekunden …';
  el('geraeteList').innerHTML = '';
  el('geraeteRow').hidden = true;
  try {
    const antwort = await fetch('/api/geraete/suchen');
    const daten = await antwort.json();
    if (!antwort.ok) { toast(daten.detail || `Fehler ${antwort.status}`, true); return; }
    renderGeraete(daten);
  } catch (error) {
    toast('Gerät nicht erreichbar', true);
  } finally {
    knopf.disabled = false;
  }
};

function renderGeraete(daten) {
  const host = el('geraeteList');
  host.innerHTML = '';
  const art = { gnss: 'Empfänger', phidget: 'Lenksteuerung', imu: 'Neigungssensor' };
  daten.funde.forEach((fund) => {
    host.appendChild(item({
      active: !!fund.hinweis,
      title: `${art[fund.art] || fund.art} · ${fund.kennung}`,
      sub: fund.name + (fund.hinweis ? ` – ${fund.hinweis}` : ''),
      actions: [],
    }));
  });
  daten.probleme.forEach((problem) => {
    const zeile = document.createElement('p');
    zeile.className = 'note';
    zeile.textContent = problem;
    host.appendChild(zeile);
  });
  el('geraeteInfo').textContent = daten.funde.length
    ? `${daten.funde.length} gefunden in ${daten.dauer_s} s`
    : `Nichts gefunden (${daten.dauer_s} s) – steckt alles, läuft der Brick Daemon?`;
  geraeteVorschlag = Object.keys(daten.vorschlag).length ? daten.vorschlag : null;
  el('geraeteRow').hidden = !geraeteVorschlag;
  if (geraeteVorschlag) {
    el('geraeteVorschlag').textContent = Object.entries(geraeteVorschlag)
      .map(([k, v]) => `${k} = ${v}`).join(' · ');
  }
}

el('btnGeraeteUebernehmen').onclick = async () => {
  if (!geraeteVorschlag) return;
  const antwort = await api('POST', '/api/settings', { aenderungen: geraeteVorschlag });
  if (!antwort) return;
  settings.geladen = false;   // die Einstellungsseite zeigt sonst alte Werte
  const neustart = antwort.data.neustart_noetig || [];
  toast(neustart.length
    ? `Übernommen – Neustart nötig für: ${neustart.join(', ')}`
    : 'Übernommen – Empfänger und Sensor werden neu verbunden');
  el('geraeteRow').hidden = true;
};

/* Diagnosepaket: Zip mit allem zum Nachvollziehen - Download und Ablage im Datenordner. */
el('btnDiagnose').onclick = async () => {
  el('diagnoseInfo').textContent = 'Wird erstellt …';
  try {
    const antwort = await fetch('/api/diagnose', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ notiz: el('diagnoseNotiz').value.trim(), lenklive: lenkAufzeichnung.punkte }),
    });
    if (!antwort.ok) throw new Error(`Fehler ${antwort.status}`);
    const name = (/filename="([^"]+)"/.exec(antwort.headers.get('Content-Disposition') || '') || [])[1]
      || 'AgriPilot-Diagnose.zip';
    const abgelegt = antwort.headers.get('X-Abgelegt');
    const url = URL.createObjectURL(await antwort.blob());
    const a = document.createElement('a');
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
    el('diagnoseInfo').textContent = `${name} – im Ordner Downloads` + (abgelegt ? `, außerdem unter ${abgelegt}` : '');
    el('diagnoseNotiz').value = '';
  } catch (fehler) {
    el('diagnoseInfo').textContent = `Nicht erstellt: ${fehler.message}`;
  }
};

/* Lenkung live: Kurven, Kennzahlen und Befund - im Menü groß, auf der Karte klein. */
const lenkAufzeichnung = new LenkLive.Aufzeichnung();
const LENKLIVE_KARTE = 'agripilot.lenkLiveKarte';

function lenkLiveKarteAn() {
  try { return localStorage.getItem(LENKLIVE_KARTE) === '1'; } catch (error) { return false; }
}

function lenkLiveKarteSetzen(an) {
  try { localStorage.setItem(LENKLIVE_KARTE, an ? '1' : '0'); } catch (error) { /* privat: nur diese Sitzung */ }
  el('lenkLiveFenster').hidden = !an;
  el('btnLenkLiveKarte').textContent = an ? 'Auf der Karte ausblenden' : 'Auf der Karte einblenden';
}

function lenkLiveZahlen(k) {
  if (!k.genug) return '';
  const z = (x, n = 1) => Number(x).toLocaleString('de-DE', { maximumFractionDigits: n, minimumFractionDigits: n });
  const teile = [`effektiv ${z(k.effektiv)} cm`, `im Mittel ${k.mittel >= 0 ? '+' : ''}${z(k.mittel)} cm`,
    `höchstens ${z(k.max, 0)} cm`, `${k.durchgaenge} Seitenwechsel`];
  if (k.nachlauf != null) teile.push(`Rad folgt ${z(k.nachlauf)} s später`);
  if (k.vollAnteil) teile.push(`volle Motorleistung ${z(k.vollAnteil * 100, 0)} %`);
  return `Letzte ${z(k.dauer, 0)} s: ` + teile.join(' · ');
}

function renderLenkLive() {
  if (document.visibilityState !== 'visible') return;
  const imMenue = !el('sheet').hidden && document.querySelector('.tab.active').dataset.tab === 'lenkung';
  const aufKarte = !el('lenkLiveFenster').hidden && el('sheet').hidden;
  if (!imMenue && !aufKarte) return;
  const regelart = state.live && state.live.profile ? state.live.profile.regelart : 'stanley';
  const schritte = state.live && state.live.abstimmung ? state.live.abstimmung.schritte : [];
  const k = lenkAufzeichnung.kennzahlen();
  const b = LenkLive.befund(k, regelart);
  if (imMenue) {
    LenkLive.zeichnen(el('lenkLiveGross'), lenkAufzeichnung,
      { sekunden: Number(el('lenkLiveZeit').value), schritte });
    el('lenkLiveBefund').textContent = b.text;
    el('lenkLiveBefund').dataset.art = b.art;
    el('lenkLiveTipps').replaceChildren(...(b.tipps || []).map((tipp) => {
      const li = document.createElement('li');
      li.textContent = tipp;
      return li;
    }));
    el('lenkLiveZahlen').textContent = lenkLiveZahlen(k);
    const ausgleich = state.live && state.live.spurausgleich;
    const grad = (x) => `${x > 0 ? '+' : ''}${Number(x).toLocaleString('de-DE', { minimumFractionDigits: 1, maximumFractionDigits: 1 })}°`;
    el('lenkLiveArbeit').textContent = [
      state.live && state.live.genauigkeit ? `Laufende Arbeit: ${genauigkeitText(state.live.genauigkeit)}` : '',
      ausgleich && (ausgleich.integral_deg || ausgleich.hang_deg)
        ? `Ausgleich Dauerversatz ${grad(ausgleich.integral_deg)} · Hang ${grad(ausgleich.hang_deg)}` : '',
      ausgleich && ausgleich.pausiert ? 'Ausgleich Dauerversatz pausiert: die Maschine pendelt.' : '',
      state.live && state.live.nachlauf && state.live.nachlauf.sicher
        ? `Rad folgt dem Befehl ${Number(state.live.nachlauf.nachlauf_s).toLocaleString('de-DE', { maximumFractionDigits: 2 })} s später (gemessen)` : '',
    ].filter(Boolean).join('\n');
  }
  if (aufKarte) {
    LenkLive.zeichnen(el('lenkLiveKlein'), lenkAufzeichnung, { sekunden: 30, schritte, kompakt: true });
    el('lenkLiveKurz').textContent = k.genug
      ? `${Number(k.effektiv).toLocaleString('de-DE', { maximumFractionDigits: 1 })} cm · ` +
        ({ gut: 'gut', pendelt: 'pendelt', versatz: 'daneben', traege: 'träge' }[b.art] || '')
      : 'sammelt …';
    el('lenkLiveKurz').dataset.art = b.art;
  }
}

el('btnLenkLiveKarte').onclick = () => lenkLiveKarteSetzen(el('lenkLiveFenster').hidden);
el('lenkLiveZu').onclick = () => lenkLiveKarteSetzen(false);
el('lenkLiveZeit').onchange = renderLenkLive;
lenkLiveKarteSetzen(lenkLiveKarteAn());
setInterval(renderLenkLive, 500);

/* Lenk-Assistent: Zustand anzeigen, Modus wählen, Motor und Anschläge am Stand einmessen. */
function renderAbstimmung() {
  const a = state.live && state.live.abstimmung;
  if (!a || el('lenkAssistent').hidden) return;
  const wahl = el('abstModus');
  if (document.activeElement !== wahl) wahl.value = a.modus;
  const zahl = (x, n = 2) => Number(x).toLocaleString('de-DE', { maximumFractionDigits: n });
  if (a.modus === 'aus') {
    el('abstInfo').textContent = 'Aus. Zum Einschalten „Nur Vorschläge“ oder „Stellt selbst nach“ wählen ' +
      'und mit eingeschalteter Lenkung geradeaus fahren.';
    return;
  }
  const zeilen = [(a.regelart === 'cerea' ? `Cerea-Regler · Lenkaggressivität ${zahl(a.cerea_lenk_aggr, 0)} · ` : '') +
    `Verstärkung ${zahl(a.steer_gain)} · Vorhalt ${a.actuator_latency_ms} ms` +
    (a.regelart === 'voraus' ? ` · Vorausschauend, Trägheit ${zahl(a.lenk_traegheit_s)} s` : '') +
    (a.ausgangswerte ? ` (Ausgang: ${zahl(a.ausgangswerte.steer_gain)} / ${a.ausgangswerte.actuator_latency_ms} ms)` : '')];
  if (a.nachlauf_s != null) zeilen.push(`Gemessen: Das Rad folgt dem Befehl ${zahl(a.nachlauf_s, 1)} s später.`);
  zeilen.push(a.befund
    ? `Letzte Bewertung: ${{ schwingt: 'pendelt', traege: 'zu träge', gut: 'gut' }[a.befund.art] || a.befund.art}` +
      ` (${zahl(a.befund.effektiv_m * 100, 0)} cm effektiv)`
    : `Sammelt Messwerte … ${zahl(a.sammelt_s, 0)} von ${a.fenster_s} s ruhige Geradeausfahrt`);
  if (a.hinweis) zeilen.push(a.hinweis);
  const name = { steer_gain: 'Verstärkung', actuator_latency_ms: 'Vorhalt', lenk_traegheit_s: 'Trägheit',
    regelart: 'Regelart', cerea_lenk_aggr: 'Lenkaggressivität' };
  const wert = (x) => (typeof x === 'number' ? zahl(x) : { voraus: 'Vorausschauend', stanley: 'Stanley', cerea: 'Cerea' }[x] || x);
  (a.schritte || []).slice(-3).forEach((s) => zeilen.push(
    `${name[s.was] || s.was} ${s.von ? wert(s.von) + ' → ' + wert(s.nach) : ''} ${s.grund}`));
  el('abstInfo').textContent = zeilen.join('\n');
}
setInterval(() => {
  if (!el('sheet').hidden && document.querySelector('.tab.active').dataset.tab === 'lenkung') {
    renderAbstimmung();
    renderRadmitte();
  }
}, 2000);

/* Radmitte aus der Fahrt lernen (radmitte.py). */
async function renderRadmitte(stand) {
  const e = stand || await fetch('/api/lenkung/radmitte').then((r) => r.json()).catch(() => null);
  if (!e) return;
  const z = (x, n = 2) => Number(x).toLocaleString('de-DE', { minimumFractionDigits: n, maximumFractionDigits: n });
  el('btnRadmitteStart').textContent = e.sammelt ? 'Sammeln anhalten' : 'Sammeln starten';
  el('btnRadmitteStart').classList.toggle('primary', !e.sammelt);
  el('btnRadmitteUebernehmen').disabled = !e.gueltig;
  el('radmitteInfo').textContent = e.proben < e.noetig
    ? `${e.sammelt ? 'Sammelt' : 'Angehalten'}: ${e.proben} von ${e.noetig} Messungen.`
    : `${e.proben} Messungen · Radwinkel geradeaus im Mittel ${z(e.median_deg)}° · Streuung ${z(e.streuung_deg)}° · ` +
      `Sicherheit ${z(e.vertrauen * 100, 0)} %` +
      (e.gueltig ? ` – Korrektur ${z(-e.korrektur_deg)}° bereit.` : Math.abs(e.median_deg) < 0.05
        ? ' – die Mitte stimmt.' : ' – noch nicht sicher genug, weiter fahren.');
}

async function radmitteAktion(aktion) {
  const antwort = await api('POST', '/api/lenkung/radmitte', { aktion });
  if (antwort) renderRadmitte(antwort.data);
  return antwort;
}
el('btnRadmitteStart').onclick = async () => {
  const e = await fetch('/api/lenkung/radmitte').then((r) => r.json()).catch(() => null);
  radmitteAktion(e && e.sammelt ? 'stop' : 'start');
};
el('btnRadmitteVerwerfen').onclick = () => radmitteAktion('zuruecksetzen');
el('btnRadmitteUebernehmen').onclick = async () => {
  if (await radmitteAktion('uebernehmen')) toast('Radmitte korrigiert und gespeichert');
};

el('abstModus').onchange = async (event) => {
  const antwort = await api('POST', '/api/abstimmung', { modus: event.target.value });
  if (antwort) toast(event.target.value === 'aus' ? 'Lenk-Assistent aus'
    : event.target.value === 'beobachten' ? 'Lenk-Assistent macht Vorschläge' : 'Lenk-Assistent stellt selbst nach');
};
el('btnAbstZurueck').onclick = async () => {
  if (await api('POST', '/api/abstimmung/zurueck')) toast('Ausgangswerte wiederhergestellt');
};

let einmessErgebnis = null;
function einmessAnbieten(werte, text) {
  einmessErgebnis = werte;
  el('einmessInfo').textContent = text;
  el('btnEinmessUebernehmen').hidden = !werte;
}
el('btnEinmessen').onclick = async () => {
  el('einmessInfo').textContent = 'Motor wird eingemessen … (etwa 10 Sekunden, der Motor dreht kurz hin und her)';
  el('btnEinmessUebernehmen').hidden = true;
  const antwort = await api('POST', '/api/steering/einmessen');
  if (!antwort) { el('einmessInfo').textContent = 'Nicht eingemessen.'; return; }
  const d = antwort.data;
  const l = (x) => (x === null ? 'keine Bewegung' : `${Math.round(x * 100)} %`);
  const text = `Losbrechleistung rechts ${l(d.losbrech_rechts)}, links ${l(d.losbrech_links)}. ` +
    (d.mindestleistung_vorschlag ? `Mindestleistung ${Math.round(d.mindestleistung_vorschlag * 100)} % vorgeschlagen. ` : '') +
    (d.hinweis || '');
  einmessAnbieten(d.mindestleistung_vorschlag ? { 'phidget.min_duty': d.mindestleistung_vorschlag } : null, text);
};
let anschlagLaeuft = false;
el('btnAnschlag').onclick = async () => {
  if (!anschlagLaeuft) {
    const antwort = await api('POST', '/api/steering/anschlag', { aktion: 'start' });
    if (!antwort) return;
    anschlagLaeuft = true;
    el('btnAnschlag').textContent = 'Anschläge fertig';
    el('btnEinmessUebernehmen').hidden = true;
    el('einmessInfo').textContent = 'Jetzt von Hand bis zum linken, dann bis zum rechten Anschlag lenken, ' +
      'danach „Anschläge fertig“ drücken. Der Motor bleibt stromlos.';
    return;
  }
  anschlagLaeuft = false;
  el('btnAnschlag').textContent = 'Anschläge messen';
  const antwort = await api('POST', '/api/steering/anschlag',
    { aktion: 'ende', gesamtwinkel_deg: parseFloat(el('anschlagWinkel').value) });
  if (!antwort) { el('einmessInfo').textContent = 'Nicht gemessen.'; return; }
  const d = antwort.data;
  einmessAnbieten({ 'phidget.counts_per_deg': d.zaehlwerte_je_grad, 'phidget.encoder_counts_per_deg': d.zaehlwerte_je_grad },
    `${d.zaehlwerte} Zählwerte über ${d.gesamtwinkel_deg}° = ${String(d.zaehlwerte_je_grad).replace('.', ',')} Zählwerte je Grad. ` +
    'Der Gesamteinschlag zählt: bitte oben den echten Wert der Räder am Boden eintragen.');
};
el('btnEinmessUebernehmen').onclick = async () => {
  if (!einmessErgebnis) return;
  if (await api('POST', '/api/settings', { aenderungen: einmessErgebnis })) {
    toast('Übernommen');
    el('btnEinmessUebernehmen').hidden = true;
    einmessErgebnis = null;
  }
};

/* Motortest: kurz und schwach in eine Richtung, danach steht der Motor wieder.
 * Der Drehgeber zeigt, ob wirklich etwas gedreht hat. */
async function motorTest(richtung) {
  const info = el('motorTestInfo');
  info.textContent = 'Motor läuft kurz …';
  const antwort = await api('POST', '/api/steering/motortest',
    { richtung, leistung: parseFloat(el('motorLeistung').value), dauer_s: 0.5 });
  if (!antwort) { info.textContent = 'Motortest nicht ausgeführt.'; return; }
  const d = antwort.data;
  const technik = [
    d.zaehlwerte_je_s !== null && d.zaehlwerte_je_s !== undefined ? `${d.zaehlwerte_je_s} Zählwerte/s` : null,
    d.strom_max_a ? `Strom bis ${String(d.strom_max_a).replace('.', ',')} A` : null,
    d.beschleunigung_ist !== undefined
      ? `Beschleunigung ${d.beschleunigung_ist} (Bereich ${d.beschleunigung_bereich.join('–')})` : null,
  ].filter(Boolean).join(' · ');
  info.textContent = (d.drehgeber_bewegt === null
    ? `Motor ${d.richtung}: kein Drehgeber auslesbar.`
    : d.drehgeber_bewegt === 0
      ? `Motor ${d.richtung}: Drehgeber hat sich NICHT bewegt (${d.drehgeber_vorher}). ` +
        'Hat der Motor gedreht? Wenn ja: Drehgeber nicht angeschlossen; wenn nein: Leistung erhöhen.'
      : `Motor ${d.richtung}: Drehgeber ${d.drehgeber_bewegt > 0 ? '+' : ''}${d.drehgeber_bewegt} Zählwerte.`) +
    (technik ? ` · ${technik}` : '');
}
el('btnMotorLinks').onclick = () => motorTest('links');
el('btnMotorRechts').onclick = () => motorTest('rechts');

el('btnCentre').onclick = async () => {
  if (!confirm('Stehen die Räder gerade? Diese Stellung wird als Mitte gemerkt.')) return;
  const result = await api('POST', '/api/steering/centre');
  if (result) toast('Mitte gelernt');
};

el('btnLevel').onclick = async () => {
  const result = await api('POST', '/api/imu/level');
  if (result) toast('Neigungssensor genullt');
};
el('btnImuM1').onclick = async () => {
  const r = await api('POST', '/api/imu/kalibrieren/m1');
  if (r) el('imuKalibInfo').textContent = `M1: ${r.data.roll_deg.toLocaleString('de-DE')}° quer – jetzt auf derselben Stelle um 180° wenden, dann M2.`;
};
el('btnImuM2').onclick = async () => {
  const r = await api('POST', '/api/imu/kalibrieren/m2');
  if (r) {
    const zahl = (x) => Number(x).toLocaleString('de-DE', { maximumFractionDigits: 1 });
    el('imuKalibInfo').textContent = `Kalibriert: Montagefehler ${zahl(r.data.roll_offset)}° quer, ` +
      `Boden hier ${zahl(r.data.boden_quer_deg)}° geneigt.`;
    toast('Neigungssensor kalibriert');
  }
};
el('compensation').onchange = (event) =>
  api('POST', '/api/imu/compensation', { enabled: event.target.checked });

el('simSpeed').oninput = (event) =>
  api('POST', '/api/simulator', { speed_kmh: parseFloat(event.target.value) });
el('simSteer').oninput = (event) =>
  api('POST', '/api/simulator', { steer_deg: parseFloat(event.target.value) });
el('btnSimInsFeld').onclick = async () => {
  if (await api('POST', '/api/simulator', { ins_feld: true })) {
    el('simSteer').value = 0;
    toast('Traktor steht wieder im Feld');
    el('sheet').hidden = true;
  }
};

/* Simulator mit den Pfeiltasten fahren: links/rechts gedrückt halten lenkt,
   loslassen stellt die Räder wieder gerade; hoch/runter ändert das Tempo. */
const SIM_LENK_SCHRITT = 4;
const SIM_LENK_MAX = 30;
function simulatorLaeuft() {
  const system = state.live && state.live.system;
  return Boolean(system && system.gnss && system.gnss.source === 'simulator');
}
function simulatorTasteErlaubt(event) {
  if(document.querySelector("dialog[open]"))return false;
  if (!simulatorLaeuft() || !el('sheet').hidden) return false;
  const ziel = event.target;
  return !(ziel && (ziel.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(ziel.tagName)));
}
function simulatorLenken(grad) {
  el('simSteer').value = grad;
  api('POST', '/api/simulator', { steer_deg: grad });
}
document.addEventListener('keydown', (event) => {
  if (!simulatorTasteErlaubt(event)) return;
  const lenkung = parseFloat(el('simSteer').value) || 0;
  const tempo = parseFloat(el('simSpeed').value) || 0;
  if (event.key === 'ArrowLeft') simulatorLenken(Math.max(-SIM_LENK_MAX, Math.min(0, lenkung) - SIM_LENK_SCHRITT));
  else if (event.key === 'ArrowRight') simulatorLenken(Math.min(SIM_LENK_MAX, Math.max(0, lenkung) + SIM_LENK_SCHRITT));
  else if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
    const neu = Math.max(0, Math.min(15, tempo + (event.key === 'ArrowUp' ? 1 : -1)));
    el('simSpeed').value = neu;
    api('POST', '/api/simulator', { speed_kmh: neu });
  } else return;
  event.preventDefault();
});
document.addEventListener('keyup', (event) => {
  if ((event.key === 'ArrowLeft' || event.key === 'ArrowRight') && simulatorLaeuft()) simulatorLenken(0);
});

/* Fahrgassenweite: eine für den Betrieb (Spritze/Streuer), steht oben in den
 * Reitern Felder und Spuren. Eine schon gesetzte Saisonspur behält ihre. */
let fahrgassenWeite = 21;
function zeigeFahrgassenWeite() {
  document.querySelectorAll('.fahrgassen-taste').forEach((b) => {
    b.textContent = `Fahrgassenweite: ${fahrgassenWeite.toLocaleString('de-DE')} m`;
  });
}
async function ladeFahrgassenWeite() {
  try {
    fahrgassenWeite = (await (await fetch('/api/fahrgassen')).json()).weite_m;
    zeigeFahrgassenWeite();
  } catch (fehler) { /* Anzeige bleibt beim letzten Wert */ }
}
document.querySelectorAll('.fahrgassen-taste').forEach((b) => {
  b.onclick = async () => {
    const eingabe = prompt('Fahrgassenweite in Metern (Arbeitsbreite von Spritze und Streuer).\n' +
      'Gilt für neu gesetzte Saisonspuren – vorhandene Fahrgassen bleiben, wo sie sind.',
      String(fahrgassenWeite).replace('.', ','));
    if (eingabe === null) return;
    const meter = parseFloat(eingabe.replace(',', '.'));
    if (Number.isNaN(meter)) { toast('Bitte eine Zahl in Metern eingeben', true); return; }
    const antwort = await api('POST', '/api/fahrgassen', { weite_m: meter });
    if (antwort) {
      fahrgassenWeite = antwort.data.weite_m;
      zeigeFahrgassenWeite();
      toast(`Fahrgassenweite ${fahrgassenWeite.toLocaleString('de-DE')} m`);
    }
  };
});

/* Seiten neu füllen. ``takt``: der Aufruf alle 5 s - Listen werden aufgefrischt,
 * Eingabeseiten nicht: der Saatplan setzte sonst jede Eingabe zurück und flackerte. */
async function refreshLists(takt = false) {
  if (el('sheet').hidden) return;
  const active = document.querySelector('.tab.active').dataset.tab;
  if (active === 'fields' || active === 'lines') ladeFahrgassenWeite();
  if (active === 'fields') {
    const [fields, lines] = await Promise.all([
      fetch('/api/fields').then((r) => r.json()), fetch('/api/lines').then((r) => r.json())]);
    renderFields(fields, lines);
  }
  if (active === 'lines') {
    const fieldId = state.live && state.live.field ? state.live.field.id : '';
    // Ohne Feld keine Liste: die Anfrage ohne field_id gäbe die Spuren aller Felder.
    renderLines(fieldId ? await (await fetch(`/api/lines?field_id=${fieldId}`)).json() : []);
    renderHindernisse(fieldId ? await (await fetch(`/api/hindernisse?field_id=${fieldId}`)).json() : null);
  }
  if (active === 'maschinen') renderFlotte();
  if (active === 'jobs') {
    const [jobs, fields] = await Promise.all([
      fetch('/api/jobs').then((r) => r.json()), fetch('/api/fields').then((r) => r.json())]);
    renderJobs(jobs, fields);
  }
  if (active === 'machine') {
    if (!settings.geladen) loadSettings();
    fillProfile();
    renderTraktoren();       // zeichnet danach auch die Geräteliste, passend zum Fahrzeug
  }
  if (active === 'headland') fillHeadland();
  if (active === 'lenkregler') {
    // Im Takt nur die Anzeige - sonst sprängen Werte, die gerade verstellt werden, zurück.
    if (!takt) await renderTraktoren();
    renderLenkregler();
    reglerLiveZeigen(state.live);
  }
  if (active === 'saatplan') {
    const feldId = state.live && state.live.field ? state.live.field.id : null;
    if (!takt || saat.seiteFeld !== feldId) renderSaatplan();
  }
  if (active === 'system') { renderSystem(); renderRohdaten(); renderUpdate(); }
  if (active === 'lenkung') {
    renderGenauigkeit();
    renderSystem();
    renderEinrichten();
    if (!settings.geladen) loadSettings();
  }
  if (active === 'daten') { renderBackup(); renderDatenRolle(); }
  if (active === 'setup') renderSetup(await (await fetch('/api/checklist')).json());
  if (active === 'settings' && !settings.geladen) loadSettings();
}

el('btnLuftbilderAlle').onclick = async () => {
  el('luftbilderInfo').textContent = 'Lade Luftbilder … (dauert je Feld ein paar Sekunden)';
  const antwort = await api('POST', '/api/luftbild/alle');
  if (!antwort) { el('luftbilderInfo').textContent = ''; return; }
  const d = antwort.data;
  el('luftbilderInfo').textContent = `${d.geholt} Luftbilder bereit` +
    (d.fehler_gesamt ? ` · ${d.fehler_gesamt} nicht geholt: ${d.fehler[0]}` : ' – auch ohne Netz');
  luftbild.feld = null;
};

el('btnRandspurenAlle').onclick = async () => {
  el('randspurenInfo').textContent = 'Lege an …';
  const antwort = await api('POST', '/api/randspuren');
  if (!antwort) { el('randspurenInfo').textContent = ''; return; }
  const mitGrenze = antwort.data.felder.filter((f) => f.angelegt.length).length;
  el('randspurenInfo').textContent = `${antwort.data.spuren} Spuren an ${mitGrenze} Feldern`;
  refreshLists();
};

const aufgeklappt = new Set();   // Felder, deren Spuren gerade offen sind

function letzteAussaat(lines) {
  return (lines || []).filter((line) => line.saison > 0 && line.fahrgasse_m > 0)
    .sort((a, b) => (b.aussaat_at || 0) - (a.aussaat_at || 0) || b.id.localeCompare(a.id))[0] || null;
}

function erntejahrWaehlen(vorgabe = new Date().getFullYear()) {
  const dialog = el('aussaatDialog');
  const eingabe = el('aussaatJahr');
  eingabe.value = String(vorgabe);
  dialog.returnValue = '';
  return new Promise((resolve) => {
    dialog.onclose = () => {
      // Die Spurzeilen werden beim Abgleich neu aufgebaut; der Menü-Reiter bleibt.
      const zurueck = document.querySelector('.tab[aria-selected="true"]') || el('btnMenu');
      zurueck.focus({ preventScroll: true });
      if (dialog.returnValue !== 'uebernehmen') { resolve(null); return; }
      const text = eingabe.value.trim();
      const jahr = Number(text);
      if (!/^\d{4}$/.test(text) || jahr < 2000 || jahr > 2200) {
        toast('Bitte ein Erntejahr von 2000 bis 2200 eingeben');
        resolve(null);
        return;
      }
      resolve(jahr);
    };
    dialog.showModal();
  });
}

function renderFields(fields, lines) {
  const host = el('fieldList');
  const offeneAktionen=new Set([...host.querySelectorAll('.item[data-field]')].filter(r=>r.querySelector('.mehr')?.getAttribute('aria-expanded')==='true').map(r=>r.dataset.field));
  const currentId = state.live && state.live.field ? state.live.field.id : null;
  const jeFeld = {};
  (lines || []).forEach((line) => { (jeFeld[line.field_id] = jeFeld[line.field_id] || []).push(line); });
  host.innerHTML = '';
  fields.forEach((field) => {
    const spuren = jeFeld[field.id] || [];
    const saison = letzteAussaat(spuren);
    const offen = aufgeklappt.has(field.id);
    const row = item({
      active: field.id === currentId,
      title: field.name,
      sub: `${field.area_ha.toFixed(2)} ha` +
           (field.boundary.length ? ` · ${field.boundary.length} Grenzpunkte` : ' · keine Grenze') +
           (field.working_boundary?.length?' · Arbeitsgrenze aktiv':'') +
           ` · ${spuren.length} ${spuren.length === 1 ? 'Spur' : 'Spuren'}` +
           (saison ? ` · Fahrgassen Erntejahr ${saison.saison}: alle ${saison.fahrgasse_m.toFixed(0)} m` : ''),
      actions: [
        ['Laden', async () => {
          if (await api('POST', `/api/fields/${field.id}/load`)) {
            toast(`${field.name} geladen`); el('sheet').hidden = true;
          }
        }],
        [offen ? 'Spuren ▴' : 'Spuren ▾', () => {
          if (offen) aufgeklappt.delete(field.id); else aufgeklappt.add(field.id);
          refreshLists();
        }],
        ...(field.boundary.length >= 3 ? [['Grenze bearbeiten',()=>feldwerkzeuge.grenze(field)]] : []),
        ...(field.boundary.length >= 3 ? [['Randspuren anlegen', async () => {
          const antwort = await api('POST', `/api/fields/${field.id}/randspuren`);
          if (antwort) {
            toast(`${field.name}: ${antwort.data.angelegt.length} Spuren an den Rändern`);
            aufgeklappt.add(field.id);
            refreshLists();
          }
        }]] : []),
        [field.boundary.length ? 'Grenze neu abfahren' : 'Grenze abfahren', async () => {
          if (field.boundary.length && !confirm(`Die Grenze von "${field.name}" neu abfahren? ` +
              'Die alte wird beim Abschließen ersetzt.')) return;
          if (field.id !== currentId && !(await api('POST', `/api/fields/${field.id}/load`))) return;
          if (await api('POST', '/api/record/start', { mode: 'boundary' })) {
            toast('Grenze aufzeichnen – einmal um das Feld fahren, dann Kontur drücken');
            el('sheet').hidden = true;
          }
        }],
        ['Löschen', async () => {
          if (confirm(`Feld "${field.name}" löschen?`)) {
            await api('DELETE', `/api/fields/${field.id}`); refreshLists();
          }
        }],
      ],
    });
    row.dataset.field=field.id;
    if(offeneAktionen.has(field.id))row.querySelector('.mehr')?.click();
    host.appendChild(row);
    if (offen) host.appendChild(spurenListe(field, spuren));
  });
  if (!fields.length) host.innerHTML = '<p class="note">Noch keine Felder angelegt.</p>';
}

/* Die Spuren eines Feldes, unter dem Feld. Eine davon darf die Saisonspur
 * sein - die mit den Fahrgassen. Sie wird beim Laden des Feldes automatisch
 * aktiv, das ganze Jahr. */
function spurenListe(field, spuren) {
  const box = document.createElement('div');
  box.className = 'unterliste';
  const saison = letzteAussaat(spuren);
  spuren.forEach((line) => box.appendChild(spurZeile(field.id, line, saison && saison.id)));
  if (!spuren.length) box.innerHTML = '<p class="note">Noch keine Spur für dieses Feld – A/B, A+ oder Kurve in der Kabine anlegen.</p>';
  return box;
}

/* Eine Spur als Zeile - dieselbe unter dem Feld wie im Reiter Spuren, damit
 * die Saisonspur an beiden Stellen gesetzt werden kann. */
function spurZeile(fieldId, line, aussaatId) {
  const currentId = state.live && state.live.line ? state.live.line.id : null;
  const feldGeladen = state.live && state.live.field && state.live.field.id === fieldId;
  const saison = line.id === aussaatId;
  const row = item({
    active: line.id === currentId,
    title: line.name,
    sub: `${line.mode === 'ab' ? 'AB-Linie' : line.mode === 'plan'
      ? `Saatplan, ${(line.spuren || []).length} Spuren` : 'Kurve'} · Abstand ${line.spacing_m.toFixed(2)} m` +
         (line.fahrgasse_m > 0 ? ` · Fahrgassen alle ${line.fahrgasse_m.toFixed(0)} m` +
           (line.saison ? ` (Erntejahr ${line.saison})` : '') : '') +
         (saison ? ' · Aktuelle Aussaat' : ''),
    actions: [
      ['Laden', async () => {
        // Die Spur gehört zu ihrem Feld: erst das Feld, dann die Spur.
        if (!feldGeladen && !(await api('POST', `/api/fields/${fieldId}/load`))) return;
        if (await api('POST', `/api/lines/${line.id}/load`)) {
          toast(`Spur "${line.name}" aktiv`); el('sheet').hidden = true;
        }
      }],
      [saison ? 'Aussaat erneuern' : 'Als Aussaat festlegen', async () => {
        const jahr = await erntejahrWaehlen(line.saison || undefined);
        if (jahr === null) return;
        const antwort = await api('POST', `/api/lines/${line.id}`, { saisonspur: true, saison: jahr });
        if (antwort) {
          toast(`Aussaat Erntejahr ${jahr}: Fahrgassen alle ${antwort.data.fahrgasse_m} m`);
          refreshLists();
        }
      }],
      ...(line.saison > 0 && line.fahrgasse_m > 0 ? [['Fahrgassen aufheben', async () => {
        if (!confirm(`Fahrgassen der Aussaat „${line.name}“ aufheben?`)) return;
        if (await api('POST', `/api/lines/${line.id}`, { saisonspur: false })) refreshLists();
      }]] : []),
      ['Umbenennen', async () => {
        const name = prompt('Neuer Name der Spur', line.name);
        if (name === null || !name.trim()) return;
        if (await api('POST', `/api/lines/${line.id}`, { name: name.trim() })) refreshLists();
      }],
      ['Löschen', async () => {
        if (!confirm(`Spur "${line.name}" löschen?`)) return;
        await api('DELETE', `/api/lines/${line.id}`); refreshLists();
      }],
    ],
  });
  if (saison) row.querySelectorAll('.actions button')[1].classList.add('an');
  return row;
}

function renderLines(lines) {
  const aktiv=state.live?.line;
  el('btnOriginalspur').disabled=!aktiv||!aktiv.nudge_m;
  el('originalspurInfo').textContent=aktiv?`Aktiver Versatz: ${(aktiv.nudge_m*100).toFixed(0)} cm · Rücksetzen im Stillstand bei ausgeschalteter Lenkung.`:'Keine Spur aktiv.';
  const host = el('lineList');
  const fieldId = state.live && state.live.field ? state.live.field.id : null;
  const saison = letzteAussaat(lines);
  host.innerHTML = '';
  lines.forEach((line) => host.appendChild(spurZeile(fieldId, line, saison && saison.id)));
  if (!lines.length) host.innerHTML = fieldId
    ? '<p class="note">Für dieses Feld gibt es noch keine Spur.</p>'
    : '<p class="note">Kein Feld geladen – Spuren gehören zu ihrem Feld (Menü → Felder).</p>';
}

/* Hindernisse des geladenen Feldes: Name, Entfernung, Löschen. */
function renderHindernisse(liste) {
  const host = el('hindernisListe');
  host.innerHTML = '';
  if (liste === null) { host.innerHTML = '<p class="note">Kein Feld geladen.</p>'; return; }
  if (!liste.length) { host.innerHTML = '<p class="note">Noch keine Hindernisse in diesem Feld.</p>'; return; }
  const lokal = Object.fromEntries(((state.live && state.live.hindernisse) || []).map((h) => [h.id, h]));
  const pos = state.live && state.live.position;
  liste.forEach((h) => {
    const l = lokal[h.id];
    const weg = l && pos ? ` · ${Math.round(Math.hypot(l.x - pos[0], l.y - pos[1]))} m entfernt` : '';
    host.appendChild(item({
      title: h.name,
      sub: `Größe ${h.radius_m.toLocaleString('de-DE')} m${weg} · markiert ${new Date(h.updated_at * 1000).toLocaleDateString('de-DE')}`,
      actions: [['Löschen', async () => {
        if (!confirm(`Hindernis „${h.name}“ löschen?`)) return;
        await api('DELETE', `/api/hindernisse/${h.id}`);
        refreshLists();
      }]],
    }));
  });
}

/* Fernansicht: alle Maschinen auf einer Übersichtskarte und als Liste. */
let flotteTakt = null;
async function renderFlotte() {
  clearTimeout(flotteTakt);
  const daten = await fetch('/api/flotte').then((r) => r.json()).catch(() => null);
  const aktiv = !el('sheet').hidden && document.querySelector('.tab.active').dataset.tab === 'maschinen';
  if (!aktiv) return;
  flotteTakt = setTimeout(renderFlotte, 5000);
  if (!daten) { el('flotteHinweis').textContent = 'Fernansicht nicht erreichbar.'; return; }
  const maschinen = daten.maschinen || [];
  const system = state.live && state.live.system;
  el('flotteHinweis').textContent = !maschinen.length
    ? (system && system.role !== 'master'
      ? 'Noch keine Meldungen – das Tablet fragt alle 5 s beim Master nach (Abgleich muss laufen).'
      : 'Noch keine Maschine gemeldet. Die Tablets melden sich, sobald sie eine Position haben und den Master erreichen.')
    : `${maschinen.length} ${maschinen.length === 1 ? 'Maschine' : 'Maschinen'} · aktualisiert alle 5 s`;
  flotteKarte(maschinen, daten.felder || {});
  const host = el('flotteListe');
  host.innerHTML = '';
  maschinen.forEach((m) => {
    const zeile = item({
      title: `${m.name || m.id}${m.selbst ? ' (dieses Gerät)' : ''}${m.simulator ? ' · Simulator' : ''}`,
      sub: `${window.Fernansicht.zustandText(m)} · ${window.Fernansicht.alterText(m.alter_s)}`,
      actions: [],
    });
    if (window.Fernansicht.veraltet(m)) zeile.classList.add('veraltet');
    host.appendChild(zeile);
  });
}

function flotteKarte(maschinen, felder) {
  const leinwand = el('flotteKarte');
  const k = window.devicePixelRatio || 1;
  const breite = leinwand.clientWidth, hoehe = leinwand.clientHeight;
  leinwand.width = breite * k; leinwand.height = hoehe * k;
  const c = leinwand.getContext('2d');
  c.setTransform(k, 0, 0, k, 0, 0);
  c.fillStyle = F.boden; c.fillRect(0, 0, breite, hoehe);
  const punkte = maschinen.map((m) => [m.lat, m.lon]);
  Object.values(felder).forEach((f) => f.grenze.forEach((p) => punkte.push(p)));
  const proj = window.Fernansicht.projektion(punkte);
  if (!proj) return;
  const bild = window.Fernansicht.einpassen(proj, breite, hoehe);
  const nachBild = (p) => bild(proj.nachMetern(p));
  c.font = '600 13px system-ui, sans-serif';
  c.textAlign = 'center';
  Object.values(felder).forEach((f) => {
    c.beginPath();
    f.grenze.forEach((p, i) => { const [x, y] = nachBild(p); i ? c.lineTo(x, y) : c.moveTo(x, y); });
    c.closePath();
    c.strokeStyle = F.grenze; c.lineWidth = 2; c.stroke();
    const [mx, my] = nachBild([f.grenze.reduce((s, p) => s + p[0], 0) / f.grenze.length,
      f.grenze.reduce((s, p) => s + p[1], 0) / f.grenze.length]);
    c.fillStyle = F.grenze; c.fillText(f.name, mx, my);
  });
  maschinen.forEach((m) => {
    const [x, y] = nachBild([m.lat, m.lon]);
    const h = ((m.kurs || 0) * Math.PI) / 180;
    c.save();
    c.translate(x, y); c.rotate(h);
    c.beginPath(); c.moveTo(0, -14); c.lineTo(9, 10); c.lineTo(0, 5); c.lineTo(-9, 10); c.closePath();
    c.fillStyle = m.selbst ? F.akzent || '#ffd84a' : window.Fernansicht.veraltet(m) ? F.raster : '#4b9dff';
    c.fill();
    c.restore();
    c.fillStyle = F.traktor || '#fff';
    c.fillText(m.name || m.id, x, y - 20);
  });
}

/* Feldkarte: den Schlag auf der Karte wählen, wie bei Cerea (Menü → Felder → Karte).
 * Rechnung und Treffer in feldkarte.js, die Projektion aus fernansicht.js. */
const FELDKARTE_PUNKT_PX = 14;   // schmaler als das auf dem Bildschirm: als Punkt zeichnen
const feldkarte = { felder: [], proj: null, ansicht: null, wahl: null, geladen: null, position: null,
                    bilder: {}, zeiger: new Map(), bewegt: false, start: null };

const feldkarteAn = () => !el('feldkarteBox').hidden;

function feldkarteZeigen(an) {
  merken('felderAnsicht', an ? 'karte' : 'liste');
  el('feldkarteBox').hidden = !an;
  el('fieldList').hidden = an;
  [['btnFelderKarte', an], ['btnFelderListe', !an]].forEach(([id, ja]) => {
    el(id).classList.toggle('an', ja);
    el(id).setAttribute('aria-pressed', String(ja));
  });
  if (an) feldkarteLaden();
}
el('btnFelderListe').onclick = () => feldkarteZeigen(false);
el('btnFelderKarte').onclick = () => feldkarteZeigen(true);
if (gemerkt('felderAnsicht') === 'karte') {
  el('feldkarteBox').hidden = false;
  el('fieldList').hidden = true;
  el('btnFelderKarte').classList.add('an'); el('btnFelderKarte').setAttribute('aria-pressed', 'true');
  el('btnFelderListe').classList.remove('an'); el('btnFelderListe').setAttribute('aria-pressed', 'false');
}

async function feldkarteLaden() {
  const daten = await fetch('/api/feldkarte').then((r) => r.json()).catch(() => null);
  if (!daten || !feldkarteAn()) return;
  if (!feldkarte.proj) {
    // Die Projektion bleibt, sonst spränge die Ansicht bei jedem Nachladen.
    const punkte = [];
    daten.felder.forEach((f) => f.grenze.forEach((p) => punkte.push(p)));
    if (daten.position) punkte.push([daten.position.lat, daten.position.lon]);
    feldkarte.proj = window.Fernansicht.projektion(punkte);
  }
  const proj = feldkarte.proj;
  if (!proj) { el('feldkarteHinweis').textContent = 'Noch kein Feld mit Grenze.'; return; }
  feldkarte.felder = daten.felder.map((f) => ({ ...f, xy: f.grenze.map(proj.nachMetern) }));
  feldkarte.geladen = daten.geladen;
  feldkarte.position = daten.position;
  if (feldkarte.wahl === null && daten.geladen) feldkarte.wahl = daten.geladen;
  daten.felder.forEach((f) => {
    if (!f.luftbild || feldkarte.bilder[f.id]) return;
    const bild = new Image();
    bild.onload = () => feldkarteZeichnen();
    bild.src = f.luftbild.bild;
    feldkarte.bilder[f.id] = { bild, box: f.luftbild.box };
  });
  const mitBild = daten.felder.filter((f) => f.luftbild).length;
  el('feldkarteHinweis').textContent = mitBild < daten.felder.length
    ? `${daten.felder.length} Felder · ${mitBild ? `Luftbild bei ${mitBild}` : 'ohne Luftbild'} – oben „Luftbilder aller Felder laden“`
    : '';
  if (!feldkarte.ansicht) feldkarteAlle();
  feldkarteZeichnen();
  feldkarteWahlZeigen();
}

function feldkarteGroesse() {
  const leinwand = el('feldkarte');
  return [leinwand.clientWidth || 1, leinwand.clientHeight || 1];
}

function feldkarteAlle() {
  if (!feldkarte.felder.length) return;
  const xs = feldkarte.felder.flatMap((f) => f.xy.map((p) => p[0]));
  const ys = feldkarte.felder.flatMap((f) => f.xy.map((p) => p[1]));
  const [b, h] = feldkarteGroesse();
  feldkarte.ansicht = window.Feldkarte.einpassen([Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)], b, h);
  feldkarteZeichnen();
}

function feldkarteZeichnen() {
  if (!feldkarteAn() || !feldkarte.ansicht || !feldkarte.proj) return;
  const leinwand = el('feldkarte');
  const k = window.devicePixelRatio || 1;
  const [breite, hoehe] = feldkarteGroesse();
  leinwand.width = breite * k; leinwand.height = hoehe * k;
  const c = leinwand.getContext('2d');
  c.setTransform(k, 0, 0, k, 0, 0);
  c.fillStyle = F.boden; c.fillRect(0, 0, breite, hoehe);
  const K = window.Feldkarte, a = feldkarte.ansicht, proj = feldkarte.proj;
  const px = (p) => K.nachBild(a, breite, hoehe, p);
  // Luftbilder: rundherum gedämpft, in den Feldern voll - so treten die Schläge hervor
  // und die harten Bildkanten der einzelnen Ausschnitte fallen weniger auf.
  const bilder = Object.values(feldkarte.bilder).filter(({ bild }) => bild.complete && bild.naturalWidth);
  const bilderMalen = () => bilder.forEach(({ bild, box }) => {
    const [x0, y0] = px(proj.nachMetern([box[2], box[1]]));    // Nordwest
    const [x1, y1] = px(proj.nachMetern([box[0], box[3]]));    // Südost
    c.drawImage(bild, x0, y0, x1 - x0, y1 - y0);
  });
  c.globalAlpha = 0.38; bilderMalen(); c.globalAlpha = 1;
  if (bilder.length) {
    c.save();
    c.beginPath();
    feldkarte.felder.forEach((f) => f.xy.forEach((p, i) => { const [x, y] = px(p); i ? c.lineTo(x, y) : c.moveTo(x, y); }));
    c.clip('evenodd');
    bilderMalen();
    c.restore();
  }
  c.lineJoin = 'round';
  feldkarte.felder.forEach((f) => {
    c.beginPath();
    f.xy.forEach((p, i) => { const [x, y] = px(p); i ? c.lineTo(x, y) : c.moveTo(x, y); });
    c.closePath();
    const gewaehlt = f.id === feldkarte.wahl, geladen = f.id === feldkarte.geladen;
    c.fillStyle = gewaehlt ? 'rgba(255, 216, 74, 0.30)' : 'rgba(0, 0, 0, 0.12)';
    c.fill();
    c.strokeStyle = gewaehlt ? '#ffd84a' : geladen ? '#48cc77' : F.grenze;
    c.lineWidth = gewaehlt || geladen ? 3 : 1.6;
    c.stroke();
  });
  // Namen nur, wo das Feld groß genug für die Schrift ist. Zu klein für die Hand
  // (weit herausgezoomt): ein Punkt zum Antippen, wie eine Stecknadel.
  c.font = '600 13px "IBM Plex Sans", system-ui, sans-serif'; c.textAlign = 'center'; c.textBaseline = 'middle';
  // Namen, die sich überdecken würden, bleiben weg - das gewählte Feld zuerst, dann die großen.
  const belegt = [];
  // Namen als Schildchen: ruhiger zu lesen als umrandete Schrift, auch auf hellem Luftbild.
  const beschriften = (text, x, y, farbe) => {
    const w = c.measureText(text).width + 16, h = 22;
    const kasten = [x - w / 2, y - h / 2, x + w / 2, y + h / 2];
    if (belegt.some((b) => kasten[0] < b[2] + 2 && kasten[2] > b[0] - 2 && kasten[1] < b[3] + 2 && kasten[3] > b[1] - 2)) return;
    belegt.push(kasten);
    c.beginPath();
    if (c.roundRect) c.roundRect(kasten[0], kasten[1], w, h, h / 2); else c.rect(kasten[0], kasten[1], w, h);
    c.fillStyle = farbe === '#ffd84a' ? 'rgba(255, 216, 74, 0.95)' : 'rgba(12, 20, 16, 0.82)'; c.fill();
    c.fillStyle = farbe === '#ffd84a' ? '#1a1400' : '#fff'; c.fillText(text, x, y + 0.5);
  };
  const reihe = [...feldkarte.felder].sort((a, b) =>
    (b.id === feldkarte.wahl) - (a.id === feldkarte.wahl) || K.flaeche(b.xy) - K.flaeche(a.xy));
  reihe.forEach((f) => {
    const xs = f.xy.map((p) => px(p)[0]);
    const name = f.name.replace(/\s*\/\s*Nr\.\s*\d+$/, '');
    const [x, y] = px(K.schwerpunkt(f.xy));
    if (Math.max(...xs) - Math.min(...xs) < FELDKARTE_PUNKT_PX) {
      c.beginPath(); c.arc(x, y, 7, 0, Math.PI * 2);
      c.fillStyle = f.id === feldkarte.wahl ? '#ffd84a' : f.id === feldkarte.geladen ? '#48cc77' : F.grenze;
      c.shadowColor = 'rgba(0,0,0,0.45)'; c.shadowBlur = 4; c.shadowOffsetY = 1;
      c.fill(); c.shadowColor = 'transparent';
      c.lineWidth = 2.5; c.strokeStyle = '#fff'; c.stroke();
      beschriften(name, x, y - 20, f.id === feldkarte.wahl ? '#ffd84a' : '#fff');
      return;
    }
    if (Math.max(...xs) - Math.min(...xs) < c.measureText(name).width + 8) return;
    beschriften(name, x, y, f.id === feldkarte.wahl ? '#ffd84a' : '#fff');
  });
  const pos = feldkarte.position;
  if (pos) {
    const [x, y] = px(proj.nachMetern([pos.lat, pos.lon]));
    c.save(); c.translate(x, y); c.rotate(((pos.kurs || 0) * Math.PI) / 180);
    c.beginPath(); c.moveTo(0, -14); c.lineTo(9, 10); c.lineTo(0, 5); c.lineTo(-9, 10); c.closePath();
    c.fillStyle = '#4b9dff'; c.fill(); c.lineWidth = 2; c.strokeStyle = '#fff'; c.stroke();
    c.restore();
  }
}

function feldkarteWahlZeigen() {
  const f = feldkarte.felder.find((x) => x.id === feldkarte.wahl);
  el('feldkarteWahl').hidden = !f;
  if (!f) return;
  el('feldkarteName').textContent = f.name;
  const teile = [`${Number(f.area_ha || 0).toLocaleString('de-DE', { maximumFractionDigits: 2 })} ha`];
  const pos = feldkarte.position;
  if (pos && feldkarte.proj) {
    const p = feldkarte.proj.nachMetern([pos.lat, pos.lon]);
    if (window.Feldkarte.imPolygon(p, f.xy)) teile.push('der Traktor steht darauf');
    else {
      let d = Infinity;
      f.xy.forEach((q) => { d = Math.min(d, Math.hypot(q[0] - p[0], q[1] - p[1])); });
      teile.push(d < 1000 ? `${Math.round(d)} m vom Traktor` : `${(d / 1000).toLocaleString('de-DE', { maximumFractionDigits: 1 })} km vom Traktor`);
    }
  }
  const geladen = f.id === feldkarte.geladen;
  if (geladen) teile.push('geladen');
  el('feldkarteInfo').textContent = teile.join(' · ');
  el('btnFeldkarteLaden').textContent = geladen ? 'Geladen' : 'Laden';
  el('btnFeldkarteLaden').disabled = geladen;
}

el('btnFeldkarteLaden').onclick = async () => {
  const f = feldkarte.felder.find((x) => x.id === feldkarte.wahl);
  if (!f) return;
  if (await api('POST', `/api/fields/${f.id}/load`)) {
    toast(`${f.name} geladen`); el('sheet').hidden = true;
    feldkarte.geladen = f.id;
  }
};
el('btnFeldkarteAlle').onclick = feldkarteAlle;
el('btnFeldkarteHier').onclick = () => {
  const pos = feldkarte.position;
  if (!pos || !feldkarte.proj || !feldkarte.ansicht) { toast('Keine Position – auf GPS warten', true); return; }
  feldkarte.ansicht = { mitte: feldkarte.proj.nachMetern([pos.lat, pos.lon]), massstab: Math.max(feldkarte.ansicht.massstab, 0.6) };
  feldkarteZeichnen();
};

/* Bedienen: ziehen verschiebt, zwei Finger oder das Rad zoomen, Antippen wählt ein Feld. */
(function feldkarteBedienung() {
  const leinwand = el('feldkarte');
  const ort = (e) => { const r = leinwand.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
  leinwand.addEventListener('pointerdown', (e) => {
    leinwand.setPointerCapture(e.pointerId);
    feldkarte.zeiger.set(e.pointerId, ort(e));
    if (feldkarte.zeiger.size === 1) { feldkarte.bewegt = false; feldkarte.start = ort(e); }
  });
  leinwand.addEventListener('pointermove', (e) => {
    if (!feldkarte.zeiger.has(e.pointerId) || !feldkarte.ansicht) return;
    const [b, h] = feldkarteGroesse();
    const vorher = [...feldkarte.zeiger.values()];
    feldkarte.zeiger.set(e.pointerId, ort(e));
    const jetzt = [...feldkarte.zeiger.values()];
    if (jetzt.length === 1) {
      const [x, y] = jetzt[0];
      if (Math.hypot(x - feldkarte.start[0], y - feldkarte.start[1]) > 6) feldkarte.bewegt = true;
      if (feldkarte.bewegt) feldkarte.ansicht = window.Feldkarte.verschieben(feldkarte.ansicht, x - vorher[0][0], y - vorher[0][1]);
    } else if (jetzt.length === 2 && vorher.length === 2) {
      feldkarte.bewegt = true;
      const mitte = (p) => [(p[0][0] + p[1][0]) / 2, (p[0][1] + p[1][1]) / 2];
      const weite = (p) => Math.hypot(p[0][0] - p[1][0], p[0][1] - p[1][1]) || 1;
      const m0 = mitte(vorher), m1 = mitte(jetzt);
      feldkarte.ansicht = window.Feldkarte.verschieben(feldkarte.ansicht, m1[0] - m0[0], m1[1] - m0[1]);
      feldkarte.ansicht = window.Feldkarte.zoomen(feldkarte.ansicht, weite(jetzt) / weite(vorher), m1, b, h);
    }
    feldkarteZeichnen();
  });
  const los = (e) => {
    if (!feldkarte.zeiger.has(e.pointerId)) return;
    const einzeln = feldkarte.zeiger.size === 1;
    feldkarte.zeiger.delete(e.pointerId);
    if (e.type !== 'pointerup' || !einzeln || feldkarte.bewegt || !feldkarte.ansicht) return;
    const [b, h] = feldkarteGroesse();
    const p = window.Feldkarte.nachMetern(feldkarte.ansicht, b, h, ort(e));
    // 12 Bildpunkte Fang: mit dem Handschuh trifft man die Grenze nicht genau.
    // Weit herausgezoomt sind die Felder Punkte: dann zählt der nächste Punkt.
    const fang = 12 / feldkarte.ansicht.massstab;
    let wahl = window.Feldkarte.feldUnter(feldkarte.felder, p, fang);
    if (wahl === null) {
      let beste = (FELDKARTE_PUNKT_PX + 10) / feldkarte.ansicht.massstab;
      feldkarte.felder.forEach((f) => {
        const s = window.Feldkarte.schwerpunkt(f.xy);
        const d = Math.hypot(s[0] - p[0], s[1] - p[1]);
        if (d < beste) { beste = d; wahl = f.id; }
      });
    }
    feldkarte.wahl = wahl;
    feldkarteZeichnen();
    feldkarteWahlZeigen();
  };
  leinwand.addEventListener('pointerup', los);
  leinwand.addEventListener('pointercancel', los);
  leinwand.addEventListener('wheel', (e) => {
    if (!feldkarte.ansicht) return;
    e.preventDefault();
    const [b, h] = feldkarteGroesse();
    feldkarte.ansicht = window.Feldkarte.zoomen(feldkarte.ansicht, Math.exp(-e.deltaY * 0.0015), ort(e), b, h);
    feldkarteZeichnen();
  }, { passive: false });
  leinwand.addEventListener('dblclick', (e) => {
    if (!feldkarte.ansicht) return;
    const [b, h] = feldkarteGroesse();
    feldkarte.ansicht = window.Feldkarte.zoomen(feldkarte.ansicht, 2, ort(e), b, h);
    feldkarteZeichnen();
  });
  window.addEventListener('resize', () => feldkarteZeichnen());
})();

let jobFelderOffen = null;   // aufgeklappte Felder in der Liste der Arbeiten

function renderJobs(jobs, fields) {
  const host = el('jobList');
  host.innerHTML = '';
  const namen = Object.fromEntries((fields || []).map((f) => [f.id, f.name]));
  // Je Feld ein Abschnitt, die zuletzt bearbeiteten Felder zuerst.
  const gruppen = new Map();
  jobs.forEach((job) => {
    if (!gruppen.has(job.field_id)) gruppen.set(job.field_id, []);
    gruppen.get(job.field_id).push(job);
  });
  gruppen.forEach((liste, fieldId) => {
    const ha = liste.reduce((summe, job) => summe + job.area_ha, 0);
    const minuten = liste.reduce((summe, job) =>
      summe + (job.ended_at ? (job.ended_at - job.started_at) / 60 : 0), 0);
    if (!jobFelderOffen) jobFelderOffen = new Set([fieldId]);   // beim ersten Mal: das neueste Feld
    const offen = jobFelderOffen.has(fieldId);
    const kopf = document.createElement('button');
    kopf.type = 'button';
    kopf.className = 'gruppe klapp';
    kopf.setAttribute('aria-expanded', String(offen));
    kopf.innerHTML = '<b></b><span></span>';
    kopf.querySelector('b').textContent = `${offen ? '▾' : '▸'} ${namen[fieldId] || 'Unbekanntes Feld'}`;
    kopf.querySelector('span').textContent =
      `${liste.length} ${liste.length === 1 ? 'Arbeit' : 'Arbeiten'} · ${ha.toFixed(2)} ha · ${(minuten / 60).toFixed(1)} h`;
    kopf.onclick = () => {
      if (offen) jobFelderOffen.delete(fieldId); else jobFelderOffen.add(fieldId);
      renderJobs(jobs, fields);
    };
    host.appendChild(kopf);
    if (!offen) return;
    liste.forEach((job) => host.appendChild(jobZeile(job)));
    const fertige = liste.filter((job) => job.ended_at).length;
    if (fertige > 1) {
      const zeile = document.createElement('div');
      zeile.className = 'row feld-loeschen';
      const knopf = document.createElement('button');
      knopf.className = 'danger';
      knopf.textContent = `Alle ${fertige} Arbeiten von „${namen[fieldId] || 'Unbekanntes Feld'}“ löschen`;
      knopf.onclick = () => arbeitenLoeschen(`/api/jobs?field_id=${encodeURIComponent(fieldId)}`,
        `Alle ${fertige} Arbeiten auf „${namen[fieldId] || 'Unbekanntes Feld'}“ löschen?`);
      zeile.appendChild(knopf);
      host.appendChild(zeile);
    }
  });
  if (!jobs.length) host.innerHTML = '<p class="note">Noch keine Arbeiten aufgezeichnet.</p>';
  el('btnJobsAlleLoeschen').hidden = !jobs.some((job) => job.ended_at);
  myterraStand();
}

/* Arbeiten löschen: die Fläche geht mit aus der Karte, das Löschen per Abgleich an alle Geräte. */
async function arbeitenLoeschen(url, frage) {
  if (!confirm(`${frage}\n\nDie bearbeitete Fläche verschwindet auch aus der Karte. ` +
               'Das lässt sich nicht rückgängig machen.')) return;
  const antwort = await api('DELETE', url);
  if (!antwort) return;
  const d = antwort.data || antwort;
  toast(`${d.geloescht} ${d.geloescht === 1 ? 'Arbeit' : 'Arbeiten'} gelöscht` +
        (d.laufend_uebersprungen ? ' – die laufende bleibt' : ''));
  if (d.karte_neu && state.live && state.live.field) {
    const karte = await fetch(`/api/fields/${state.live.field.id}/coverage?hours=24`)
      .then((r) => r.json()).catch(() => null);
    if (karte) {
      flaeche.leeren();
      flaeche.zellenMalen(karte.cells || [], karte.cell_size || state.cellSize);
      zeichnenAnfordern();
    }
  }
  refreshLists();
}

el('btnJobsAlleLoeschen').onclick = () => arbeitenLoeschen('/api/jobs?alle=true',
  'Wirklich ALLE Arbeiten auf allen Feldern löschen?');

/* MyTerra: fertige Arbeiten als Datei für den Prüfeingang. Gebucht wird erst dort, nach Freigabe. */
const MYTERRA_TAETIGKEIT = { boden: 'Bodenbearbeitung', saat: 'Saat', walzen: 'Walzen', duengung: 'Düngung',
                             psm: 'Pflanzenschutz', ernte: 'Ernte', sonstiges: 'Sonstiges' };

async function myterraStand() {
  const liste = await api('GET', '/api/myterra/arbeiten');
  if (!liste) return;
  const neu = liste.filter((a) => !a.uebergeben).length;
  el('myterraInfo').textContent = liste.length
    ? `${neu} von ${liste.length} fertigen Arbeiten noch nicht an MyTerra übergeben. Die Datei kommt in MyTerra in den ` +
      'Prüfeingang – gebucht wird erst, wenn du sie dort freigibst.'
    : 'Noch keine fertigen Arbeiten mit Fläche.';
  el('btnMyterraExport').textContent = neu ? `${neu} neue Arbeiten für MyTerra exportieren` : 'Neue Arbeiten für MyTerra exportieren';
  const nach = Object.fromEntries(liste.map((a) => [a.id, a]));
  document.querySelectorAll('#jobList [data-job]').forEach((zeile) => {
    const a = nach[zeile.dataset.job];
    const sub = zeile.querySelector('.sub, small, span:nth-of-type(2)');
    if (!a || !sub || zeile.dataset.myterra) return;
    zeile.dataset.myterra = '1';
    sub.textContent += ` · MyTerra: ${MYTERRA_TAETIGKEIT[a.taetigkeit] || a.taetigkeit}` +
      (a.uebergeben ? ' · übergeben' : '');
  });
}

async function myterraDatei(parameter) {
  const antwort = await fetch(`/api/myterra/export?${parameter}`);
  if (!antwort.ok) { toast('Export fehlgeschlagen', true); return; }
  const anzahl = Number(antwort.headers.get('X-Anzahl') || 0);
  if (!anzahl) { toast('Nichts Neues zu übergeben'); return; }
  const blob = await antwort.blob();
  const name = (antwort.headers.get('Content-Disposition') || '').match(/filename="([^"]+)"/);
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = name ? name[1] : 'agripilot-arbeiten.json';
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 10000);
  toast(`${anzahl} Arbeit(en) exportiert – in MyTerra einlesen`);
  myterraStand();
}

el('btnMyterraExport').onclick = () => myterraDatei(el('myterraAlle').checked ? 'alle=1' : '');

/* Wie genau gelenkt wurde - eine Zeile (genauigkeit.py). */
function genauigkeitText(g) {
  if (!g) return '';
  const z = (x, n = 1) => Number(x).toLocaleString('de-DE', { minimumFractionDigits: n, maximumFractionDigits: n });
  return `${z(g.effektiv_cm)} cm effektiv · 95 % unter ${z(g.p95_cm)} cm · höchstens ${z(g.max_cm, 0)} cm · ` +
    `${z(g.lenkanteil * 100, 0)} % gelenkt` + (g.abgaben ? ` · ${g.abgaben}× abgegeben` : '') +
    // Warum (ab 1.5.81): „3× zu weit von der Spur, 2× GPS-Daten veraltet“
    (g.abgabe_gruende && Object.keys(g.abgabe_gruende).length
      ? ` (${Object.entries(g.abgabe_gruende).map(([grund, n]) => `${n}× ${grund}`).join(', ')})` : '');
}

async function renderGenauigkeit() {
  const jobs = await fetch('/api/jobs').then((r) => r.json()).catch(() => []);
  const host = el('genauigkeitListe');
  host.innerHTML = '';
  const mit = jobs.filter((j) => j.genauigkeit && j.ended_at).slice(0, 8);
  if (!mit.length) {
    host.innerHTML = '<p class="note">Noch keine Arbeit mit Lenkung aufgezeichnet.</p>';
    return;
  }
  mit.forEach((j) => host.appendChild(item({
    title: `${new Date(j.started_at * 1000).toLocaleDateString('de-DE')} · ${j.operation || 'Arbeit'} · ${j.area_ha.toFixed(2)} ha`,
    sub: genauigkeitText(j.genauigkeit),
    actions: [],
  })));
}

function jobZeile(job) {
  const started = new Date(job.started_at * 1000);
  const minutes = job.ended_at ? (job.ended_at - job.started_at) / 60 : null;
  const row = item({
    title: `${job.operation || 'Arbeit'} · ${job.area_ha.toFixed(2)} ha`,
    sub: `${started.toLocaleString('de-DE')} · ${job.vehicle || 'Traktor'}` +
         (minutes ? ` · ${minutes.toFixed(0)} min` : ' · läuft') +
         ` · ${(job.distance_m / 1000).toFixed(1)} km` +
         (job.overlap_ha ? ` · ${job.overlap_ha.toFixed(2)} ha doppelt` : '') +
         (job.genauigkeit ? `\nLenkung: ${genauigkeitText(job.genauigkeit)}` : ''),
    actions: [],
  });
  row.dataset.job = job.id;
  const actions = row.querySelector('.actions');
  if(job.ended_at){const b=document.createElement('button');b.textContent='Arbeitslinie übernehmen';b.onclick=()=>feldwerkzeuge.referenz(job);actions.appendChild(b);}
  if (job.ended_at && job.area_ha > 0) {
    const knopf = document.createElement('button');
    knopf.textContent = 'MyTerra';
    knopf.title = 'Diese Arbeit als Datei für MyTerra exportieren';
    knopf.onclick = () => myterraDatei(`ids=${job.id}`);
    actions.appendChild(knopf);
  }
  ['gpx', 'geojson', 'csv'].forEach((format) => {
    const link = document.createElement('a');
    link.className = 'button';
    link.href = `/api/jobs/${job.id}/${format}`;
    link.textContent = format.toUpperCase();
    actions.appendChild(link);
  });
  if (job.ended_at) {
    const weg = document.createElement('button');
    weg.className = 'danger abgesetzt';
    weg.textContent = 'Löschen';
    weg.onclick = () => arbeitenLoeschen(`/api/jobs/${job.id}`,
      `Arbeit „${job.operation || 'Arbeit'}“ vom ${started.toLocaleString('de-DE')} löschen?`);
    actions.appendChild(weg);
  }
  return row;
}

function fillProfile() {
  const profile = state.live && state.live.profile;
  if (!profile) return;
  el('autoSections').checked = !!(state.live && state.live.auto_sections);
  if (formularOffen('profileForm')) return;
  document.querySelectorAll('#profileForm input, #profileForm select').forEach((input) => {
    if (profile[input.name] === undefined) return;
    if (input.type === 'checkbox') input.checked = !!profile[input.name];
    else input.value = profile[input.name];
  });
}

async function renderBackup() {
  const antwort = await api('GET', '/api/backup');
  if (!antwort) return;
  const orte = (antwort.orte || antwort.data && antwort.data.orte || []);
  el('backupInfo').textContent = orte.map((o) => {
    if (!o.vorhanden) return `${o.ordner}: noch keine Sicherung (oder nicht erreichbar)`;
    if (!o.letzte) return `${o.ordner}: noch keine`;
    return `${o.ordner}: ${new Date(o.letzte.zeit * 1000).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' })}`;
  }).join(' · ') || 'keine Sicherung eingerichtet';
}

el('btnBackup').onclick = async () => {
  const ergebnis = await api('POST', '/api/backup');
  if (ergebnis) toast('Gesichert');
  renderBackup();
};

function renderSystem() {
  renderBackup();
  renderDatenRolle();
  const system = state.live && state.live.system;
  const host = el('systemList');
  if (!system) { host.innerHTML = '<p class="note">Keine Daten.</p>'; return; }
  const imu = state.live.imu;
  el('imuRow').hidden = !imu;
  el('imuNote').hidden = !imu;
  el('imuKalibRow').hidden = !imu;
  el('imuKalibNote').hidden = !imu;
  if (imu) {
    // Was die Fahrt über die Einbaulage verraten hat (Gier aus der Kurve, Längs aus der RTK-Höhe).
    const pruef = (geprueft, vz) => (geprueft ? (vz < 0 ? 'geprüft, umgedreht' : 'geprüft') : 'noch nicht geprüft');
    el('imuNote').textContent = 'Zum Nullen auf ebenem Boden stehen – ein Grad Montagefehler sind bei 3 m ' +
      `Antennenhöhe schon 5 cm Dauerversatz. Drehrichtung: ${pruef(imu.gier_geprueft, imu.gier_vorzeichen)} · ` +
      `Längsneigung: ${pruef(imu.laengs_geprueft, imu.laengs_vorzeichen)} (am Hang fahren).`;
  }
  if (imu) el('compensation').checked = !!imu.compensation;

  const steering = state.live.steering;
  const output = system.steering_output;
  // Die Mitte lässt sich nur lernen, wo ein Drehgeber zählt.
  el('motorTestRow').hidden = !(output && output.typ === 'phidget' && output.bereit);
  el('lenkAssistent').hidden = !(output && output.bereit);
  el('einmessBereich').hidden = !(output && output.typ === 'phidget');
  renderAbstimmung();
  el('steerRow').hidden = !(output && output.mitte_gelernt !== undefined);
  el('lenkWerkzeugeLeer').hidden = !(el('imuRow').hidden && el('motorTestRow').hidden && el('steerRow').hidden);
  if (!el('steerRow').hidden) {
    el('steerInfo').textContent =
      `${output.zaehlwerte_je_grad} Zählwerte je Grad · ` +
      (output.mitte_gelernt ? `Ist ${(output.radwinkel || 0).toFixed(1)}° · ` +
        `Soll ${(output.soll_grad || 0).toFixed(1)}°`
        : 'Mitte noch nicht gelernt – bis dahin gilt die Stellung beim Scharfschalten als gerade');
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
    ...(system.corrections.rtcm && system.corrections.rtcm.rahmen ? [['RTCM3 vom Caster',
      rtcmText(system.corrections.rtcm) +
      ` · ${((system.corrections.an_empfaenger || 0) / 1024).toFixed(0)} kB an den Empfänger`,
      !system.corrections.hinweis]] : []),
    ...(system.corrections.hinweis
      ? [['Warum kein RTK?', system.corrections.hinweis, false]] : []),
    ['Korrektur-Weitergabe', system.relay.running
      ? `aktiv · ${system.relay.clients} Traktor(en)` : 'aus', system.relay.running],
    ['Abgleich', system.sync.status + (system.sync.age_s != null
      ? ` · vor ${system.sync.age_s.toFixed(0)} s` : ''), true],
  ];
  host.innerHTML = '';
  rows.forEach(([label, value, good]) => {
    // Als Text, nicht als HTML: hier stehen auch Antworten fremder Caster.
    const row = document.createElement('div');
    row.className = 'item';
    const main = document.createElement('div');
    main.className = 'main';
    const title = document.createElement('div');
    title.className = 'title';
    title.textContent = label;
    const sub = document.createElement('div');
    sub.className = 'sub';
    sub.textContent = value;
    main.append(title, sub);
    const chip = document.createElement('div');
    chip.className = `chip ${good ? 'good' : 'warn'}`;
    chip.textContent = good ? 'OK' : 'prüfen';
    row.append(main, chip);
    host.appendChild(row);
  });
  (system.devices || []).forEach((device) => {
    const age = (Date.now() / 1000 - device.last_seen);
    const row = document.createElement('div');
    row.className = 'item';
    const main = document.createElement('div'); main.className = 'main';
    const title = document.createElement('div'); title.className = 'title';
    title.textContent = device.name;
    const sub = document.createElement('div'); sub.className = 'sub';
    sub.textContent = `${device.role} · zuletzt vor ${age < 90 ? age.toFixed(0) + ' s'
      : (age / 60).toFixed(0) + ' min'}`;
    main.append(title, sub); row.append(main);
    host.appendChild(row);
  });
  el('simRow').hidden = system.gnss.source !== 'simulator';
}

/* Einstellungen: die Oberfläche baut sich aus dem Schema des Servers auf, damit
   sie nicht davon abdriften kann. Gespeichert wird nur, was wirklich geändert
   wurde - so überschreibt ein Tablet nicht die Eingaben eines anderen. */
const settings = { geladen: false, werte: {}, gruppen: [] };
const LENK_GRUPPEN = ['steering', 'phidget'];   // stehen auf der Seite Lenkung
const GERAET_GRUPPEN = ['teilbreiten'];         // stehen bei Traktor & Gerät

async function loadSettings() {
  const daten = await (await fetch('/api/settings')).json();
  settings.gruppen = daten.gruppen;
  settings.werte = daten.werte;
  settings.geladen = true;
  el('settingsGroups').innerHTML = '';
  el('settingsLenkung').innerHTML = '';
  el('settingsGeraet').innerHTML = '';
  daten.gruppen.forEach((gruppe) => {
    const host = el(LENK_GRUPPEN.includes(gruppe.id) ? 'settingsLenkung'
      : GERAET_GRUPPEN.includes(gruppe.id) ? 'settingsGeraet' : 'settingsGroups');
    const box = document.createElement('details');
    box.className = 'setting-group';
    box.open = false;
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
    if (gruppe.id === 'corrections') box.appendChild(ntripWerkzeug());
    host.appendChild(box);
  });
  el('settingsInfo').textContent = daten.datei
    ? `Datei: ${daten.datei}` : 'Noch keine Konfigurationsdatei – beim Übernehmen wird eine angelegt.';
}

/* NTRIP: Mountpoints vom Caster holen und die Verbindung probieren, bevor man
   speichert. Beides nimmt, was gerade in der Maske steht - nicht das Gespeicherte. */
function rtcmText(rtcm) {
  const teile = [`${rtcm.rahmen} Nachrichten`];
  if (rtcm.systeme.length) teile.push(rtcm.systeme.join('/'));
  if (rtcm.station != null) teile.push(`Basis ${rtcm.station}`);
  if (rtcm.basis_km != null) teile.push(`${rtcm.basis_km.toFixed(1)} km entfernt`);
  if (rtcm.crc_fehler) teile.push(`${rtcm.crc_fehler} kaputte`);
  return teile.join(' · ');
}

function casterAngaben() {
  const wert = (schluessel) => {
    const input = document.getElementById(`set-corrections.${schluessel}`);
    if (!input) return undefined;
    return input.type === 'checkbox' ? input.checked : input.value;
  };
  return {
    host: wert('host'), port: wert('port'), mountpoint: wert('mountpoint'),
    username: wert('username'), password: wert('password'), send_gga: wert('send_gga'),
  };
}

function ntripWerkzeug() {
  const box = document.createElement('div');
  box.className = 'ntrip-tool';
  const knoepfe = document.createElement('div');
  knoepfe.className = 'ntrip-buttons';
  const holen = document.createElement('button');
  holen.type = 'button';
  holen.textContent = 'Mountpoints abrufen';
  const testen = document.createElement('button');
  testen.type = 'button';
  testen.textContent = 'Verbindung testen';
  knoepfe.append(holen, testen);
  const liste = document.createElement('select');
  liste.className = 'ntrip-list';
  liste.hidden = true;
  const ergebnis = document.createElement('div');
  ergebnis.className = 'ntrip-result';
  box.append(knoepfe, liste, ergebnis);

  const melden = (text, gut) => {
    ergebnis.textContent = text;
    ergebnis.className = `ntrip-result ${gut ? 'good' : 'bad'}`;
  };

  holen.onclick = async () => {
    holen.disabled = true;
    melden('Frage den Caster …', true);
    const antwort = await api('POST', '/api/ntrip/mountpoints', casterAngaben());
    holen.disabled = false;
    if (!antwort) { melden('Keine Liste – siehe Meldung oben.', false); return; }
    const punkte = antwort.data.mountpoints;
    if (!punkte.length) { melden('Der Caster kennt keine Mountpoints.', false); return; }
    liste.innerHTML = '';
    const leer = document.createElement('option');
    leer.value = '';
    leer.textContent = `– ${antwort.data.anzahl} Mountpoints, nächste zuerst –`;
    liste.appendChild(leer);
    punkte.forEach((p) => {
      const option = document.createElement('option');
      option.value = p.mountpoint;
      option.textContent = [p.mountpoint, p.km != null ? `${p.km.toFixed(1)} km` : '',
        p.format, p.systeme, p.netz ? 'Netz/VRS' : '', p.gebuehr ? 'kostenpflichtig' : '']
        .filter(Boolean).join(' · ');
      liste.appendChild(option);
    });
    liste.hidden = false;
    melden('Mountpoint auswählen, dann „Verbindung testen“ und speichern.', true);
  };

  liste.onchange = () => {
    if (!liste.value) return;
    const feld = document.getElementById('set-corrections.mountpoint');
    feld.value = liste.value;
    feld.dispatchEvent(new Event('input'));
    const quelle = document.getElementById('set-corrections.source');
    if (quelle && quelle.value !== 'ntrip') {
      quelle.value = 'ntrip';
      quelle.dispatchEvent(new Event('change'));
    }
  };

  testen.onclick = async () => {
    testen.disabled = true;
    melden('Verbinde und lese 8 Sekunden mit …', true);
    const antwort = await api('POST', '/api/ntrip/test', casterAngaben());
    testen.disabled = false;
    if (!antwort) { melden('Test nicht möglich – siehe Meldung oben.', false); return; }
    const t = antwort.data;
    if (t.fehler) { melden(t.fehler, false); return; }
    const kopf = `Verbunden (NTRIP ${t.version}${t.chunked ? ', in Stücken' : ''}) · ` +
      `${(t.bytes / 1024).toFixed(1)} kB`;
    const rtcm = t.rtcm && t.rtcm.rahmen ? ` · ${rtcmText(t.rtcm)}` : '';
    melden(t.ok ? `${kopf}${rtcm} – passt, jetzt speichern.`
      : `${kopf}${rtcm} – ${t.urteil}`, t.ok);
  };
  return box;
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

async function einstellungenSpeichern() {
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
}

el('btnSaveSettings').onclick = einstellungenSpeichern;
el('btnSaveSettingsLenkung').onclick = einstellungenSpeichern;
el('btnSaveSettingsGeraet').onclick = einstellungenSpeichern;

/* Lenkung einrichten: sieben Schritte, jeder mit seinem Werkzeug. Was ein
 * Schritt braucht, steht in ihm selbst; spätere bleiben gesperrt, bis die
 * früheren erledigt sind (nur der Not-Aus-Schritt steht frei). */
const EINRICHTEN_TEXT = {
  verbindung: 'Sucht die Phidget-Motorsteuerung (Phidget21 oder Phidget22) und stellt den Lenkausgang darauf ein. Das AgriPilot Terminal vorher schließen.',
  richtung: 'Der Motor lenkt kurz nach rechts. Sag dann, wohin die Räder gingen - AgriPilot stellt Motor- und Drehgeberrichtung selbst richtig. Danach zur Kontrolle noch einmal.',
  mindestleistung: 'Kurze Impulse links und rechts, stufenweise stärker: ab wann läuft der Motor an? Daraus die Mindestleistung.',
  zaehlwerte: 'Starten, von Hand bis zum linken und rechten Anschlag lenken, beenden. Mit dem Gesamteinschlag der Räder am Boden ergeben sich die Zählwerte je Grad.',
  feinabstimmung: 'Der Motorregler fährt aus der Mitte 6° nach rechts und zurück, mehrmals, und stellt P/I/D und bei Bedarf die Größte Leistung nach, bis der Radwinkel schnell und ohne Überschwingen sitzt (bis etwa 60 Sekunden). Motor des Traktors läuft (Servolenkung), Räder in der Mitte.',
  notaus: 'Not-Aus einbauen und prüfen, dann unten in der Liste „Lenkmotor anbauen“ abhaken.',
  freigabe: 'Erst jetzt darf die Automatik lenken. Scharf geschaltet wird weiterhin von dir, mit geraden Rädern.',
  probefahrt: 'Die Feinabstimmung der Spurregelung: auf einer AB-Spur mit 5–8 km/h geradeaus, Lenkung scharf. Der Lenk-Assistent stellt Verstärkung und Vorhalt nach, bei trägem Motor die Regelart – erst nur als Vorschlag, dann auf Wunsch selbst. Erledigt, sobald er „gut“ meldet.',
};
const lenkWerkstatt = new LenkWerkstatt({el,api});
let einrichtenStand = null;
let einrichtenErgebnis = null;

const einrichtenOffen = new Set();   // von Hand aufgeklappte Schritte

async function renderEinrichten() {
  lenkWerkstatt.laden();
  const antwort = await fetch('/api/lenkung/einrichten').then((r) => r.json()).catch(() => null);
  if (!antwort || !antwort.data) return;
  einrichtenStand = antwort.data;
  const liste = el('einrichtenListe');
  liste.innerHTML = '';
  const anstehend = einrichtenStand.schritte.findIndex((s) => !s.fertig);
  einrichtenStand.schritte.forEach((s, i) => {
    const li = document.createElement('li');
    li.className = s.fertig ? 'fertig' : s.moeglich ? '' : 'gesperrt';
    const offen = i === anstehend || einrichtenOffen.has(s.id);
    if (!offen) li.classList.add('zu');
    li.innerHTML = `<div class="kopf"><span class="haken">${s.fertig ? '✓' : i + 1}</span><span></span></div>` +
      '<p class="schritt-text"></p><div class="tasten"></div>';
    li.querySelector('.kopf span:last-child').textContent = s.titel;
    if (i !== anstehend) {
      // Erledigte und spätere Schritte aufklappen, um einen zu wiederholen.
      const kopf = li.querySelector('.kopf');
      kopf.setAttribute('role', 'button');
      kopf.tabIndex = 0;
      kopf.setAttribute('aria-expanded', String(offen));
      kopf.onclick = () => {
        if (einrichtenOffen.has(s.id)) einrichtenOffen.delete(s.id); else einrichtenOffen.add(s.id);
        renderEinrichten();
      };
      kopf.onkeydown = (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); kopf.onclick(); } };
    }
    li.querySelector('.schritt-text').textContent = EINRICHTEN_TEXT[s.id] || '';
    const tasten = li.querySelector('.tasten');
    const taste = (text, fn, primaer) => {
      const b = document.createElement('button');
      b.textContent = text;
      if (primaer) b.className = 'primary';
      b.disabled = !s.moeglich;
      b.onclick = fn;
      tasten.appendChild(b);
      return b;
    };
    if (s.id === 'verbindung') taste('Geräte suchen und übernehmen', einrichtenVerbinden, !s.fertig);
    if (s.id === 'richtung') {
      taste('Kurz nach rechts lenken', einrichtenRichtungTest, !s.fertig);
    }
    if (s.id === 'mindestleistung') taste('Motor einmessen', einrichtenEinmessen, !s.fertig);
    if (s.id === 'zaehlwerte') {
      taste(anschlagLaeuftE ? 'Anschläge fertig' : 'Anschläge messen', einrichtenAnschlag, !s.fertig);
    }
    if (s.id === 'feinabstimmung') taste('Feinabstimmung starten', einrichtenFeinabstimmung, !s.fertig);
    if (s.id === 'notaus') taste('Zur Einbau-Liste', () => el('setupList').scrollIntoView({ behavior: 'smooth' }));
    if (s.id === 'freigabe') taste('Lenkung freigeben', einrichtenFreigeben, !s.fertig);
    if (s.id === 'probefahrt') {
      taste('Nur Vorschläge', () => einrichtenProbefahrt('beobachten'), !s.fertig);
      taste('Stellt selbst nach', () => einrichtenProbefahrt('automatisch'));
    }
    liste.appendChild(li);
  });
}

function einrichtenMelden(text, knoepfe) {
  const info = el('einrichtenInfo');
  info.textContent = text;
  (knoepfe || []).forEach(([beschriftung, fn]) => {
    const b = document.createElement('button');
    b.textContent = beschriftung;
    b.style.margin = '6px 8px 0 0';
    b.onclick = fn;
    info.appendChild(document.createElement('br'));
    info.appendChild(b);
  });
}

async function einrichtenVerbinden() {
  einrichtenMelden('Suche Geräte …');
  const funde = await fetch('/api/geraete/suchen').then((r) => r.json()).catch(() => null);
  const vorschlag = funde && (funde.vorschlag || (funde.data && funde.data.vorschlag));
  const phidget = vorschlag && Object.fromEntries(Object.entries(vorschlag)
    .filter(([k]) => k.startsWith('phidget.') || k === 'steering.output'));
  if (!phidget || !phidget['steering.output']) {
    const probleme = (funde && (funde.probleme || (funde.data && funde.data.probleme))) || [];
    einrichtenMelden('Keine Lenkmotorsteuerung gefunden. ' + probleme.filter((p) => /phidget/i.test(p)).join(' · '));
    return;
  }
  const antwort = await api('POST', '/api/settings', { aenderungen: phidget });
  if (antwort) einrichtenMelden(`Übernommen: ${antwort.data.lenkung || 'Lenkausgang eingestellt'}.`);
  renderEinrichten();
}

async function einrichtenRichtungTest() {
  einrichtenMelden('Motor lenkt kurz nach rechts …');
  const antwort = await api('POST', '/api/lenkung/einrichten/richtung-test');
  if (!antwort) { einrichtenMelden('Test nicht ausgeführt.'); return; }
  const b = antwort.data.bewegt;
  einrichtenMelden(`Drehgeber: ${b === null ? 'nicht auslesbar' : (b > 0 ? '+' : '') + b + ' Zählwerte'}. Wohin sind die Räder gegangen?`,
    [['Nach rechts', () => einrichtenRichtung('rechts')], ['Nach links', () => einrichtenRichtung('links')],
     ['Gar nicht', () => einrichtenRichtung('nichts')]]);
}

async function einrichtenRichtung(gesehen) {
  const antwort = await api('POST', '/api/lenkung/einrichten/richtung', { gesehen });
  if (antwort) einrichtenMelden(antwort.data.text);
  renderEinrichten();
}

async function einrichtenEinmessen() {
  einrichtenMelden('Motor wird eingemessen … (etwa 10 Sekunden, der Motor dreht kurz hin und her)');
  const antwort = await api('POST', '/api/steering/einmessen');
  if (!antwort) { einrichtenMelden('Nicht eingemessen.'); return; }
  const d = antwort.data;
  const l = (x) => (x === null ? 'keine Bewegung' : `${Math.round(x * 100)} %`);
  if (!d.mindestleistung_vorschlag) { einrichtenMelden(`Rechts ${l(d.losbrech_rechts)}, links ${l(d.losbrech_links)}. ${d.hinweis}`); return; }
  einrichtenMelden(`Läuft an bei rechts ${l(d.losbrech_rechts)}, links ${l(d.losbrech_links)}. ` +
    `Mindestleistung ${Math.round(d.mindestleistung_vorschlag * 100)} %.`,
    [['Übernehmen', async () => {
      if (await api('POST', '/api/settings', { aenderungen: { 'phidget.min_duty': d.mindestleistung_vorschlag } })) {
        await api('POST', '/api/lenkung/einrichten/erledigt', { schritt: 'mindestleistung', werte: { min_duty: d.mindestleistung_vorschlag } });
        einrichtenMelden('Mindestleistung übernommen.');
        renderEinrichten();
      }
    }]]);
}

let anschlagLaeuftE = false;
async function einrichtenAnschlag() {
  if (!anschlagLaeuftE) {
    if (!(await api('POST', '/api/steering/anschlag', { aktion: 'start' }))) return;
    anschlagLaeuftE = true;
    einrichtenMelden('Jetzt von Hand bis zum linken, dann bis zum rechten Anschlag lenken, danach „Anschläge fertig“.');
    renderEinrichten();
    return;
  }
  anschlagLaeuftE = false;
  const winkel = parseFloat(el('einrichtenWinkel').value || '70');
  const antwort = await api('POST', '/api/steering/anschlag', { aktion: 'ende', gesamtwinkel_deg: winkel });
  renderEinrichten();
  if (!antwort) { einrichtenMelden('Nicht gemessen.'); return; }
  const d = antwort.data;
  einrichtenMelden(`${d.zaehlwerte} Zählwerte über ${d.gesamtwinkel_deg}° = ${String(d.zaehlwerte_je_grad).replace('.', ',')} Zählwerte je Grad.`,
    [['Übernehmen', async () => {
      const werte = { 'phidget.counts_per_deg': d.zaehlwerte_je_grad, 'phidget.encoder_counts_per_deg': d.zaehlwerte_je_grad };
      if (await api('POST', '/api/settings', { aenderungen: werte })) {
        await api('POST', '/api/lenkung/einrichten/erledigt', { schritt: 'zaehlwerte', werte: { zaehlwerte_je_grad: d.zaehlwerte_je_grad } });
        einrichtenMelden('Zählwerte je Grad übernommen.');
        renderEinrichten();
      }
    }]]);
}

async function einrichtenFreigeben() {
  const antwort = await api('POST', '/api/settings', { aenderungen: { 'steering.enabled': true } });
  if (antwort) einrichtenMelden('Lenkung freigegeben. Scharf schaltest du sie weiter selbst - mit geraden Rädern.');
  renderEinrichten();
}

async function einrichtenProbefahrt(modus) {
  if (await api('POST', '/api/abstimmung', { modus })) {
    einrichtenMelden((modus === 'automatisch'
      ? 'Lenk-Assistent stellt selbst nach (in kleinen Schritten, Weg zurück unter Menü → System). '
      : 'Lenk-Assistent macht Vorschläge (Menü → System). ') +
      'Jetzt auf einer AB-Spur 5–8 km/h geradeaus fahren, Lenkung scharf, Not-Aus in Reichweite. ' +
      'Nach 20 Sekunden ruhiger Fahrt kommt die erste Bewertung.');
  }
  renderEinrichten();
}

async function einrichtenFeinabstimmung() {
  einrichtenMelden('Feinabstimmung läuft … der Motor fährt mehrmals 6° hin und zurück (bis etwa 60 Sekunden). ' +
    'Lenkrad nicht festhalten, Not-Aus in Reichweite.');
  const antwort = await api('POST', '/api/lenkung/einrichten/feinabstimmung');
  if (!antwort) { einrichtenMelden('Feinabstimmung nicht ausgeführt.'); renderEinrichten(); return; }
  const e = antwort.data;
  const z = (x) => Number(x).toLocaleString('de-DE', { maximumFractionDigits: 3 });
  const zeilen = [e.text,
    `P ${z(e.start.gain_p)} → ${z(e.ende.gain_p)} · I ${z(e.start.gain_i)} → ${z(e.ende.gain_i)} · ` +
    `D ${z(e.start.gain_d)} → ${z(e.ende.gain_d)}` +
    (e.start.max_duty !== undefined ? ` · Größte Leistung ${z(e.start.max_duty)} → ${z(e.ende.max_duty)}` : '') +
    (e.gut ? ' (gespeichert)' : ' (Ausgangswerte bleiben)')];
  e.verlauf.forEach((v, i) => zeilen.push(`${i + 1}. P ${z(v.p)} I ${z(v.i)} D ${z(v.d)}` +
    (v.leistung !== undefined ? ` Leistung ${z(v.leistung)}` : '') + ': ' +
    `90 % nach ${v.hin.anstieg_s === null ? '–' : z(v.hin.anstieg_s) + ' s'}, ` +
    `Überschwingen ${Math.round(v.hin.ueberschwingen * 100)} %, Rest ${z(v.hin.rest_deg)}° – ${v.text}`));
  einrichtenMelden(zeilen.join(String.fromCharCode(10)));
  renderEinrichten();
}

el('btnMotorAbbrechen').onclick = async () => {
  if (await api('POST', '/api/steering/disarm')) {
    einrichtenMelden('Motor angehalten. Werkstattlauf abgebrochen.');
  }
};

el('btnEinrichtenNeu').onclick = async () => {
  if (await api('POST', '/api/lenkung/einrichten/neu')) { einrichtenMelden(''); renderEinrichten(); }
};

function renderSetup(stand) {
  renderEinrichten();
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
                       `abgeschaltet und die Lenkung geht aus – das System läuft ` +
                       `danach auf der Aufzeichnung, nicht auf der Wirklichkeit. ` +
                       `Zurück über Einstellungen → Empfänger → Quelle.`)) return;
          const ok_ = await api('POST', '/api/settings', { aenderungen: {
            'gnss.source': 'replay', 'gnss.replay_file': datei.datei,
          }});
          if (ok_) { toast('Aufzeichnung läuft – Lenkung ist aus'); el('sheet').hidden = true; }
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
  // Vorn nur die zwei Handgriffe, die man ständig braucht; der Rest (Umbenennen,
  // Grenze, Löschen) klappt unter "Mehr" auf - eine Zeile statt einer Knopfwand.
  const alle = actions || [];
  const vorn = alle.length > 3 ? alle.slice(0, 2) : alle;
  const hinten = alle.length > 3 ? alle.slice(2) : [];
  const knopf = ([label, handler]) => {
    const button = document.createElement('button');
    button.textContent = label;
    button.onclick = handler;
    // Laden ist der Handgriff im Feld; Löschen steht rot und abgesetzt am Ende,
    // damit es mit dem Handschuh auf dem rüttelnden Sitz nicht getroffen wird.
    if (label === 'Laden') button.classList.add('primary');
    if (label === 'Löschen') button.classList.add('danger', 'abgesetzt');
    return button;
  };
  vorn.forEach((a) => box.appendChild(knopf(a)));
  row.append(main, box);
  if (hinten.length) {
    const mehr = document.createElement('button');
    mehr.className = 'mehr';
    mehr.textContent = 'Mehr ▾';
    mehr.setAttribute('aria-expanded', 'false');
    const leiste = document.createElement('div');
    leiste.className = 'actions mehr-leiste';
    leiste.hidden = true;
    hinten.forEach((a) => leiste.appendChild(knopf(a)));
    mehr.onclick = () => {
      leiste.hidden = !leiste.hidden;
      mehr.setAttribute('aria-expanded', String(!leiste.hidden));
      mehr.textContent = leiste.hidden ? 'Mehr ▾' : 'Weniger ▴';
    };
    box.appendChild(mehr);
    row.appendChild(leiste);
  }
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

/* Im eigenen Fenster (Windows): F11 schaltet auf Vollbild, wie man es kennt. Im
   Browser macht das der Browser selbst - dort ist window.pywebview nicht da. */
document.addEventListener('keydown', (event) => {
  if (event.key === 'F11' && window.pywebview && window.pywebview.api) {
    event.preventDefault();
    window.pywebview.api.vollbild();
  }
});

/* ------------------------------------------------------------------ Start */

kartenfarben = new window.KartenfarbenModul.Kartenfarben({
  el,
  speicher: {getItem: key => localStorage.getItem(key), setItem: (key, wert) => localStorage.setItem(key, wert)},
  stil: document.body.style,
  geaendert: () => {farbenLesen(); zeichnenAnfordern();},
});
farbenLesen();
tagSetzen(gemerkt('agripilot.tag') === '1');
ansichtSetzen(gemerkt('agripilot.ansicht') || 'perspektive');
resize();
connect();
bildschirmWachhalten();
requestAnimationFrame(render);
menueAusAdresse();
setInterval(() => { if (!el('sheet').hidden) refreshLists(true); }, 5000);
