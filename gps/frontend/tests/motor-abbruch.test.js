const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('Werkstatt-Abbruch sendet AUS und meldet nur bestaetigten Erfolg', async () => {
  const root = path.join(__dirname, '..');
  const html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  assert.match(html, /<button[^>]+id="btnMotorAbbrechen"[^>]*>Motor sofort anhalten<\/button>/);
  const code = fs.readFileSync(path.join(root, 'app.js'), 'utf8');
  const handler = code.match(/el\('btnMotorAbbrechen'\)\.onclick = async \(\) => \{[\s\S]*?\n\};/);
  assert.ok(handler, 'echter Abbruchhandler fehlt');
  const button = {};
  const calls = [];
  let result = null;
  vm.runInNewContext(handler[0], {
    el: () => button,
    api: async (...args) => { calls.push(args); return result; },
    einrichtenMelden: text => calls.push(text),
  });
  await button.onclick();
  assert.deepEqual(calls, [['POST', '/api/steering/disarm']]);
  result = {ok: true};
  await button.onclick();
  assert.match(calls[2], /angehalten/);
});
