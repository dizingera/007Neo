// node --test frontend/tests/flaeche.test.js
const test = require('node:test');
const assert = require('node:assert');
const { Flaeche, querStuecke, vierecke, farbeZerlegen, FRISCH_MS } = require('../flaeche.js');

/* Eine Leinwand, die nur mitschreibt, was gemalt wird. */
function attrappe() {
  return () => {
    const c = {
      fills: 0,
      setTransform() {}, beginPath() {}, moveTo() {}, lineTo() {}, closePath() {},
      fill() { c.fills += 1; }, stroke() {}, fillRect() { c.fills += 1; }, drawImage() {},
      save() {}, restore() {}, translate() {}, scale() {},
    };
    return { getContext: () => c, c };
  };
}

const lage = (y, kurs = 0, aktiv = true, stuecke = querStuecke([], 6)) => ({ pos: [0, y], kurs, stuecke, aktiv });

test('ohne Teilbreiten arbeitet die ganze Breite, in halben Metern', () => {
  const s = querStuecke([], 3);
  assert.strictEqual(s.length, 6);
  assert.deepStrictEqual(s[0], [-1.5, -1]);
  assert.deepStrictEqual(s[5], [1, 1.5]);
});

test('abgeschaltete Teilbreiten markieren nicht', () => {
  const teile = [
    { left_m: -3, right_m: 0, enabled: true, forced_off: false },
    { left_m: 0, right_m: 3, enabled: false, forced_off: false },
  ];
  const s = querStuecke(teile, 6);
  assert.ok(s.every(([l, r]) => r <= 0 && l >= -3));
});

test('der Streifen ist so breit wie das Gerät und liegt zwischen den Lagen', () => {
  const [v] = vierecke(lage(0), lage(2), [[-3, 3]]);
  const xs = v.ecken.map((p) => p[0]), ys = v.ecken.map((p) => p[1]);
  assert.strictEqual(Math.min(...xs), -3);
  assert.strictEqual(Math.max(...xs), 3);
  assert.deepStrictEqual([Math.min(...ys), Math.max(...ys)], [0, 2]);
});

test('ohne laufende Arbeit wird nichts gemalt', () => {
  const f = new Flaeche(attrappe());
  f.fahrt(lage(0, 0, false), 0);
  assert.deepStrictEqual(f.fahrt(lage(1, 0, false), 100), []);
  assert.strictEqual(f.bedeckt.kacheln.size, 0);
});

test('ein Sprung (neu verbunden, versetzt) malt keinen Streifen quer übers Feld', () => {
  const f = new Flaeche(attrappe());
  f.fahrt(lage(0), 0);
  assert.deepStrictEqual(f.fahrt(lage(200), 100), []);
  assert.strictEqual(f.fahrt(lage(201), 200).length, 12);
});

test('die eigene Spur von eben ist keine Überlappung', () => {
  const f = new Flaeche(attrappe());
  f.fahrt(lage(0), 0);
  f.zellenMerken([[0, 0], [0, 1], [0, 2]], 100);
  const liste = f.fahrt(lage(1), 200);
  assert.ok(liste.every((v) => !v.doppelt));
});

test('wer in Bearbeitetes einfährt, bekommt dort die zweite Farbe', () => {
  const f = new Flaeche(attrappe());
  // Früher bearbeitet: links der Mitte, auf Höhe 0 bis 3 m
  const alt = [];
  for (let ix = -6; ix < 0; ix++) for (let iy = 0; iy < 6; iy++) alt.push([ix, iy]);
  f.zellenMalen(alt, 0.5);
  f.fahrt(lage(1), FRISCH_MS + 1000);
  const liste = f.fahrt(lage(2), FRISCH_MS + 1100);
  const links = liste.filter((v) => v.mitte[0] < 0), rechts = liste.filter((v) => v.mitte[0] > 0);
  assert.ok(links.every((v) => v.doppelt), 'links ist schon bearbeitet');
  assert.ok(rechts.every((v) => !v.doppelt), 'rechts ist neu');
  assert.ok(f.doppelt.kacheln.size > 0);
});

test('Farben aus dem Stylesheet: deckend plus Deckkraft', () => {
  assert.deepStrictEqual(farbeZerlegen('rgba(93,255,142,.22)'), { deckend: 'rgb(93,255,142)', alpha: 0.22 });
  assert.deepStrictEqual(farbeZerlegen('#ff0000'), { deckend: 'rgb(255,0,0)', alpha: 1 });
});
