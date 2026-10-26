"use strict";
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const root=path.join(__dirname,'..'),source=fs.readFileSync(path.join(root,'app.js'),'utf8');
function umgebung(live){
 const buttons={btnJob:{disabled:false},btnSteer:{disabled:false}},calls=[],messages=[],pending=[];
 const state={live};
 for(const id of ['btnJob','btnSteer']){
  const handler=source.match(new RegExp("el\\('"+id+"'\\)\\.onclick = async \\(\\) => \\{[\\s\\S]*?\\r?\\n\\};"));
  assert.ok(handler,'Originalhandler '+id);
  vm.runInNewContext(handler[0],{el:id=>buttons[id],state,arbeitBezeichnung:()=> 'Grubbern',toast:(...a)=>messages.push(a),api:async(...a)=>{calls.push(a);return new Promise(resolve=>pending.push(resolve));}});
 }
 return {buttons,calls,messages,pending,state};
}
const job={id:'j1',operation:'Säen',area_ha:1.25};
test('Markieren pausiert und setzt ohne Websocket-Zwischenstand denselben Auftrag fort',async()=>{
 const u=umgebung({job:{...job}}),off=u.buttons.btnJob.onclick();
 assert.deepEqual(JSON.parse(JSON.stringify(u.calls[0])),['POST','/api/job/markierung',{an:false,operation:'Grubbern'}]);
 u.pending.shift()({ok:true,data:{...job,pausiert:true}});await off;
 assert.equal(u.state.live.job,null);assert.equal(u.state.live.pausierte_arbeit.id,'j1');assert.match(u.messages[0][0],/pausiert.*Fläche bleibt erhalten/);
 const on=u.buttons.btnJob.onclick();assert.equal(u.calls[1][2].an,true);
 u.pending.shift()({ok:true,data:{...job,pausiert:false}});await on;
 assert.equal(u.state.live.job.id,'j1');assert.equal(u.state.live.pausierte_arbeit,null);assert.match(u.messages[1][0],/fortgesetzt.*Säen/);
});
test('Ausstehende Markierung sperrt Doppelklick und wird danach freigegeben',async()=>{
 const u=umgebung({job:null}),first=u.buttons.btnJob.onclick();const second=u.buttons.btnJob.onclick();
 assert.equal(u.calls.length,1);await second;assert.equal(u.buttons.btnJob.disabled,true);
 u.pending.shift()({ok:true,data:job});await first;assert.equal(u.buttons.btnJob.disabled,false);assert.equal(u.state.live.job.id,'j1');assert.match(u.messages[0][0],/Markieren an.*Säen/);
});
test('Fehlgeschlagenes Markieren meldet keinen Erfolg und erhält den Zustand',async()=>{
 const u=umgebung({job}),first=u.buttons.btnJob.onclick();u.pending.shift()(null);await first;
 assert.equal(u.state.live.job,job);assert.equal(u.messages.length,0);assert.equal(u.buttons.btnJob.disabled,false);
});
test('Lenkung AUS pausiert auch im Rückwärts-Wartezustand und übernimmt die Bestätigung',async()=>{
 const u=umgebung({job,steering:{armed:true,command:{engaged:false,reason:'Rückwärtsfahrt'}}}),off=u.buttons.btnSteer.onclick();
 assert.equal(u.calls[0][1],'/api/steering/disarm');u.pending.shift()({ok:true,data:{armed:false,markieren:false,pausierte_arbeit:{...job,pausiert:true}}});await off;
 assert.equal(u.state.live.job,null);assert.equal(u.state.live.pausierte_arbeit.id,'j1');assert.equal(u.state.live.steering.armed,false);assert.match(u.messages[0][0],/Lenkung und Markierung aus/);
});
test('Lenkung AUS meldet bei fehlender Bestätigung keinen Erfolg',async()=>{
 const u=umgebung({job,steering:{armed:true,command:{engaged:true}}}),off=u.buttons.btnSteer.onclick();u.pending.shift()(null);await off;
 assert.equal(u.messages.length,0);assert.equal(u.state.live.job,job);assert.equal(u.state.live.steering.armed,true);assert.equal(u.buttons.btnSteer.disabled,false);
});
test('Ausstehender Lenkbefehl wird bei Doppelklick nur einmal gesendet',async()=>{
 const u=umgebung({job,steering:{armed:true,command:{engaged:true}}}),first=u.buttons.btnSteer.onclick();const second=u.buttons.btnSteer.onclick();assert.equal(u.calls.length,1);await second;
 u.pending.shift()({ok:true,data:{armed:false,markieren:false,pausierte_arbeit:job}});await first;assert.equal(u.buttons.btnSteer.disabled,false);
});
test('Lenkung EIN behält den vorhandenen Freigabepfad',async()=>{
 const u=umgebung({job:null,steering:{armed:false,command:{engaged:false}}}),on=u.buttons.btnSteer.onclick();assert.equal(u.calls[0][1],'/api/steering/arm');
 u.pending.shift()({ok:true,data:{armed:true,markieren_gestartet:true}});await on;assert.match(u.messages[0][0],/Lenkung scharf.*Markieren an/);
});
test('Schnellzugriffe liegen einmal im Arbeitstag-Menü vor dessen Inhalt',()=>{
 const html=fs.readFileSync(path.join(root,'index.html'),'utf8'),panel=html.match(/<section[^>]+data-panel="arbeitstag"[\s\S]*?<\/section>/)[0];
 assert.equal((html.match(/id="komfortSchnell"/g)||[]).length,1);assert.match(panel,/id="komfortSchnell"[\s\S]*id="komfortInhalt"/);assert.ok(html.indexOf('id="komfortSchnell"')>html.indexOf('</main>'));
});
test('Schnellzugriffe stehen im Menüfluss mit sheet-Präfix',()=>{
 const css=fs.readFileSync(path.join(root,'komfort.css'),'utf8');assert.match(css,/\.sheet \.komfort-schnell\{position:static;/);assert.doesNotMatch(css,/(?:^|[},])\.komfort-schnell/);
});
