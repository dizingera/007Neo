'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const A = require('../arbeitstag.js');
test('Text behandelt fehlende Werte ohne HTML-Ausführung', () => {
  assert.equal(A.text(null), 'Nicht bekannt');
  assert.equal(A.text(0), '0');
  assert.equal(A.text(false), 'Nein');
  assert.equal(A.text('<img src=x onerror=alert(1)>'), '<img src=x onerror=alert(1)>');
  assert.equal(A.text({wert: 2}), 'wert: 2');
});
test('Zahlen und Dauer zeigen Null und deutsche Darstellung; ungültige Werte bleiben unbekannt', () => {
  assert.equal(A.zahl(1.25), '1,3');
  for (const v of [null, undefined, '', NaN, Infinity]) assert.equal(A.zahl(v), '–');
  assert.equal(A.dauer(0), '0 s');
  assert.equal(A.dauer(125), '2 min 5 s');
  assert.equal(A.dauer(-1), 'Dauer unbekannt');
  assert.equal(A.dauer(null), 'Dauer unbekannt');
});
test('Zeit akzeptiert Sekunden, Millisekunden und ISO ohne ungültige Datumsanzeigen', () => {
  assert.equal(A.zeit(null), 'Zeit unbekannt');
  assert.equal(A.zeit('kaputt'), 'Zeit unbekannt');
  assert.equal(A.zeit(1700000000), A.zeit(1700000000000));
  assert.equal(A.zeit('2023-11-14T22:13:20Z'), A.zeit(1700000000000));
});
test('API akzeptiert vorhandene Hülle sowie bereits ausgepackte Daten, aber keine Fehlerantwort', () => {
  const data = {checks: []};
  assert.equal(A.auspacken({ok: true, data}), data);
  assert.equal(A.auspacken(data), data);
  assert.deepEqual(A.auspacken({ok: false, befunde: [], vollstaendig: false}), {ok: false, befunde: [], vollstaendig: false});
  assert.equal(A.auspacken({ok: false, felder: 0, auftraege: 0, fehler: 'Defekt'}).fehler, 'Defekt');
  assert.throws(() => A.auspacken(null), /gültige Antwort/);
  assert.throws(() => A.auspacken({ok: false, detail: 'Stillstand nötig'}), /Stillstand/);
});
test('Übungsfenster erlaubt nur Webadressen ohne Zugangsdaten', () => {
  const basis = 'http://localhost:8080/';
  assert.equal(A.uebungsUrl('http://127.0.0.1:18089/', basis), 'http://127.0.0.1:18089/');
  for (const v of [undefined, null, '', ' ', 'javascript:alert(1)', 'data:text/html,test', 'file:///tmp/test', 'http://user:pass@host/'])
    assert.equal(A.uebungsUrl(v, basis), null);
});
test('Kartenpunkte nehmen nur endliche Koordinaten an', () => {
  assert.deepEqual(A.kartePunkte([[1, 2], {x: 3, y: 4}, {lon: 5, lat: 6}, [NaN, 2], ['x', 2], null, {}, [null, 2], ['', 3]]), [[1, 2], [3, 4], [5, 6]]);
});

test('Standardjob verwendet echte Flächen, Strecke und Arbeitszeit einschließlich Null', () => {
  const r = A.arbeitsZeilen({operation: 'Ernte', area_ha: 2.5, overlap_ha: 0, distance_m: 1250, working_time_s: 125});
  assert.equal(r.Arbeit, 'Ernte'); assert.equal(r['Bearbeitet (ha)'], '2,5');
  assert.equal(r['Überlappung (ha)'], '0'); assert.equal(r.Arbeitszeit, '2 min 5 s');
  assert.equal(A.arbeitsZeilen(null).Arbeit, 'Keine laufende Arbeit');
});
test('Sicherungsprüfung unterscheidet Erfolg, Fehler und ungeprüft', () => {
  assert.equal(A.sicherungsZeilen().Prüfung, 'Noch nicht geprüft');
  const r = A.sicherungsZeilen({pruefung: {ok: false, zeit: 1700000000, felder: 34, auftraege: 4, fehler: 'Datei beschädigt'}});
  assert.equal(r.Prüfung, 'Prüfung fehlgeschlagen'); assert.equal(r.Felder, '34'); assert.equal(r.Aufträge, '4');
  assert.equal(r.Fehler, 'Datei beschädigt'); assert.equal(r.Prüfzeit, A.zeit(1700000000));
  assert.equal(A.sicherungsZeilen({pruefung: {ok: true}}).Prüfung, 'Wiederherstellbarkeit geprüft');
});
test('Änderungen und Konflikte zeigen vorher/nachher und lokal/eingehend eindeutig', () => {
  assert.deepEqual(A.aenderungsZeilen({aenderungen: {breite_m: {vorher: 0, nachher: 6}}}), ['breite_m: 0 → 6']);
  assert.deepEqual(A.konfliktZeilen({lokal: {name: '<Feld>', breite: 3}, eingehend: {name: 'Neu', breite: 4}}),
    ['breite: dieses Gerät = 3; eingehend = 4', 'name: dieses Gerät = <Feld>; eingehend = Neu']);
});
test('Leere Planprüfung ist bei fehlender Vollständigkeit keine Freigabe', () => {
  assert.match(A.planHinweis({vollstaendig: false, befunde: [], hinweis: 'Daten fehlen'}), /unvollständig.*0 Befunde.*Daten fehlen/);
  assert.match(A.planHinweis({vollstaendig: true, befunde: []}), /vollständig.*0 Befunde/);
});
test('Startkoordinaten behalten die Reihenfolge Breitengrad/Längengrad', () => {
  assert.equal(A.koordinaten([48.1234567, 9.1234567]), '48,1234567° N / 9,1234567° O');
  assert.equal(A.koordinaten({lat: 48.1234567, lon: 9.1234567}), A.koordinaten([48.1234567, 9.1234567]));
  assert.equal(A.koordinaten(null), 'Position unbekannt');
});
// Kleines DOM-Modell für Lebenszyklus, Backenddaten und Fokus; kein zusätzlicher Browserbaustein.
function testDom() {
  class Element {
    constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {}; this.events = {}; this.attrs = {}; this.isConnected = true; this._text = ''; this.open = false;
      const c = new Set(); this.classList = {add: x => c.add(x), toggle: (x, an) => { if (an) c.add(x); else c.delete(x); }}; }
    set textContent(v) { this._text = String(v); this.children = []; }
    get textContent() { return this._text + this.children.map(c => c.textContent).join(' '); }
    appendChild(c) { c.parentElement = this; this.children.push(c); return c; }
    append(...cs) { cs.forEach(c => this.appendChild(c)); }
    replaceChildren(...cs) { this.children = []; this._text = ''; this.append(...cs); if (this.testBeimErsetzen) this.testBeimErsetzen(); }
    closest(q) { return this.testScrollContainer || (this.parentElement && this.parentElement.closest(q)) || null; }
    setAttribute(k, v) { this.attrs[k] = v; }
    addEventListener(k, v) { this.events[k] = v; }
    removeEventListener(k) { delete this.events[k]; }
    contains(el) { return el === this || this.children.some(c => c.contains(el)); }
    getClientRects() { return [{}]; }
    get options() { return this.children; }
    get value() { return this._value ?? (this.tagName === 'SELECT' && this.children.length ? this.children[0].value : ''); }
    set value(v) { this._value = v; }
    querySelectorAll(q) { return this.children.flatMap(c => [...((q === 'details[data-at-detail]' ? c.tagName === 'DETAILS' && c.dataset.atDetail : c.dataset.plan) ? [c] : []), ...c.querySelectorAll(q)]); }
    querySelector(q) { return this.querySelectorAll(q)[0] || null; }
    getContext() { return null; }
  }
  const host = new Element('div'), body = new Element('body'); body.appendChild(host);
  const doc = {hidden: true, body, activeElement: body, getElementById: () => host, createElement: t => new Element(t),
    createTextNode: s => { const n = new Element('text'); n.textContent = s; return n; }, addEventListener() {}, removeEventListener() {}};
  doc.defaultView = {localStorage: {getItem: () => null}, setInterval: () => 1, clearInterval() {}, setTimeout, clearTimeout,
    MutationObserver: class {observe() {} disconnect() {}}};
  const alle = el => [el, ...el.children.flatMap(alle)];
  return {doc, host, alle: () => alle(host), button: t => alle(host).find(e => e.tagName === 'BUTTON' && e.textContent === t)};
}
test('Echte Backendfelder werden gerendert; Konfliktwahl sendet eingehend, gelöste bleiben verborgen', async () => {
  const d = testDom(); global.document = d.doc;
  const posts = [], tag = {checks: [{titel: 'GPS', status: 'bereit', text: 'Aktuell'}], gespann: {traktor: 'T1', geraet: 'G1', bauform: 'maehdrescher', breite_m: 6,
    antenne: {antenna_height_m: 3}, masse: {traktor_breite_m: 2.5}, vollstaendig: true, kalibrierung: 'Prüfen'},
    arbeit: {operation: 'Ernte', area_ha: 2.5, working_time_s: 125}, einstieg: {start: [48.1, 9.2], einfahrt: null},
    sicherung: {pruefung: {ok: true, felder: 34, auftraege: 4}}, offline: {}};
  const daten = {'/api/arbeitstag': tag, '/api/betriebsbuch': {unterbrechungen: [{zeitpunkt: 1700000000, klasse: 'Pause', snapshot: {fix: {speed_ms: 0}}, dauer_s: 5}],
    einstellungen: [{zeitpunkt: 1700000000, quelle: 'fahrer', kontext: {traktor_id: 't'}, aenderungen: {breite_m: {vorher: 3, nachher: 6}}}]},
    '/api/archiv': {saisons: [2026, 2025], felder: [], jobs: [{id: 'fremd', feld: 'Feld', fortsetzbar: false, area_ha: 1}]},
    '/api/sync/konflikte': {konflikte: [{id: 'k', status: 'offen', tabelle: 'fields', datensatz_id: 'f1', lokal: {name: 'Alt'}, eingehend: {name: 'Neu'}},
      {id: 'g', status: 'geloest', tabelle: 'Unsichtbar'}]}};
  const app = A.installieren({api: async (m, u, b) => { if (m === 'POST') {posts.push([u, b]); return {ok: true, data: {}};} return {ok: true, data: daten[u]}; }});
  try {
    d.doc.hidden = false; await app.neuLaden();
    assert.match(d.host.textContent, /Antennenhöhe \(m\).*3/); assert.match(d.host.textContent, /Überlappung/);
    assert.match(d.host.textContent, /48,1° N \/ 9,2° O/); assert.match(d.host.textContent, /speed_ms: 0/);
    assert.match(d.host.textContent, /breite_m: 3 → 6/); assert.match(d.host.textContent, /dieses Gerät = Alt; eingehend = Neu/);
    assert.doesNotMatch(d.host.textContent, /Unsichtbar/); assert.equal(d.button('Diesen Auftrag fortsetzen'), undefined);
    await d.button('Stand des anderen Geräts übernehmen').events.click();
    assert.deepEqual(posts[0], ['/api/sync/konflikte/loesen', {id: 'k', auswahl: 'eingehend'}]);
    const select = d.alle().find(e => e.tagName === 'SELECT'); select.value = '2025'; select.events.change();
    const detail = d.alle().find(e => e.dataset.atDetail === 'archiv'); detail.open = true;
    d.doc.activeElement = select; tag.arbeit.operation = 'Neue Arbeit'; await app.neuLaden();
    assert.equal(d.alle().find(e => e.tagName === 'SELECT'), select); assert.equal(select.value, '2025'); assert.equal(detail.open, true);
    assert.doesNotMatch(d.host.textContent, /Neue Arbeit/);
    d.doc.activeElement = d.doc.body; d.host.events.focusout(); await new Promise(r => setTimeout(r, 5));
    assert.match(d.host.textContent, /Neue Arbeit/);
    assert.equal(d.alle().find(e => e.tagName === 'SELECT').value, '2025');
    assert.equal(d.alle().find(e => e.dataset.atDetail === 'archiv').open, true);
    daten['/api/arbeitstag'] = null; await app.neuLaden();
    assert.match(d.host.textContent, /Nicht aktuell/); assert.match(d.host.textContent, /Neue Arbeit/);
  } finally { app.entfernen(); delete global.document; }
});
test('Fehlgeschlagene automatische Runden werden trotz vieler Zustandsbilder zehn Sekunden gedrosselt', async () => {
  const d = testDom(); global.document = d.doc; d.doc.hidden = false;
  const vorher = Date.now; let jetzt = 1000, abrufe = 0; Date.now = () => jetzt;
  const app = A.installieren({api: async () => { abrufe++; return null; }});
  try {
    await new Promise(setImmediate); assert.equal(abrufe, 4);
    for (let i = 0; i < 50; i++) { app.aktualisieren({}); await new Promise(setImmediate); }
    assert.equal(abrufe, 4);
    jetzt = 10999; app.aktualisieren({}); await new Promise(setImmediate); assert.equal(abrufe, 4);
    jetzt = 11000; app.aktualisieren({}); await new Promise(setImmediate); assert.equal(abrufe, 8);
    jetzt = 11001; app.aktualisieren({}); await new Promise(setImmediate); assert.equal(abrufe, 8);
    assert.match(d.host.textContent, /Nicht aktuell/);
  } finally { app.entfernen(); Date.now = vorher; delete global.document; }
});

test('Ohne Fokus bleiben bei identischen Daten DOM und bei Änderungen Scrollposition/Details erhalten', async () => {
  const d = testDom(); global.document = d.doc;
  const scroll = {scrollTop: 460}; d.host.testScrollContainer = scroll;
  const tag = {checks: [], arbeit: {operation: 'Saat', area_ha: 1}, einstieg: {grenze: [[0, 0], [20, 0], [20, 20]], start_xy: [0, 0]}, gespann: {}};
  const app = A.installieren({api: async (m, u) => ({data: u === '/api/arbeitstag' ? structuredClone(tag) : {}})});
  try {
    d.doc.hidden = false; await app.neuLaden();
    const gruppe = d.alle().find(e => e.dataset.atDetail === 'gruppe1'); gruppe.open = true;
    const archiv = d.alle().find(e => e.dataset.atDetail === 'archiv'); archiv.open = true;
    const select = d.alle().find(e => e.tagName === 'SELECT'); select.value = '2026'; select.events.change();
    const karte = d.alle().find(e => e.tagName === 'CANVAS'); assert.ok(karte);
    await app.neuLaden();
    assert.equal(d.alle().find(e => e.tagName === 'CANVAS'), karte, 'Gleiche Daten erhalten auch den Kartenknoten');
    assert.equal(d.alle().find(e => e.dataset.atDetail === 'archiv'), archiv, 'Unveränderte Daten dürfen Details nicht ersetzen');
    assert.equal(d.alle().find(e => e.tagName === 'SELECT'), select);
    const inhalt = gruppe.children[1]; inhalt.testBeimErsetzen = () => { scroll.scrollTop = 0; };
    tag.arbeit.area_ha = 2; await app.neuLaden();
    assert.equal(scroll.scrollTop, 460, 'Scrollposition nach dem Neuaufbau wiederherstellen');
    assert.equal(d.alle().find(e => e.dataset.atDetail === 'gruppe1'), gruppe);
    assert.equal(gruppe.open, true); assert.equal(d.alle().find(e => e.dataset.atDetail === 'archiv').open, true);
    assert.equal(d.alle().find(e => e.tagName === 'SELECT').value, '2026');
    assert.match(d.host.textContent, /Bearbeitet \(ha\).*2/);
  } finally { app.entfernen(); delete global.document; }
});
test('Bereitschaft bleibt bei fokussierter Auswahl aktuell, Eingabe und DOM bleiben erhalten', async () => {
  const d = testDom(); global.document = d.doc;
  const tag = {checks: [{titel: 'GPS', status: 'bereit', text: 'Position aktuell'}]};
  const app = A.installieren({api: async (m, u) => ({data: u === '/api/arbeitstag' ? structuredClone(tag) : {}})});
  try {
    d.doc.hidden = false; await app.neuLaden();
    const select = d.alle().find(e => e.tagName === 'SELECT'); d.doc.activeElement = select;
    tag.checks[0] = {titel: 'GPS', status: 'offen', text: 'GPS veraltet'}; await app.neuLaden();
    assert.equal(d.alle().find(e => e.tagName === 'SELECT'), select);
    assert.match(d.host.textContent, /GPS veraltet/); assert.doesNotMatch(d.host.textContent, /Position aktuell/);
    tag.checks[0] = {titel: 'GPS', status: 'bereit', text: 'Position wieder aktuell'}; await app.neuLaden();
    assert.match(d.host.textContent, /Position wieder aktuell/); assert.doesNotMatch(d.host.textContent, /GPS veraltet/);
  } finally { app.entfernen(); delete global.document; }
});
test('Verspätete Planantwort wird nach Feldwechsel und Rückwechsel verworfen; passender Plan erscheint', async () => {
  const d = testDom(); global.document = d.doc; let state = {field: {id: 'A'}}, antwort;
  const app = A.installieren({getState: () => state, api: async (m, u) => u.endsWith('/planpruefung') ? new Promise(r => {antwort = r;}) : {data: {}}});
  try {
    d.doc.hidden = false; await app.neuLaden();
    const a = d.button('Feldplan prüfen').events.click();
    state = {field: {id: 'B'}}; app.aktualisieren(state);
    state = {field: {id: 'A'}}; app.aktualisieren(state);
    antwort({data: {field_id: 'A', text: 'Alter Plan A', befunde: []}}); await a;
    assert.doesNotMatch(d.host.textContent, /Alter Plan A/);
    state = {field: {id: 'B'}}; app.aktualisieren(state);
    const b = d.button('Feldplan prüfen').events.click();
    antwort({data: {field_id: 'B', text: 'Aktueller Plan B', vollstaendig: true, befunde: []}}); await b;
    assert.match(d.host.textContent, /Aktueller Plan B/);
    const falsch = d.button('Feldplan prüfen').events.click();
    antwort({data: {field_id: 'C', text: 'Falscher Plan C', befunde: []}}); await falsch;
    assert.doesNotMatch(d.host.textContent, /Falscher Plan C/);
  } finally { app.entfernen(); delete global.document; }
});
test('Arbeitsansicht führt in Kabine und verspricht keine nicht vorhandene Unterbrechungsfunktion', async () => {
  const d = testDom(); global.document = d.doc; let ziel;
  const app = A.installieren({api: async () => ({data: {}}), oeffnen: v => {ziel = v;}});
  try {
    d.doc.hidden = false; await app.neuLaden(); await d.button('Zur Arbeitsansicht').events.click();
    assert.equal(ziel, 'kabine'); assert.doesNotMatch(d.host.textContent, /beenden oder unterbrechen/);
    assert.match(d.host.textContent, /maßstäbliche Kontur/); assert.match(d.host.textContent, /kein Kollisionsnachweis/);
  } finally { app.entfernen(); delete global.document; }
});
