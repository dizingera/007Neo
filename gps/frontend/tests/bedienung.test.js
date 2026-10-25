const test = require('node:test');
const assert = require('node:assert/strict');
const { zahlenrad } = require('../bedienung.js');

test('Rollen am Zahlenfeld scrollt die Seite und verhindert die Wertänderung', () => {
  let verhindert = false, bewegung;
  const scroller = { clientHeight: 600, scrollBy: v => { bewegung = v; } };
  const feld = { tagName: 'INPUT', type: 'number', value: '3', closest: () => scroller };
  const e = { target: feld, deltaY: 80, deltaX: 0, deltaMode: 0, preventDefault: () => { verhindert = true; } };
  assert.equal(zahlenrad(e), true);
  assert.equal(verhindert, true);
  assert.equal(feld.value, '3');
  assert.equal(bewegung.top, 80);
  e.deltaMode = 1; e.deltaY = 3;
  zahlenrad(e);
  assert.equal(bewegung.top, 60);
});

test('Andere Felder und Browserzoom behalten ihre normale Bedienung', () => {
  let verhindert = false;
  const e = { target: { tagName: 'INPUT', type: 'range' }, preventDefault: () => { verhindert = true; } };
  assert.equal(zahlenrad(e), false);
  e.target.type = 'number'; e.ctrlKey = true;
  assert.equal(zahlenrad(e), false);
  assert.equal(verhindert, false);
});
