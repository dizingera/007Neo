/* Gemessene Gespannkontur in Metern; die dekorative Maschine kann größer gezeichnet sein. */
(function(root){
'use strict';
function imPolygon(p,ring){let innen=false;for(let i=0,j=ring.length-1;i<ring.length;j=i++){
 const a=ring[i],b=ring[j],dx=b[0]-a[0],dy=b[1]-a[1];
 if(Math.abs((p[0]-a[0])*dy-(p[1]-a[1])*dx)<1e-7 && p[0]>=Math.min(a[0],b[0])-1e-7 && p[0]<=Math.max(a[0],b[0])+1e-7 && p[1]>=Math.min(a[1],b[1])-1e-7 && p[1]<=Math.max(a[1],b[1])+1e-7)return true;
 if((a[1]>p[1])!==(b[1]>p[1]) && p[0]<(b[0]-a[0])*(p[1]-a[1])/(b[1]-a[1])+a[0])innen=!innen;
}return innen;}
function rechteck(x,y,b,v,h,winkel=0){const k=winkel*Math.PI/180,c=Math.cos(k),s=Math.sin(k);
 return [[-b/2,-h],[b/2,-h],[b/2,v],[-b/2,v]].map(([rx,ry])=>[x+c*rx+s*ry,y-s*rx+c*ry]);}
function kontur(p,geraet,pose,ring){
 const keys=['traktor_breite_m','traktor_vorn_m','traktor_hinten_m','geraet_breite_m','geraet_vorn_m','geraet_hinten_m'];
 if(keys.some(k=>!Number.isFinite(p[k])||p[k]<=0))return {vollstaendig:false,teile:[]};
 const hinten=rechteck(0,0,p.traktor_breite_m,p.traktor_vorn_m,p.traktor_hinten_m);
 const werkzeug=rechteck(geraet[0],geraet[1],p.geraet_breite_m,p.geraet_vorn_m,p.geraet_hinten_m,p.trailed && geraet[2]!=null?geraet[2]-pose[2]:0);
 const k=pose[2]*Math.PI/180,c=Math.cos(k),s=Math.sin(k);
 const welt=teil=>teil.map(([x,y])=>[pose[0]+c*x+s*y,pose[1]-s*x+c*y]);
 const teile=[hinten,werkzeug];
 // Stützpunkte markieren Überstand; keine vollständige Kollisions-/Hüllenprüfung.
 return {vollstaendig:true,teile,ausserhalb:Array.isArray(ring)&&ring.length>=3?teile.map(t=>welt(t).some(p=>!imPolygon(p,ring))):null};
}
function zeichnen(ctx,p,g,scale,ring,pose){const v=kontur(p,g,pose,ring);if(!v.vollstaendig)return;
 ctx.save();ctx.lineWidth=2/scale;ctx.setLineDash([4/scale,3/scale]);
 v.teile.forEach((teil,i)=>{ctx.strokeStyle=v.ausserhalb&&v.ausserhalb[i]?'#ff744f':'#9ce5eb';ctx.beginPath();teil.forEach((p,j)=>j?ctx.lineTo(...p):ctx.moveTo(...p));ctx.closePath();ctx.stroke();});ctx.restore();}
const api={imPolygon,rechteck,kontur,zeichnen};if(typeof module==='object'&&module.exports)module.exports=api;else root.Umriss=api;
})(typeof window!=='undefined'?window:globalThis);
