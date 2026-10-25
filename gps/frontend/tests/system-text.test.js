const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
test('tatsächlicher Systemrenderer zeigt fremde Gerätenamen und Rollen nur als Text',()=>{
  const quell=fs.readFileSync(require.resolve('../app.js'),'utf8');
  const start=quell.indexOf('function renderSystem() {');
  const ende=quell.indexOf('/* Einstellungen:',start);
  function element(){return {children:[],hidden:false,append(...kinder){this.children.push(...kinder);},appendChild(kind){this.children.push(kind);},set innerHTML(wert){assert.equal(wert,'','Fremde Daten dürfen kein HTML erzeugen');this.children=[];}};}
  const elemente={};const el=id=>elemente[id]||(elemente[id]=element());
  const name='<img src=x onerror="window.marker=1">';
  const rolle='<svg onload="window.marker=2">';
  const kontext={el,document:{createElement:element},renderBackup(){},renderDatenRolle(){},renderAbstimmung(){},state:{live:{system:{role:'master',version:'Prüfung',gnss:{source:'simulator'},imu:{source:'aus'},steering_output:{typ:'none'},corrections:{},relay:{},sync:{},devices:[{name,role:rolle,last_seen:Date.now()/1000}]}}}};
  vm.runInNewContext(quell.slice(start,ende)+';renderSystem();',kontext);
  const row=elemente.systemList.children.at(-1);const main=row.children[0];
  assert.equal(main.children[0].textContent,name);assert.ok(main.children[1].textContent.startsWith(rolle));
  assert.equal(main.children[0].children.length,0);
});
