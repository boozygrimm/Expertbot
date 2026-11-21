"""Build an isolated browser-only discovery-design demo; no real accounts."""
import base64
import json
from pathlib import Path
from handlers.alc_recipes import ELEMENTS, RECIPES, is_battle_unit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
EXCLUDED = {'base_crystal', 'active_base', 'recipe_scroll', 'pack_s', 'pack_m', 'pack_l', 'pack_xl'}

def main():
    data = {'elements': ELEMENTS, 'recipes': [
        {'ingredients': list(pair), 'result': result}
        for pair, result in RECIPES.items()
        if not is_battle_unit(result) and result not in EXCLUDED and not any(x in EXCLUDED for x in pair)
    ]}
    html = (HERE / 'discovery.html').read_text(encoding='utf-8')
    css = (HERE / 'discovery.css').read_text(encoding='utf-8')
    js = (HERE / 'discovery.js').read_text(encoding='utf-8')
    image = base64.b64encode((HERE / 'assets/discovery_cauldron.jpg').read_bytes()).decode('ascii')
    css = css.replace('__LAB_IMAGE__', 'data:image/jpeg;base64,' + image)
    payload = json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
    html = html.replace('/*__STYLE__*/', css).replace('/*__DATA__*/', payload).replace('/*__SCRIPT__*/', js.replace('</', '<\\/'))
    target = ROOT / 'lunapark-discovery.html'
    target.write_text(html, encoding='utf-8')
    print(f'Built {target} ({target.stat().st_size} bytes)')

if __name__ == '__main__':
    main()
