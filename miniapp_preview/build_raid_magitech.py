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
BOTTLE_CLINK = 'assets/clinkbottle.mp3'
BOTTLE_OPEN = 'assets/openbottle.mp3'
SHIELD_FIRST = 'assets/firstshield.mp3'
SHIELD_COLLECT = 'assets/sborshield.mp3'
PHYLACTERY_ART = 'assets/lich_phylactery.webp'
TROPHY_ART = 'assets/raid_trophy.webp'
MONSTER_ART_DIR = 'assets/monsters'
PILOT_ASSETS = tuple(f'assets/pilots/{slug}_{kind}.webp'
                     for slug in ('mercenary', 'biochemist', 'breacher', 'quartermaster', 'survivalist',
                                  'assault', 'armorer', 'interceptor', 'thermite', 'tactician')
                     for kind in ('portrait', 'seated'))
MECH_HEAD_ASSETS = tuple(f'assets/mech_head_{mech}.webp'
                         for mech in ('adventurer', 'barbarian', 'mage', 'ranger', 'rogue',
                                      'priest', 'paladin', 'raider', 'warrior', 'assassin'))
SOURCES = ('build_raid_magitech.py', 'raid_magitech.html', 'raid_magitech.css',
           'raid_magitech.js', 'raid_monsters.json', GARAGE_BACKGROUND, LAUNCH_TEXTURE,
           ROUND_BUTTON_TEXTURE, CONTINUE_TEXTURE, INTRO_MUSIC, SWORD_TAKEN, SWORD_BLADE,
           COIN_TAKEN, COIN_DROP, BOTTLE_CLINK, BOTTLE_OPEN, SHIELD_FIRST, SHIELD_COLLECT,
           PHYLACTERY_ART, TROPHY_ART,
           'assets/back_button_ornament.png', 'assets/pilot_arrow_button.webp',
           'assets/pilot_accept_button.webp', *PILOT_ASSETS, *MECH_HEAD_ASSETS, *SCENES.values())


def _asset_url(rel: str, build_id: str) -> str:
    return f'miniapp_preview/{rel}?v={build_id}'


def _unpack_monster_portraits(portraits: dict, build_id: str) -> dict:
    out_dir = HERE / MONSTER_ART_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    urls = {}
    prefix = 'data:image/webp;base64,'
    for key, value in portraits.items():
        if not value.startswith(prefix):
            raise ValueError(f'Unexpected monster portrait format for {key}')
        raw = base64.b64decode(value[len(prefix):])
        rel = f'{MONSTER_ART_DIR}/{key}.webp'
        dest = HERE / rel
        if not dest.exists() or dest.read_bytes() != raw:
            dest.write_bytes(raw)
        urls[key] = _asset_url(rel, build_id)
    return urls


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
    monster_urls = _unpack_monster_portraits(art['portraits'], build_id)
    for element, name in SCENES.items():
        css = css.replace(f'__RAID_SCENE_{element.upper()}__', _asset_url(name, build_id))
    css = css.replace('__RAID_GARAGE_BACKGROUND__', _asset_url(GARAGE_BACKGROUND, build_id))
    css = css.replace('__RAID_LAUNCH_TEXTURE__', _asset_url(LAUNCH_TEXTURE, build_id))
    css = css.replace('__RAID_ROUND_BUTTON_TEXTURE__', _asset_url(ROUND_BUTTON_TEXTURE, build_id))
    css = css.replace('__RAID_CONTINUE_TEXTURE__', _asset_url(CONTINUE_TEXTURE, build_id))
    html = html.replace('__RAID_INTRO_MUSIC__', _asset_url(INTRO_MUSIC, build_id))
    html = html.replace('__RAID_SWORDTAKEN__', _asset_url(SWORD_TAKEN, build_id))
    html = html.replace('__RAID_SWORDBLADE__', _asset_url(SWORD_BLADE, build_id))
    html = html.replace('__RAID_COINTAKEN__', _asset_url(COIN_TAKEN, build_id))
    html = html.replace('__RAID_COINDROP__', _asset_url(COIN_DROP, build_id))
    html = html.replace('__RAID_CLINKBOTTLE__', _asset_url(BOTTLE_CLINK, build_id))
    html = html.replace('__RAID_OPENBOTTLE__', _asset_url(BOTTLE_OPEN, build_id))
    html = html.replace('__RAID_FIRSTSHIELD__', _asset_url(SHIELD_FIRST, build_id))
    html = html.replace('__RAID_SBORSHIELD__', _asset_url(SHIELD_COLLECT, build_id))
    js = js.replace("'assets/lich_phylactery.png'", "'" + _asset_url(PHYLACTERY_ART, build_id) + "'")
    js = js.replace("'assets/raid_trophy.png'", "'" + _asset_url(TROPHY_ART, build_id) + "'")
    art_js = ('window.RAID_MONSTER_CATALOG = ' + json.dumps(catalog, separators=(',', ':')) + ';\n'
              + 'window.RAID_MONSTER_ART = ' + json.dumps(monster_urls, separators=(',', ':')) + ';\n')
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
