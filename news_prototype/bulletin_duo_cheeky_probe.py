"""One-off listening comparison; no worker, queue, database, or video changes.

Run only with owner approval: requests Gemini using the existing key rotation
and sends the resulting audio to the owner once. Output is in a fresh folder.
"""
from pathlib import Path
import wave

from news_prototype import bulletin_duo_content_probe as probe

JOB = Path('/data/video-prototype/news-jobs/fe65e362-28b2-51bd-b626-0e33388b165c')
FOLDER = Path('/data/video-prototype/bulletin-duo-content-cheeky-fe65e362')
BENDER_STYLE = 'По-русски, уверенно, дерзко и насмешливо, с выразительным сарказмом.'


def main():
    original_voices = probe.VOICES
    probe.VOICES = {**original_voices, 'Бендер': ('Bender', 'Algenib', BENDER_STYLE)}
    try:
        audio, delivery = probe.run(JOB, FOLDER)
        with wave.open(str(audio), 'rb') as f:
            print(f'Аудио сохранено, {f.getnframes()/f.getframerate():.1f} сек.')
        print(delivery)
        print('Сравни манеру Бендера, обе реплики и голоса на слух; видео и очередь не запускались.')
        return 0
    except FileExistsError:
        print('Проба уже запускалась. Проверь папку и личку; повтор не выполнен.')
    except probe.ProbeError as exc:
        print('Проба не удалась:', str(exc))
    except OSError:
        print('Ошибка доступа к файлам. Проверь состояние пробы; не повторяй вслепую.')
    finally:
        probe.VOICES = original_voices
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
