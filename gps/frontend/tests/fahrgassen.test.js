const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const quelle = fs.readFileSync(path.join(__dirname, '../app.js'), 'utf8');

function umgebung(eingabe) {
  const aufrufe = [];
  const elemente = { btnSaatplanUebernehmen: {}, sheet: {}, aussaatJahr: {},
    aussaatDialog: { showModal() {
      elemente.aussaatJahr.value = eingabe === null ? '' : String(eingabe);
      this.returnValue = eingabe === null ? 'abbrechen' : 'uebernehmen';
      this.onclose();
    } },
  };
  const kontext = { prompt: () => eingabe, toast: () => {}, refreshLists() {}, confirm: () => true,
    document: { querySelector: () => ({ focus() {} }) },
    state: { live: { field: { id: 'feld' } } }, saat: {},
    el: id => elemente[id], item: werte => ({ werte, querySelectorAll: () => [{}, { classList: { add() {} } }] }),
    api: async (...werte) => { aufrufe.push(werte); return { data: { name: 'Saat', fahrgasse_m: 21 } }; },
  };
  vm.createContext(kontext);
  const beginn = quelle.indexOf('function letzteAussaat(');
  const ende = quelle.indexOf('function renderFields(', beginn);
  vm.runInContext(quelle.slice(beginn, ende), kontext);
  const spur = quelle.indexOf('function spurZeile(');
  vm.runInContext(quelle.slice(spur, quelle.indexOf('function renderLines(', spur)), kontext);
  const plan = quelle.indexOf("el('btnSaatplanUebernehmen').onclick");
  vm.runInContext(quelle.slice(plan, quelle.indexOf("el('btnSaatplanLoeschen')", plan)), kontext);
  return { kontext, aufrufe, elemente };
}

test('letzte Aussaat richtet sich nach Festlegung statt Kalenderjahr oder Umbenennen', () => {
  const { kontext } = umgebung();
  const alt = { id: 'alt', saison: 2027, fahrgasse_m: 21, aussaat_at: 10, updated_at: 50 };
  const neu = { id: 'neu', saison: 2026, fahrgasse_m: 21, aussaat_at: 20, updated_at: 20 };
  const keine = { id: 'hilfsspur', saison: 0, fahrgasse_m: 0, aussaat_at: 100 };
  assert.equal(kontext.letzteAussaat([alt, keine, neu]), neu);
  assert.equal(kontext.letzteAussaat([keine]), null);
});

test('tatsächliche Saatplan-Taste übermittelt das ausgewählte Erntejahr', async () => {
  const { elemente, aufrufe } = umgebung('2027');
  await elemente.btnSaatplanUebernehmen.onclick();
  assert.equal(aufrufe.length, 1);
  assert.equal(aufrufe[0][1], '/api/fields/feld/saatplan/uebernehmen');
  assert.equal(aufrufe[0][2].saison, 2027);
});

test('tatsächliche Spuraktion veröffentlicht Aussaat mit gewähltem Erntejahr', async () => {
  const { kontext, aufrufe } = umgebung('2028');
  const zeile = kontext.spurZeile('feld', { id: 'spur', name: 'Saat', mode: 'ab', spacing_m: 3,
    saison: 2026, fahrgasse_m: 21 }, 'spur');
  await zeile.werte.actions[1][1]();
  assert.equal(aufrufe[0][1], '/api/lines/spur');
  assert.equal(aufrufe[0][2].saisonspur, true);
  assert.equal(aufrufe[0][2].saison, 2028);
});

test('Abbruch und ungültiges Erntejahr übernehmen keinen Saatplan', async () => {
  for (const eingabe of [null, '', '2027.5', '2027abc', '1999', '2201']) {
    const { elemente, aufrufe } = umgebung(eingabe);
    await elemente.btnSaatplanUebernehmen.onclick();
    assert.equal(aufrufe.length, 0, String(eingabe));
  }
});