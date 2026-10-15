// node --test frontend/tests/alerts.test.js
const test = require('node:test');
const assert = require('node:assert');
const { ereignisse } = require('../alerts.js');

const lenkt = { steering: { armed: true, command: { engaged: true, reason: 'lenkt' } },
                fix: { rank: 4, fix_label: 'RTK fix' } };
const aus = (grund) => ({ steering: { armed: false, command: { engaged: false, reason: grund } },
                          fix: { rank: 4, fix_label: 'RTK fix' } });

test('Fahrer schaltet selbst ab: kein Ton', () => {
  assert.deepStrictEqual(ereignisse(lenkt, aus('vom Fahrer ausgeschaltet')), []);
});
test('Fahrer greift ins Lenkrad: kurzer Hinweis', () => {
  const e = ereignisse(lenkt, aus('Fahrer hat eingegriffen'));
  assert.strictEqual(e.length, 1);
  assert.strictEqual(e[0].stufe, 'info');
});
test('Lenkung fällt aus anderem Grund aus: Alarm mit Grund', () => {
  const e = ereignisse(lenkt, aus('zu weit von der Spur (1.20 m)'));
  assert.strictEqual(e[0].stufe, 'alarm');
  assert.match(e[0].text, /zu weit/);
});
test('Lenkung war nicht aktiv: nichts', () => {
  assert.deepStrictEqual(ereignisse(aus('nicht scharf'), aus('nicht scharf')), []);
});
test('RTK weg: Alarm bei scharfer Lenkung, Warnung bei laufender Arbeit', () => {
  const schlecht = { ...lenkt, fix: { rank: 2, fix_label: 'RTK float' } };
  assert.strictEqual(ereignisse(lenkt, schlecht).find((x) => x.id === 'fix-verloren').stufe, 'alarm');
  const arbeit = { job: {}, steering: { armed: false, command: { engaged: false } }, fix: { rank: 4 } };
  const arbeitSchlecht = { ...arbeit, fix: { rank: 2, fix_label: 'DGPS' } };
  assert.strictEqual(ereignisse(arbeit, arbeitSchlecht)[0].stufe, 'warn');
});
test('RTK weg ohne Lenkung und ohne Arbeit: still', () => {
  const frei = { steering: { armed: false, command: { engaged: false } }, fix: { rank: 4 } };
  assert.deepStrictEqual(ereignisse(frei, { ...frei, fix: { rank: 1, fix_label: 'GPS' } }), []);
});
test('Vorgewende nur an der steigenden Flanke', () => {
  const a = { headland: { alarm: false, rest_m: 50 } };
  const b = { headland: { alarm: true, rest_m: 30 } };
  assert.strictEqual(ereignisse(a, b)[0].id, 'vorgewende');
  assert.deepStrictEqual(ereignisse(b, b), []);
});
test('Verbindung weg während Lenkung aktiv: Alarm', () => {
  assert.strictEqual(ereignisse(lenkt, lenkt, false)[0].id, 'verbindung');
});
test('erstes Bild ohne Vorgänger löst nichts aus', () => {
  assert.deepStrictEqual(ereignisse(null, lenkt), []);
});
test('Rückwärtsfahrt: Warnung statt Alarm', () => {
  const e = ereignisse(lenkt, aus('Rückwärtsfahrt'));
  assert.strictEqual(e[0].stufe, 'warn');
});
