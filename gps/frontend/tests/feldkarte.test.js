const test = require('node:test');
const assert = require('node:assert/strict');
const K = require('../feldkarte.js');

const nah = (a, b, d = 1e-9) => assert.ok(Math.abs(a - b) <= d, `${a} ≠ ${b}`);

test('einpassen zeigt alle Felder mit Rand', () => {
  const a = K.einpassen([0, 0], [1000, 500], 800, 600, 30);
  assert.deepEqual(a.mitte, [500, 250]);
  nah(a.massstab, 0.74);
  const [x0] = K.nachBild(a, 800, 600, [0, 0]);
  const [x1] = K.nachBild(a, 800, 600, [1000, 0]);
  nah(x0, 30); nah(x1, 770);
});

test('Bild und Meter rechnen sich ineinander zurück', () => {
  const a = { mitte: [120, -40], massstab: 1.7 };
  const p = K.nachMetern(a, 640, 480, K.nachBild(a, 640, 480, [333, 77]));
  nah(p[0], 333, 1e-6); nah(p[1], 77, 1e-6);
});

test('Zoomen hält den Punkt unter dem Finger fest und bleibt in Grenzen', () => {
  const a = { mitte: [0, 0], massstab: 1 };
  const finger = [500, 100];
  const vorher = K.nachMetern(a, 800, 600, finger);
  const b = K.zoomen(a, 2.5, finger, 800, 600);
  nah(b.massstab, 2.5);
  const nachher = K.nachMetern(b, 800, 600, finger);
  nah(nachher[0], vorher[0], 1e-9); nah(nachher[1], vorher[1], 1e-9);
  assert.equal(K.zoomen(a, 1e6, finger, 800, 600).massstab, 8);
  assert.equal(K.zoomen(a, 1e-9, finger, 800, 600).massstab, 0.002);
});

test('Verschieben folgt dem Finger', () => {
  const a = { mitte: [0, 0], massstab: 2 };
  const b = K.verschieben(a, 40, -20);       // Finger 40 px nach rechts, 20 px nach oben
  const p = K.nachBild(b, 800, 600, [0, 0]);
  nah(p[0], 440); nah(p[1], 280);
});

test('das Feld unter dem Finger, bei Überlappung das kleinere, knapp daneben das nächste', () => {
  const felder = [
    { id: 'gross', xy: [[0, 0], [100, 0], [100, 100], [0, 100]] },
    { id: 'klein', xy: [[40, 40], [60, 40], [60, 60], [40, 60]] },
    { id: 'nachbar', xy: [[110, 0], [200, 0], [200, 100], [110, 100]] },
  ];
  assert.equal(K.feldUnter(felder, [10, 10]), 'gross');
  assert.equal(K.feldUnter(felder, [50, 50]), 'klein');
  assert.equal(K.feldUnter(felder, [150, 50]), 'nachbar');
  assert.equal(K.feldUnter(felder, [106, 50]), null);
  assert.equal(K.feldUnter(felder, [107, 50], 5), 'nachbar');   // 3 m daneben, mit Fang
  assert.equal(K.feldUnter(felder, [300, 50], 5), null);
});
