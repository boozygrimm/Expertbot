import io
from unittest.mock import Mock, patch

import pytest
from news_prototype import telegram_video_source_probe as probe


def page(url='https://cdn4.telesco.pe/file/example', post='SenseiDurova/7266'):
    return f'''<div class="tgme_widget_message" data-post="{post}">
      <a class="tgme_widget_message_video_player" href="https://t.me/{post}">
       <video src="{url}"></video></a></div>'''.encode()


def test_exact_post_video_only_and_known_hosts():
    url = 'https://cdn4.telesco.pe/file/example'
    assert probe.extract_video(page(), 'SenseiDurova/7266') == (url, 1)
    for bad in [page('http://cdn4.telesco.pe/file/a'), page('https://evil.example/file/a'),
                page(post='SenseiDurova/7265'), page() + page(),
                page().replace(b'SenseiDurova/7266"', b'SenseiDurova/7265"', 1)]:
        with pytest.raises(probe.ProbeError):
            probe.extract_video(bad, 'SenseiDurova/7266')


def test_no_video_from_neighbour_or_reply():
    html = b'''<div class="tgme_widget_message" data-post="SenseiDurova/7266">
      <div class="tgme_widget_message_reply"><a class="tgme_widget_message_video_player" href="https://t.me/SenseiDurova/7266">
      <video src="https://cdn4.telesco.pe/file/wrong"></video></a></div></div>'''
    with pytest.raises(probe.ProbeError): probe.extract_video(html, 'SenseiDurova/7266')


def test_download_bounded_and_save_in_new_folder(tmp_path):
    video = b'\x00\x00\x00\x18ftypisom' + b'x' * 128
    class Response(io.BytesIO):
        def getheader(self, name): return str(len(video)) if name == 'Content-Length' else None
    html_connection, video_connection = Mock(), Mock()
    folder = tmp_path / 'probe'
    with patch.object(probe, 'get_response', side_effect=[(html_connection, Response(page())),
                                                           (video_connection, Response(video))]) as get, \
         patch.object(probe, 'inspect_video', return_value=(53., 1280, 720)):
        probe.probe(folder)
        assert get.call_count == 2
        with pytest.raises(FileExistsError): probe.probe(folder)
        assert get.call_count == 2
    html_connection.close.assert_called_once(); video_connection.close.assert_called_once()
    assert (folder / 'source.mp4').read_bytes() == video
    assert not (folder / 'source.partial.mp4').exists()


def test_size_limit_refuses_before_creating_folder(tmp_path):
    class Response(io.BytesIO):
        def getheader(self, name): return str(probe.MAX_VIDEO + 1)
    conn = Mock()
    with patch.object(probe, 'get_response', side_effect=[(conn, Response(page())), (conn, Response(b''))]):
        with pytest.raises(probe.ProbeError): probe.probe(tmp_path / 'probe')
    assert not (tmp_path / 'probe').exists()


def test_dns_must_be_public_for_every_answer():
    public = (2, 1, 6, '', ('8.8.8.8', 443))
    private = (2, 1, 6, '', ('127.0.0.1', 443))
    with patch.object(probe.socket, 'getaddrinfo', return_value=[public]):
        assert probe.vetted_address('t.me') == '8.8.8.8'
    with patch.object(probe.socket, 'getaddrinfo', return_value=[public, private]):
        with pytest.raises(probe.ProbeError): probe.vetted_address('t.me')


def test_rejects_invalid_ffprobe_output_without_leaking_metadata(tmp_path):
    output = Mock(stdout=b'{"format":{"duration":"secret metadata"},"streams":[{"codec_type":"video"}]}')
    with patch.object(probe.shutil, 'which', return_value='/usr/bin/ffprobe'), \
         patch.object(probe.subprocess, 'run', return_value=output):
        with pytest.raises(probe.ProbeError) as exc:
            probe.inspect_video(tmp_path / 'video.mp4')
    assert 'secret' not in str(exc.value)


def test_module_imports_without_third_party_packages():
    import subprocess, sys
    result = subprocess.run([sys.executable, '-S', '-c', 'import news_prototype.telegram_video_source_probe'],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
