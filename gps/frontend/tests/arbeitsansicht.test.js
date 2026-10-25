const test=require('node:test'),assert=require('node:assert/strict'),A=require('../arbeitsansicht.js');
test('Nullfläche ist bekannt, fehlende Messwerte bleiben unbekannt',()=>{assert.equal(A.zahl(0),'0,00');for(const v of [null,undefined,'',Infinity,-2])assert.equal(A.zahl(v),'–');});
test('Offener Auftrag wird nur bei aktiver Aufzeichnung als laufend gezeigt',()=>{assert.equal(A.arbeit({},false).status,'Offener Auftrag');assert.equal(A.arbeit({},true).status,'In Arbeit');assert.equal(A.arbeit({ended_at:100},true).status,'Abgeschlossen');});
test('Kurze abgeschlossene Arbeit bleibt abgeschlossen und hat null Minuten',()=>{const v=A.arbeit({started_at:100,ended_at:120},false);assert.equal(v.dauer,'0 min');assert.equal(v.status,'Abgeschlossen');});
test('Arbeitszeit hat Vorrang vor vergangener Zeit',()=>{assert.equal(A.sekunden({working_time_s:0,started_at:100,ended_at:300}),0);assert.equal(A.sekunden({started_at:100}),null);assert.equal(A.dauer(3660),'1 h 1 min');});
test('Mehrere Durchgänge werden als Fläche addiert ohne Prozentangabe',()=>{assert.deepEqual(A.summe([{area_ha:2,ended_at:1},{area_ha:2}]),{anzahl:2,offen:1,flaeche:4});});
test('Feldumriss passt auch bei negativen Koordinaten in die Vorschau',()=>{const p=A.umriss([[-100,0],[100,0],[100,100]]);assert.ok(p);for(const n of p.split(/[ ,]/).map(Number))assert.ok(n>=14&&n<=86);assert.equal(A.umriss([[1,1],[1,1],[1,1]]),null);assert.equal(A.umriss([[null,0],[1,1]]),null);});

test('Pausierte Arbeit bleibt in der Karte ausdrücklich als pausiert erkennbar',()=>{assert.equal(A.arbeit({pausiert:true},false).status,'Pausiert');assert.equal(A.arbeit({pausiert:true,ended_at:12},false).status,'Abgeschlossen');});
