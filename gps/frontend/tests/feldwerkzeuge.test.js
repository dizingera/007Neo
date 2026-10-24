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
test('Gerade ziehen nimmt die Punkte der Rundung weg, nicht den Rest des Feldes',()=>{
 // Rechteck 100 x 60, die Südostecke als Rundung aus drei Punkten.
 const e=new Grenzentwurf([[0,0],[90,0],[97,3],[100,10],[100,60],[0,60]]);
 assert.equal(e.geradeZiehen(1,4),2);
 assert.deepEqual(e.punkte,[[0,0],[90,0],[100,60],[0,60]]);
 assert.equal(e.auswahl,1);
 // Andersherum angetippt: dieselbe (kürzere) Strecke.
 const f=new Grenzentwurf([[0,0],[90,0],[97,3],[100,10],[100,60],[0,60]]);
 assert.equal(f.geradeZiehen(4,1),2);
 assert.equal(f.punkte.length,4);
 assert.equal(f.geradeZiehen(0,1),0);                      // Nachbarn: nichts dazwischen
 f.zurueck(); assert.equal(f.punkte.length,6);             // rücknehmbar
});
test('Ecke abrunden legt einen Bogen an, der beide Kanten berührt',()=>{
 const e=new Grenzentwurf([[0,0],[100,0],[100,60],[0,60]]);
 const r=e.abrunden(1,10);
 assert.ok(Math.abs(r-10)<1e-9);
 const bogen=e.punkte.slice(1,e.punkte.length-2);
 assert.deepEqual(bogen[0].map(v=>Math.round(v*1e6)/1e6),[90,0]);        // Anfang auf der Südkante
 assert.deepEqual(bogen.at(-1).map(v=>Math.round(v*1e6)/1e6),[100,10]);  // Ende auf der Ostkante
 for(const p of bogen) assert.ok(Math.abs(Math.hypot(p[0]-90,p[1]-10)-10)<1e-6);   // alle auf dem Kreis um (90,10)
 // Zu großer Radius: so groß, wie die Kanten zulassen (45 % der kürzeren).
 const g=new Grenzentwurf([[0,0],[100,0],[100,60],[0,60]]);
 assert.ok(Math.abs(g.abrunden(1,500)-27)<1e-9);
 // Keine Ecke (gerade durch): nichts.
 const h=new Grenzentwurf([[0,0],[50,0],[100,0],[100,60],[0,60]]);
 assert.equal(h.abrunden(1,10),0);
});
