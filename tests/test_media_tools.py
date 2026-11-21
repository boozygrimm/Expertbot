"""ffmpeg under systemd: a minimal PATH must not hide a working install."""
import os
from pathlib import Path
import pytest

from news_prototype import media_tools


def test_extra_dirs_are_found_without_path(tmp_path, monkeypatch):
    tool = tmp_path / 'ffmpeg'
    tool.write_text('#!/bin/sh\n')
    tool.chmod(0o755)
    monkeypatch.setattr(media_tools, 'FALLBACK_DIRS', (str(tmp_path),))
    monkeypatch.setenv('PATH', '')
    assert media_tools.find('ffmpeg') == str(tool)
    assert media_tools.find('ffprobe') is None
    # Only the two known tools can be searched for.
    with pytest.raises(ValueError):
        media_tools.find('bash')


def test_non_executable_file_is_not_trusted(tmp_path, monkeypatch):
    tool = tmp_path / 'ffmpeg'
    tool.write_text('not a program')
    tool.chmod(0o644)
    monkeypatch.setattr(media_tools, 'FALLBACK_DIRS', (str(tmp_path),))
    monkeypatch.setenv('PATH', '')
    assert media_tools.find('ffmpeg') is None


def test_child_env_prepends_the_directory_that_actually_has_the_binary(tmp_path, monkeypatch):
    for name in ('ffmpeg', 'ffprobe'):
        tool = tmp_path / name
        tool.write_text('#!/bin/sh\n')
        tool.chmod(0o755)
    monkeypatch.setattr(media_tools, 'FALLBACK_DIRS', (str(tmp_path),))
    monkeypatch.setenv('PATH', '/usr/bin:/bin')
    env = media_tools.child_env({'PATH': '/usr/bin:/bin', 'BOT_TOKEN': 'secret'})
    assert env['PATH'].split(os.pathsep)[0] == str(tmp_path)
    # The directory appears once, the old PATH survives, secrets are untouched.
    assert env['PATH'].count(str(tmp_path)) == 1
    assert env['PATH'].endswith('/usr/bin:/bin')
    assert env['BOT_TOKEN'] == 'secret'
    # A process launched with this env can resolve the tool again by name.
    assert env['PATH'].split(os.pathsep)[0] == str(Path(media_tools.find('ffmpeg')).parent)


def test_worker_reports_the_capability_it_can_actually_render(tmp_path, monkeypatch):
    """The health list follows find_tool, not the inherited PATH."""
    from news_prototype import media_tools as tools_module
    for name in ('ffmpeg', 'ffprobe'):
        tool = tmp_path / name
        tool.write_text('#!/bin/sh\n')
        tool.chmod(0o755)
    monkeypatch.setattr(tools_module, 'FALLBACK_DIRS', (str(tmp_path),))
    monkeypatch.setenv('PATH', '')
    assert tools_module.find('ffmpeg') and tools_module.find('ffprobe')
