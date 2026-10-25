const test=require('node:test');
const assert=require('node:assert/strict');
const {LenkWerkstatt}=require('../kalibrierung.js');
const basis=()=>({traktor:{id:'a',name:'Traktor A'},geraet:'Tablet A',kontext:'alt',
  geometrie:{wheelbase_m:2.6,antenna_forward_m:1.2,antenna_right_m:0,antenna_height_m:3,max_steer_left_deg:35,max_steer_right_deg:35},
  sperren:[],schritte:[{fertig:false}],sicherung:null,protokoll:null,uebung:true,vermessen:false});
function ansicht(holen,api){
  const elemente=new Map();const el=id=>{if(!elemente.has(id))elemente.set(id,{value:'',checked:false,disabled:false,dataset:{},textContent:''});return elemente.get(id);};
  return {w:new LenkWerkstatt({el,holen,api}),el};
}
const antwort=data=>({ok:true,json:async()=>({data})});
test('Entwurf bleibt beim Traktorwechsel erhalten und wird nicht auf fremden Traktor geschrieben',async()=>{
  let stand=basis(),posts=0;
  const {w,el}=ansicht(async()=>antwort(stand),async()=>{posts++;});
  await w.laden();el('wk_wheelbase_m').value='3.2';el('werkstattForm').oninput();
  stand={...basis(),kontext:'neu',traktor:{id:'b',name:'Traktor B'}};
  await w.laden();await w.senden('geometrie');
  assert.equal(el('wk_wheelbase_m').value,'3.2');assert.equal(w.daten.traktor.id,'a');assert.equal(posts,0);
  assert.equal(el('werkstattSpeichern').disabled,true);
});
test('abgewiesenes Speichern erhält Eingaben und bestätigt keinen Erfolg',async()=>{
  const {w,el}=ansicht(async()=>antwort(basis()),async()=>null);
  await w.laden();el('wk_wheelbase_m').value='3.2';el('werkstattForm').oninput();el('werkstattVermessen').checked=true;
  await w.senden('geometrie');
  assert.equal(w.dirty,true);assert.equal(el('wk_wheelbase_m').value,'3.2');assert.match(el('werkstattInfo').textContent,/Nicht gespeichert/);
});
test('bestätigter POST wird direkt angezeigt, auch wenn ein zusätzlicher GET scheitern würde',async()=>{
  let gets=0,payload;
  const neu={...basis(),kontext:'neu',sicherung:{id:'s',zeit:1},geometrie:{...basis().geometrie,wheelbase_m:3.2}};
  const {w,el}=ansicht(async()=>{if(++gets>1)throw new Error('offline');return antwort(basis());},async(method,url,d)=>{payload=d;return {ok:true,data:neu};});
  await w.laden();el('wk_wheelbase_m').value='3.2';el('werkstattForm').oninput();el('werkstattVermessen').checked=true;
  await w.senden('geometrie');
  assert.equal(payload.kontext,'alt');assert.equal(payload.traktor_id,'a');assert.equal(payload.werte.wheelbase_m,3.2);
  assert.equal(w.daten.kontext,'neu');assert.equal(w.daten.sicherung.id,'s');assert.equal(gets,1);assert.equal(w.dirty,false);
});
test('eine späte ältere Ladeantwort überschreibt keinen neueren Stand',async()=>{
  const wartend=[];const {w}=ansicht(()=>new Promise(resolve=>wartend.push(resolve)),async()=>null);
  const a=w.laden(),b=w.laden();wartend[1](antwort({...basis(),kontext:'neu'}));await b;
  wartend[0](antwort(basis()));await a;assert.equal(w.daten.kontext,'neu');
});