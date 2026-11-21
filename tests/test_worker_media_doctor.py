"""The doctor only reads: no restart, no upload, no key output."""
import os
from pathlib import Path

from video_prototype import worker_media_doctor as doctor
from news_prototype import media_tools


def test_report_names_missing_binaries_and_the_exact_next_steps(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(media_tools, 'FALLBACK_DIRS', (str(tmp_path),))
    monkeypatch.setattr(doctor, 'systemctl', lambda *args: '')
    monkeypatch.setenv('PATH', '')
    monkeypatch.delenv('BENDER_WORKER_URL', raising=False)
    monkeypatch.setattr('sys.argv', ['doctor'])
    assert doctor.main() == 1
    out = capsys.readouterr().out
    assert 'ffmpeg: НЕ НАЙДЕН' in out
    assert 'apt-get install -y ffmpeg' in out
    assert 'systemctl restart bender-video.service' in out
    assert 'только чтение' in out
    # Nothing that looks like a secret is printed.
    assert 'VIDEO_WORKER_KEY' not in out and 'Bearer' not in out


def test_report_says_replace_the_code_when_binaries_exist(monkeypatch, capsys, tmp_path):
    for name in ('ffmpeg', 'ffprobe'):
        tool = tmp_path / name
        tool.write_text('#!/bin/sh\n')
        tool.chmod(0o755)
    monkeypatch.setattr(media_tools, 'FALLBACK_DIRS', (str(tmp_path),))
    monkeypatch.setattr(doctor, 'systemctl', lambda *args: 'active' if args[:2] == ('show', doctor.UNIT) else '')
    monkeypatch.setenv('PATH', '')
    monkeypatch.setattr('sys.argv', ['doctor'])
    code = doctor.main()
    out = capsys.readouterr().out
    assert 'ffmpeg:' in out and 'НЕ НАЙДЕН' not in out
    assert 'старым кодом' in out or 'обновить код воркера' in out
    assert code in (0, 1)


def test_video_check_reports_a_frozen_stretch_and_a_moving_file(monkeypatch, tmp_path):
    """The check must name the exact seconds, not guess from a description."""
    import subprocess
    import imageio_ffmpeg
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    for name in ('ffmpeg', 'ffprobe'):
        tool = tmp_path / 'bin' / name
        tool.parent.mkdir(exist_ok=True)
        tool.symlink_to(ffmpeg)
    monkeypatch.setattr(media_tools, 'FALLBACK_DIRS', (str(tmp_path / 'bin'),))
    monkeypatch.setenv('PATH', '')
    frozen = tmp_path / 'frozen.mp4'
    # Two seconds of a still picture, then two seconds of a moving one.
    subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                    'color=c=navy:s=160x90:r=24:d=2', '-f', 'lavfi', '-i',
                    'testsrc2=size=160x90:rate=24', '-filter_complex',
                    '[0:v][1:v]concat=n=2:v=1:a=0[v]', '-map', '[v]', '-t', '4',
                    '-pix_fmt', 'yuv420p', str(frozen)], check=True)
    lines = doctor.video_lines(frozen)
    text = '\n'.join(lines)
    assert '0.0–' in text or '0.0-–' in text
    assert 'картинка в ролике меняется' not in text

    moving = tmp_path / 'moving.mp4'
    subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=size=160x90:rate=24:d=4', '-pix_fmt', 'yuv420p', str(moving)], check=True)
    text = '\n'.join(doctor.video_lines(moving))
    assert 'неподвижных участков' in text


def test_video_check_is_skipped_for_a_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(media_tools, 'FALLBACK_DIRS', (str(tmp_path),))
    assert 'не проверен' in ' '.join(doctor.video_lines(tmp_path / 'none.mp4'))
