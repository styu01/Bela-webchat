'use strict';
const $=id=>document.getElementById(id);let logged=false,latest=null,lastKey='',busy=false,pending=null,authEpoch=0,trackedSubmission=null;
// P5.8.4.3: long owner text travels as an authenticated private TXT attachment.
// This does not enlarge the terminal paste or weaken backend queue limits.
const LONG_INPUT_MAX=5000;
const LONG_FILE_NAME='bela-webchat-teljes-keres.txt';
const LONG_REQUEST_MARKER='A teljes keresem a csatolt bela-webchat-teljes-keres.txt fajlban van. Olvasd el es hajtsd vegre.';

// P5.9.8.2: browser-local appearance editor. No message queues, network calls,
// backend file writes, Telegram/Marveen or worker operations are performed here.
// IMPORTANT: theme CSS lives in the served style.css due to style-src 'self' CSP.
const themeNames = new Set(['light','dark','ocean','cream','peach','slate','midnight','terracotta','cinnabar','sunlight','patina']);
const themeKeyV3 = 'bela-webchat-theme-v3';
const themeKeyV2 = 'bela-webchat-theme-v2';
const themeKeyV1 = 'bela-webchat-theme-v1';
const themeCustomKeyV3 = 'bela-webchat-custom-palette-v3';
const themeFields = [
  {group:'Hátterek',key:'bg',label:'Oldal háttere'},
  {group:'Hátterek',key:'panel',label:'Alap panelszín'},
  {group:'Hátterek',key:'header-bg',label:'Fejléc háttér'},
  {group:'Hátterek',key:'sidebar-bg',label:'Oldalsáv háttér'},
  {group:'Hátterek',key:'input',label:'Beviteli mezők'},
  {group:'Hátterek',key:'code',label:'Kódblokkok háttere'},
  {group:'Hátterek',key:'table-head',label:'Táblázatfejléc'},
  {group:'Szövegek',key:'text',label:'Elsődleges szöveg'},
  {group:'Szövegek',key:'muted',label:'Másodlagos szöveg'},
  {group:'Szövegek',key:'primary-text',label:'Elsődleges gomb szövege'},
  {group:'Szövegek',key:'pill-text',label:'Állapotcímke szövege'},
  {group:'Szövegek',key:'selected-text',label:'Aktív menü szövege'},
  {group:'Szövegek',key:'logo-text',label:'Béla logó szövege'},
  {group:'Gombok és kiválasztás',key:'primary',label:'Elsődleges gomb'},
  {group:'Gombok és kiválasztás',key:'accent',label:'Kiemelések és hivatkozások'},
  {group:'Gombok és kiválasztás',key:'control',label:'Másodlagos gombok'},
  {group:'Gombok és kiválasztás',key:'selected',label:'Aktív menü háttere'},
  {group:'Beszélgetés',key:'bubble',label:'Béla üzenetbuboréka'},
  {group:'Beszélgetés',key:'user',label:'Saját üzenetbuborék'},
  {group:'Beszélgetés',key:'user-border',label:'Saját buborék szegélye'},
  {group:'Keretek',key:'border',label:'Általános szegély'},
  {group:'Keretek',key:'selected-border',label:'Aktív menü szegélye'},
  {group:'Keretek',key:'table-border',label:'Táblázat szegélye'},
  {group:'Állapotok és logó',key:'logo-bg',label:'Béla logó háttere'},
  {group:'Állapotok és logó',key:'notice',label:'Tájékoztató / figyelmeztetés'}
];
const themeKeys = themeFields.map(field => field.key);
let themeCommitted = null;
let themeDraft = null;
let themePreviouslyFocused = null;

function safeRead(key) {try {return localStorage.getItem(key);} catch {return null;}}
function safeWrite(key,value) {try {localStorage.setItem(key,value);} catch { /* nonpersistent private-mode browser */ }}
function validColor(value) {
  if (typeof value !== 'string') return null;
  const v=value.trim();
  if (/^#[0-9a-fA-F]{3}$/.test(v)) return '#'+[...v.slice(1)].map(c=>c+c).join('').toLowerCase();
  if (/^#[0-9a-fA-F]{6}$/.test(v)) return v.toLowerCase();
  return null;
}
function normalizePalette(palette) {
  if (!palette || typeof palette!=='object' || Array.isArray(palette)) return null;
  const out={};
  for (const key of themeKeys) {
    const color=validColor(palette[key]);
    if (!color) return null;
    out[key]=color;
  }
  return out;
}
function readSavedTheme() {
  const raw=safeRead(themeKeyV3);
  if (raw) {
    try {
      const item=JSON.parse(raw);
      if (item?.mode==='custom' && themeNames.has(item.base)) {
        const palette=normalizePalette(item.palette);
        if (palette) return {mode:'custom',base:item.base,palette};
      }
      if (item?.mode==='preset' && themeNames.has(item.base)) return {mode:'preset',base:item.base};
    }catch{}
  }
  // P5.9.8.1 used inaccurate handwritten colors. Preserve builtin choice,
  // but NEVER silently import those broken custom color definitions.
  const old=safeRead(themeKeyV2) || safeRead(themeKeyV1) || 'light';
  if (themeNames.has(old)) return {mode:'preset',base:old};
  const last=safeRead('bela-webchat-theme-last-builtin-v1');
  return {mode:'preset',base:themeNames.has(last)?last:'light'};
}
function readStoredCustom(){
  try {
    const entry=JSON.parse(safeRead(themeCustomKeyV3)||'null');
    if(entry && themeNames.has(entry.base)){
      const palette=normalizePalette(entry.palette);
      if(palette)return {mode:'custom',base:entry.base,palette};
    }
  }catch{}
  return null;
}
function storeCommittedTheme(){
  safeWrite(themeKeyV3,JSON.stringify(themeCommitted));
  if(themeCommitted.mode==='custom')safeWrite(themeCustomKeyV3,JSON.stringify({base:themeCommitted.base,palette:themeCommitted.palette}));
}
function clearThemeOverrides(){for(const key of themeKeys) document.documentElement.style.removeProperty('--'+key);}
function applyTheme(theme) {
  clearThemeOverrides();
  document.documentElement.dataset.theme=theme.base;
  document.documentElement.classList.toggle('theme-custom-active',theme.mode==='custom');
  if(theme.mode==='custom') for (const key of themeKeys) document.documentElement.style.setProperty('--'+key,theme.palette[key]);
  $('theme').value=theme.mode==='custom'?'custom':theme.base;
  updateThemeSummary();
}
function paletteFromCSS(base) {
  // Read REAL colors from the original theme stylesheet; zero hardcoded palettes.
  // Save current custom inline overrides to avoid resetting user's saved colors.
  const root=document.documentElement;
  const oldTheme=root.dataset.theme;
  const oldActive=root.classList.contains('theme-custom-active');
  const saved=themeKeys.map(key=>[key,root.style.getPropertyValue('--'+key)]);
  clearThemeOverrides();
  root.dataset.theme=base;
  root.classList.remove('theme-custom-active');
  const style=getComputedStyle(root);
  const colors={};
  for(const key of themeKeys) {
    let v=validColor(style.getPropertyValue('--'+key));
    if(key==='primary' && !v) v=validColor(style.getPropertyValue('--accent'));
    if((key==='header-bg'||key==='sidebar-bg') && !v) v=validColor(style.getPropertyValue('--panel'));
    if(!v) throw Error('Nem olvasható a sablon színe: '+key);
    colors[key]=v;
  }
  root.dataset.theme=oldTheme;
  root.classList.toggle('theme-custom-active',oldActive);
  for(const [key,value] of saved) if(value) root.style.setProperty('--'+key,value);
  return colors;
}
function previewDraft() {
  if(!themeDraft || !$('themeLivePreview').checked)return;
  const root=document.documentElement;
  clearThemeOverrides();
  root.dataset.theme=themeDraft.base;
  root.classList.add('theme-custom-active');
  for(const key of themeKeys)root.style.setProperty('--'+key,themeDraft.palette[key]);
}
function hexToLuminance(hex){const rgb=[1,3,5].map(i=>parseInt(hex.slice(i,i+2),16)/255).map(v=>v<=0.04045?v/12.92:Math.pow((v+0.055)/1.055,2.4));return rgb[0]*.2126+rgb[1]*.7152+rgb[2]*.0722;}
function contrastRatio(a,b){const x=hexToLuminance(a),y=hexToLuminance(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);}
function updateContrast(){
  const n=$('themeContrast');if(!n || !themeDraft)return;
  const c=themeDraft.palette;
  const pairs=[['Főszöveg / oldal',c.text,c.bg],['Főszöveg / kártya',c.text,c.bubble],['Elsődleges gomb',c['primary-text'],c.primary],['Állapotcímke',c['pill-text'],c.control],['Aktív menü',c['selected-text'],c.selected]];
  const fails=pairs.filter(([,a,b])=>contrastRatio(a,b)<4.5);
  n.classList.toggle('has-warning',fails.length>0);
  n.textContent=fails.length?'⚠ Gyenge kontraszt: '+fails.map(([label])=>label).join(', ')+'. Az olvashatóság miatt érdemes módosítani.':'✓ A fő szöveg–háttér színpárok legalább 4,5:1 kontrasztúak.';
}
function paintThemeFields(){
  const target=$('themeFields');target.replaceChildren();
  let group=null,grid=null;
  for(const field of themeFields){
    if(group!==field.group){
      group=field.group;
      const section=document.createElement('section');section.className='theme-editor-group';
      const title=document.createElement('h3');title.textContent=group;section.append(title);
      grid=document.createElement('div');grid.className='theme-editor-grid';section.append(grid);target.append(section);
    }
    const card=document.createElement('div');card.className='theme-editor-card';
    const label=document.createElement('label');label.textContent=field.label;label.htmlFor='themeHex-'+field.key;
    const row=document.createElement('div');row.className='theme-editor-color-row';
    const picker=document.createElement('input');picker.type='color';picker.value=themeDraft.palette[field.key];picker.setAttribute('aria-label',field.label+' színválasztó');
    const hex=document.createElement('input');hex.type='text';hex.id='themeHex-'+field.key;hex.maxLength=7;hex.spellcheck=false;hex.value=themeDraft.palette[field.key];hex.setAttribute('aria-label',field.label+' HEX');
    const reset=document.createElement('button');reset.type='button';reset.className='theme-editor-field-reset';reset.textContent='↺';reset.title=field.label+' – sablonérték visszaállítása';reset.setAttribute('aria-label',reset.title);
    const warning=document.createElement('small');warning.className='theme-editor-field-error';warning.hidden=true;warning.textContent='Hibás HEX-kód (#RRGGBB).';
    const update=(value)=>{
      const good=validColor(value);
      hex.classList.toggle('invalid',!good);warning.hidden=Boolean(good);
      if(!good){updateEditorSaveState();return;}
      themeDraft.palette[field.key]=good;hex.value=good;picker.value=good;
      previewDraft();updateContrast();updateEditorSaveState();
    };
    picker.addEventListener('input',()=>update(picker.value));
    hex.addEventListener('change',()=>update(hex.value));
    hex.addEventListener('input',()=>{const good=validColor(hex.value);hex.classList.toggle('invalid',!good);warning.hidden=Boolean(good);updateEditorSaveState();});
    reset.addEventListener('click',()=>update(themeDraft.origin[field.key]));
    row.append(picker,hex,reset);card.append(label,row,warning);grid.append(card);
  }
}
function updateEditorSaveState(){
  const invalid=Array.from($('themeFields').querySelectorAll('input[type=text]')).some(el=>!validColor(el.value));
  $('themeSave').disabled=invalid;
  $('themeSave').title=invalid?'Javítsd ki a hibás HEX-értékeket':'';
}
function updateThemeSummary(){
  if(!$('themeSummary'))return;
  const name=themeCommitted.mode==='custom'?'Egyedi · '+($('theme').querySelector('option[value="'+themeCommitted.base+'"]')?.textContent||themeCommitted.base):$('theme').querySelector('option[value="'+themeCommitted.base+'"]')?.textContent||themeCommitted.base;
  $('themeSummary').textContent='11 sablon · '+themeFields.length+' szín · '+name;
}
function showThemeEditor(){
  if(themeDraft)return;
  themePreviouslyFocused=document.activeElement;
  const base=themeCommitted.base;
  const origin=paletteFromCSS(base);
  themeDraft={base,origin,palette:themeCommitted.mode==='custom'?{...themeCommitted.palette}:{...origin}};
  paintThemeFields();
  $('themeLivePreview').checked=true;
  $('themeEditor').hidden=false;$('themeBackdrop').hidden=false;
  document.body.classList.add('theme-editor-open');
  updateContrast();updateEditorSaveState();
  $('themeCancel').focus();
}
function closeThemeEditor(){
  $('themeEditor').hidden=true;$('themeBackdrop').hidden=true;
  document.body.classList.remove('theme-editor-open');
  themeDraft=null;
  applyTheme(themeCommitted);
  if(themePreviouslyFocused?.isConnected)themePreviouslyFocused.focus();
}
function updateThemeFieldFromCards(){
  const next={...themeDraft.palette};
  for(const field of themeFields){
    const value=validColor($('themeHex-'+field.key).value);
    if(!value)return null;
    next[field.key]=value;
  }
  return next;
}
function saveThemeEditor(){
  const colors=updateThemeFieldFromCards();
  if(!colors)return;
  themeCommitted={mode:'custom',base:themeDraft.base,palette:colors};
  storeCommittedTheme();
  $('themeEditor').hidden=true;$('themeBackdrop').hidden=true;
  document.body.classList.remove('theme-editor-open');
  themeDraft=null;
  applyTheme(themeCommitted);
  if(themePreviouslyFocused?.isConnected)themePreviouslyFocused.focus();
}
function resetThemeDraft(){
  if(!themeDraft)return;
  themeDraft.palette={...themeDraft.origin};paintThemeFields();
  previewDraft();updateContrast();updateEditorSaveState();
}
function selectTheme(value){
  if(value==='custom'){
    if(themeCommitted.mode==='custom'){applyTheme(themeCommitted);return;}
    const saved=readStoredCustom();
    if(saved){themeCommitted=saved;storeCommittedTheme();applyTheme(themeCommitted);return;}
    $('theme').value=themeCommitted.base;showThemeEditor();return;
  }
  const name=themeNames.has(value)?value:'light';
  if(themeDraft)closeThemeEditor();
  themeCommitted={mode:'preset',base:name};storeCommittedTheme();applyTheme(themeCommitted);
}
themeCommitted=readSavedTheme();
applyTheme(themeCommitted);
$('theme').addEventListener('change',e=>selectTheme(e.target.value));
$('themeCustomizeToggle').addEventListener('click',showThemeEditor);
$('themeCancel').addEventListener('click',closeThemeEditor);
$('themeEditorClose').addEventListener('click',closeThemeEditor);
$('themeBackdrop').addEventListener('click',closeThemeEditor);
$('themeSave').addEventListener('click',saveThemeEditor);
$('themeReset').addEventListener('click',resetThemeDraft);
$('themeLivePreview').addEventListener('change',()=>{if(themeDraft){if($('themeLivePreview').checked)previewDraft();else applyTheme(themeCommitted);}});
$('themeEditor').addEventListener('keydown',e=>{
  if(e.key==='Escape'){e.preventDefault();closeThemeEditor();return;}
  if(e.key!=='Tab')return;
  const focusable=[...$('themeEditor').querySelectorAll('button:not([disabled]),input:not([disabled])')].filter(x=>x.offsetParent!==null);
  if(!focusable.length)return;
  const first=focusable[0],last=focusable[focusable.length-1];
  if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}
  else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}
});

const labels={queued:'Feldolgozásra vár',handed_off:'Átadás folyamatban',seen:'Béla feldolgozza',answered:'Válaszolt',unknown:'Bizonytalan – megállítva',failed_unverified:'Sikertelen – nincs újrapróbálás',legacy_75_failed_unverified:'Archiválva – nem indul újra',cancelled_too_long:'Túl hosszú, nem lett elküldve',cancelled_by_user:'Visszavonva – nem lett elküldve',closed:'Tulajdonos lezárta – nincs újraküldés'};
const workLabels={waiting:'vár',running:'folyamatban',completed:'kész',completed_no_text:'kész, szöveges válasz nincs',uncertain:'sikertelen / nem indul újra'};
const telegramLabels={waiting:'vár',delivered:'kézbesítve',partial:'részleges',blocked_by_gate:'kapu tiltja',uncertain:'bizonytalan'};
function progressText(item){
 const state=item.work_state||({queued:'waiting',handed_off:'running',seen:'running',answered:'completed',unknown:'uncertain',failed_unverified:'uncertain',legacy_75_failed_unverified:'uncertain'})[item.state];
 if(!state)return '';
 if(item.webchat_isolated){
   const sync=item.sync||item.sync_out||item.sync_in;
   const names={new:'előkészítés alatt',ready:'küldésre vár',done:'kézbesítve',invalid:'elutasítva, ellenőrzés szükséges',uncertain:'nem igazolt – nincs automatikus újraküldés'};
   const status=sync?(names[sync.state]||sync.state):'korábbi üzenet, nincs visszamenőleges küldés';
   const parts=sync?.parts?(' ('+sync.confirmed+'/'+sync.parts+' rész)'):'';
   return 'Munka: '+(workLabels[state]||state)+' · Telegram: '+status+parts;
 }
 const tg=item.telegram||{},parts=Array.isArray(tg.parts)?tg.parts:[];
 const reasons=new Set([...(Array.isArray(tg.anomalies)?tg.anomalies:[]),...parts.flatMap(p=>p?.tool_call_id===null&&!p?.content_ref?[]:Array.isArray(p?.reasons)?p.reasons:[])].filter(x=>typeof x==='string'));
 const mismatches=parts.filter(p=>Array.isArray(p?.reasons)&&p.reasons.includes('echo_mismatch'));
 const echoMismatch=reasons.has('echo_mismatch');
 const accentOnly=echoMismatch&&mismatches.length>0&&mismatches.every(p=>p.echo_difference==='accent_only');
 const infoCodes=new Set(['echo_mismatch','plain_fallback','document_fallback']);
 const anomaly=[...reasons].some(x=>!infoCodes.has(x));
 let projection=telegramLabels[tg.state]||'nincs igazolás';
 // Accent-only is informational ONLY with both owner delivery receipts and no other problem.
 const received=role=>parts.some(p=>p?.role===role&&Array.isArray(p.message_ids)&&p.message_ids.length===1&&p.message_ids.every(id=>typeof id==='string'&&/^[1-9][0-9]*$/.test(id)));
 const onlyAccentParts=parts.every(p=>['echo','answer'].includes(p?.role)&&(p.state==='blocked_by_gate'||p.state==='delivered'||(p.role==='echo'&&p.echo_difference==='accent_only'&&Array.isArray(p.reasons)&&p.reasons.every(x=>x==='echo_mismatch'))));
 if(accentOnly&&!anomaly&&received('echo')&&received('answer')&&onlyAccentParts&&tg.state==='uncertain')projection='kézbesítve';
 if(anomaly)projection+=' · küldési anomália';
 if(reasons.has('plain_fallback'))projection+=' · sima szöveg';
 if(echoMismatch)projection+=accentOnly?' · ékezet eltér':' · eltérő visszhang';
 if(tg.state==='partial'&&parts.some(p=>p?.state==='blocked_by_gate'))projection+=' · kapu tiltja';
 const photos=parts.filter(p=>p?.role==='photo');
 if(photos.length){const ok=photos.filter(p=>p.state==='delivered'&&Array.isArray(p.message_ids)&&p.message_ids.length===1).length;
   projection+=' · képek '+ok+'/'+photos.length;
   if(photos.some(p=>p.media_transport==='document'))projection+=' · nagy kép dokumentumként';
 }else if(Array.isArray(item.uploads)&&item.uploads.some(u=>u?.image===true))projection+=' · képek nem tükrözve';
 return 'Munka: '+(workLabels[state]||'bizonytalan')+' · Telegram: '+projection;
}
async function api(path,body){const r=await fetch(path,{method:body===undefined?'GET':'POST',credentials:'same-origin',signal:AbortSignal.timeout(15000),headers:body===undefined?{}:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});if(!r.ok){let detail;try{detail=await r.json();}catch{}const e=new Error(detail?.code==='transport_too_long'?'Túl hosszú, nem lett elküldve. Rövidítsd a kérést.':detail?.code==='dispatch_paused'?'Feldolgozás szünetel; a kérés nem került sorba.':'HTTP '+r.status);e.status=r.status;e.code=detail?.code;throw e;}return r.json();}
function showLogin(){authEpoch++;latest=null;lastKey='';$('messages').replaceChildren();if($('gallery').open)$('gallery').close();logged=false;$('login').hidden=false;$('workspace').hidden=true;$('logout').hidden=true;$('connection').textContent='Nincs kapcsolat';}
function showChat(){logged=true;$('login').hidden=true;$('workspace').hidden=false;$('logout').hidden=false;$('connection').textContent='Helyi kapcsolat';}
function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
// Safe text-node renderer. Telegram's ledger stores MarkdownV2 wire text;
// web answers retain their canonical Markdown. Never interpret raw HTML.
function inline(node,s,tg=false){
 const re=tg?/\\[\\_\*\[\]\(\)~`>#+\-=|{}.!]|`[^`\n]+`|\*\*[^*\n]+\*\*|\*(?:\\.|[^*\\\n])+\*|__(?:\\.|[^_\\\n])+__|_(?:\\.|[^_\\\n])+_|~(?:\\.|[^~\\\n])+~|\[(?:\\.|[^\]\\\n])+\]\((?:\\.|[^)\\\n])+\)/g:/\\[\\`*_{}\[\]()#+\-.!|>~]|`[^`\n]+`|\*\*[^*\n]+\*\*|__[^_\n]+__|\*[^*\n]+\*|_[^_\n]+_|~~[^~\n]+~~|\[[^\]\n]+\]\([^)\n]+\)/g;
 let i=0;const unescape=x=>x.replace(/\\([\\_\*\[\]\(\)~`>#+\-=|{}.!])/g,'$1');
 for(const m of s.matchAll(re)){
  node.append(document.createTextNode(s.slice(i,m.index)));const v=m[0];
  if(v.startsWith('\\'))node.append(document.createTextNode(v.slice(1)));
  else if(v.startsWith('[')){const z=/^\[(.*)\]\((.*)\)$/.exec(v);if(z){let u;try{u=new URL(unescape(z[2]));}catch{}if(u&&['http:','https:'].includes(u.protocol)){const a=el('a',unescape(z[1]));a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';node.append(a);}else node.append(document.createTextNode(unescape(z[1])));}else node.append(document.createTextNode(v));}
  else {let tag='em',n=1;if(v.startsWith('`'))tag='code';else if(v.startsWith('**')||(!tg&&v.startsWith('__'))){tag='strong';n=2;}else if(tg&&v.startsWith('*'))tag='strong';else if(tg&&v.startsWith('__')){tag='u';n=2;}else if(v.startsWith('~')){tag='s';n=tg?1:2;}node.append(el(tag,unescape(v.slice(n,-n))));}
  i=m.index+v.length;
 }
 node.append(document.createTextNode(s.slice(i)));
}
function markdown(text,tg=false){
 const frag=document.createDocumentFragment();let src=String(text).replace(/\r\n?/g,'\n');
 // Legacy Telegram messages sometimes have bullets flattened onto one line.
 if(tg&&!src.includes('```'))src=src.replace(/[ \t]+•[ \t]+/g,'\n• ');
 const lines=src.split('\n');let i=0;
 const cells=s=>s.trim().replace(/^\|/,'').replace(/\|$/,'').split('|').map(x=>x.trim());
 const bullet=/^\s*(?:[-*•]|\\-)\s+/;const ordered=/^\s*\d+(?:\\?\.|\))\s+/;
 while(i<lines.length){let l=lines[i];if(!l.trim()){i++;continue;}
  if(l.startsWith('```')){let a=[];i++;while(i<lines.length&&!lines[i].startsWith('```'))a.push(lines[i++]);if(i<lines.length)i++;const pre=el('pre');pre.append(el('code',a.join('\n')));frag.append(pre);continue;}
  const h=/^(#{1,6})\s+(.*)$/.exec(l);if(h){const n=el('h'+Math.min(h[1].length,3));inline(n,h[2],tg);frag.append(n);i++;continue;}
  if(l.includes('|')&&i+1<lines.length&&/^\s*\|?\s*:?-{3,}/.test(lines[i+1])&&cells(lines[i+1]).every(x=>/^:?-{3,}:?$/.test(x))){const t=el('table'),head=el('thead'),tr=el('tr');for(const c of cells(l)){const n=el('th');inline(n,c,tg);tr.append(n);}head.append(tr);t.append(head);i+=2;const b=el('tbody');while(i<lines.length&&lines[i].includes('|')&&lines[i].trim()){const row=el('tr');for(const c of cells(lines[i++])){const n=el('td');inline(n,c,tg);row.append(n);}b.append(row);}t.append(b);const wrap=el('div',undefined,'table-wrap');wrap.append(t);frag.append(wrap);continue;}
  if(bullet.test(l)||ordered.test(l)){const numbered=ordered.test(l),rx=numbered?ordered:bullet,ul=el(numbered?'ol':'ul');if(numbered){const start=parseInt(l,10);if(Number.isFinite(start))ul.start=start;}while(i<lines.length&&rx.test(lines[i])){const li=el('li');inline(li,lines[i++].replace(rx,''),tg);ul.append(li);}frag.append(ul);continue;}
  const q=/^\s*(?:>|\\>)\s+(.*)$/.exec(l);if(q){const n=el('blockquote');inline(n,q[1],tg);frag.append(n);i++;continue;}
  const para=el('p');inline(para,l,tg);frag.append(para);i++;
 }return frag;
}
function bubble(role,text,meta,md=false,tg=false){const b=el('div',undefined,'bubble '+(role==='user'?'user':role==='assistant'?'assistant':''));b.append(el('div',meta,'meta'));if(md||tg){const content=el('div',undefined,'answer-text');content.append(markdown(text,tg));b.append(content);if(md){const c=el('button','Markdown másolása','copy');c.type='button';c.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(text);c.textContent='Másolva';}catch{c.textContent='Nem másolható';}});b.append(c);}}else b.append(el('div',text,'plain-text'));return b;}
const timeFormat=new Intl.DateTimeFormat('hu-HU',{timeZone:'Europe/Budapest',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
const validUpload=e=>e?.upload?.available===true&&/^[a-f0-9]{32}\.(txt|md|csv|json|pdf|png|jpg|jpeg|webp)$/.test(e.upload.name);
const validImage=e=>e&&((validUpload(e)&&e.upload.image===true&&/\.(png|jpg|jpeg|webp)$/.test(e.upload.name))||(typeof e.image==='string'&&/^[a-f0-9]{64}\.(jpg|png|webp|gif)$/.test(e.image)));
const imageURL=e=>validUpload(e)?'/api/preview/uploads/'+e.upload.name:'/api/preview/media/'+e.image;
const validVideo=e=>e&&typeof e.video==='string'&&/^[a-f0-9]{64}\.(mp4|webm)$/.test(e.video);
let galleryItems=[],galleryPos=0,galleryMode='photo';
const galleryDialog=$('gallery');
function latestPhotos(){return (latest?.gallery||[]).filter(validImage).slice(0,20);}
function latestVideos(){return (latest?.video_gallery||[]).filter(validVideo).slice(0,20);}
function paintGallery(){
 const e=galleryItems[galleryPos];if(!e)return;
 const video=validVideo(e),name=video?e.video:validUpload(e)?e.upload.name:e.image,url=video?'/api/preview/media/'+name:imageURL(e);
 const v=$('galleryVideo'),img=$('galleryImage');v.pause();v.removeAttribute('src');v.load();img.removeAttribute('src');
 v.hidden=!video;img.hidden=video;if(video)v.src=url;else{img.src=url;img.alt='Csatolt kép '+(galleryPos+1);}
 $('galleryTitle').textContent=video?'Videógaléria':'Képgaléria';$('galleryLatest').textContent=video?'Legutóbbi 20 videó':'Legutóbbi 20 kép';
 $('galleryCount').textContent=(galleryPos+1)+' / '+galleryItems.length;
 $('galleryCaption').textContent=e.text||'';
 $('galleryDownload').href=url+'?download=1';$('galleryDownload').download='bela-'+name;
 $('galleryPrev').disabled=galleryPos===0;$('galleryNext').disabled=galleryPos===galleryItems.length-1;
}
function openGallery(e=null,mode='photo'){
 galleryMode=validVideo(e)?'video':mode;galleryItems=galleryMode==='video'?latestVideos():latestPhotos();galleryPos=e?galleryItems.findIndex(x=>x.key===e.key):0;
 if(e&&galleryPos<0){galleryItems=[e];galleryPos=0;}
 if(!galleryItems.length)return;paintGallery();if(!galleryDialog.open)galleryDialog.showModal();
}
function moveGallery(n){galleryPos=Math.max(0,Math.min(galleryItems.length-1,galleryPos+n));paintGallery();}
$('galleryOpen').addEventListener('click',()=>openGallery());$('galleryLatest').addEventListener('click',()=>openGallery(null,galleryMode));$('videoOpen').addEventListener('click',()=>openGallery(null,'video'));
$('galleryClose').addEventListener('click',()=>galleryDialog.close());
$('galleryPrev').addEventListener('click',()=>moveGallery(-1));$('galleryNext').addEventListener('click',()=>moveGallery(1));
galleryDialog.addEventListener('keydown',e=>{if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();moveGallery(e.key==='ArrowLeft'?-1:1);}});
galleryDialog.addEventListener('click',e=>{if(e.target===galleryDialog)galleryDialog.close();});
galleryDialog.addEventListener('close',()=>{$('galleryImage').removeAttribute('src');const v=$('galleryVideo');v.pause();v.removeAttribute('src');v.load();galleryItems=[];});
function render(force=false){
 if(!latest)return;updateCapacityHint();const jobs=latest.jobs||[],entries=latest.entries||[]; $('galleryOpen').disabled=!latestPhotos().length;$('videoOpen').disabled=!latestVideos().length;
 const quarantined=Math.max(0,Number(latest.sync_quarantined)||0);
 const retryAt=Number(latest.sync_retry_at)||0;
 const recovering=latest.sync_enabled&&latest.sync_recovery_state==='recovering';
 const mirrorLabel=!latest.sync_enabled?'⛔ Telegram-tükrözés letiltva – beavatkozás szükséges.':
  recovering?'⚠️ Telegram-kapcsolati hiba – automatikus helyreállítás. '+(retryAt>Date.now()/1000?'Következő ellenőrzés: '+timeFormat.format(new Date(retryAt*1000))+'. ':''):
  quarantined?'⚠️ Telegram-tükrözés működik, de '+quarantined+' esemény kézbesítése nem igazolt / érvénytelen. Kézi ellenőrzés szükséges.':
  'Telegram-tükrözés aktív – igazolt kézbesítések jelölve.';
 $('modeHint').textContent=(latest.automatic?'Feldolgozás engedélyezve · a Küldés sorba állítja a kérést. ':'Feldolgozás szünetel · új kérés nem küldhető. ')+mirrorLabel;
 // Distinguish historical manually held jobs from actionable queued work.
 // Never alter the backend queue limit: held rows still count towards capacity.
 const waiting=Math.max(jobs.filter(j=>j.state==='queued').length,Number.isInteger(latest.queue?.waiting)?latest.queue.waiting:0);
 const held=Math.min(waiting,Number.isInteger(latest.queue?.held)?Math.max(0,latest.queue.held):0);
 const actionableWaiting=Math.max(0,waiting-held);
 const running=jobs.filter(j=>j.state==='handed_off'||j.state==='seen');
 const stale=Number.isFinite(latest.generated_at)&&Date.now()/1000-latest.generated_at>20;
 $('send').disabled=busy||!!pending||waiting>=50||stale||!latest.automatic;
 const heldLabel=held?' · '+held+' korábbi zárolt feladat':'';
 $('queueStatus').textContent=stale?'A Webchat-nézet nem frissült – beküldés szünetel':
  waiting>=50?'A várakozási sor megtelt (50/50)'+heldLabel:
  !latest.automatic?'Feldolgozás szünetel'+heldLabel:
  latest.problem?latest.problem+heldLabel:
  running.length?'Béla dolgozik ('+running.length+') · várakozik: '+actionableWaiting+heldLabel:
  actionableWaiting?'Várakozó új kérés: '+actionableWaiting+heldLabel:
  'Készen áll'+heldLabel;
 const current=running.at(-1);
 if(current&&progressText(current))$('queueStatus').textContent+=' · '+progressText(current);
 const key=JSON.stringify([entries,latest.problem,latest.sync_enabled,latest.sync_problem]);if(!force&&key===lastKey)return;lastKey=key;
 const m=$('messages');if(!force&&Array.from(m.querySelectorAll('video')).some(v=>!v.paused&&!v.ended)){lastKey='';return;}const atBottom=m.scrollHeight-m.scrollTop-m.clientHeight<90,oldTop=m.scrollTop;
 m.replaceChildren();
 for(const e of entries){
  const source=e.source==='web+telegram'?'Web + Telegram':e.source==='telegram'?'Telegram-napló':'Web';
  const time=Number.isFinite(e.at)?timeFormat.format(new Date(e.at*1000)):'';
  let meta=(e.role==='user'?'Te':e.role==='status'?'Állapot':'Béla')+' · '+source+(time?' · '+time:'');
  if(e.role==='user'&&e.state)meta+=' · '+(labels[e.state]||e.state);
  if(e.role==='user'&&e.state==='queued'){
   const job=jobs.find(j=>e.key==='w:'+j.request_id+':in');
   if(job&&Number.isInteger(job.queue_position))meta+=' · '+job.queue_position+'. a sorban';
  }
  if(progressText(e))meta+=' · '+progressText(e);
  if(e.answer_source==='telegram')meta+=' · Telegram-válasz (eredeti webes Markdown nincs)';
  if(e.markdown&&e.source!=='web+telegram'&&e.delivery?.confirmed)meta+=e.delivery.same_markdown?' · Telegram-küldés igazolt':' · Telegram-formátum külön';
  const reasonNames={quota:'Claude-keret elérhetetlen',auth:'Claude-hitelesítési hiba',timeout:'Időtúllépés',empty_result:'Üres Claude-válasz',cli_failure:'Claude-hívási hiba',invalid_result:'Érvénytelen Claude-kimenet',invalid_request:'Érvénytelen kérés',internal:'Belső feldolgozási hiba',unverified:'Ellenőrizetlen hiba'};
  const text=e.role==='status'?(e.state==='failed_unverified'?(labels[e.state]+' · '+(reasonNames[e.failure_code]||'Kézi ellenőrzés szükséges')):(labels[e.state]||e.state)):(e.text|| (e.attachment?'[Csatolmány: '+e.attachment+']':'[Üres naplóbejegyzés]'));
  const b=bubble(e.answer_source==='no_text'?'status':e.role,text,meta,e.markdown===true,e.telegram_format===true);
  if(e.role==='user'&&e.source.startsWith('web')&&e.state==='queued'&&/^w:[a-f0-9-]{36}:in$/.test(e.key)){
   const id=e.key.slice(2,-3),btn=el('button','Várakozó kérés törlése','copy');btn.type='button';
   btn.addEventListener('click',async()=>{
    if(!confirm('Biztosan visszavonod ezt a még el nem indított kérést?'))return;
    btn.disabled=true;
    try{await api('/api/preview/queue/cancel',{request_id:id});$('notice').textContent='Várakozó kérés visszavonva.';await refresh();}
    catch(err){$('notice').textContent='Törlés nem sikerült: '+err.message;btn.disabled=false;}
   });
   b.append(btn);
  }
  if(e.uploads){
   const panel=el('div',undefined,'sent-attachments');for(const u of e.uploads){const item={...e,key:e.key+':'+u.name,upload:u};if(validImage(item)){const a=el('button',undefined,'photo-link web-upload-image');a.type='button';a.title=u.display_name||'Kép nagyítása';a.addEventListener('click',()=>openGallery(item));const img=el('img');img.src=imageURL(item);img.alt=u.display_name||'Csatolt kép';img.loading='lazy';a.append(img);panel.append(a);}else if(validUpload(item)){const dl=el('a',u.display_name||'Csatolt fájl','upload-file');dl.href='/api/preview/uploads/'+u.name+'?download=1';dl.download=u.display_name||'bela-'+u.name;panel.append(dl);}else panel.append(el('span',(u.display_name||'Fájl')+' – nem elérhető','photo-missing'));}if(!e.uploads.length)panel.append(el('div','A csatolmányjegyzék nem érhető el.','photo-missing'));b.insertBefore(panel,b.children[1]);
  }else if(validImage(e)){

   const a=el('button',undefined,'photo-link'+(validUpload(e)?' web-upload-image':''));a.type='button';a.setAttribute('aria-label','Kép nagyítása');a.addEventListener('click',()=>openGallery(e));
   const img=el('img');img.src=imageURL(e);img.alt=validUpload(e)?'Feltöltött kép':'Telegramon küldött kép';img.loading='lazy';img.decoding='async';a.append(img);b.insertBefore(a,b.children[1]);
   img.addEventListener('error',()=>{a.replaceWith(el('div','A kép jelenleg nem elérhető.','photo-missing'));});
  }else if(validUpload(e)){const dl=el('a','Csatolt '+e.upload.name.split('.').pop().toUpperCase()+' fájl · Letöltés','upload-file');dl.href='/api/preview/uploads/'+e.upload.name+'?download=1';dl.download='bela-'+e.upload.name;b.insertBefore(dl,b.children[1]);
  }else if(e.upload){b.insertBefore(el('div','A csatolt fájl nem elérhető.','photo-missing'),b.children[1]);
  }else if(validVideo(e)){
   const panel=el('div',undefined,'video-panel'),v=el('video');v.controls=true;v.playsInline=true;v.preload='metadata';v.src='/api/preview/media/'+e.video;panel.append(v);
   const btn=el('button','Videó nagyítása','copy');btn.type='button';btn.addEventListener('click',()=>{v.pause();openGallery(e,'video');});panel.append(btn);
   const dl=el('a','Letöltés','copy');dl.href=v.src+'?download=1';dl.download='bela-'+e.video;panel.append(dl);b.insertBefore(panel,b.children[1]);
   v.addEventListener('error',()=>panel.append(el('div','A videó nem játszható le ebben a böngészőben. Próbáld letölteni.','photo-missing')));
  }else if(e.video_state)b.insertBefore(el('div',e.video_state==='loading'?'Videó előkészítése…':'A videó helyi másolata nem érhető el (hiányzó fájl vagy méretkorlát).','photo-missing'),b.children[1]);
  else if(e.attachment_file&&/^[a-f0-9]{64}\.[a-z0-9]{1,12}$/.test(e.attachment_file)&&e.attachment_state==='ready'){
   const label=String(e.attachment_name||'Elküldött dokumentum').slice(0,180);
   const kb=Number.isFinite(e.attachment_bytes)?' ('+Math.ceil(e.attachment_bytes/1024)+' KB)':'';
   const dl=el('a','📎 '+label+kb+' · Letöltés','upload-file');
   dl.href='/api/preview/media/'+e.attachment_file+'?download=1';dl.download=label;
   b.insertBefore(dl,b.children[1]);
  }else if(e.attachment_state)b.insertBefore(el('div',e.attachment_state==='loading'?'Fájl biztonságos átvétele…':'A fájl másolata nem érhető el (méretkorlát, hiányzó forrás vagy biztonsági ellenőrzés).','photo-missing'),b.children[1]);
  else if(e.attachment==='photo')b.insertBefore(el('div','A kép helyi fájlja nem elérhető.','photo-missing'),b.children[1]);
  m.append(b);
 }
 if(!m.children.length)m.append(el('div','Még nincs megjeleníthető üzenet.','empty'));
 m.scrollTop=atBottom||force?m.scrollHeight:oldTop;
}
let refreshing=false;
function submissionNotice(job){
 // Only the exact UUID of this browser's latest successful submission controls this label.
 // Do not guess from Telegram entries, order, or another owner's job.
 switch(job?.state){
  case 'queued':return 'Kérés rögzítve, feldolgozásra vár.';
  case 'handed_off':case 'seen':return 'Béla dolgozik a kérésen.';
  case 'answered':return 'Kész: Béla válasza megérkezett.';
  case 'failed_unverified':case 'legacy_75_failed_unverified':case 'unknown':return 'Bizonytalan vagy sikertelen feldolgozás – nincs automatikus újraküldés.';
  case 'cancelled_by_user':return 'A várakozó kérés visszavonva.';
  default:return null;
 }
}
async function refresh(){if(refreshing)return;refreshing=true;const epoch=authEpoch;try{const data=await api('/api/preview/timeline');if(epoch!==authEpoch)return;latest=data;if(!logged)showChat();$('connection').textContent='Helyi kapcsolat';if(pending&&[...(latest.jobs||[]),...(latest.entries||[])].some(j=>j.request_id===pending.id||j.key==='w:'+pending.id+':in')){if(!trackedSubmission)trackedSubmission=pending.id;pending=null;}
 if(trackedSubmission){const job=(latest.jobs||[]).find(j=>j.request_id===trackedSubmission);const info=submissionNotice(job);if(info)$('notice').textContent=info;}
 render();}catch(e){if(epoch!==authEpoch)return;if(e.status===401)showLogin();else $('connection').textContent='Kapcsolati hiba';}finally{refreshing=false;}}
$('loginForm').addEventListener('submit',async e=>{e.preventDefault();try{await api('/api/auth/login',{username:'preview-owner',password:$('password').value});$('password').value='';$('loginError').textContent='';showChat();await refresh();}catch{$('loginError').textContent='Sikertelen belépés.';}});
$('logout').addEventListener('click',async()=>{authEpoch++;logged=false;trackedSubmission=null;clearAttachment();try{await api('/api/auth/logout',{});}finally{latest=null;lastKey='';$('messages').replaceChildren();showLogin();}});
let attachments=[];
const allowedExt=new Set(['txt','md','csv','json','pdf','png','jpg','jpeg','webp']);
function paintAttachments(){const list=$('attachmentList');list.replaceChildren();$('attachName').textContent=attachments.length?attachments.length+' / 20 fájl':'';$('removeAttach').hidden=!attachments.length;for(const a of attachments){const card=el('div',undefined,'attach-card');if(a.preview){const img=el('img');img.src=a.preview;img.alt=a.file.name;card.append(img);}card.append(el('span',a.file.name+' ('+Math.ceil(a.file.size/1024)+' KB)'));const x=el('button','×');x.type='button';x.setAttribute('aria-label',a.file.name+' eltávolítása');x.addEventListener('click',()=>{if(busy||pending)return;if(a.preview)URL.revokeObjectURL(a.preview);attachments=attachments.filter(z=>z!==a);paintAttachments();});card.append(x);list.append(card);}if(typeof updateCapacityHint==='function')updateCapacityHint();}
function clearAttachment(){for(const a of attachments)if(a.preview)URL.revokeObjectURL(a.preview);attachments=[];$('attachment').value='';paintAttachments();}
$('attach').addEventListener('click',()=>{if(!busy&&!pending)$('attachment').click();});
$('removeAttach').addEventListener('click',()=>{if(!busy&&!pending)clearAttachment();});
function addAttachments(files){if(busy||pending)return;const selected=files.map(file=>({file,ext:file.name.split('.').pop().toLowerCase()}));if(attachments.length+selected.length>20){$('notice').textContent='Legfeljebb 20 fájlt csatolhatsz.';return;}if(selected.some(a=>!allowedExt.has(a.ext)||a.file.size<1||a.file.size>30*1024*1024)){$('notice').textContent='Nem támogatott fájl vagy méret (max. 30 MB/fájl).';return;}for(const a of selected){if(['png','jpg','jpeg','webp'].includes(a.ext))a.preview=URL.createObjectURL(a.file);attachments.push(a);}paintAttachments();$('notice').textContent='';}
$('attachment').addEventListener('change',()=>{const files=Array.from($('attachment').files);$('attachment').value='';addAttachments(files);});
// Native clipboard paste: no clipboard permission prompt; only user paste events.
// Do not intercept normal text paste or clipboard events outside the composer.
$('text').addEventListener('paste',e=>{
 const data=e.clipboardData;if(!data)return;
 const types={'image/png':'png','image/jpeg':'jpg','image/webp':'webp'};
 let images=Array.from(data.items||[]).filter(x=>x.kind==='file'&&types[x.type]).map(x=>x.getAsFile()).filter(Boolean);
 if(!images.length)images=Array.from(data.files||[]).filter(x=>types[x.type]);
 if(!images.length)return;
 e.preventDefault();if(busy||pending){$('notice').textContent='A jelenlegi kérés még folyamatban van.';return;}
 const stamp=Date.now();const files=images.map((f,i)=>new File([f],'kepernyokep-'+stamp+'-'+(i+1)+'.'+types[f.type],{type:f.type}));
 addAttachments(files);
});

async function uploadFile(a){if(a.path)return a.path;const r=await fetch('/api/preview/upload',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/octet-stream','X-Bela-Extension':a.ext},body:a.file});if(!r.ok)throw Error('Feltöltési hiba: HTTP '+r.status);const x=await r.json();if(typeof x.path!=='string'||!/^\/[^\r\n]+\/uploads\/[a-f0-9]{32}\.(txt|md|csv|json|pdf|png|jpg|jpeg|webp)$/.test(x.path))throw Error('Érvénytelen fájlazonosító');a.path=x.path;return x.path;}
// Match Python json.dumps(..., ensure_ascii=True) string transport, including
// surrogate pairs. Capacity metadata comes from the worker's real prompt builder.
function transportSize(text,withFiles,capacity,useLong=false){
 if(!capacity||!Number.isFinite(capacity.limit))return null;
 const overhead=useLong?capacity.long_batch_overhead:withFiles?capacity.batch_overhead:capacity.plain_overhead;
 if(!Number.isFinite(overhead))return null;
 const ascii=JSON.stringify(text).replace(/[\u007f-\uffff]/g,c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0'));
 return {used:overhead+ascii.length-2,limit:capacity.limit};
}
function needsLongFile(text,hasFiles,capacity){
 const direct=transportSize(text,hasFiles,capacity);
 return text.length>(hasFiles?580:800)||!!(direct&&direct.used>direct.limit);
}
function currentTransportSize(){
 const text=$('text').value.trim()||(attachments.length?'Olvasd el a csatolt fájlokat, és foglald össze.':'');
 const long=needsLongFile(text,attachments.length>0,latest?.transport_capacity);
 return transportSize(long?LONG_REQUEST_MARKER:text,attachments.length>0||long,latest?.transport_capacity,long);
}
function updateCapacityHint(){
 const hint=$('capacityHint');if(!hint)return;
 const text=$('text').value.trim(),c=currentTransportSize();
 const long=needsLongFile(text,attachments.length>0,latest?.transport_capacity);
 const count=text.length+' / '+LONG_INPUT_MAX+' karakter';
 const info=long?' · A teljes kérés automatikusan privát TXT-csatolmányként kerül átadásra.':' · Közvetlen terminál-átadás.';
 const capacityInfo=c?' · Terminál: '+c.used+' / '+c.limit+' átadási karakter.':' · Terminálkapacitás nem ismert.';
 hint.textContent=count+info+capacityInfo+(c&&c.used>c.limit?' A rövid átvivő üzenet sem fér el, nem küldhető.':'');
}
$('text').addEventListener('input',updateCapacityHint);
$('text').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing&&e.keyCode!==229){e.preventDefault();if(!$('send').disabled)$('composer').requestSubmit();}});
// Only real interaction renews idle time. Background polling does not.
let lastActivity=0,activityTimer;function pingActivity(){if(!logged)return;lastActivity=Date.now();const epoch=authEpoch;api('/api/preview/activity',{}).catch(e=>{if(e.status===401&&epoch===authEpoch)showLogin();});}function activity(e){if(!logged||!e.isTrusted)return;clearTimeout(activityTimer);if(Date.now()-lastActivity>=15000)pingActivity();else{const epoch=authEpoch;activityTimer=setTimeout(()=>{if(epoch===authEpoch)pingActivity();},15000-(Date.now()-lastActivity));}}
for(const type of ['pointerdown','keydown','wheel','touchstart'])document.addEventListener(type,activity,{passive:true});
$('composer').addEventListener('submit',async e=>{
 e.preventDefault();if(busy||pending||!latest||!latest.automatic||(latest.queue?.waiting??0)>=50)return;
 let text=$('text').value.trim(),files=attachments.slice();
 if(!text&&!files.length)return;if(!text)text='Olvasd el a csatolt fájlokat, és foglald össze.';
 if(text.length>LONG_INPUT_MAX||/[\u0000-\u0009\u000b-\u001f\u007f-\u009f\u200b-\u200f\u2028-\u202e\u2060-\u206f]/u.test(text)){$('notice').textContent='A kérés legfeljebb 5000 karakter lehet, vezérlőkarakterek nélkül.';return;}
 const long=needsLongFile(text,files.length>0,latest?.transport_capacity);
 if(long){
   if(files.some(a=>a.file.name===LONG_FILE_NAME)){$('notice').textContent='A fenntartott fájlnév már csatolva van; nevezd át a saját fájlt.';return;}
   if(files.length>=20){$('notice').textContent='A hosszú kérés egy privát TXT-fájlnak számít. Távolíts el egy csatolmányt (max. 20).';return;}
   files.push({file:new File([text],LONG_FILE_NAME,{type:'text/plain'}),ext:'txt'});
   text=LONG_REQUEST_MARKER;
 }
 const transport=transportSize(text,files.length>0,latest?.transport_capacity,long);
 if(transport&&transport.used>transport.limit){$('notice').textContent='A rövid átvivő üzenet sem fér el a terminálon; a kérés nem lett elküldve.';return;}
 busy=true;trackedSubmission=null;$('notice').textContent='';render();
 try{
  if(files.length){const items=[];for(let i=0;i<files.length;i++){$('notice').textContent='Fájl feltöltése: '+(i+1)+' / '+files.length;const path=await uploadFile(files[i]);items.push({id:path.split('/').pop(),name:files[i].file.name});}const batch=await api('/api/preview/batch',{files:items});if(typeof batch.path!=='string'||!/^\/[^\r\n]+\/uploads\/[a-f0-9]{32}\.json$/.test(batch.path))throw Error('Érvénytelen jegyzék');text+='\nCsatolt fajlok jegyzeke: '+batch.path;if(text.length>800)throw Error('A teljes kérés túl hosszú.');}
  const requestId=crypto.randomUUID();pending={id:requestId,text};
  const receipt=await api('/api/chat/f0/requests',{request_id:requestId,text});
  if(receipt?.request_id!==requestId||!['queued','handed_off','seen','answered','unknown','failed_unverified','legacy_75_failed_unverified'].includes(receipt?.state))throw Error('Beküldési nyugta nem igazolható');
  trackedSubmission=requestId;pending=null;$('text').value='';clearAttachment();$('notice').textContent='Kérés rögzítve, állapotfrissítés folyamatban.';await refresh();
 }catch(e){
  trackedSubmission=null;
  if(e.code==='dispatch_paused'){pending=null;$('notice').textContent=e.message;}
  else if(!pending){$('notice').textContent=e.message;}
  else if(e.status&&e.status<500&&e.status!==404){
   pending=null;$('notice').textContent='Kérés elutasítva: '+e.message;
  }else{
   // Read-only verification with the SAME UUID. Do not repeat POST ingestion.
   try{
    const status=await api('/api/preview/queue/status',{request_id:pending.id});
    if(status.request_id===pending.id&&['queued','handed_off','seen','answered','unknown','failed_unverified','legacy_75_failed_unverified'].includes(status.state)){
     pending=null;$('text').value='';clearAttachment();
     $('notice').textContent='A kérés a szerveren rögzítve ('+status.state+'). Nincs újraküldés.';
    }else $('notice').textContent='A beküldés nem igazolható. Ne küldd újra.';
   }catch{$('notice').textContent='A beküldés eredménye bizonytalan. Ne küldd újra; ellenőrizd a terminált.';}
  }
 }finally{busy=false;render();}
});
refresh();setInterval(()=>{if(logged)refresh();},1800);
