"""Build the separate RAID design prototype. Never writes the classic trial."""
import base64
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SCENES = {element: f'assets/raid_magitech_{element}.jpg'
          for element in ('fire', 'air', 'earth', 'water')}
GARAGE_BACKGROUND = 'assets/mech_garage_bg.webp'
LAUNCH_TEXTURE = 'assets/raid_launch_texture.webp'
ROUND_BUTTON_TEXTURE = 'assets/raid_round_button_texture.webp'
CONTINUE_TEXTURE = 'assets/raid_continue_texture.webp'
INTRO_MUSIC = 'intro_music.mp3'
SOURCES = ('build_raid_magitech.py', 'raid_magitech.html', 'raid_magitech.css',
           'raid_magitech.js', 'raid_monsters.json', GARAGE_BACKGROUND, LAUNCH_TEXTURE,
           ROUND_BUTTON_TEXTURE, CONTINUE_TEXTURE, INTRO_MUSIC, *SCENES.values())


def main():
    fingerprint = hashlib.sha256()
    for name in SOURCES:
        fingerprint.update(name.encode('utf-8') + b'\0' + (HERE / name).read_bytes() + b'\0')
    build_id = fingerprint.hexdigest()[:12]
    html = (HERE / 'raid_magitech.html').read_text(encoding='utf-8')
    css = (HERE / 'raid_magitech.css').read_text(encoding='utf-8')
    js = (HERE / 'raid_magitech.js').read_text(encoding='utf-8')
    art = json.loads((HERE / 'raid_monsters.json').read_text(encoding='utf-8'))
    catalog = art['special_catalog']
    expected, ready = list(range(2, 33)), catalog['ready_types']
    if (catalog['expected_types'] != expected or ready != sorted(set(ready))
            or any(type(enemy) is not int or enemy not in expected for enemy in ready)
            or type(catalog['complete']) is not bool or catalog['complete'] != (ready == expected)):
        raise ValueError('Invalid special portrait coverage; never label an unfinished set complete')
    for element in SCENES:
        keys = [f'{element}_special_{enemy:02d}' for enemy in ready]
        if any(key not in art['portraits'] for key in keys) or len({art['portraits'][key] for key in keys}) != len(keys):
            raise ValueError(f'Missing or duplicate special portraits for {element}')
    if not catalog['complete']:
        print(f'WORK IN PROGRESS: unique special portraits {len(ready)}/31 per element; not a complete catalogue')
    for element, name in SCENES.items():
        image = base64.b64encode((HERE / name).read_bytes()).decode('ascii')
        css = css.replace(f'__RAID_SCENE_{element.upper()}__', 'data:image/jpeg;base64,' + image)
    garage_image = base64.b64encode((HERE / GARAGE_BACKGROUND).read_bytes()).decode('ascii')
    css = css.replace('__RAID_GARAGE_BACKGROUND__', 'data:image/webp;base64,' + garage_image)
    launch_image = base64.b64encode((HERE / LAUNCH_TEXTURE).read_bytes()).decode('ascii')
    css = css.replace('__RAID_LAUNCH_TEXTURE__', 'data:image/webp;base64,' + launch_image)
    round_image = base64.b64encode((HERE / ROUND_BUTTON_TEXTURE).read_bytes()).decode('ascii')
    css = css.replace('__RAID_ROUND_BUTTON_TEXTURE__', 'data:image/webp;base64,' + round_image)
    continue_image = base64.b64encode((HERE / CONTINUE_TEXTURE).read_bytes()).decode('ascii')
    css = css.replace('__RAID_CONTINUE_TEXTURE__', 'data:image/webp;base64,' + continue_image)
    intro_music = base64.b64encode((HERE / INTRO_MUSIC).read_bytes()).decode('ascii')
    html = html.replace('__RAID_INTRO_MUSIC__', 'data:audio/mpeg;base64,' + intro_music)
    art_js = ('window.RAID_MONSTER_CATALOG = ' + json.dumps(catalog, separators=(',', ':')) + ';\n'
              + 'window.RAID_MONSTER_ART = ' + json.dumps(art['portraits'], separators=(',', ':')) + ';\n')
    html = html.replace('__RAID_BUILD__', build_id)
    html = html.replace('/*__STYLE__*/', css).replace('/*__SCRIPT__*/', art_js + js.replace('</', '<\\/'))
    target = ROOT / 'lunapark-raid-magitech.html'
    target.write_text(html, encoding='utf-8')
    print(f'Built {target} ({target.stat().st_size} bytes, build {build_id})')


if __name__ == '__main__':
    main()
