const test=require('node:test');
const assert=require('node:assert/strict');
const {Kartenfarben}=require('../kartenfarben.js');
function umgebung(speicher=new Map(),fehler=false) {
  const elemente={};const stil=new Map();let aenderungen=0;
  for(const id of ['kartenfarbenThema','kartenfarbe_grenze','kartenfarbe_fahrgasse','kartenfarbenSpeichern','kartenfarbenStandard','kartenfarbenInfo','kartenfarbenPalette','tagNacht'])elemente[id]={value:'',textContent:''};
  const blau={dataset:{art:'grenze',farbe:'#4b9dff'}};
  elemente.kartenfarbenPalette.querySelectorAll=()=>[blau];
  const k=new Kartenfarben({el:id=>elemente[id],speicher:{getItem:key=>speicher.get(key),setItem:(key,wert)=>{if(fehler)throw new Error('gesperrt');speicher.set(key,wert);}},stil:{setProperty:(key,wert)=>stil.set(key,wert),removeProperty:key=>stil.delete(key)},geaendert:()=>aenderungen++});
  elemente.tagNacht.click=()=>k.themaSetzen(k.thema==='tag'?'nacht':'tag');
  return {k,elemente,stil,speicher,blau,aenderungen:()=>aenderungen};
}
test('Farbmuster wird erst nach Übernehmen angewandt und über Neuladen erhalten',()=>{
  const u=umgebung();u.blau.onclick();assert.equal(u.stil.size,0);
  u.elemente.kartenfarbenSpeichern.onclick();assert.equal(u.stil.get('--f-grenze'),'#4b9dff');
  assert.equal(umgebung(u.speicher).stil.get('--f-grenze'),'#4b9dff');assert.equal(u.aenderungen(),1);
});
test('Tag und Nacht behalten getrennte Farben, Rücksetzen erhält die andere Ansicht',()=>{
  const u=umgebung();u.blau.onclick();u.k.speichern();u.elemente.kartenfarbenThema.value='tag';u.elemente.kartenfarbenThema.onchange();
  assert.equal(u.k.thema,'tag');assert.equal(u.stil.size,0);
  u.elemente.kartenfarbe_fahrgasse.value='#c68bff';u.k.speichern();
  u.k.zuruecksetzen();assert.equal(u.stil.size,0);u.k.themaSetzen('nacht');
  assert.equal(u.stil.get('--f-grenze'),'#4b9dff');
});
test('offene Farbauswahl bleibt bei Ansichtswechsel erhalten',()=>{
  const u=umgebung();u.blau.onclick();u.k.themaSetzen('tag');u.k.themaSetzen('nacht');
  assert.equal(u.elemente.kartenfarbe_grenze.value,'#4b9dff');assert.match(u.elemente.kartenfarbenInfo.textContent,/bitte übernehmen/);assert.equal(u.stil.size,0);
});
test('defekte oder fremde gespeicherte Farbwerte werden nicht als CSS angewandt',()=>{
  const key='agripilot.kartenfarben.v1';
  const u=umgebung(new Map([[key,JSON.stringify({nacht:{grenze:'url(javascript:0)',fahrgasse:'#123456'},tag:null})]]));
  assert.equal(u.stil.has('--f-grenze'),false);assert.equal(u.stil.get('--f-fahrgasse'),'#123456');
  const kaputt=umgebung(new Map([[key,'{']]));assert.equal(kaputt.stil.size,0);
});
test('Schreibfehler bleibt ehrlich und erlaubt Farben nur für die Sitzung',()=>{
  const u=umgebung(new Map(),true);u.blau.onclick();u.k.speichern();
  assert.equal(u.stil.get('--f-grenze'),'#4b9dff');assert.match(u.elemente.kartenfarbenInfo.textContent,/nicht speichern/);assert.equal(u.speicher.size,0);
  u.k.zuruecksetzen();assert.equal(u.stil.size,0);assert.match(u.elemente.kartenfarbenInfo.textContent,/nicht speichern/);
});
test('ungültige Eingaben überschreiben keine gespeicherte Farbe',()=>{
  const u=umgebung();u.blau.onclick();u.k.speichern();u.elemente.kartenfarbe_grenze.value='red';u.k.speichern();
  assert.equal(u.stil.get('--f-grenze'),'#4b9dff');assert.match(u.elemente.kartenfarbenInfo.textContent,/gültige/);
});
