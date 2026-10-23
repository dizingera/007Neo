// node --test frontend/tests/fahrzeugbild.test.js
const test = require('node:test');
const assert = require('node:assert');
const { MARKEN, BAUFORMEN, GERAETE, farben, radwinkel, fahrzeug, geraet } = require('../fahrzeugbild.js');

const F = { lack: '#111', 'lack-hell': '#222', 'lack-dunkel': '#333', akzent: '#444', reifen: '#000', kabine: '#555' };

test('every brand has a complete paint set; standard follows the theme', () => {
  Object.entries(MARKEN).forEach(([id, m]) => {
    const p = farben(id, F);
    ['lack', 'hell', 'dunkel', 'akzent', 'dach', 'felge'].forEach((k) => assert.ok(p[k], `${id}.${k}`));
    assert.ok(m.name);
  });
  assert.strictEqual(farben('standard', F).lack, '#111');
  assert.strictEqual(farben('gibtsnicht', F).lack, '#111');
  assert.strictEqual(farben('johndeere', F).akzent, '#ffde00');
});

test('wheels follow the measured angle first, then the command, else straight', () => {
  assert.strictEqual(radwinkel({ steering: { wheel_angle_deg: 7.5, command: { engaged: true, angle_deg: 12 } } }), 7.5);
  assert.strictEqual(radwinkel({ steering: { wheel_angle_deg: null, command: { engaged: true, angle_deg: 12 } } }), 12);
  assert.strictEqual(radwinkel({ steering: { command: { engaged: false, angle_deg: 12 } } }), 0);
  assert.strictEqual(radwinkel(null), 0);
});

/* Eine Zeichenfläche, die nur mitzählt - reicht, um jede Form einmal durchzuspielen. */
function leinwand() {
  const zaehler = { fills: 0, rotationen: [] };
  const verlauf = { addColorStop() {} };
  const ctx = new Proxy({}, {
    get(_, name) {
      if (name === 'createLinearGradient' || name === 'createRadialGradient') return () => verlauf;
      if (name === 'fill') return () => { zaehler.fills += 1; };
      if (name === 'rotate') return (w) => zaehler.rotationen.push(w);
      if (name === 'globalAlpha') return 1;
      return () => {};
    },
    set() { return true; },
  });
  return { ctx, zaehler };
}

test('every body shape and implement draws without error; front wheels turn', () => {
  Object.keys(BAUFORMEN).forEach((form) => {
    const { ctx, zaehler } = leinwand();
    const laenge = fahrzeug(ctx, { profil: { bauform: form, wheelbase_m: 2.8, spurweite_m: 2 }, px: 0.05, F,
      p: farben('fendt', F), winkel: 15 });
    assert.ok(laenge > 3, form);
    assert.ok(zaehler.fills > 5, form);
    assert.ok(zaehler.rotationen.some((w) => Math.abs(Math.abs(w) - 15 * Math.PI / 180) < 1e-9), `${form} lenkt nicht`);
  });
  Object.keys(GERAETE).forEach((art) => {
    const { ctx } = leinwand();
    geraet(ctx, art, 6, 0.05, F);
  });
});
