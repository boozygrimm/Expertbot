from unittest.mock import Mock, patch
import urllib.error

from news_prototype import bulletin_full_video_trial as trial


def test_uncertain_video_send_does_not_retry(tmp_path):
    video = tmp_path / 'video.mp4'
    video.write_bytes(b'fake-video')
    opener = Mock()
    opener.open.side_effect = OSError('secret URL')
    with patch.object(trial, 'read_settings', return_value={'BOT_TOKEN':'secret', 'ADMIN_ID':'123'}), \
         patch.dict(trial.os.environ, {}, clear=True), \
         patch.object(trial.urllib.request, 'build_opener', return_value=opener):
        assert 'не подтверждена' in trial.send_video_once(video, tmp_path, {})
        assert 'уже начиналась' in trial.send_video_once(video, tmp_path, {})
    opener.open.assert_called_once()
    assert b'secret' not in opener.open.call_args.args[0].data


def test_http_status_is_safe_and_does_not_leak_url(tmp_path):
    video = tmp_path / 'video.mp4'; video.write_bytes(b'fake-video')
    opener = Mock()
    opener.open.side_effect = urllib.error.HTTPError('https://secret', 413, 'secret', {}, None)
    with patch.object(trial, 'read_settings', return_value={'BOT_TOKEN':'secret', 'ADMIN_ID':'123'}), \
         patch.dict(trial.os.environ, {}, clear=True), \
         patch.object(trial.urllib.request, 'build_opener', return_value=opener):
        message = trial.send_video_once(video, tmp_path, {})
        assert 'HTTP 413' in message and 'secret' not in message
