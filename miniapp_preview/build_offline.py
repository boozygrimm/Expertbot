"""Build a single-file, browser-only demo (not a production build)."""
import base64
from copy import deepcopy
import json
from pathlib import Path

from handlers.alc_recipes import ELEMENTS, RECIPES, is_battle_unit
from miniapp_backend.profile import STATION_NAMES, build_profile
from miniapp_backend.sandbox import EXAMPLE_PLAYER
from miniapp_backend.sandbox_production import ALTAR_SLOTS, STATIONS, STORAGE_CAPACITY

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def script_safe(value):
    return value.replace('</script', '<\\/script').replace('</SCRIPT', '<\\/SCRIPT')


def main():
    data = {
        'player': deepcopy(EXAMPLE_PLAYER), 'profile': build_profile(EXAMPLE_PLAYER),
        'elements': ELEMENTS, 'stationNames': STATION_NAMES, 'stations': STATIONS,
        'altarSlots': ALTAR_SLOTS, 'storageCapacity': STORAGE_CAPACITY,
        'recipes': [{'ingredients': list(pair), 'result': result, 'fighter': is_battle_unit(result)}
                    for pair, result in RECIPES.items()],
    }
    html = (HERE / 'index.html').read_text(encoding='utf-8')
    css = (HERE / 'styles.css').read_text(encoding='utf-8')
    ui = (HERE / 'app.js').read_text(encoding='utf-8')
    adapter = (HERE / 'offline_adapter.js').read_text(encoding='utf-8')
    for asset in ('park.jpg', 'lab.jpg', 'base_scene.jpg', 'expedition_scene.jpg', 'fighter_fire.jpg', 'fighter_water.jpg'):
        encoded = base64.b64encode((HERE / 'assets' / asset).read_bytes()).decode('ascii')
        embedded = 'data:image/jpeg;base64,' + encoded
        html = html.replace(f'assets/{asset}', embedded)
        css = css.replace(f'assets/{asset}', embedded)
    html = html.replace('<link rel="stylesheet" href="styles.css">', '<style>' + css + '</style>')
    html = html.replace('Тестовый час запускается кнопкой; добыча сразу попадает на склад.',
                        'Вымышленный профиль в браузере. Кнопка моделирует час; добыча попадает на склад.')
    html = html.replace('Только общий тестовый профиль, не настоящий прогресс.',
                        'Вымышленный профиль только в этом браузере, не настоящий прогресс.')
    html = html.replace('✦ Это демонстрационные данные. Реальный профиль не подключён.',
                        '✦ Автономная демонстрация: данные только в браузере. Реальный профиль не подключён.')
    ui = ui.replace('ТЕСТОВЫЙ СТЕНД · ВЫМЫШЛЕННЫЙ ПРОФИЛЬ', 'АВТОНОМНАЯ ДЕМОВЕРСИЯ · ВЫМЫШЛЕННЫЙ ПРОФИЛЬ')
    ui = ui.replace('Тестовый профиль загружен · действия только в песочнице', 'Автономное демо · профиль только в этом браузере')
    ui = ui.replace("['Сохранение', 'До перезапуска']", "['Сохранение', 'До перезагрузки страницы']")
    # IDs in this local-only fake are not security credentials; no secure-context
    # browser APIs are required to view a downloaded HTML file.
    uuid = "function offlineUuid(){return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g,c=>{const r=Math.random()*16|0;return (c==='x'?r:(r&3|8)).toString(16);});}"
    ui = ui.replace('crypto.randomUUID()', 'offlineUuid()')
    adapter = adapter.replace('crypto.randomUUID()', 'offlineUuid()')
    payload = script_safe(json.dumps(data, ensure_ascii=False, separators=(',', ':')))
    bootstrap = '<script>' + uuid + '\nwindow.OFFLINE_DATA=' + payload + ';\n' + script_safe(adapter) + '</script>'
    html = html.replace('<script src="config.js" defer></script>', bootstrap)
    # The app script runs after the HTML body, equivalent to defer.
    html = html.replace('  <script src="app.js" defer></script>\n', '')
    html = html.replace('</body>', '<script>' + script_safe(ui) + '</script>\n</body>')
    output = ROOT / 'lunapark-offline.html'
    output.write_text(html, encoding='utf-8')
    print(f'Built {output} ({output.stat().st_size} bytes)')


if __name__ == '__main__':
    main()
