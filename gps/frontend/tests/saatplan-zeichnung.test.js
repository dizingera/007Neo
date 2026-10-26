const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname,'../app.js'),'utf8');
function environment(plan) {
 const calls={depth:0,lines:0,frames:0,vehicle:0,messages:[]};
 const ctx={save(){calls.depth++;},restore(){calls.depth--;},beginPath(){},closePath(){},setLineDash(){},stroke(){},moveTo(...p){assert.ok(p.every(Number.isFinite));},lineTo(...p){assert.ok(p.every(Number.isFinite));calls.lines++;}};
 const c={state:{live:{field:{id:'f'}},view:{scale:1}},saat:{feld:'f',plan,zeigen:true},ctx,F:{},toast:m=>calls.messages.push(m),weltText(){},polyline(points){points.forEach((p,i)=>i?ctx.lineTo(...p):ctx.moveTo(...p));},saatplanLaden(){},requestAnimationFrame(){calls.frames++;},inBewegung:()=>false,document:{visibilityState:'visible'},neuZeichnen:true,letztesBild:0,BILD_ABSTAND_MS:33};
 vm.createContext(c);
 const a=source.indexOf('function drawSaatplan() {'),b=source.indexOf('/* Hindernisse:',a);
 vm.runInContext(source.slice(a,b),c);
 c.zeichnen=()=>{c.drawSaatplan();calls.vehicle++;};
 const r=source.indexOf('function render(zeit) {'),e=source.indexOf('function zeichnen()',r);
 vm.runInContext(source.slice(r,e),c);
 return {c,calls};
}
test('Unvollständige Pläne halten Karten- und Fahrzeugdarstellung nicht an',()=>{
 for(const plan of [{ringe:[],spuren:[]},{},{ringe:[null,{punkte:[null]}],spuren:[null,{fahrgasse:true,stuecke:[]}],konflikte:[null]}, {ringe:1,spuren:{},konflikte:'defekt'}]) {
  const {c,calls}=environment(plan);assert.doesNotThrow(()=>c.render(100));
  assert.equal(calls.frames,1);assert.equal(calls.vehicle,1);assert.equal(calls.depth,0);
  assert.equal(calls.messages.length,1);c.drawSaatplan();assert.equal(calls.messages.length,1);
 }
});
test('Gültige Planteile bleiben sichtbar, ungültige Koordinaten werden ausgelassen',()=>{
 const {c,calls}=environment({ringe:[],spuren:[{stuecke:[[[0,0],[0,10]],[[0,0],[Infinity,2]],null]}],konflikte:[]});
 c.drawSaatplan();assert.equal(calls.lines,1);assert.equal(calls.depth,0);assert.equal(calls.messages.length,1);
});
test('Gültiger Plan zeichnet unverändert ohne Warnung',()=>{
 const {c,calls}=environment({fahrgasse_m:20,ringe:[{punkte:[[0,0],[10,0],[10,10]]}],spuren:[{fahrgasse:true,versatz_m:0,stuecke:[[[0,0],[0,10]]]}],konflikte:[{text:'Hinweis ohne Kartenpunkt'}]});
 c.drawSaatplan();assert.equal(calls.lines,3);assert.equal(calls.messages.length,0);assert.equal(calls.depth,0);
});
