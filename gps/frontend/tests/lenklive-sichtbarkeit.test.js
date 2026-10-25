const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

// Die echte Renderfunktion aus app.js mit protokollierenden Abhängigkeiten prüfen.
const quelle = fs.readFileSync(path.join(__dirname, '../app.js'), 'utf8');
const start = quelle.indexOf('function renderLenkLive() {');
const ende = quelle.indexOf("el('btnLenkLiveKarte').onclick", start);
assert.ok(start >= 0 && ende > start);

function umgebung({ menue = false, karte = false, tab = 'lenkung', sichtbar = true } = {}) {
  const aufrufe = { kennzahlen: 0, befund: 0, zeichnungen: [] };
  const elemente = {
    sheet: { hidden: !menue }, lenkLiveFenster: { hidden: !karte }, lenkLiveZeit: { value: '60' },
    lenkLiveGross: {}, lenkLiveKlein: {}, lenkLiveBefund: { dataset: {} },
    lenkLiveTipps: { replaceChildren() {} }, lenkLiveZahlen: {}, lenkLiveKurz: { dataset: {} }, lenkLiveArbeit: {},
  };
  const kontext = {
    state: { live: { profile: { regelart: 'voraus' }, abstimmung: { schritte: [] } } },
    document: { visibilityState: sichtbar ? 'visible' : 'hidden',
      querySelector: () => ({ dataset: { tab } }), createElement: () => ({}) },
    el: id => elemente[id], lenkLiveZahlen: () => 'Messwerte',
    lenkAufzeichnung: { kennzahlen() { aufrufe.kennzahlen++; return { genug: true, effektiv: 1 }; } },
    LenkLive: { befund() { aufrufe.befund++; return { art: 'gut', text: 'Gut', tipps: [] }; },
      zeichnen(ziel) { aufrufe.zeichnungen.push(ziel); } },
  };
  vm.createContext(kontext);
  vm.runInContext(quelle.slice(start, ende), kontext);
  return { aufrufe, elemente, kontext };
}

test('geschlossene oder verdeckte Live-Anzeige berechnet keine Kennzahlen', () => {
  for (const optionen of [{}, { menue: true, tab: 'felder', karte: true }, { sichtbar: false, karte: true }]) {
    const u = umgebung(optionen);
    u.kontext.renderLenkLive();
    assert.equal(u.aufrufe.kennzahlen, 0);
    assert.equal(u.aufrufe.befund, 0);
    assert.equal(u.aufrufe.zeichnungen.length, 0);
  }
});

test('Lenkungsseite und Kartenfenster zeichnen die jeweils sichtbare Anzeige', () => {
  for (const [optionen, ziel] of [[{ menue: true }, 'lenkLiveGross'], [{ karte: true }, 'lenkLiveKlein']]) {
    const u = umgebung(optionen);
    u.kontext.renderLenkLive();
    assert.equal(u.aufrufe.kennzahlen, 1);
    assert.equal(u.aufrufe.befund, 1);
    assert.deepEqual(u.aufrufe.zeichnungen, [u.elemente[ziel]]);
  }
});

test('nach Wiederöffnen wird die Auswertung wieder aktiviert', () => {
  const u = umgebung();
  u.kontext.renderLenkLive();
  u.elemente.lenkLiveFenster.hidden = false;
  u.kontext.renderLenkLive();
  assert.equal(u.aufrufe.kennzahlen, 1);
  assert.equal(u.elemente.lenkLiveKurz.dataset.art, 'gut');
});

test('echte Zustandsaufnahme sammelt verdeckt weiter und zeigt aktuelle Kennzahlen', () => {
  const { Aufzeichnung, befund } = require('../lenklive.js');
  const aufzeichnung = new Aufzeichnung();
  const elemente = {
    sheet: { hidden: true }, lenkLiveFenster: { hidden: true },
    lenkLiveKurz: { dataset: {} }, lenkLiveKlein: {}, lenkLiveZeit: { value: '60' },
  };
  let kennzahlenAufrufe = 0;
  const echteKennzahlen = aufzeichnung.kennzahlen.bind(aufzeichnung);
  aufzeichnung.kennzahlen = () => { kennzahlenAufrufe++; return echteKennzahlen(); };
  const kontext = {
    state: { live: null, trail: [] }, lenkAufzeichnung: aufzeichnung,
    glaettungNachfuehren() {}, reglerKontextPruefen() {}, saatKontextPruefen() {}, performance: { now: () => 1 }, geraeteLage: () => null,
    flaeche: {}, zeichnenAnfordern() {}, window: {}, refreshLists() {}, updateHud() {}, reglerLiveZeigen() {},
    el: id => elemente[id], document: { visibilityState: 'visible',
      querySelector: () => ({ dataset: { tab: 'fields' } }) },
    LenkLive: { befund, zeichnen() {} }, lenkLiveZahlen: () => '',
  };
  const aufnahmeStart = quelle.indexOf('function onState(data) {');
  const aufnahmeEnde = quelle.indexOf('/* -------------------------------------------------------------------- HUD */', aufnahmeStart);
  assert.ok(aufnahmeStart >= 0 && aufnahmeEnde > aufnahmeStart);
  vm.createContext(kontext);
  vm.runInContext(quelle.slice(aufnahmeStart, aufnahmeEnde), kontext);
  vm.runInContext(quelle.slice(start, ende), kontext);
  for (let i = 0; i < 100; i++) {
    kontext.onState({ time: 1000 + i / 10, guidance: { active: true, cross_track_m: 0.05 },
      steering: { armed: true, command: { engaged: true, angle_deg: 0 } }, fix: { speed_ms: 2 } });
    kontext.renderLenkLive();
  }
  assert.equal(aufzeichnung.punkte.length, 100);
  assert.equal(kennzahlenAufrufe, 0);
  elemente.lenkLiveFenster.hidden = false;
  kontext.renderLenkLive();
  assert.equal(kennzahlenAufrufe, 1);
  assert.equal(elemente.lenkLiveKurz.dataset.art, 'versatz');
  assert.match(elemente.lenkLiveKurz.textContent, /5 cm/);
});
