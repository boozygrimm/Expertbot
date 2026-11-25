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
SWORD_TAKEN = 'assets/swordtaken.mp3'
SWORD_BLADE = 'assets/swordblade.mp3'
COIN_TAKEN = 'assets/cointaken.mp3'
COIN_DROP = 'assets/coinsdrop.mp3'
PHYLACTERY_ART = 'assets/lich_phylactery.png'
TROPHY_ART = 'assets/raid_trophy.png'
PILOT_ASSETS = tuple(f'assets/pilots/{slug}_{kind}.webp'
                     for slug in ('mercenary', 'biochemist', 'breacher', 'quartermaster', 'survivalist',
                                  'assault', 'armorer', 'interceptor', 'thermite', 'tactician')
                     for kind in ('portrait', 'seated'))
MECH_HEAD_ASSETS = tuple(f'assets/mech_head_{mech}.webp'
                         for mech in ('adventurer', 'barbarian', 'mage', 'ranger', 'rogue',
                                      'priest', 'paladin', 'raider', 'warrior', 'assassin'))
SOURCES = ('build_raid_magitech.py', 'raid_magitech.html', 'raid_magitech.css',
           'raid_magitech.js', 'raid_monsters.json', GARAGE_BACKGROUND, LAUNCH_TEXTURE,
           ROUND_BUTTON_TEXTURE, CONTINUE_TEXTURE, INTRO_MUSIC, COIN_TAKEN, COIN_DROP, PHYLACTERY_ART, TROPHY_ART,
           'assets/back_button_ornament.png', 'assets/pilot_arrow_button.webp',
           'assets/pilot_accept_button.webp', *PILOT_ASSETS, *MECH_HEAD_ASSETS, *SCENES.values())


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
    sword_taken = base64.b64encode((HERE / SWORD_TAKEN).read_bytes()).decode('ascii')
    html = html.replace('__RAID_SWORDTAKEN__', 'data:audio/mpeg;base64,' + sword_taken)
    sword_blade = base64.b64encode((HERE / SWORD_BLADE).read_bytes()).decode('ascii')
    html = html.replace('__RAID_SWORDBLADE__', 'data:audio/mpeg;base64,' + sword_blade)
    coin_taken = base64.b64encode((HERE / COIN_TAKEN).read_bytes()).decode('ascii')
    html = html.replace('__RAID_COINTAKEN__', 'data:audio/mpeg;base64,' + coin_taken)
    coin_drop = base64.b64encode((HERE / COIN_DROP).read_bytes()).decode('ascii')
    html = html.replace('__RAID_COINDROP__', 'data:audio/mpeg;base64,' + coin_drop)
    phylactery_art = base64.b64encode((HERE / PHYLACTERY_ART).read_bytes()).decode('ascii')
    js = js.replace("'assets/lich_phylactery.png'", "'data:image/png;base64," + phylactery_art + "'")
    trophy_art = base64.b64encode((HERE / TROPHY_ART).read_bytes()).decode('ascii')
    js = js.replace("'assets/raid_trophy.png'", "'data:image/png;base64," + trophy_art + "'")
    art_js = ('window.RAID_MONSTER_CATALOG = ' + json.dumps(catalog, separators=(',', ':')) + ';\n'
              + 'window.RAID_MONSTER_ART = ' + json.dumps(art['portraits'], separators=(',', ':')) + ';\n')
    # Explicit visual-test switch: preview with every mech at least level 5 to inspect
    # the pilot carousel. It is inert unless ?pilot-demo=1 is requested.
    pilot_demo = """(function(){
      if (new URLSearchParams(location.search).get('pilot-demo') !== '1') return;
      try {
        var ids = ['adventurer','barbarian','mage','ranger','rogue','priest','paladin','raider','warrior','assassin'];
        var telegram = new URLSearchParams(location.search).get('telegram') === '1';
        var uid = window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initDataUnsafe && window.Telegram.WebApp.initDataUnsafe.user && window.Telegram.WebApp.initDataUnsafe.user.id;
        var prefix = telegram && Number.isSafeInteger(uid) ? 'raid:magitech:tg:' + uid + ':' : 'raid:magitech:';
        var key = prefix + 'classLevels';
        var old = JSON.parse(localStorage.getItem(key) || '{}');
        ids.forEach(function(id){ old[id] = Math.max(5, Number(old[id]) || 0); });
        localStorage.setItem(key, JSON.stringify(old));
      } catch (error) { console.warn('Pilot demo levels could not be prepared', error); }
    })();\n"""
    html = html.replace('__RAID_BUILD__', build_id)
    html = html.replace('/*__STYLE__*/', css).replace('/*__SCRIPT__*/', art_js + pilot_demo + js.replace('</', '<\\/'))
    target = ROOT / 'lunapark-raid-magitech.html'
    target.write_text(html, encoding='utf-8')
    print(f'Built {target} ({target.stat().st_size} bytes, build {build_id})')


if __name__ == '__main__':
    main()
