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
