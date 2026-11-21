"""Portable CSS/template structural checks (Python standard library only)."""
from pathlib import Path
from html.parser import HTMLParser
import hashlib

HERE=Path(__file__).resolve().parent
css=(HERE/'style.css').read_bytes()
base=(HERE/'test-fixtures'/'p5981'/'style.css').read_bytes()
BASE_SHA='35bcaccb33af3857b87528737ce51cc05cfef421c8579decc5c36defccd27596'
assert hashlib.sha256(base).hexdigest()==BASE_SHA
assert css.startswith(base), 'The original theme CSS was changed!'
s=css.decode('utf-8')
assert s.count('{')==s.count('}'), 'CSS braces unbalanced'
for name in ['dark','ocean','cream','peach','slate','midnight','terracotta','cinnabar','sunlight','patina']:
    assert f':root[data-theme="{name}"]' in base.decode('utf-8')
for selector in ['.theme-editor-backdrop','.theme-editor-header','.theme-editor-scroll','.theme-editor-footer',
                 '.theme-editor-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))',
                 '@media(max-width:510px)', '.theme-editor-grid{grid-template-columns:minmax(0,1fr)}',
                 'position:fixed;right:0;top:0;bottom:0', ':root.theme-custom-active header',
                 ':root.theme-custom-active #workspace>aside']:
    assert selector in s, 'Missing scoped CSS: '+selector
class Parser(HTMLParser):
    def __init__(self):super().__init__();self.ids=[];self.roles={};self.styles=0
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='style':self.styles+=1
        assert 'style' not in a, 'Inline style violates CSP'
        if 'id' in a:self.ids.append(a['id']);self.roles[a['id']]=(tag,a)
parser=Parser();parser.feed((HERE/'index.html').read_text('utf-8'))
assert len(parser.ids)==len(set(parser.ids)), 'Duplicate DOM ids'
assert parser.roles['themeEditor'][1].get('role')=='dialog'
assert parser.roles['themeEditor'][1].get('aria-modal')=='true'
assert parser.roles['themeBackdrop'][1].get('hidden')==None
assert not parser.styles
js=(HERE/'app.js').read_text('utf-8')
assert 'injectThemeCSS' not in js and "createElement('style')" not in js
orig=(HERE/'test-fixtures'/'p5981'/'app.js').read_text('utf-8')
cut='const labels={queued:'
assert js[js.index(cut):] == orig[orig.index(cut):], 'Core Webchat logic changed!'
print('PASS: original 11 CSS presets byte-identical; no inline style injection')
print('PASS: fixed drawer, 2-column desktop / 1-column mobile CSS, DOM ids unique')
print('PASS: Webchat/Telegram application logic byte-identical')
