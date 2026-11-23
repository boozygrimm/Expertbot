"""Build the separate RAID design prototype. Never writes the classic trial."""
import base64
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SCENES = {element: f'assets/raid_magitech_{element}.jpg'
          for element in ('fire', 'air', 'earth', 'water')}
SOURCES = ('build_raid_magitech.py', 'raid_magitech.html', 'raid_magitech.css',
           'raid_magitech.js', 'raid_monsters.json', *SCENES.values())


def main():
    fingerprint = hashlib.sha256()
    for name in SOURCES:
        fingerprint.update(name.encode('utf-8') + b'\0' + (HERE / name).read_bytes() + b'\0')
    build_id = fingerprint.hexdigest()[:12]
    html = (HERE / 'raid_magitech.html').read_text(encoding='utf-8')
    css = (HERE / 'raid_magitech.css').read_text(encoding='utf-8')
    js = (HERE / 'raid_magitech.js').read_text(encoding='utf-8')
    art = json.loads((HERE / 'raid_monsters.json').read_text(encoding='utf-8'))
    for element, name in SCENES.items():
        image = base64.b64encode((HERE / name).read_bytes()).decode('ascii')
        css = css.replace(f'__RAID_SCENE_{element.upper()}__', 'data:image/jpeg;base64,' + image)
    art_js = 'window.RAID_MONSTER_ART = ' + json.dumps(art['portraits'], separators=(',', ':')) + ';\n'
    html = html.replace('__RAID_BUILD__', build_id)
    html = html.replace('/*__STYLE__*/', css).replace('/*__SCRIPT__*/', art_js + js.replace('</', '<\\/'))
    target = ROOT / 'lunapark-raid-magitech.html'
    target.write_text(html, encoding='utf-8')
    print(f'Built {target} ({target.stat().st_size} bytes, build {build_id})')


if __name__ == '__main__':
    main()
