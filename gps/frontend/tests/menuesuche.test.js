const test = require('node:test');
const assert = require('node:assert/strict');
const { normal, suchen } = require('../menuesuche.js');

const eintrag = (text, panel, gewicht = 2, extra = '', abschnitt = '') =>
  ({ text, panel, seite: panel, abschnitt, extra, element: {}, gewicht });

test('Umlaute und Großschreibung spielen keine Rolle', () => {
  assert.equal(normal('Gerät & Größe'), 'geraet groesse');
  const liste = [eintrag('Arbeitsbreite und Teilbreiten', 'machine')];
  assert.equal(suchen(liste, 'TEILBREITEN')[0].panel, 'machine');
  assert.equal(suchen([eintrag('Gerät löschen', 'machine')], 'gerat').length, 0);   // "a" ist kein "ä"
  assert.equal(suchen([eintrag('Gerät löschen', 'machine')], 'geraet loe').length, 1);
});

test('alle Wörter müssen passen, Wortanfang und Überschrift gehen vor', () => {
  const liste = [
    eintrag('Spuren an allen Feldrändern anlegen', 'fields', 2),
    eintrag('Hindernisse im geladenen Feld', 'lines', 4),
    eintrag('Feld hier anlegen', 'fields', 2),
  ];
  assert.deepEqual(suchen(liste, 'feld anlegen').map((e) => e.text),
    ['Feld hier anlegen', 'Spuren an allen Feldrändern anlegen']);
  assert.deepEqual(suchen(liste, 'hindernis').map((e) => e.panel), ['lines']);
  assert.deepEqual(suchen(liste, '   '), []);
});

test('Stichworte finden die Seite, auch wenn das Wort dort nicht steht', () => {
  const liste = [eintrag('Daten & Geräte', 'daten', 5, 'abgleich sync backup')];
  assert.equal(suchen(liste, 'backup')[0].panel, 'daten');
});

test('höchstens so viele Treffer wie verlangt', () => {
  const liste = Array.from({ length: 30 }, (_, i) => eintrag(`Spur ${i}`, 'lines'));
  assert.equal(suchen(liste, 'spur').length, 12);
  assert.equal(suchen(liste, 'spur', 5).length, 5);
});
