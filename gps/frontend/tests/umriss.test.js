const {test}=require('node:test');const assert=require('node:assert/strict');const u=require('../umriss.js');
const p={traktor_breite_m:2,traktor_vorn_m:3,traktor_hinten_m:1,geraet_breite_m:6,geraet_vorn_m:1,geraet_hinten_m:1};
test('fehlende Maße werden nicht erfunden',()=>assert.equal(u.kontur({},[0,0],[0,0,0],[]).vollstaendig,false));
test('Frontschneidwerk bleibt vor der Bezugsachse',()=>{const r=u.kontur(p,[0,5],[0,0,0],[]);assert.ok(r.teile[1].every(v=>v[1]>=4));});
test('Gespannüberstand nutzt die echte Breite',()=>{const r=u.kontur(p,[0,0],[0,0,0],[[-2,-5],[2,-5],[2,5],[-2,5]]);assert.deepEqual(r.ausserhalb,[false,true]);});
test('Randpunkte gelten als enthalten',()=>assert.equal(u.imPolygon([0,1],[[0,0],[2,0],[2,2],[0,2]]),true));
test('Kurs 90 dreht Front nach Osten',()=>{const r=u.kontur(p,[0,5],[0,0,90],[[3,-4],[7,-4],[7,4],[3,4]]);assert.equal(r.ausserhalb[1],false);});
