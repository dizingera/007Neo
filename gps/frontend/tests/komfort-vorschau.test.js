'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../komfort.js'), 'utf8');
function environment() {
  const requests = [], dialogs = [];
  function element(tag) {
    return {tag, children: [], listeners: {}, closes: 0, removes: 0,
      appendChild(child) { this.children.push(child); return child; },
      append(...children) { this.children.push(...children); },
      addEventListener(name, callback) { this.listeners[name] = callback; },
      showModal() { this.open = true; dialogs.push(this); },
      close() { this.open = false; this.closes++; },
      remove() { this.removes++; }, focus() {}};
  }
  const context = vm.createContext({module: {exports: {}}, document: {body: element('body'), createElement: element},
    fetch(url, options) { return new Promise((resolve, reject) => requests.push({url, options, resolve, reject})); }});
  vm.runInContext(source, context);
  const answer = (index, changes = [{label: 'Breite', vorher: 2, nachher: 3}], status = 200) => requests[index].resolve({ok: status < 400, json: async () => ({ok: status < 400, detail: 'Testablehnung', data: {aenderungen: changes, stand: 'stand'}})});
  return {K: context.module.exports, requests, dialogs, answer};
}
const tick = () => new Promise(resolve => setImmediate(resolve));
const buttons = dialog => dialog.children.at(-1).children;

test('Parallele Vorschauen erzeugen nur einen POST und einen Dialog', async () => {
  const u = environment(), first = u.K.vorschau('/api/profile', {width_m: 3});
  await assert.rejects(u.K.vorschau('/api/profile', {width_m: 4}), /vorschau/i);
  assert.equal(u.requests.length, 1); assert.equal(u.requests[0].options.method, 'POST');
  u.answer(0); await tick(); assert.equal(u.dialogs.length, 1);
  buttons(u.dialogs[0])[0].onclick(); assert.equal(await first, null);
});

test('Mehrfaches Cancel eines alten Dialogs entsperrt keine neue Vorschau', async () => {
  const u = environment(), first = u.K.vorschau('/api/profile', {width_m: 3});
  u.answer(0); await tick(); const old = u.dialogs[0]; let prevented = 0;
  old.listeners.cancel({preventDefault() { prevented++; }}); assert.equal(await first, null);
  const second = u.K.vorschau('/api/profile', {width_m: 4});
  old.listeners.cancel({preventDefault() { prevented++; }}); buttons(old)[1].onclick();
  await assert.rejects(u.K.vorschau('/api/profile', {width_m: 5}), /vorschau/i);
  assert.equal(u.requests.length, 2); assert.equal(old.closes, 1); assert.equal(old.removes, 1); assert.equal(prevented, 2);
  u.answer(1); await tick(); buttons(u.dialogs[1])[0].onclick(); assert.equal(await second, null);
});

test('Netz-, JSON- und HTTP-Fehler geben Vorschau fuer erneuten Versuch frei', async () => {
  for (const kind of ['network', 'json', 'http']) {
    const u = environment(), first = u.K.vorschau('/api/settings', {aenderungen: {x: 1}});
    const rejected = assert.rejects(first);
    if (kind === 'network') u.requests[0].reject(new Error('Testnetz'));
    else if (kind === 'json') u.requests[0].resolve({ok: true, json: async () => { throw new Error('Testjson'); }});
    else u.answer(0, [], 409);
    await rejected; assert.equal(u.dialogs.length, 0);
    const second = u.K.vorschau('/api/profile', {width_m: 3}); assert.equal(u.requests.length, 2);
    u.answer(1, []); const result = await second; assert.equal(result._vorschau, 'stand');
  }
});

test('Unveraenderte Werte geben Sperre ohne Dialog frei und lassen Original unveraendert', async () => {
  const u = environment(), original = {width_m: 3}, first = u.K.vorschau('/api/profile', original);
  u.answer(0, []); const result = await first;
  assert.equal(result.width_m, 3); assert.equal(result._vorschau, 'stand'); assert.equal(original._vorschau, undefined); assert.equal(u.dialogs.length, 0);
  const second = u.K.vorschau('/api/profile', {width_m: 4}); assert.equal(u.requests.length, 2);
  u.answer(1); await tick(); buttons(u.dialogs[0])[1].onclick(); assert.equal((await second).width_m, 4);
});
