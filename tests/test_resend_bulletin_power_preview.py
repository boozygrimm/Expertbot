from unittest.mock import patch

import pytest
from news_prototype import resend_bulletin_power_preview as retry


def test_resend_existing_video_once_without_render_or_tts(tmp_path):
    folder = tmp_path / 'video-preview-power-1'; folder.mkdir()
    video = folder / 'video.mp4'; video.write_bytes(b'fake video')
    marker = folder / 'telegram-send-started'; marker.write_text('one send')
    with patch.object(retry, 'EXPECTED_SIZE', video.stat().st_size), \
         patch.object(retry.subprocess, 'run') as check, \
         patch.object(retry.bulletin_duo_content_probe, 'read_worker_env', return_value={}), \
         patch.object(retry.bulletin_full_video_trial, 'send_video_once', return_value='sent') as send:
        assert retry.run(folder, 'ffmpeg') == 'sent'
        send.assert_called_once_with(video, folder / 'telegram-retry-1', {})
        with pytest.raises(FileExistsError): retry.run(folder, 'ffmpeg')
        send.assert_called_once()
        assert check.call_count == 2
    assert video.read_bytes() == b'fake video'
    assert marker.read_text() == 'one send'


def test_missing_marker_size_mismatch_and_symlink_are_rejected(tmp_path):
    folder = tmp_path / 'preview'; folder.mkdir()
    video = folder / 'video.mp4'; video.write_bytes(b'video')
    with patch.object(retry, 'EXPECTED_SIZE', video.stat().st_size), \
         patch.object(retry.subprocess, 'run') as check:
        with pytest.raises(ValueError): retry.run(folder, 'ffmpeg')
        (folder / 'telegram-send-started').write_text('one')
        video.write_bytes(b'other length')
        with pytest.raises(ValueError): retry.run(folder, 'ffmpeg')
        video.unlink(); video.symlink_to(tmp_path / 'other')
        with pytest.raises(ValueError): retry.run(folder, 'ffmpeg')
        check.assert_not_called()
