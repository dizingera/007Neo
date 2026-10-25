// Isolierte Originalfunktionen; keine Produktivendpunkte und keine Hardware.
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const app = fs.readFileSync(path.join(__dirname, '../app.js'), 'utf8');
function block(src, start, end) { const a = src.indexOf(start); const b = src.indexOf(end, a); assert.ok(a >= 0 && b > a, start); return src.slice(a, b); }
function deferred() { let resolve; const promise = new Promise(r => { resolve = r; }); return {promise, resolve}; }
function context(x) { return vm.createContext(x); }
function run(c, s) { vm.runInContext(s, c); }
const flush = () => new Promise(setImmediate);
function node() { const classes = new Set(); return {dataset: {}, textContent: '', value: '', hidden: false,
  classList: {add: k => classes.add(k), remove: k => classes.delete(k), toggle(k, v) { v ? classes.add(k) : classes.delete(k); }, contains: k => classes.has(k)},
  querySelector: () => null, appendChild() {}}; }
function regler() {
  const els = {}, el = id => els[id] || (els[id] = node());
  const row = node(); row.dataset = {feld: 'cerea_lenk_aggr', wert: '100', min: '0', max: '200', stellen: '0'};
  const sub = {}; row.querySelector = q => q === '.regler-schieber' ? null : sub[q] || (sub[q] = node());
  const state = {live: {traktor_id: 'A', profile: {cerea_lenk_aggr: 100, regelart: 'cerea'}}};
  const posts = [], waiting = deferred();
  const c = context({el, state, traktorenListe: [{id: 'A', name: 'A', aktiv: true, cerea_lenk_aggr: 100, regelart: 'cerea'}],
    document: {querySelectorAll: () => [row]}, api: async (m, u, b) => {posts.push({u, b}); return waiting.promise;},
    toast() {}, renderTraktoren: async () => {}});
  run(c, block(app, 'const CEREA_GRUNDWERTE', 'let motorstaerkeGespeichert'));
  run(c, block(app, "el('btnReglerOk').onclick", '/* ------------------------------------------------------------ Vorgewende */'));
  return {c, els, row, posts, waiting, set(id, val) {state.live = {traktor_id: id, profile: {cerea_lenk_aggr: val, regelart: 'cerea'}}; c.traktorenListe = [{id, name: id, aktiv: true, cerea_lenk_aggr: val, regelart: 'cerea'}]; c.renderLenkregler();}};
}
test('Reglerentwurf von A wird bei B nicht übernommen; Speichern bindet echte ID', async () => {
  const r = regler(); r.c.renderLenkregler(); r.c.reglerAendern(r.row, 140); r.set('B', 70);
  assert.equal(r.row.dataset.wert, '70'); const p = r.els.btnReglerOk.onclick();
  assert.equal(r.posts[0].b.erwartete_traktor_id, 'B'); assert.equal(r.posts[0].b.cerea_lenk_aggr, 70);
  r.waiting.resolve(null); await p;
});
test('Ohne echte Traktor-ID wird kein Regler gespeichert', async () => {
  const r = regler(); r.set(undefined, 70); const p = r.els.btnReglerOk.onclick(); r.waiting.resolve(null); await p;
  assert.equal(r.posts.length, 0); assert.equal(r.els.btnReglerOk.disabled, true);
});
test('Verspäteter Reglererfolg A nach A-B-A löscht neuen A-Entwurf nicht', async () => {
  const r = regler(); r.c.renderLenkregler(); r.c.reglerAendern(r.row, 140); const p = r.els.btnReglerOk.onclick();
  r.set('B', 70); r.set('A', 100); r.c.reglerAendern(r.row, 125);
  r.waiting.resolve({data: {}}); await p; assert.equal(r.row.dataset.wert, '125');
  assert.equal(r.row.classList.contains('geaendert'), true);
});
test('Traktorwechsel vor Neuzeichnen: OK sendet keine alten Bildschirmwerte', async () => {
  const r=regler(); r.c.renderLenkregler(); r.c.reglerAendern(r.row,140);
  r.c.state.live={traktor_id:'B',profile:{cerea_lenk_aggr:70,regelart:'cerea'}};
  r.c.reglerKontextPruefen(); const p=r.els.btnReglerOk.onclick(); r.waiting.resolve(null); await p;
  assert.equal(r.posts.length,0); assert.equal(r.row.dataset.wert,'70');
});
test('Regleränderung während Speichern bleibt nach alter Erfolgsantwort als Entwurf erhalten', async () => {
  const r=regler(); r.c.renderLenkregler(); r.c.reglerAendern(r.row,140);const p=r.els.btnReglerOk.onclick();
  r.c.reglerAendern(r.row,125); r.waiting.resolve({data:{}});await p;
  assert.equal(r.row.dataset.wert,'125');assert.equal(r.row.classList.contains('geaendert'),true);
});
function saat() {
  const els = {}, inputs = Object.fromEntries(['breite_m','fahrgasse_m','randspuren'].map(k => [k,Object.assign(node(),{value:'3'})]));
  const el = id => els[id] || (els[id] = Object.assign(node(), {querySelector: q => inputs[q.match(/name="([^"]+)/)[1]], querySelectorAll: () => Object.values(inputs)}));
  const gets = [], posts = [], shown = [], toasts = [], state = {live: {field: {id: 'A', name: 'A'}}};
  const c = context({el, state, clearTimeout() {}, zeichnenAnfordern() {}, toast: t => toasts.push(t), document: {createElement: () => node(), querySelector: () => ({onclick(){}})},
    fetch: async url => {const d = deferred(); gets.push({url, d}); return {json: () => d.promise};},
    api: async (m, url, body) => {const d = deferred(); posts.push({m, url, body, d}); return d.promise;},
    erntejahrWaehlen: async () => 2026, zeigeSaatplan: p => shown.push(p),
  });
  run(c, block(app, 'const saat =', '/* Schrift an einem'));
  run(c, block(app, 'async function renderSaatplan()', '/* Vorschau unter'));
  run(c, block(app, "el('btnSaatplanPlanen').onclick", "el('autoSections').onchange"));
  run(c, block(app, "el('btnSaatplanHier').onclick", '/* Wende: planen'));
  return {c, el, state, gets, posts, shown, toasts, set(id) {state.live.field = id ? {id, name: id} : null; if(c.saatKontextPruefen) c.saatKontextPruefen();}};
}
const plan = name => ({name, startwahl: name, anzahl_spuren: 2, anzahl_fahrgassen: 1, konflikte: []});
const data = name => ({data: {optionen: [{id: name+'-start',text: name}], vorgabe: {start:name+'-start',breite_m:3,fahrgasse_m:21,randspuren:2}, plan: plan(name)}});
async function bereit(s, name='A') { const p=s.c.renderSaatplan();s.gets.at(-1).d.resolve(data(name));await p;s.shown.length=0;s.toasts.length=0; }
test('Saatplanabrufe A und B ignorieren die verspätete A-Antwort', async () => {
  const s = saat(); const a = s.c.renderSaatplan(); s.set('B'); const b = s.c.renderSaatplan();
  s.gets[1].d.resolve(data('B')); await b; s.gets[0].d.resolve(data('A')); await a;
  assert.equal(s.shown.at(-1).name, 'B'); assert.equal(s.el('saatplanFeld').textContent, 'Schlag: B');
});
test('Saatplan A-B-A invalidiert alten Abruf auch bei wieder gleicher ID', async () => {
  const s = saat(); const a = s.c.renderSaatplan(); s.set('B'); s.set('A'); const neu = s.c.renderSaatplan();
  s.gets[1].d.resolve(data('A-neu')); await neu; s.gets[0].d.resolve(data('A-alt')); await a;
  assert.equal(s.shown.at(-1).name, 'A-neu');
});
for (const id of ['btnSaatplanPlanen', 'btnSaatplanAbHier', 'btnSaatplanHier', 'btnSaatplanLoeschen', 'btnSaatplanUebernehmen']) {
  test(`Saatplanaktion ${id}: verspätete Antwort verändert B nicht`, async () => {
    const s = saat(); await bereit(s); const p = s.el(id).onclick(); await flush(); assert.equal(s.posts.length, 1);
    s.set('B'); s.posts[0].d.resolve({data: plan('A')}); await p;
    assert.equal(s.shown.length, 0); assert.equal(s.toasts.length, 0);
    assert.equal(vm.runInContext('saat.plan',s.c),null);
  });
}
for (const id of ['btnSaatplanPlanen','btnSaatplanAbHier','btnSaatplanHier','btnSaatplanLoeschen','btnSaatplanUebernehmen']) {
  test(`Saatplanaktion ${id}: A-B-A ignoriert alte Antwort`,async()=>{
    const s=saat(); await bereit(s); const p=s.el(id).onclick();await flush();s.set('B');s.set('A');
    s.posts[0].d.resolve({data:plan('A-alt')});await p;
    assert.equal(s.shown.length,0);assert.equal(s.toasts.length,0);
  });
}
test('Übernehmen nach Feldwechsel im Saisondialog sendet keinen Auftrag',async()=>{
  const s=saat(), year=deferred();await bereit(s);s.c.erntejahrWaehlen=()=>year.promise;
  const p=s.el('btnSaatplanUebernehmen').onclick();s.set('B');year.resolve(2026);await p;
  assert.equal(s.posts.length,0);
});
test('Saatplan: ältere Anfrage desselben Feldes überschreibt neuere nicht',async()=>{
  const s=saat();const alt=s.c.renderSaatplan(),neu=s.c.renderSaatplan();
  s.gets[1].d.resolve(data('neu'));await neu;s.gets[0].d.resolve(data('alt'));await alt;
  assert.equal(s.shown.at(-1).name,'neu');
});
test('Grenzverlauf von geschlossenem A-Editor ersetzt B-Entwurf nicht', async () => {
  const src = fs.readFileSync(path.join(__dirname, '../feldwerkzeuge.js'), 'utf8'); const {Grenzentwurf} = require('../feldwerkzeuge.js');
  const request = deferred(), el = {}, w = {busy:false, feld:{id:'A'}, folge:1, modus:'grenze', entwurf:new Grenzentwurf([[0,0],[1,0],[0,1]]),
    verlaufStelle:0, api:() => request.promise, geaendert(){this.dirty=true;}, status(){}};
  run(context({w, el:() => el}), '(function(){' + block(src, "el('fwVerlauf').onclick", "el('fwZiel').onchange") + '}).call(w)');
  const p = el.onclick(); w.feld={id:'B'}; w.folge=2; w.entwurf=new Grenzentwurf([[100,100],[110,100],[100,110]]);
  request.resolve({ok:true,data:{verlauf:[{boundary:[[0,0],[1,0],[0,1]],zeit:1}]}}); await p;
  assert.equal(w.entwurf.punkte[0][0],100); assert.equal(w.dirty, undefined);
});
test('Speicherantwort markiert nachträglich veränderte Eingaben weiter als Entwurf', async () => {
  const request = deferred(), input = {value:'2'}, box = node(); box.classList.add('changed');
  const c = context({settings:{gruppen:[{felder:[{typ:'zahl',schluessel:'test.wert'}]}],werte:{'test.wert':1}},
    document:{getElementById:() => input, querySelectorAll:() => [box]}, el:() => node(), toast(){}, api:() => request.promise});
  input.closest = () => box;
  run(c, block(app, 'function feldWert(', "el('btnSaveSettings').onclick"));
  const p = c.einstellungenSpeichern(); input.value='3'; request.resolve({data:{werte:{'test.wert':2},datei:'Fixture',neustart_noetig:[]}}); await p;
  assert.equal(box.classList.contains('changed'),true);
});
function worker(status, reject=false) {
  const events={}, writes=[], good={status:200,ok:true,clone(){return this}}, response={status,ok:status>=200&&status<300,clone(){return this}};
  const cache={put:async (req,r) => writes.push(r), match:async () => good};
  const c=context({self:{location:{origin:'http://fixture'},addEventListener:(n,f) => events[n]=f}, URL,
    fetch:async () => {if(reject) throw Error('offline'); return response;}, caches:{open:async () => cache, match:async () => good}});
  run(c,fs.readFileSync(path.join(__dirname,'../sw.js'),'utf8'));let promise;
  events.fetch({request:{method:'GET',url:'http://fixture/app.js'},respondWith:p => promise=p});
  return {promise,writes,good,response};
}
for(const status of [404,500]) test(`Offline-Hülle behält guten Cache bei HTTP ${status}`, async () => {
  const w=worker(status); assert.equal(await w.promise,w.good); await flush(); assert.equal(w.writes.length,0);
});
test('Erfolgreiche Hülle wird aktualisiert, Netzausfall nutzt Cache', async () => {
  const w=worker(200); assert.equal(await w.promise,w.response); await flush(); assert.equal(w.writes.length,1);
  const offline=worker(0,true); assert.equal(await offline.promise,offline.good);
});
test('Neue Ausgangsstörung während Pause warnt einmal; unveränderte Folgebilder bleiben still', () => {
  const {ereignisse,pruefen}=require('../alerts.js');
  const pause={steering:{armed:true,command:{engaged:false,reason:'zu langsam'}}};
  const fault={steering:{armed:true,command:{engaged:false,reason:'Lenkausgang gestört: getrennt'}}};
  const es=ereignisse(pause,fault,true); assert.equal(es.length,1); assert.equal(es[0].stufe,'alarm');
  assert.equal(ereignisse(fault,fault,true).length,0); assert.equal(pruefen(pause,fault,true,100000).length,1);
  assert.equal(pruefen(pause,fault,true,100001).length,0);
});

for(const id of ['btnSaatplanPlanen','btnSaatplanAbHier','btnSaatplanHier','btnSaatplanUebernehmen','btnSaatplanLoeschen']) {
  test(`Feld B lädt: ${id} darf keinen POST mit Formular A auslösen`,async()=>{
    const s=saat();await bereit(s);s.set('B');const p=s.c.renderSaatplan();
    assert.equal(s.el('saatplanStart').value,'');assert.equal(s.el(id).disabled,true);
    await s.el(id).onclick();assert.equal(s.posts.length,0);
    s.gets.at(-1).d.resolve(data('B'));await p;assert.equal(s.el(id).disabled,false);
    const action=s.el(id).onclick();await flush();assert.equal(s.posts.length,1);
    if(id==='btnSaatplanPlanen') assert.equal(s.posts[0].body.start,'B-start');
    s.posts[0].d.resolve({data:plan('B')});await action;
  });
}
test('Alte Formularfreigabe A gilt nach A-B-A nicht; nur neuer GET entsperrt',async()=>{
  const s=saat();await bereit(s);s.set('B');s.set('A');
  assert.equal(s.el('btnSaatplanHier').disabled,true);await s.el('btnSaatplanHier').onclick();assert.equal(s.posts.length,0);
  await bereit(s);assert.equal(s.el('btnSaatplanHier').disabled,false);
});
function automatikUmgebung(r) {
  const effects=[];r.c.automatikZeigen=()=>effects.push('automatik');r.c.probefahrtZeigen=()=>effects.push('probefahrt');r.c.toast=()=>effects.push('toast');
  r.c.renderTraktoren=async()=>effects.push('traktoren');
  run(r.c,block(app,'let automatikBedienungLaeuft','/* Die Zeile oben:'));
  run(r.c,block(app,'let probefahrtBedienungLaeuft',"el('btnProbefahrtVorher').onclick"));
  return effects;
}
for(const aktion of ['start','stop','zurueck']) test(`Automatik ${aktion}: Antwort A darf neuen B-Entwurf nicht löschen`,async()=>{
  const r=regler(),effects=automatikUmgebung(r);r.c.renderLenkregler();const p=r.c.automatikBedienen(aktion);
  r.set('B',70);r.c.reglerAendern(r.row,125);r.waiting.resolve({data:{hinweis:'A'}});await p;
  assert.equal(r.row.dataset.wert,'125');assert.equal(r.row.classList.contains('geaendert'),true);assert.deepEqual(effects,[]);
  assert.equal(r.posts[0].b.erwartete_traktor_id,'A');
});
test('Automatik: A-B-A und Bearbeitung während Nachladen unterdrücken alte UI-Effekte',async()=>{
  const r=regler(),effects=automatikUmgebung(r),nachladen=deferred();r.c.renderLenkregler();
  r.c.renderTraktoren=()=>nachladen.promise;const p=r.c.automatikBedienen('start');r.waiting.resolve({data:{}});await flush();
  r.set('B',70);r.set('A',100);r.c.reglerAendern(r.row,125);nachladen.resolve();await p;
  assert.equal(r.row.dataset.wert,'125');assert.deepEqual(effects,[]);
});
test('Probefahrt sendet erwartete ID und ignoriert Antwort nach Gespannwechsel',async()=>{
  const r=regler(),effects=automatikUmgebung(r);r.c.renderLenkregler();const p=r.c.probefahrtBedienen('vorher');
  r.set('B',70);r.waiting.resolve({data:{hinweis:'A'}});await p;
  assert.equal(r.posts[0].b.erwartete_traktor_id,'A');assert.deepEqual(effects,[]);
});
test('Neue Ausgangsstörung während aktiver Führung ist genau ein Alarm ohne Pause-Info',()=>{
  const {ereignisse}=require('../alerts.js');const vorher={steering:{armed:true,command:{engaged:true,reason:'lenkt'}}};
  const jetzt={steering:{armed:true,command:{engaged:false,reason:'Lenkausgang gestört: Verbindung fehlt'}}};
  const e=ereignisse(vorher,jetzt,true);assert.equal(e.length,1);assert.equal(e[0].stufe,'alarm');
  assert.equal(e[0].id,'lenkausgang-stoerung');assert.equal(ereignisse(jetzt,jetzt,true).length,0);
});

test('Karten-Hintergrundabruf füllt das aktuelle Formular vor Freigabe',async()=>{
  const s=saat(),p=s.c.saatplanLaden('A');s.gets.at(-1).d.resolve(data('A'));await p;
  assert.equal(s.el('btnSaatplanHier').disabled,false);assert.equal(s.el('saatplanStart').value,'A-start');
});
test('Fehlgeschlagener Formularabruf lässt alle Aktionen gesperrt',async()=>{
  const s=saat();await bereit(s);s.set('B');s.c.fetch=async()=>({ok:false});await s.c.renderSaatplan();
  assert.equal(s.el('saatplanStart').value,'');assert.equal(s.el('btnSaatplanHier').disabled,true);
  await s.el('btnSaatplanPlanen').onclick();assert.equal(s.posts.length,0);
});
for(const fn of ['automatikBedienen','probefahrtBedienen']) {
  test(`${fn}: ohne echte ID kein Request; gültiger Kontext behält Wirkung`,async()=>{
    const r=regler(),effects=automatikUmgebung(r);r.set(undefined,70);await r.c[fn]('start');assert.equal(r.posts.length,0);
    r.set('A',100);const p=r.c[fn](fn==='automatikBedienen'?'start':'vorher');
    r.waiting.resolve({data:{hinweis:'Fertig'}});await p;assert.equal(r.posts[0].b.erwartete_traktor_id,'A');
    assert.ok(effects.includes(fn==='automatikBedienen'?'automatik':'probefahrt'));assert.ok(effects.includes('toast'));
  });
  test(`${fn}: neue Bearbeitung desselben Traktors verdrängt alte Rückmeldung`,async()=>{
    const r=regler(),effects=automatikUmgebung(r);r.c.renderLenkregler();const p=r.c[fn]('start');
    r.c.reglerAendern(r.row,125);r.waiting.resolve({data:{hinweis:'Alt'}});await p;
    assert.equal(r.row.dataset.wert,'125');assert.deepEqual(effects,[]);
  });
}
test('Geschützter Traktorabruf verwirft nach seinem GET einen überholten Kontext',async()=>{
  const request=deferred();let aktuell=true,zugriffe=0;
  const c=context({fetch:()=>request.promise,el(){zugriffe++;throw Error('Darf nicht rendern');}});
  run(c,block(app,'async function renderTraktoren(',"el('traktorSelect').onchange"));
  const p=c.renderTraktoren(()=>aktuell);aktuell=false;
  request.resolve({json:async()=>[{id:'A',aktiv:true}]});await p;assert.equal(zugriffe,0);
});
