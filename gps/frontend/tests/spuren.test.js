const test = require('node:test');
const assert = require('node:assert/strict');
const { SpurPuffer, versetzen } = require('../spuren.js');

const linie = (mode = 'curve') => ({ mode, points: [[0, 0], [0, 10], [10, 10]],
  spacing_m: 3, nudge_m: 0, raster_m: 0 });

test('AB-Spuren bleiben gerade und mindestens 600 m lang', () => {
  assert.deepEqual(versetzen({ ...linie('ab'), points: [[0, 0], [0, 10]] }, 3), [[3, -295], [3, 305]]);
});

test('Kurven werden an jedem Punkt entlang ihrer bisherigen Normalen versetzt', () => {
  const p = versetzen(linie(), 2);
  assert.deepEqual(p[0], [2, 0]);
  assert.ok(Math.abs(p[1][0] - Math.SQRT2) < 1e-12);
  assert.ok(Math.abs(p[1][1] - (10 - Math.SQRT2)) < 1e-12);
  assert.ok(Math.abs(p[2][0] - 10) < 1e-12);
  assert.equal(p[2][1], 8);
});

test('Konturspuren zeigen bei beiden Umlaufrichtungen nach innen', () => {
  const points = [[0, 0], [10, 0], [10, 10], [0, 10]];
  const vor = versetzen({ ...linie('contour'), points }, 2);
  const rueck = versetzen({ ...linie('contour'), points: [...points].reverse() }, 2).reverse();
  vor.forEach((p, i) => p.forEach((x, j) => assert.ok(Math.abs(x - rueck[i][j]) < 1e-12)));
  assert.ok(vor.every(([x, y]) => x > 0 && x < 10 && y > 0 && y < 10));
});

test('derselbe und neu eingelesener gleicher Zustand verwenden dieselben Spuren', () => {
  const puffer = new SpurPuffer();
  const l = linie();
  const spuren = puffer.spuren(l, 4);
  assert.equal(spuren.length, 13);
  assert.equal(spuren[0].nummer, -2);
  assert.equal(spuren[12].nummer, 10);
  assert.equal(puffer.spuren(l, 4), spuren);
  assert.equal(puffer.spuren(JSON.parse(JSON.stringify(l)), 4), spuren);
});

test('neue Punkte, Spurart, Abstand und Versätze erscheinen sofort', () => {
  for (const aendern of [l => { l.points[1][0] += 2; }, l => { l.mode = 'ab'; },
    l => { l.spacing_m += 1; }, l => { l.nudge_m += 0.1; }, l => { l.raster_m += 1.5; }]) {
    const puffer = new SpurPuffer();
    const l = linie();
    const vorher = puffer.spuren(l, 4);
    const neu = JSON.parse(JSON.stringify(l));
    aendern(neu);
    const nachher = puffer.spuren(neu, 4);
    assert.notEqual(nachher, vorher);
    assert.notDeepEqual(nachher, vorher);
    for (const s of nachher) assert.deepEqual(s.punkte,
      versetzen(neu, s.nummer * neu.spacing_m + neu.nudge_m + neu.raster_m));
  }
});

test('Spurwechsel und leere Daten ersetzen das bisherige Muster', () => {
  const puffer = new SpurPuffer();
  const l = linie();
  for (let i = 0; i < 100; i++) {
    const s = puffer.spuren(l, i);
    assert.equal(s.length, 13);
    assert.equal(s[6].nummer, i);
  }
  assert.deepEqual(puffer.spuren(null, 0), []);
  assert.equal(puffer.spuren(l, 0).length, 13);
});

test('Kontur beginnt bei Ring null, kurze Geometrie zeichnet nichts', () => {
  const puffer = new SpurPuffer();
  const s = puffer.spuren(linie('contour'), 0);
  assert.deepEqual(s.map(p => p.nummer), [0, 1, 2, 3, 4, 5, 6]);
  assert.ok(puffer.spuren({ ...linie(), points: [[0, 0]] }, 0).every(p => !p.punkte.length));
});
