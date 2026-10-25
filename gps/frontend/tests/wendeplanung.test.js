const test=require('node:test');const assert=require('node:assert/strict');
const fs=require('node:fs');const vm=require('node:vm');const path=require('node:path');
const quelle=fs.readFileSync(path.join(__dirname,'../app.js'),'utf8');
test('Langsame Wendeplanung lässt Autotakte aus und bündelt neue manuelle Auswahl',async()=>{
 const block=quelle.slice(quelle.indexOf('let wendeInArbeit ='),quelle.indexOf('async function wendeBerechnen'));
 const wartend=[],aufrufe=[];let aktiv=0,maximum=0;
 const c=vm.createContext({el:()=>({hidden:false}),wendeErgebnis:()=>{},
   wendeBerechnen:async speichern=>{aufrufe.push(speichern);aktiv++;maximum=Math.max(maximum,aktiv);
     await new Promise(r=>wartend.push(r));aktiv--;}});
 vm.runInContext(block,c);
 const lauf=c.wendePlanen(false);
 await c.wendePlanen(false);await c.wendePlanen(false);
 await c.wendePlanen(true);await c.wendePlanen(true);
 assert.deepEqual(aufrufe,[false]);wartend.shift()();
 await new Promise(r=>setImmediate(r));
 assert.deepEqual(aufrufe,[false,true]);assert.equal(maximum,1);
 wartend.shift()();await lauf;
});
