"""Build an isolated browser-only classic-Raid demo; no real accounts."""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

def main():
    html = (HERE / 'raid.html').read_text(encoding='utf-8')
    css = (HERE / 'raid.css').read_text(encoding='utf-8')
    js = (HERE / 'raid.js').read_text(encoding='utf-8')
    sprites_path = HERE / 'skull_sprites.json'
    art_js = ''
    if sprites_path.exists():
        sprites = json.loads(sprites_path.read_text(encoding='utf-8'))
        art_js = 'window.SKULL_ART = ' + json.dumps(sprites) + ';\n'
    html = html.replace('/*__STYLE__*/', css).replace('/*__SCRIPT__*/', art_js + js.replace('</', '<\\/'))
    target = ROOT / 'lunapark-raid.html'
    target.write_text(html, encoding='utf-8')
    print(f'Built {target} ({target.stat().st_size} bytes)')

if __name__ == '__main__':
    main()
