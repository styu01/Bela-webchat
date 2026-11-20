import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';

const js=fs.readFileSync(new URL('./app.js',import.meta.url),'utf8');
const old=fs.readFileSync(new URL('./baseline_app.js',import.meta.url),'utf8');
assert.equal(crypto.createHash('sha256').update(old).digest('hex'),'ef3a6664621bf0714aab9ce560e13c7c217824dc5f6ed8eb37cd9429c0a4424e');
assert.ok(js.includes('trackedSubmission=requestId;pending=null;'));
assert.ok(js.includes('if(trackedSubmission){const job=(latest.jobs||[]).find(j=>j.request_id===trackedSubmission)'));
assert.ok(!js.includes("'Kérés sorban áll.'"));
assert.ok(js.includes('const actionableWaiting=Math.max(0,waiting-held)'));
const extract=(start,end)=>{
  const a=js.indexOf(start);assert.ok(a>=0,start);
  const b=js.indexOf(end,a+start.length);assert.ok(b>a,end);
  return js.slice(a,b);
};
const noticeSrc=extract('function submissionNotice(job){','async function refresh()');
const context=vm.createContext({});
vm.runInContext(noticeSrc,context);
for(const [state,exp] of [
  ['queued','feldolgozásra vár'],['handed_off','Béla dolgozik'],
  ['seen','Béla dolgozik'],['answered','Kész:'],
  ['unknown','nincs automatikus újraküldés'],['failed_unverified','nincs automatikus újraküldés'],
]){
  assert.ok(vm.runInContext(`submissionNotice({state:${JSON.stringify(state)}})`,context).includes(exp),state);
}
console.log('PASS: 6 valós kérésállapot helyes szöveget kap');
const renderSrc=extract('function render(force=false){','let refreshing=false;');
function renderWith(jobs,queue){
 const elements=new Map();
 const latest={jobs,entries:[],queue,automatic:true,sync_enabled:true,generated_at:Date.now()/1000,problem:'',sync_problem:''};
 const c=vm.createContext({latest,busy:false,pending:null,lastKey:JSON.stringify([[], '',true,'']),
   $:id=>{if(!elements.has(id))elements.set(id,{disabled:false,textContent:''});return elements.get(id);},
   latestPhotos:()=>[],latestVideos:()=>[],updateCapacityHint:()=>{},progressText:()=>'',Date,JSON,Number,Math});
 vm.runInContext(renderSrc+"\nrender();",c);
 return [elements.get('queueStatus').textContent,elements.get('send').disabled];
}
let j=[...Array(7)].map((_,i)=>({request_id:'held'+i,state:'queued'}));
let [txt,disabled]=renderWith(j,{waiting:7,held:7});
assert.ok(txt.includes('Készen áll')&&txt.includes('7 korábbi zárolt')&&!txt.includes('Munka: vár'),txt);
assert.equal(disabled,false);
console.log('PASS: 7 régi zárolt sor nem látszik aktív munkának');
[txt]=renderWith([...j,{state:'handed_off',request_id:'current'}],{waiting:7,held:7});
assert.ok(txt.includes('Béla dolgozik (1)'),txt);
console.log('PASS: aktív munka felismerhető');
[txt]=renderWith([...j,{state:'queued',request_id:'current'}],{waiting:8,held:7});
assert.ok(txt.includes('Várakozó új kérés: 1'),txt);
console.log('PASS: új várakozó elkülönül a régi zároltaktól');
// Polling must replace the submission acknowledgement with the verified state.
const refreshSrc=extract('let refreshing=false;',"$('loginForm').addEventListener");
const elements2=new Map();
const requestId='00000000-0000-4000-8000-000000000099';
let serverState='queued';
const c2=vm.createContext({
  trackedSubmission:requestId,pending:null,latest:null,logged:true,authEpoch:0,
  $:id=>{if(!elements2.has(id))elements2.set(id,{textContent:''});return elements2.get(id);},
  api:async()=>({jobs:[{request_id:requestId,state:serverState}],entries:[]}),
  render:()=>{},showChat:()=>{},
});
vm.runInContext('function submissionNotice(job){'+noticeSrc.split('function submissionNotice(job){')[1]+'\n'+refreshSrc,c2);
await vm.runInContext('refresh()',c2);
assert.ok(elements2.get('notice').textContent.includes('feldolgozásra vár'));
serverState='answered';
await vm.runInContext('refresh()',c2);
assert.ok(elements2.get('notice').textContent.includes('Kész: Béla válasza megérkezett.'));
serverState='unknown';
await vm.runInContext('refresh()',c2);
assert.ok(elements2.get('notice').textContent.includes('nincs automatikus újraküldés'));
console.log('PASS: polling queued → answered → unknown transitions');
console.log('ALL UI REGRESSION TESTS PASSED');
