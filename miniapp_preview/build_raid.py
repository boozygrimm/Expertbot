"""Build an isolated browser-only classic-Raid demo; no real accounts."""
import hashlib
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
    # Stable across machines/rebuilds; changes with any deliverable input. The
    # visible ID lets a phone screenshot distinguish an old tab from this build.
    fingerprint = hashlib.sha256()
    for name in ('build_raid.py', 'raid.html', 'raid.css', 'raid.js', 'skull_sprites.json'):
        source = HERE / name
        if source.exists():
            fingerprint.update(name.encode('utf-8') + b'\0' + source.read_bytes() + b'\0')
    build_id = fingerprint.hexdigest()[:12]
    html = html.replace('__RAID_BUILD__', build_id)
    html = html.replace('/*__STYLE__*/', css).replace('/*__SCRIPT__*/', art_js + js.replace('</', '<\\/'))
    target = ROOT / 'lunapark-raid.html'
    target.write_text(html, encoding='utf-8')
    print(f'Built {target} ({target.stat().st_size} bytes, build {build_id})')

if __name__ == '__main__':
    main()
