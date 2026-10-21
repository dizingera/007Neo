const test=require('node:test');const assert=require('node:assert/strict');
const {Grenzentwurf,kuerzen}=require('../feldwerkzeuge.js');
test('Original bleibt beim Verschieben, Einfügen und Rücknehmen unverändert',()=>{
 const original=[[0,0],[0,100],[100,100],[100,0]],g=new Grenzentwurf(original);
 g.verschieben(2,3);g.einfuegen();assert.equal(g.punkte.length,5);assert.deepEqual(original[0],[0,0]);
 g.zurueck();assert.equal(g.punkte.length,4);g.zurueck();assert.deepEqual(g.punkte,original);
});
test('Entfernen lässt mindestens drei Punkte und Auswahlnummer gültig',()=>{
 const g=new Grenzentwurf([[0,0],[0,100],[100,100],[100,0]]);g.auswahl=3;g.entfernen();g.entfernen();
 assert.equal(g.punkte.length,3);assert.equal(g.auswahl,2);assert.equal(g.setzen([NaN,0]),false);
});
test('Kürzung zeigt genau die neue Referenz und sperrt zu kurze Abschnitte',()=>{
 assert.deepEqual(kuerzen([[0,0],[0,100]],10,20),[[0,10],[0,80]]);
 assert.deepEqual(kuerzen([[0,0],[0,100]],50,40),[]);
 assert.deepEqual(kuerzen([[0,0],[0,50],[50,50]],10,20),[[0,10],[0,50],[30,50]]);
});

test('Vorgangskennung funktioniert auch auf einem Tablet ohne sicheren Browserkontext',()=>{
 const {vorgangskennung}=require('../feldwerkzeuge.js');
 const id=vorgangskennung({getRandomValues:b=>{b.fill(171);return b;}});
 assert.match(id,/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
});
test('Ungültige sichtbare Koordinaten verhindern jeden Speicheraufruf',async()=>{
 const {Feldwerkzeuge}=require('../feldwerkzeuge.js');let meldung='',aufrufe=0;
 const u={busy:false,modus:'grenze',koordinatenUngueltig:false,el:()=>({valueAsNumber:NaN}),
   status:t=>meldung=t,api:()=>aufrufe++};
 await Feldwerkzeuge.prototype.speichern.call(u);
 assert.equal(aufrufe,0);assert.match(meldung,/gültige Koordinaten/);
});
