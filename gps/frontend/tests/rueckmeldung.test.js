const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');const vm=require('node:vm');const path=require('node:path');
const R=require('../rueckmeldung.js');
const ok=data=>({ok:true,data});
function umgebung(){
 const timers=new Map();let id=0;
 const zeit={setTimeout(fn,ms){timers.set(++id,{fn,ms});return id;},clearTimeout(id){timers.delete(id);}};
 function n(){return {hidden:false,attrs:{},children:[],parentElement:null,setAttribute(k,v){this.attrs[k]=v;},replaceChildren(){this.children=[];},append(...els){els.forEach(e=>this.appendChild(e));},appendChild(e){e.parentElement=this;this.children.push(e);}};}
 const body=n(),box=n();box.hidden=true;body.appendChild(box);const handlers={};let dialog=null;
 const doc={body,getElementById:()=>box,querySelector:()=>dialog,createElement:n,addEventListener:(name,fn)=>{handlers[name]=fn;}};
 const meldung=R.erstellen(doc,zeit);
 return {meldung,box,body,n,handlers,modal:d=>dialog=d,flush(ms){for(const [i,t] of [...timers])if(t.ms===ms){timers.delete(i);t.fn();}},inhalt:()=>box.children[1]?.textContent,timers};
}
test('Nur angenommene Speicherung wird bestätigt, nicht HTTP-Fehler oder fehlende Bestätigung',()=>{
 assert.equal(R.erfolgstext('POST','/api/settings',{},ok({gespeichert:2})),'Einstellungen gespeichert');
 for(const r of [null,{}, {ok:false},ok({gespeichert:0}),ok({gespeichert:1,ok:false}),ok({gespeichert:1,fehler:'voll'})])assert.equal(R.erfolgstext('POST','/api/settings',{},r),null);
});
test('Fahrdaten und Hintergrundabfragen erzeugen keine Meldungsflut',()=>{
 for(const p of ['/api/simulator','/api/sync/live','/api/turn/plan','/api/lenkung/einrichten/feinabstimmung'])assert.equal(R.erfolgstext('POST',p,{},ok({})),null);
 assert.equal(R.erfolgstext('GET','/api/settings',{},ok({gespeichert:1})),null);
});
test('Grenze, Kalibrierung und bestätigte Checkliste erhalten passende Rückmeldung',()=>{
 assert.equal(R.erfolgstext('POST','/api/fields/A/arbeitsgrenze',{},ok({})),'Grenze gespeichert');
 assert.equal(R.erfolgstext('POST','/api/lenkung/kalibrierung',{aktion:'geometrie'},ok({})),'Gemessene Maße gespeichert');
 assert.equal(R.erfolgstext('POST','/api/checklist/imu',{erledigt:true},ok({})),'Schritt bestätigt');
 assert.equal(R.erfolgstext('POST','/api/checklist/imu',{erledigt:false},ok({})),'Bestätigung zurückgenommen');
});
test('Teilweise Sicherung wird nicht als vollständig erfolgreich bestätigt',()=>{
 assert.equal(R.erfolgstext('POST','/api/backup',{},ok({ok:true,ziele:[{ok:true},{ok:false}]})),null);
 assert.equal(R.erfolgstext('POST','/api/backup',{},ok({ok:true,ziele:[{ok:true}]})),'Sicherung erfolgreich erstellt');
});
test('Erfolg bleibt sechs Sekunden sichtbar und kann geschlossen werden',()=>{
 const u=umgebung();u.meldung.vormerken('POST','/api/profile',{},ok({}));assert.equal(u.box.hidden,true);u.flush(180);
 assert.match(u.box.className,/erfolg/);assert.equal(u.box.children[0].textContent,'✓');assert.equal(u.box.attrs.role,'status');assert.equal(u.inhalt(),'Gerätewerte übernommen');
 assert.ok([...u.timers.values()].some(t=>t.ms===6000));u.box.children[2].onclick();assert.equal(u.box.hidden,true);
});
test('Genauere vorhandene Erfolgsmeldung ersetzt die generische ohne zweites Popup',()=>{
 const u=umgebung();u.meldung.vormerken('POST','/api/settings',{},ok({gespeichert:1}));u.meldung.anzeigen('Motorstärke 60 % übernommen');u.flush(180);
 assert.equal(u.inhalt(),'Motorstärke 60 % übernommen');assert.match(u.box.className,/erfolg/);assert.equal([...u.timers.values()].filter(t=>t.ms===180).length,0);
});
test('Fehler verdrängt vorgemerkten Erfolg und bleibt als Alarm sichtbar',()=>{
 const u=umgebung();u.meldung.vormerken('POST','/api/profile',{},ok({}));u.meldung.anzeigen('Speichern fehlgeschlagen',true);u.flush(180);
 assert.match(u.box.className,/error/);assert.equal(u.box.attrs.role,'alert');assert.equal(u.inhalt(),'Speichern fehlgeschlagen');
});
test('Native Dialoge verdecken Meldung nicht; nach Schließen zurück in die Seite',()=>{
 const u=umgebung(),d=u.n();u.modal(d);u.meldung.anzeigen('Maße gespeichert');assert.equal(u.box.parentElement,d);
 u.modal(null);u.handlers.close();assert.equal(u.box.parentElement,u.body);assert.equal(u.box.hidden,false);
});
test('Meldungstext bleibt Text und negative Hinweise werden nicht grün',()=>{
 const u=umgebung();u.meldung.anzeigen('<img src=x onerror=evil> gespeichert');assert.equal(u.inhalt(),'<img src=x onerror=evil> gespeichert');
 assert.equal(R.art('Nicht gespeichert',false,true),'hinweis');assert.equal(R.art('Nichts geändert',false,false),'hinweis');
});
function apiTest(antwort,roh=false){
 const src=fs.readFileSync(path.join(__dirname,'../app.js'),'utf8');
 const start=src.indexOf(roh?'async function apiRoh(':'async function api('),ende=src.indexOf(roh?'function wahlSetzen(':'let toastTimer',start);
 const success=[],fehler=[];const c=vm.createContext({fetch:async()=>antwort,window:{Rueckmeldung:{vormerken:(...x)=>success.push(x),anzeigen:(...x)=>fehler.push(x)}},toast:(...x)=>fehler.push(x)});
 vm.runInContext(src.slice(start,ende),c);return {aufruf:()=>c[roh?'apiRoh':'api']('POST','/api/settings',{aenderungen:{x:1}}),success,fehler};
}
test('Echte API-Hülle bestätigt erst nach gültiger Serverantwort',async()=>{
 let aufloesen;const promise=new Promise(r=>aufloesen=r);const u=apiTest({ok:true,json:()=>promise});const req=u.aufruf();assert.equal(u.success.length,0);
 aufloesen(ok({gespeichert:1}));await req;assert.equal(u.success.length,1);
});
test('Beide API-Wege bestätigen weder Fehler noch ungültiges JSON oder leere Antwort',async()=>{
 for(const roh of [false,true])for(const antwort of [{ok:false,status:409,json:async()=>({detail:'abgelehnt'})},{ok:true,json:async()=>{throw Error('json');}},{ok:true,json:async()=>({})},{ok:true,json:async()=>({ok:false,detail:'abgelehnt'})}]){
 const u=apiTest(antwort,roh);const r=await u.aufruf();assert.equal(u.success.length,0);assert.ok(u.fehler.length);assert.ok(roh?!r.ok:r===null);
 }
});

test('Ein frischer Alarm behält Vorrang vor einer späteren Speicherbestätigung',()=>{
 const u=umgebung();u.meldung.anzeigen('Lenkausgang gestört: getrennt',true);
 u.meldung.vormerken('POST','/api/profile',{},ok({}));u.flush(180);
 assert.match(u.box.className,/error/);assert.equal(u.inhalt(),'Lenkausgang gestört: getrennt');
 assert.ok([...u.timers.values()].some(t=>t.ms===9000));
 u.flush(9000);assert.match(u.box.className,/erfolg/);assert.equal(u.inhalt(),'Gerätewerte übernommen');
});
test('Genauere Bestätigung während Alarm wird nur einmal nach der Quittierung gezeigt',()=>{
 const u=umgebung();u.meldung.anzeigen('Störung',true);
 u.meldung.vormerken('POST','/api/settings',{},ok({gespeichert:1}));u.meldung.anzeigen('Motorstärke 60 % übernommen');u.flush(180);
 assert.equal(u.inhalt(),'Störung');u.box.children[2].onclick();
 assert.equal(u.inhalt(),'Motorstärke 60 % übernommen');u.flush(6000);assert.equal(u.box.hidden,true);
});


// Neue Bedienwege benötigen ebenso eine angenommene Antwort.
test('Arbeitszentrale bestätigt angenommene Aktionen und keine abgewiesene Messung',()=>{
 const R=require('../rueckmeldung.js');
 assert.equal(R.erfolgstext('POST','/api/arbeitstag/pause',{}, {ok:true,data:{pausiert:true}}),'Arbeit pausiert');
 assert.equal(R.erfolgstext('POST','/api/ausbau/fahrserie',{aktion:'vorher'}, {ok:false}),null);
 assert.match(R.erfolgstext('POST','/api/ausbau/fahrserie',{aktion:'vorher'}, {ok:true,data:{}}),/vorbereitet/);
 assert.equal(R.erfolgstext('GET','/api/ausbau/diagnose',null,{ok:true,data:{}}),null);
});
