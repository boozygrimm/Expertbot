from pathlib import Path
from unittest.mock import patch

import pytest
from news_prototype import telegram_video_source_probe as probe


def page(url='https://cdn4.telesco.pe/file/example', post='SenseiDurova/7266'):
    return f'''<div class="tgme_widget_message" data-post="{post}">
      <a class="tgme_widget_message_video_player" href="https://t.me/{post}">
       <video src="{url}"></video></a></div>'''.encode()


def test_exact_post_video_only_and_known_hosts():
    url = 'https://cdn4.telesco.pe/file/example'
    assert probe.extract_video(page(), 'SenseiDurova/7266') == url
    for bad in [page('http://cdn4.telesco.pe/file/a'), page('https://evil.example/file/a'),
                page(post='SenseiDurova/7265'), page() + page(),
                page().replace(b'SenseiDurova/7266"', b'SenseiDurova/7265"', 1)]:
        with pytest.raises(ValueError):
            probe.extract_video(bad, 'SenseiDurova/7266')


def test_no_video_from_neighbour_or_reply():
    html = b'''<div class="tgme_widget_message" data-post="SenseiDurova/7266">
      <div class="tgme_widget_message_reply"><a class="tgme_widget_message_video_player" href="https://t.me/SenseiDurova/7266">
      <video src="https://cdn4.telesco.pe/file/wrong"></video></a></div></div>'''
    with pytest.raises(ValueError): probe.extract_video(html, 'SenseiDurova/7266')


@pytest.mark.asyncio
async def test_download_bounded_and_save_in_new_folder(tmp_path):
    video = b'\x00\x00\x00\x18ftypisom' + b'x' * 128
    class Content:
        def __init__(self, data): self.data = data
        async def iter_chunked(self, _):
            yield self.data[:12]; yield self.data[12:]
    class Response:
        status = 200
        def __init__(self, data): self.content = Content(data); self.content_length = len(data)
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
    class Session:
        def __init__(self): self.calls = []
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        def get(self, url, allow_redirects):
            assert allow_redirects is False
            self.calls.append(url)
            return Response(page() if len(self.calls) == 1 else video)
    folder = tmp_path / 'probe'; session = Session()
    with patch.object(probe.aiohttp, 'ClientSession', return_value=session), \
         patch.object(probe, 'inspect_video', return_value=(53., 1280, 720)):
        await probe.probe(probe.POST_URL, folder)
    assert len(session.calls) == 2
    assert (folder / 'source.mp4').read_bytes() == video
    assert not (folder / 'source.partial.mp4').exists()


def test_rejects_invalid_ffprobe_output_without_leaking_metadata(tmp_path):
    from unittest.mock import Mock
    output = Mock(stdout=b'{"format":{"duration":"secret metadata"},"streams":[{"codec_type":"video"}]}')
    with patch.object(probe.shutil, 'which', return_value='/usr/bin/ffprobe'), \
         patch.object(probe.subprocess, 'run', return_value=output):
        with pytest.raises(ValueError) as exc:
            probe.inspect_video(tmp_path / 'video.mp4')
    assert 'secret' not in str(exc.value)
