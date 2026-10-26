'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../komfort.js'), 'utf8');
const A = require('../arbeitsansicht.js');
const tick = () => new Promise(resolve => setImmediate(resolve));

// Die echten Ereignisbehandler laufen mit verzögerten API-Antworten.
function environment({postDeferred=false}={}) {
  class Element {
    constructor(tag) { this.tagName = tag.toUpperCase(); this.children = []; this.events = {}; this.attrs = {}; this._text = ''; this._value = ''; this.disabled = false; }
    set textContent(value) { this._text = String(value); this.children = []; }
    get textContent() { return this._text + this.children.map(c => c.textContent).join(' '); }
    appendChild(child) { child.parentElement = this; this.children.push(child); return child; }
    append(...children) { children.forEach(c => this.appendChild(c)); }
    replaceChildren(...children) { this.children = []; this._text = ''; this._value = ''; this.append(...children); }
    setAttribute(k, v) { this.attrs[k] = v; }
    addEventListener(k, v) { this.events[k] = v; }
    get options() { return this.children; }
    get value() { return this.tagName === 'SELECT' ? (this.children.find(c => c.value === this._value) || this.children[0])?.value || '' : this._value; }
    set value(v) { this._value = String(v); }
    getClientRects() { return [{}]; }
    querySelector() { return null; }
    async change() { return this.onchange ? this.onchange() : this.events.change?.(); }
  }
  const host = new Element('div'), body = new Element('body'), shortcuts = new Element('nav');
  body.append(host, shortcuts);
  const doc = {hidden: true, body, createElement: t => new Element(t), createTextNode: t => { const x = new Element('text'); x.textContent = t; return x; }, getElementById: id => id === 'komfortInhalt' ? host : id === 'komfortSchnell' ? shortcuts : null};
  const context = {felder:[{id:'f',name:'Testfeld',updated_at:1}], traktoren:[{id:'t1',name:'Alt',bauform:'traktor',aktiv:false},{id:'t2',name:'Aktiv',bauform:'traktor',aktiv:true},{id:'d',name:'Drescher',bauform:'maehdrescher',aktiv:false}], profile:[{id:'g1',name:'Altgerät',geraet_art:'grubber',width_m:3,aktiv:false},{id:'g2',name:'Aktivgerät',geraet_art:'grubber',width_m:4,aktiv:true},{id:'h',name:'Schneidwerk',geraet_art:'schneidwerk',width_m:6,aktiv:false}], jobs:['j1','j2'].map((id,i)=>({id,field_id:'f',operation:'Arbeit '+i,device_id:'lokal',started_at:Date.now()/1000,ended_at:Date.now()/1000,area_ha:1})), vorlieben:{favoriten:[],zuletzt:[],vorlagen:[],schnellzugriffe:['fields','lines','jobs']}};
  const requests = [], messages = [], saved = [], posts = [];
  const live = {field:context.felder[0],traktor_id:'t2',profile:context.profile[1],device:{id:'lokal'}};
  const sandbox = vm.createContext({module:{exports:{}},document:doc,window:{Arbeitsansicht:A,setInterval() { return 1; }}});
  vm.runInContext(source, sandbox);
  const app = sandbox.module.exports.installieren({getState:()=>live,melden:(...x)=>messages.push(x),aktualisieren(){},oeffnen(){},api:async(method,url,data)=>{
    if (url === '/api/ausbau/kontext') return {ok:true,data:structuredClone(context)};
    if (method === 'POST') {saved.push({url,data});if(postDeferred)return new Promise((resolve,reject)=>posts.push({resolve,reject}));return {ok:true,data:{}};}
    return new Promise((resolve,reject)=>requests.push({url,resolve,reject}));
  }});
  const all = e => [e,...e.children.flatMap(all)];
  const label = text => all(host).find(e=>e.tagName==='LABEL' && e._text===text).children[0];
  const button = text => all(host).find(e=>e.tagName==='BUTTON' && e._text===text);
  function answer(id, value=7) {
    for(const r of requests.filter(r=>r.url.includes('/'+id+'/')&&!r.done)) {
      r.done=true;r.resolve({ok:true,data:r.url.endsWith('/kosten')?{eingaben:{diesel_liter:value},gesamt_eur:value,kosten_eur_ha:value}:{bekannt:true,gesamt_ha:2,bearbeitet_ha:1,rest_ha:1,prozent:50,restzeit_min:null}});
    }
  }
  return {app,host,label,button,requests,messages,saved,answer,context,live,posts};
}

test('Arbeitsstart zeigt das aktive Gespann statt des ersten Listeneintrags', async()=>{
  const u=environment();await tick();
  assert.equal(u.label('2 · Traktor').value,'t2');
  assert.equal(u.label('Gerät').value,'g2');
});

test('Drescherauswahl bietet ausschließlich Schneidwerke an', async()=>{
  const u=environment();await tick();const tractor=u.label('2 · Traktor');tractor.value='d';await tractor.change();
  assert.deepEqual(u.label('Gerät').options.map(x=>x.value),['h']);
  tractor.value='t2';await tractor.change();
  assert.deepEqual(u.label('Gerät').options.map(x=>x.value),['g1','g2']);
});

test('Kosten bleiben bei Arbeitswechsel bis zur passenden Antwort gesperrt', async()=>{
  const u=environment();await tick();const job=u.label('Arbeitsgang für Restfläche und Kosten'),save=u.button('Verbrauch und Kosten speichern');
  job.value='j1';const first=job.change();u.answer('j1',12);await first;
  assert.equal(u.label('Diesel (Liter)').value,'12');
  job.value='j2';const second=job.change();
  assert.equal(save.disabled,true);
  assert.equal(u.label('Diesel (Liter)').value,'');
  await save.onclick();assert.equal(u.saved.length,0);
  u.answer('j2',24);await second;
  assert.equal(save.disabled,false);assert.equal(u.label('Diesel (Liter)').value,'24');
});

test('Fehlgeschlagener Kostenabruf darf Werte der vorherigen Arbeit nicht speichern', async()=>{
  const u=environment();await tick();const job=u.label('Arbeitsgang für Restfläche und Kosten'),save=u.button('Verbrauch und Kosten speichern');
  job.value='j1';const first=job.change();u.answer('j1',12);await first;
  job.value='j2';const second=job.change();u.requests.filter(r=>r.url.includes('/j2/')).forEach(r=>r.reject(new Error('Verbindung unterbrochen')));await second;
  const saving=save.onclick();await tick();u.answer('j2',24);await saving;assert.equal(u.saved.length,0);assert.equal(save.disabled,true);
});

test('Verspäteter Fehler einer alten Auswahl überschreibt keine neu geladene Arbeit', async()=>{
  const u=environment();await tick();const job=u.label('Arbeitsgang für Restfläche und Kosten');
  job.value='j1';const first=job.change();job.value='j2';const second=job.change();u.answer('j2',24);await second;
  u.requests.filter(r=>r.url.includes('/j1/')).forEach(r=>r.reject(new Error('Alter Abruf gescheitert')));await first;
  assert.doesNotMatch(u.host.textContent,/Alter Abruf gescheitert/);
  assert.equal(u.label('Diesel (Liter)').value,'24');
});


test('Bestätigte Pause überstimmt alten Livestand und bleibt fortsetzbar', async()=>{
  const u=environment();await tick();
  const j=u.context.jobs[0];j.ended_at=null;j.pausiert=true;
  u.live.job={...j,pausiert:false};
  await u.app.laden();
  assert.ok(u.label('Offenen Auftrag fortsetzen').options.some(x=>x.value===j.id));
  assert.doesNotMatch(u.host.textContent,/In Arbeit/);
  assert.match(u.host.textContent,/Pausiert/);
  u.live.job=null;u.live.pausierte_arbeit=j;u.app.live(u.live);
  assert.ok(u.label('Offenen Auftrag fortsetzen').options.some(x=>x.value===j.id));
});

test('Live-Arbeitswechsel erhält ungespeicherte Kosteneingaben', async()=>{
  const u=environment();await tick();const job=u.label('Arbeitsgang für Restfläche und Kosten');
  job.value='j1';const loading=job.change();u.answer('j1',12);await loading;
  u.label('Diesel (Liter)').value='37';
  u.live.job={id:'j2'};u.app.live(u.live);
  assert.equal(job.value,'j1');assert.equal(u.label('Diesel (Liter)').value,'37');
  assert.equal(u.button('Verbrauch und Kosten speichern').disabled,false);
});


test('Nachladen während des Speicherns darf bestätigte Kosten nicht zurücksetzen', async()=>{
  const u=environment({postDeferred:true});await tick();const job=u.label('Arbeitsgang für Restfläche und Kosten');
  job.value='j1';const loading=job.change();u.answer('j1',7);await loading;
  u.label('Diesel (Liter)').value='20';const saving=u.button('Verbrauch und Kosten speichern').onclick();await tick();
  const reload=u.button('Restfläche und Kosten neu laden');
  assert.equal(reload.disabled,true);
  await reload.onclick();await job.change();
  assert.equal(u.requests.length,2);
  assert.equal(u.label('Diesel (Liter)').value,'20');
  u.posts[0].resolve({ok:true,data:{}});await tick();u.answer('j1',20);await saving;
  assert.equal(u.label('Diesel (Liter)').value,'20');
  assert.equal(u.button('Verbrauch und Kosten speichern').disabled,false);
  assert.equal(reload.disabled,false);assert.equal(u.saved[0].data.diesel_liter,20);
});

test('Gescheitertes Kostenspeichern erhält Eingaben und gibt erneutes Speichern frei', async()=>{
  const u=environment({postDeferred:true});await tick();const job=u.label('Arbeitsgang für Restfläche und Kosten');
  job.value='j1';const loading=job.change();u.answer('j1',7);await loading;
  u.label('Diesel (Liter)').value='20';const saving=u.button('Verbrauch und Kosten speichern').onclick();await tick();
  u.posts[0].reject(new Error('Speichern fehlgeschlagen'));await saving;
  assert.equal(u.label('Diesel (Liter)').value,'20');
  assert.equal(u.button('Verbrauch und Kosten speichern').disabled,false);
  assert.equal(u.button('Restfläche und Kosten neu laden').disabled,false);
});
