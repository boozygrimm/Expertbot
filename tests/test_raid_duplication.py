"""Классический RAID заморожен: он не должен догонять магитех.

`raid.js` и `raid_magitech.js` выросли из одной кодовой базы, и магитех до сих
пор совпадает с классикой на 91 % строк (5357 из 5861). Разделить их — отдельная
переработка движка; принятое решение (см. README, «Пересборка страниц RAID») —
держать классику контрольным пробником и не пускать в неё новый код.

Замок на сам файл уже стоит в браузерных тестах:
`integration.test.cjs` сверяет sha256 `lunapark-raid.html`, поэтому любая
правка `raid.js`/`raid.css` роняет сборку, пока хеш не обновят осознанно.
Здесь — то, чего хешем не проверишь: что заморозка не отменена молча
(документация, размеры, адрес /raid_test).
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLASSIC_JS = ROOT / 'miniapp_preview' / 'raid.js'
CLASSIC_CSS = ROOT / 'miniapp_preview' / 'raid.css'
MAGITECH_JS = ROOT / 'miniapp_preview' / 'raid_magitech.js'

# Потолки на момент заморозки (плюс небольшой запас). Уменьшать можно свободно,
# увеличивать — только осознанно: это и есть сигнал «в классику снова поехал
# новый код вместо магитеха».
JS_LINES_LIMIT = 5900
CSS_LINES_LIMIT = 790


def lines(path: Path) -> int:
    return len(path.read_text(encoding='utf-8').splitlines())


class FrozenClassicTests(unittest.TestCase):
    def test_classic_script_does_not_grow(self):
        self.assertLessEqual(
            lines(CLASSIC_JS), JS_LINES_LIMIT,
            'raid.js заморожен: новые правила добавляй в raid_magitech.js')

    def test_classic_styles_do_not_grow(self):
        self.assertLessEqual(
            lines(CLASSIC_CSS), CSS_LINES_LIMIT,
            'raid.css заморожен: новые стили добавляй в raid_magitech.css')

    def test_freeze_is_documented(self):
        readme = (ROOT / 'README.md').read_text(encoding='utf-8')
        self.assertIn('заморожена', readme)
        self.assertIn('tests/test_raid_duplication.py', readme)

    def test_magitech_remains_the_larger_game(self):
        """Если классика вдруг сравняется по размеру — копирование вернулось."""
        self.assertGreater(lines(MAGITECH_JS) - lines(CLASSIC_JS), 1000)

    def test_raid_test_preview_points_at_magitech(self):
        """Заморозка имеет смысл только пока /raid_test ведёт в магитех."""
        panel = (ROOT / 'handlers' / 'owner_control_panel.py').read_text(encoding='utf-8')
        self.assertIn("RAID_PREVIEW_URL = 'https://boozygrimm.github.io/Expertbot/"
                      "lunapark-raid-magitech.html?telegram=1'", panel)


if __name__ == '__main__':
    unittest.main()
