// node --test frontend/tests/lenklive.test.js
const test = require('node:test');
const assert = require('node:assert');
const { Aufzeichnung, messpunkt, kennzahlen, nachlauf, befund } = require('../lenklive.js');

/* Eine Fahrt mit Lenkung, 10 Bilder je Sekunde. quer(t) in cm, soll/ist in Grad. */
function fahrt(sekunden, quer, soll = () => 0, ist = null) {
  const punkte = [];
  for (let i = 0; i <= sekunden * 10; i += 1) {
    const t = 1000 + i / 10;
    punkte.push({ t, quer: quer(t - 1000), aktiv: true, lenkt: true, soll: soll(t - 1000),
      ist: ist ? ist(t - 1000) : null, leistung: null, tempo: 2.5 });
  }
  return punkte;
}

test('steady and close is good', () => {
  const k = kennzahlen(fahrt(30, (t) => Math.sin(t * 3) * 1.0));
  assert.ok(k.genug);
  assert.strictEqual(befund(k, 'stanley').art, 'gut');
});

test('swinging back and forth is called pendeln, with period and amplitude', () => {
  const k = kennzahlen(fahrt(30, (t) => 8 * Math.sin(2 * Math.PI * t / 6)));
  const b = befund(k, 'stanley');
  assert.strictEqual(b.art, 'pendelt');
  assert.ok(Math.abs(k.periode - 6) < 0.8, `Periode ${k.periode}`);
  assert.ok(k.amplitude > 6 && k.amplitude < 9, `Amplitude ${k.amplitude}`);
  assert.match(b.tipps[0], /Lenkschärfe/);
  assert.match(befund(k, 'voraus').tipps[0], /Trägheit/);
});

test('staying beside the line is an offset, with its side', () => {
  const b = befund(kennzahlen(fahrt(30, () => -5)), 'stanley');
  assert.strictEqual(b.art, 'versatz');
  assert.match(b.text, /links/);
});

test('the wheel lagging the command is measured', () => {
  const soll = (t) => 5 * Math.sin(2 * Math.PI * t / 4);
  const punkte = fahrt(30, () => 0, soll, (t) => soll(t - 0.5));
  const lag = nachlauf(punkte);
  assert.ok(Math.abs(lag - 0.5) < 0.11, `Nachlauf ${lag}`);
  const b = befund(kennzahlen(punkte), 'stanley');
  assert.ok(b.tipps.some((t) => /0,5 s später/.test(t)), b.tipps.join(' | '));
});

test('too few points: still collecting', () => {
  assert.strictEqual(befund(kennzahlen(fahrt(2, () => 0)), 'stanley').art, 'sammelt');
});

test('only driving with steering counts', () => {
  const a = new Aufzeichnung();
  for (let i = 0; i < 100; i += 1) {
    a.aufnehmen({ time: 2000 + i / 10, guidance: { active: true, cross_track_m: 0.1, steer_angle_deg: 0 },
      steering: { armed: false, command: { engaged: false } }, fix: { speed_ms: 2 } });
  }
  assert.strictEqual(a.punkte.length, 100);
  assert.strictEqual(a.bewertbar().length, 0);
});

test('a state picture becomes a point; the simulator wheel follows the command', () => {
  const p = messpunkt({ time: 5, guidance: { active: true, cross_track_m: -0.034, steer_angle_deg: 3 },
    steering: { armed: true, simulator: true, wheel_angle_deg: null, command: { engaged: true, angle_deg: 2.5 } },
    fix: { speed_ms: 3 } });
  assert.ok(Math.abs(p.quer + 3.4) < 1e-9);
  assert.strictEqual(p.soll, 2.5);
  assert.strictEqual(p.ist, 2.5);
  assert.ok(p.lenkt);
  assert.strictEqual(messpunkt({}), null);
});

test('the buffer forgets what is older than three minutes', () => {
  const a = new Aufzeichnung();
  for (let i = 0; i < 4000; i += 1) {
    a.aufnehmen({ time: i / 10, guidance: { active: true, cross_track_m: 0 }, steering: {}, fix: {} });
  }
  assert.ok(a.punkte[a.punkte.length - 1].t - a.punkte[0].t <= 180);
});

test('a slow wheel points Stanley to the predictive mode, and the predictive mode to its inertia', () => {
  const k = { genug: true, durchgaenge: 0, amplitude: 0, mittel: 0, effektiv: 1, max: 2, nachlauf: 1.2, vollAnteil: 0 };
  assert.match(befund(k, 'stanley').tipps.join(' '), /Vorausschauend.*1,2 s/);
  assert.match(befund(k, 'voraus').tipps.join(' '), /Trägheit der Lenkung auf 1,2 s/);
  assert.match(befund({ ...k, nachlauf: 0.4 }, 'stanley').tipps.join(' '), /Latenz auf 400 ms/);
  const { schrittKurz } = require('../lenklive.js');
  assert.strictEqual(schrittKurz({ was: 'regelart', von: 'stanley', nach: 'voraus' }), 'Regelart Vorausschauend');
  assert.strictEqual(schrittKurz({ was: 'lenk_traegheit_s', von: 0.3, nach: 1.2 }), 'Trägheit 0,30→1,20');
});


test('eingestellte Motorgrenze wird als Auslastung erkannt', () => {
  const p = fahrt(30, () => 1);
  p.forEach(v => { v.leistung = -.55; v.grenze = .55; });
  const k = kennzahlen(p);
  assert.equal(k.vollAnteil, 1);
  assert.match(befund(k, 'cerea').tipps.join(' '), /eingestellten/);
  p.forEach(v => { v.leistung = .4; });
  assert.equal(kennzahlen(p).vollAnteil, 0);
});

test('Messpunkt übernimmt nur bekannte Motorgrenzen', () => {
  const p = messpunkt({ time: 1, guidance: { active: true }, steering: { duty: .55 },
    motor_grenzen: { max_duty: .55, eigener_regelkreis: true } });
  assert.equal(p.grenze, .55);
  const fremd = messpunkt({ guidance: {}, motor_grenzen: { max_duty: .55, eigener_regelkreis: false } });
  assert.equal(fremd.grenze, null);
});
