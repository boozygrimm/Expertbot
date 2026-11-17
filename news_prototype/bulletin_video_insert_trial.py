"""Offline trial: play a local source video on the studio screen without audio.

This standalone demonstration is not connected to the bulletin worker and does
not synthesize speech or access providers. On manual execution it sends one
finished preview to the owner in Telegram; it does not publish anything.
Source length is capped for this trial; the source file is never modified.
"""
from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import threading

from PIL import Image
from news_prototype import (bulletin_animation, bulletin_duo_content_probe, bulletin_full_video_trial,
                            bulletin_wide_screen, podcast_audio_trial)

SOURCE = Path('/data/video-prototype/telegram-video-probe-SenseiDurova-7266/source.mp4')
FOLDER = Path('/data/video-prototype/telegram-video-screen-trial-7266')
SOURCE_SIZE = 10_961_206
FPS = 24
SECONDS = 12
PANEL_SIZE = (456, 212)


def render(source: Path, output: Path, ffmpeg: str, *, seconds: int = SECONDS) -> int:
    """Render up to 12 seconds of local video; keep presenters in front."""
    if not isinstance(seconds, int) or isinstance(seconds, bool) or not 1 <= seconds <= SECONDS:
        raise ValueError('Недопустимая длина пробы')
    if source.is_symlink() or not source.is_file() or source.resolve() == output.resolve():
        raise ValueError('Недоступный исходный файл')
    if output.exists() or output.is_symlink():
        raise FileExistsError('Результат уже существует')
    width, height = PANEL_SIZE
    filt = (f'scale={width}:{height}:force_original_aspect_ratio=decrease,'
            f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x12355e,fps={FPS}')
    decoder = [ffmpeg, '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe',
               '-format_whitelist', 'mov,mp4,m4a,3gp,3g2,mj2', '-i', str(source),
               '-map', '0:v:0', '-an', '-sn', '-dn', '-vf', filt,
               '-frames:v', str(seconds * FPS), '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1']
    encoder = [ffmpeg, '-nostdin', '-v', 'error', '-y', '-f', 'rawvideo', '-pixel_format', 'rgb24',
               '-video_size', '960x540', '-framerate', str(FPS), '-i', 'pipe:0', '-an',
               '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p',
               '-movflags', '+faststart', str(output)]
    transparent = Image.new('RGBA', bulletin_wide_screen.SIZE)
    background = bulletin_wide_screen.compose(speaker_layers=(transparent, transparent)).convert('RGBA')
    layers = bulletin_animation.frames()
    env = podcast_audio_trial.normalized_env()
    dec = subprocess.Popen(decoder, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    try:
        enc = subprocess.Popen(encoder, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env)
    except BaseException:
        dec.kill(); dec.wait()
        raise
    def stop_stalled():
        for process in (dec, enc):
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass
    watchdog = threading.Timer(300, stop_stalled)
    watchdog.daemon = True
    watchdog.start()
    count = 0
    try:
        assert dec.stdout is not None and enc.stdin is not None
        block_size = width * height * 3
        while count < seconds * FPS:
            data = dec.stdout.read(block_size)
            if not data:
                break
            if len(data) != block_size:
                raise ValueError('Видеокадр неполон')
            panel = Image.frombytes('RGB', PANEL_SIZE, data)
            frame = background.copy()
            x0, y0, _, _ = bulletin_wide_screen.SCREEN
            frame.paste(panel, (x0 + 4, y0 + 4))
            bender, sensei = layers[(False, bulletin_animation.is_blink(count / FPS), False)]
            frame.alpha_composite(bender); frame.alpha_composite(sensei)
            enc.stdin.write(frame.convert('RGB').tobytes())
            count += 1
        dec.stdout.close()
        enc.stdin.close()
        if dec.wait(timeout=60) or enc.wait(timeout=120) or not count:
            raise RuntimeError('Не удалось декодировать или создать видеопробу')
    except BaseException:
        stop_stalled()
        dec.wait(); enc.wait()
        raise
    finally:
        watchdog.cancel()
    return count


def main() -> int:
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        print('Не найден ffmpeg; проба не создана.')
        return 1
    try:
        if SOURCE.is_symlink() or SOURCE.stat().st_size != SOURCE_SIZE:
            raise ValueError('Не найден ожидаемый файл поста')
        FOLDER.mkdir(mode=0o700, parents=False, exist_ok=False)
        partial, output = FOLDER / 'screen.partial.mp4', FOLDER / 'screen.mp4'
        count = render(SOURCE, partial, ffmpeg)
        subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-xerror', '-i', str(partial), '-f', 'null', '-'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=120,
                       env=podcast_audio_trial.normalized_env())
        partial.replace(output)
        print(f'Готово: {count / FPS:.1f} с без звука; файл: {output}', flush=True)
        env = bulletin_duo_content_probe.read_worker_env()
        delivery = bulletin_full_video_trial.send_video_once(
            output, FOLDER, env, caption='Проба видеовставки из поста: без озвучки и звука источника.')
        print(delivery, flush=True)
        print('Исходное видео не изменено; озвучка, очередь и бот не запускались.')
        return 0
    except FileExistsError:
        print('Папка или файл пробы уже существует. Проверь результат, не повторяй вслепую.')
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, bulletin_duo_content_probe.ProbeError):
        print('Видеопроба или отправка остановилась. Пришли вывод и не повторяй запуск вслепую.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
