from pathlib import Path
from playwright.sync_api import sync_playwright
import re, json
folder=Path(__file__).resolve().parent
html=(folder/'index.html').read_text('utf8')
css=(folder/'style.css').read_text('utf8')
js=(folder/'app.js').read_text('utf8')
html=re.sub(r'<link rel="stylesheet" href="/style.css">','<style>'+css+'</style>',html)
html=html.replace('<script defer src="/app.js"></script>','')
data={'entries':[{'key':'t:1','role':'assistant','source':'telegram','text':'Béla válasz: a rendszer készen áll.','at':1795189000},{'key':'t:2','role':'user','source':'telegram','text':'Milyen színeket válasszak?','at':1795189100},{'key':'t:3','role':'assistant','source':'telegram','text':'Az egyedi színkezelőben a fő színek külön állíthatók.','at':1795189200}], 'jobs':[],'queue':{'waiting':7,'held':7},'automatic':True,'sync_enabled':True,'generated_at':1795189300,'problem':'','sync_problem':''}
js_stub='window.fetch=async function(url,options){if(String(url).includes("timeline"))return {ok:true,json:async()=>('+json.dumps(data,ensure_ascii=False)+')};return {ok:true,json:async()=>({})}};'

with sync_playwright() as p:
 browser=p.chromium.launch(headless=True,executable_path=__import__('shutil').which('chromium') or __import__('shutil').which('google-chrome'),args=['--no-sandbox','--disable-dev-shm-usage','--disable-gpu'])
 for width,height in ((1920,1080),(1600,900),(1366,768),(1280,720),(768,1024),(390,844)):
  pg=browser.new_page(viewport={'width':width,'height':height},device_scale_factor=1)
  errors=[];pg.on('pageerror',lambda e:errors.append(str(e)))
  pg.set_content(html,wait_until='domcontentloaded',timeout=15000)
  pg.evaluate(js_stub)
  pg.add_script_tag(content=js)
  pg.wait_for_timeout(100)
  pre=pg.evaluate('''() => ({theme:document.documentElement.dataset.theme, chat: getComputedStyle(document.querySelector('#workspace')).display, rootScroll:document.documentElement.scrollWidth>innerWidth, bodyWidth:document.body.scrollWidth})''')
  # Preset colors must remain CSS-defined (no added inline token variables).
  pg.locator('#theme').select_option('patina')
  orig_color=pg.evaluate('getComputedStyle(document.querySelector("header")).backgroundColor')
  assert pg.evaluate('document.documentElement.style.length')==0
  for preset in ('light','dark','ocean','cream','peach','slate','midnight','terracotta','cinnabar','sunlight','patina'):
   pg.locator('#theme').select_option(preset)
   assert pg.evaluate('document.documentElement.style.length')==0
  assert pg.evaluate('getComputedStyle(document.querySelector("header")).backgroundColor')==orig_color
  pg.locator('#themeCustomizeToggle').click()
  pg.wait_for_timeout(30)
  stats=pg.evaluate('''() => {const d=document.getElementById('themeEditor'),g=document.querySelector('.theme-editor-grid'),r=d.getBoundingClientRect(),scroll=document.querySelector('.theme-editor-scroll');return {position:getComputedStyle(d).position,rect:[r.x,r.y,r.width,r.height],columns:getComputedStyle(g).gridTemplateColumns,fieldCards:document.querySelectorAll('.theme-editor-card').length,scrollOverflow:scroll.scrollHeight>scroll.clientHeight,chatHidden:document.querySelector('#workspace').hidden,bodyOverflow:document.documentElement.scrollWidth>innerWidth,theme:document.documentElement.dataset.theme};}''')
  if width==1600 and '--screenshots' in __import__('sys').argv: pg.screenshot(path=str(folder/'preview_1600.png'))
  if width==390 and '--screenshots' in __import__('sys').argv:pg.screenshot(path=str(folder/'preview_390.png'))
  print(width,'pre=',pre,'stats=',stats,'errors=',errors[:2])
  assert not errors,(width,errors)
  assert stats['position']=='fixed' and stats['fieldCards']==25 and stats['scrollOverflow']
  assert not stats['bodyOverflow']
  assert stats['columns'].count(' ')==(0 if width<=510 else 1),stats['columns']
  # Edit a custom-only header color, then Cancel must restore original preset.
  pg.evaluate('''() => {const input=document.querySelector('#themeHex-header-bg');input.value='#ff0000';input.dispatchEvent(new Event('change',{bubbles:true}));}''')
  edited=pg.evaluate('getComputedStyle(document.querySelector("header")).backgroundColor')
  assert edited=='rgb(255, 0, 0)',(width,edited)
  pg.locator('#themeCancel').click()
  assert pg.locator('#themeEditor').is_hidden()
  assert pg.evaluate("document.documentElement.dataset.theme")=='patina'
  assert pg.evaluate('getComputedStyle(document.querySelector("header")).backgroundColor')==orig_color
  pg.close()
 browser.close()
print('PASS ALL VIEWPORT LAYOUTS')
