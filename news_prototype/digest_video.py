"""Local-only sequential digest assembly from existing MP4s, no speech synthesis.

Each complete input retains its own start/end, including any remote effects.
This is a technical first assembly, not yet the final between-story transition.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import tempfile


def assemble(inputs: list[Path], output: Path, ffmpeg: str) -> None:
    if not inputs:
        raise ValueError('Нужен хотя бы один готовый сюжет')
    if output.suffix.lower() != '.mp4' or output.exists() or output.is_symlink():
        raise ValueError('Результат должен быть новым MP4-файлом')
    resolved = []
    for path in inputs:
        if path.is_symlink() or not path.is_file() or path.suffix.lower() != '.mp4':
            raise ValueError('Нужны существующие локальные MP4 без символических ссылок')
        resolved.append(path.resolve())
    if output.resolve() in resolved:
        raise ValueError('Результат совпадает с исходным файлом')
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix='.digest-', suffix='.mp4', dir=output.parent)
    os.close(fd)
    temporary = Path(tmp_name)
    filters = []
    for i in range(len(resolved)):
        filters.append(f'[{i}:v:0]fps=24,scale=960:540:force_original_aspect_ratio=decrease,'
                       f'pad=960:540:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p,setpts=PTS-STARTPTS[v{i}]')
        filters.append(f'[{i}:a:0]aresample=24000,aformat=sample_fmts=fltp:sample_rates=24000:'
                       f'channel_layouts=mono,asetpts=PTS-STARTPTS[a{i}]')
    streams = ''.join(f'[v{i}][a{i}]' for i in range(len(resolved)))
    filters.append(f'{streams}concat=n={len(resolved)}:v=1:a=1[v][a]')
    command = [ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-xerror', '-y']
    for path in resolved:
        command += ['-i', str(path)]
    command += ['-filter_complex', ';'.join(filters), '-map', '[v]', '-map', '[a]',
                '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p',
                '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', str(temporary)]
    try:
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-xerror', '-i', str(temporary), '-f', 'null', '-'],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Atomic no-overwrite publication on the same filesystem as output.
        os.link(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ffmpeg', default='ffmpeg')
    parser.add_argument('inputs', type=Path, nargs='+')
    args = parser.parse_args()
    assemble(args.inputs, args.output, args.ffmpeg)
    print(f'Дайджест сохранён: {args.output}')


if __name__ == '__main__': main()
