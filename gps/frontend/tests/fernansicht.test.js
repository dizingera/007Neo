// node --test frontend/tests/fernansicht.test.js
const test = require('node:test');
const assert = require('node:assert');
const { projektion, einpassen, alterText, zustandText, veraltet } = require('../fernansicht.js');

test('projection puts points in metres around their centre', () => {
  const p = projektion([[50, 10], [50.001, 10]]);
  const a = p.nachMetern([50, 10]);
  const b = p.nachMetern([50.001, 10]);
  assert.ok(Math.abs(b[1] - a[1] - 111.2) < 0.5, `Nord-Abstand ${b[1] - a[1]}`);
  assert.ok(Math.abs(a[0]) < 1e-6);
  assert.strictEqual(projektion([]), null);
});

test('fitting keeps everything inside with a margin', () => {
  const p = projektion([[50, 10], [50.01, 10.02]]);
  const nachBild = einpassen(p, 800, 500, 40);
  for (const punkt of [[50, 10], [50.01, 10.02]]) {
    const [x, y] = nachBild(p.nachMetern(punkt));
    assert.ok(x >= 39 && x <= 761 && y >= 39 && y <= 461, `${x},${y}`);
  }
});

test('age and state read like the cab', () => {
  assert.strictEqual(alterText(3), 'gerade eben');
  assert.strictEqual(alterText(40), 'vor 40 s');
  assert.strictEqual(alterText(600), 'vor 10 min');
  assert.strictEqual(zustandText({ feld: 'Am Bach', markiert: true, arbeit: 'Grubbern', flaeche_ha: 1.234,
    tempo_ms: 2.5, lenkt: true, fix: 'RTK fix' }), 'Am Bach · Grubbern · 1,23 ha · 9 km/h · lenkt · RTK fix');
  assert.match(zustandText({ tempo_ms: 0 }), /steht/);
  assert.ok(veraltet({ alter_s: 120 }));
  assert.ok(!veraltet({ alter_s: 5 }));
});
