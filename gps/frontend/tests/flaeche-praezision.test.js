const test = require('node:test');
const assert = require('node:assert/strict');
const { Flaeche, querStuecke } = require('../flaeche.js');
function leinwand() {
  const c = { rechtecke: [], setTransform() {}, beginPath() {}, moveTo() {}, lineTo() {}, closePath() {},
    fill() {}, stroke() {}, clearRect() {}, drawImage() {}, save() {}, restore() {}, translate() {}, scale() {},
    fillRect(...r) { this.rechtecke.push(r); } };
  return { getContext: () => c, c };
}
const lage = (y, stuecke = querStuecke([], 1), extra = {}) => ({pos:[0,y], kurs:0, aktiv:true, stuecke, ...extra});
function bild() { return { direkt:0, bilder:0, save(){},restore(){},translate(){},scale(){},beginPath(){},moveTo(){},lineTo(){},closePath(){},fill(){this.direkt++;},drawImage(){this.bilder++;} }; }
function flaecheninhalt(v) {
  return Math.abs(v.ecken.reduce((s,p,i,a)=>{const q=a[(i+1)%a.length];return s+p[0]*q[1]-q[0]*p[1];},0)/2);
}
test('Überlappung beginnt dort, wo der längere Streifen auf alte Fläche trifft',()=>{
  const f=new Flaeche(leinwand);
  f.zellenMalen([-1,0].flatMap(x=>[4,5,6,7].map(y=>[x,y])),.5);
  f.fahrt(lage(0),6000);
  const v=f.fahrt(lage(4),6100);
  const doppelt=v.filter(p=>p.doppelt);
  assert.ok(doppelt.length>0);
  assert.ok(doppelt.every(p=>p.ecken.every(e=>e[1]>=2)), 'nur der Abschnitt ab2m darf orange sein');
  assert.equal(doppelt.reduce((s,p)=>s+flaecheninhalt(p),0),2);
});
test('neu eingeschaltete Teilbreite malt die vorherige Strecke nicht nach',()=>{
  const f=new Flaeche(leinwand);
  f.fahrt(lage(0,[[-1,0]]),0);
  const v=f.fahrt(lage(1,[[-1,0],[0,1]]),100);
  assert.ok(v.length>0);
  assert.ok(v.every(p=>p.ecken.every(e=>e[0]<=0)));
  assert.ok(f.fahrt(lage(2,[[-1,0],[0,1]]),200).some(p=>p.mitte[0]>0));
});
test('Auftragswechsel verbindet zwei aktive Aufträge nicht mit einem Streifen',()=>{
  const f=new Flaeche(leinwand);
  f.fahrt(lage(0,undefined,{auftrag:'a'}),0);
  assert.deepEqual(f.fahrt(lage(1,undefined,{auftrag:'b'}),100),[]);
  assert.ok(f.fahrt(lage(2,undefined,{auftrag:'b'}),200).length>0);
});
test('zusammenhängende importierte Zellen bilden fugenlose Reihen',()=>{
  const f=new Flaeche(leinwand);
  f.zellenMalen([[0,0],[1,0],[2,0],[3,0],[0,1],[1,1]],.5);
  const r=[...f.bedeckt.kacheln.values()].flatMap(k=>k.c.rechtecke);
  assert.deepEqual(r,[[0,0,2,.5],[0,.5,1,.5]]);
});
test('Live-Endstück wird einmal mit der Kachel verrechnet und verändert den Speicher nicht',()=>{
  const f=new Flaeche(leinwand);
  f.fahrt(lage(0),0);f.fahrt(lage(1),100);
  const vorher=f.bedeckt.kacheln.size;
  const c=bild();f.zeichnen(c,[-10,-10,10,10],lage(2),200);
  assert.equal(c.direkt,0,'keine zusätzliche transparente Lage über der gespeicherten Fläche');
  assert.ok(c.bilder>0);assert.equal(f.bedeckt.kacheln.size,vorher);
  assert.equal(f.letzte.pos[1],1);
});
test('ausgeschaltete Markierung erzeugt kein Live-Endstück',()=>{
  const f=new Flaeche(leinwand);f.fahrt(lage(0),0);
  const c=bild();f.zeichnen(c,[-10,-10,10,10],lage(1,undefined,{aktiv:false}),200);
  assert.equal(c.direkt,0);assert.equal(c.bilder,0);
});test('im Stand eingeschaltete rechte Teilbreite wirkt ab dem Losfahren',()=>{
  const f=new Flaeche(leinwand);f.fahrt(lage(0,[[-1,0]]),0);
  f.fahrt(lage(0,[[-1,0],[0,1]]),100);
  assert.ok(f.fahrt(lage(1,[[-1,0],[0,1]]),200).some(p=>p.mitte[0]>0));
});
test('im Stand vollständig eingeschaltetes Gerät lässt den ersten Streifen nicht aus',()=>{
  const f=new Flaeche(leinwand);f.fahrt(lage(0,[]),0);
  f.fahrt(lage(0),100);
  const v=f.fahrt(lage(1),200);
  assert.equal(v.reduce((summe,p)=>summe+flaecheninhalt(p),0),1);
});
