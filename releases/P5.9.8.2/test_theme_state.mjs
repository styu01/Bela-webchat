import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const js=fs.readFileSync(new URL('./app.js',import.meta.url),'utf8');
const html=fs.readFileSync(new URL('./index.html',import.meta.url),'utf8');
const css=fs.readFileSync(new URL('./style.css',import.meta.url),'utf8');
const begin=js.indexOf('// P5.9.8.2: browser-local appearance editor.');
const end=js.indexOf('const labels={queued:',begin);
assert.ok(begin>=0 && end>begin);
const code=js.slice(begin,end);
const themeNames=['light','dark','ocean','cream','peach','slate','midnight','terracotta','cinnabar','sunlight','patina'];
const optionNames={light:'Világos',dark:'Sötét',ocean:'Óceánkék',cream:'Meleg krém',peach:'Pasztell narancs',slate:'Palaszürke',midnight:'Éjkék',terracotta:'Fenyő és agyag',cinnabar:'Cinóber szalon',sunlight:'Aranyóra',patina:'Réz és páva',custom:'Egyedi'};
function varsFrom(s){const result={};for(const m of s.matchAll(/--([a-z-]+)\s*:\s*([^;\n}]+)/g))result[m[1]]=m[2].trim();return result;}
const root=varsFrom(css.match(/:root\s*\{([^}]*)\}/)?.[1]||'');
const themes={};for(const name of themeNames){const match=css.match(new RegExp(':root\\[data-theme="'+name+'"\\]\\s*\\{([^}]*)\\}'));themes[name]={...root,...varsFrom(match?.[1]||'')};}
class Style {
 constructor(){this.map=new Map();}
 setProperty(k,v){this.map.set(k,String(v));}
 getPropertyValue(k){return this.map.get(k)||'';}
 removeProperty(k){this.map.delete(k);}
}
class ClassList {
 constructor(){this.items=new Set()}
 add(v){this.items.add(v)}
 remove(v){this.items.delete(v)}
 contains(v){return this.items.has(v)}
 toggle(v,on){if(on===undefined)on=!this.contains(v);if(on)this.add(v);else this.remove(v);return on;}
}
function fixture(stored={}){
 const map=new Map();const elements=[];let active=null;
 class E {
  constructor(tag='div',id){this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.classList=new ClassList();this.listeners={};this.attributes={};this.style=new Style();this.hidden=false;this.disabled=false;this.value='';this.textContent='';this.offsetParent={};this.isConnected=true;if(id)this.id=id;elements.push(this);}
  set id(v){this._id=v;map.set(v,this)}
  get id(){return this._id}
  append(...e){this.children.push(...e)}
  replaceChildren(...e){this.children=[...e]}
  addEventListener(name,fn){this.listeners[name]||=[];this.listeners[name].push(fn)}
  setAttribute(k,v){this.attributes[k]=v}
  querySelectorAll(sel){const out=[];function visit(node){for(const ch of node.children){if(sel==='input[type=text]' && ch.tagName==='INPUT'&&ch.type==='text')out.push(ch);if(sel.startsWith('button:not')&&(ch.tagName==='INPUT'||ch.tagName==='BUTTON')&&!ch.disabled)out.push(ch);visit(ch);}}visit(this);return out;}
  querySelector(sel){const m=sel.match(/^option\[value="([a-z]+)"\]$/);return m?{textContent:optionNames[m[1]]}:null}
  focus(){active=this}
  dispatch(name){for(const fn of this.listeners[name]||[])fn({target:this,key:name})}
 }
 const rootElement=new E('html');rootElement.dataset.theme='light';
 const body=new E('body');
 const ids=['theme','themeSummary','themeFields','themeContrast','themeCustomizeToggle','themeCancel','themeEditorClose','themeBackdrop','themeSave','themeReset','themeLivePreview','themeEditor'];
 for(const id of ids)new E('div',id);
 map.get('theme').value='light';
 map.get('themeLivePreview').checked=true;
 map.get('themeEditor').hidden=true;map.get('themeBackdrop').hidden=true;
 const document={documentElement:rootElement,body,getElementById:id=>map.get(id),createElement:tag=>new E(tag),get activeElement(){return active}};
 const local=new Map(Object.entries(stored));
 const localStorage={getItem:k=>local.get(k)??null,setItem:(k,v)=>local.set(k,v)};
 const ctx=vm.createContext({document,localStorage,$:id=>map.get(id),getComputedStyle:element=>({getPropertyValue:(key)=>{
  const override=element.style.getPropertyValue(key);if(override)return override;
  const t=element.dataset.theme||'light';return themes[t]?.[key.slice(2)]||'';
 }}),console});
 vm.runInContext(code,ctx);
 return {ctx,map,root:rootElement,local,document};
}
const q=x=>JSON.stringify(x);
let f=fixture({'bela-webchat-theme-v2':'patina'});
assert.equal(f.root.dataset.theme,'patina');
assert.equal(f.map.get('theme').value,'patina');
assert.equal(f.root.style.map.size,0,'existing preset cannot gain inline palette overrides');
for(const name of themeNames){
 vm.runInContext(`selectTheme(${q(name)})`,f.ctx);
 assert.equal(f.root.dataset.theme,name);assert.equal(f.root.style.map.size,0,`Preset ${name} changed by inline overrides`);
}
console.log('PASS: all 11 original CSS themes survive without any token override');
for (const name of themeNames){
  const palette=vm.runInContext('paletteFromCSS('+q(name)+')',f.ctx);
  assert.equal(Object.keys(palette).length,25,'missing controls in '+name);
  assert.ok(Object.values(palette).every(v=>/^#[0-9a-f]{6}$/.test(v)),'bad CSS color in '+name);
}
console.log('PASS: all 11 preset palettes expose all 25 valid editor colors');
vm.runInContext("selectTheme('patina');showThemeEditor()",f.ctx);
assert.equal(f.map.get('themeEditor').hidden,false);
assert.equal(f.map.get('themeFields').children.length,6,'six logical color groups');
assert.equal(f.root.style.map.size,0,'opening editor may not mutate colors');
assert.equal(vm.runInContext('themeKeys.length',f.ctx),25);
console.log('PASS: editor has 25 independent fields in 6 groups');
const original=vm.runInContext('themeDraft.palette.primary',f.ctx);
f.map.get('themeHex-primary').value='#112233';
f.map.get('themeHex-primary').dispatch('change');
assert.equal(f.root.style.getPropertyValue('--primary'),'#112233');
assert.equal(f.map.get('themeSave').disabled,false);
vm.runInContext('closeThemeEditor()',f.ctx);
assert.equal(f.root.style.map.size,0,'Cancel must undo preview');
assert.equal(f.local.get('bela-webchat-theme-v3') && JSON.parse(f.local.get('bela-webchat-theme-v3')).mode,'preset');
console.log('PASS: live preview and Cancel restore the exact original preset');
vm.runInContext('showThemeEditor()',f.ctx);
f.map.get('themeHex-primary').value='hacker;url()';
f.map.get('themeHex-primary').dispatch('input');
assert.equal(f.map.get('themeSave').disabled,true);
vm.runInContext('saveThemeEditor()',f.ctx);
assert.equal(f.map.get('themeEditor').hidden,false,'invalid form may not save');
f.map.get('themeHex-primary').value='#445566';
f.map.get('themeHex-primary').dispatch('change');
assert.equal(f.map.get('themeSave').disabled,false);
vm.runInContext('saveThemeEditor()',f.ctx);
assert.equal(f.map.get('theme').value,'custom');
assert.equal(f.root.style.getPropertyValue('--primary'),'#445566');
assert.equal(JSON.parse(f.local.get('bela-webchat-theme-v3')).palette.primary,'#445566');
console.log('PASS: invalid HEX refused; Save persists valid custom colors');
const restored=fixture(Object.fromEntries(f.local));
assert.equal(restored.map.get('theme').value,'custom');
assert.equal(restored.root.style.getPropertyValue('--primary'),'#445566');
vm.runInContext("selectTheme('dark')",restored.ctx);
assert.equal(restored.root.style.map.size,0);
vm.runInContext("selectTheme('custom')",restored.ctx);
// Switching back to Custom must restore the SAVED custom palette, not a new draft.
assert.equal(restored.map.get('themeEditor').hidden,true,'saved custom palette can be restored directly');
assert.equal(restored.root.style.getPropertyValue('--primary'),'#445566');
vm.runInContext('showThemeEditor()',f.ctx);
vm.runInContext('resetThemeDraft()',f.ctx);
assert.equal(vm.runInContext('themeDraft.palette.primary',f.ctx),original);
assert.equal(f.map.get('themeEditor').hidden,false);
vm.runInContext('closeThemeEditor()',f.ctx);
assert.equal(f.root.style.getPropertyValue('--primary'),'#445566');
console.log('PASS: persisted custom colors, preset switching, and non-destructive reset');
assert.equal(vm.runInContext("validColor('#abc')",f.ctx),'#aabbcc');
assert.equal(vm.runInContext("validColor('red')",f.ctx),null);
assert.ok(vm.runInContext("contrastRatio('#000000','#ffffff')",f.ctx)>20);
assert.ok(vm.runInContext("contrastRatio('#ffffff','#ffffff')",f.ctx)<2);
console.log('PASS: strict HEX input and WCAG contrast calculation');
// Edit every color in one session; saved palette must match all 25 fields.
vm.runInContext('showThemeEditor()',f.ctx);
const keys=vm.runInContext('themeKeys',f.ctx);
for (let i=0;i<keys.length;i++){
  const h='#'+((i+5)*135791 % 0xffffff).toString(16).padStart(6,'0');
  const field=f.map.get('themeHex-'+keys[i]);
  field.value=h;
  field.dispatch('change');
}
vm.runInContext('saveThemeEditor()',f.ctx);
assert.equal(Object.keys(JSON.parse(f.local.get('bela-webchat-custom-palette-v3')).palette).length,25);
for (let i=0;i<keys.length;i++)assert.equal(f.root.style.getPropertyValue('--'+keys[i]),JSON.parse(f.local.get('bela-webchat-custom-palette-v3')).palette[keys[i]]);
console.log('PASS: all 25 colors can be edited and persisted in one session');
// Repeated open/cancel must not accumulate active styles or persist partial drafts.
for(let i=0;i<150;i++){
 vm.runInContext('showThemeEditor();closeThemeEditor()',f.ctx);
 assert.equal(f.root.style.getPropertyValue('--primary'),JSON.parse(f.local.get('bela-webchat-custom-palette-v3')).palette.primary);
}
console.log('PASS: 150 repeated editor open/close operations preserve saved settings');
for(const id of ['themeEditor','themeBackdrop','themeSave','themeCancel','themeLivePreview','themeReset'])assert.ok(html.includes('id="'+id+'"'));
assert.ok(css.includes('grid-template-columns:repeat(2,minmax(0,1fr))'));
assert.ok(css.includes('position:fixed;right:0;top:0;bottom:0'));
assert.ok(!code.includes('document.createElement(\'style\')')&&!code.includes('injectThemeCSS'));
assert.ok(!code.includes('fetch(')&&!code.includes('systemctl')&&!code.includes('tmux'));
console.log('PASS: external CSP-compatible CSS, fixed drawer and no backend operations');
