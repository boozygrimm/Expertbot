import hashlib
import io
import json
import subprocess
import sys
from unittest.mock import Mock, patch

import imageio_ffmpeg
import pytest

from news_prototype import telegram_public_source as source, telegram_video_source_probe as probe


CAPTION = 'Здесь подпись новости с фактами. Проверьте, пожалуйста, выбранный источник.'
VIDEO = 'https://cdn4.telesco.pe/file/correct?token=opaque'


def page(post='ExampleNews/102', src=VIDEO, extra=''):
    return f'''<div class="tgme_widget_message" data-post="ExampleNews/101">
      <div class="tgme_widget_message_text js-message_text">Соседний пост</div>
      <a class="tgme_widget_message_video_player" href="https://t.me/ExampleNews/101"><video src="https://cdn1.telesco.pe/file/other"></video></a></div>
    <div class="tgme_widget_message" data-post="{post}">
      <div class="tgme_widget_message_reply"><div class="tgme_widget_message_text js-message_text">Чужая цитата</div></div>
      <div class="tgme_widget_message_text js-message_text">{CAPTION}<br>Ещё факт. <img alt="📰"><script>НЕ ИСПОЛНЯТЬ</script></div>
      <a class="tgme_widget_message_video_player" href="https://t.me/{post}"><video src="{src}"></video></a>{extra}</div>'''.encode()


class Response(io.BytesIO):
    def __init__(self, data, length=None, encoding=None):
        super().__init__(data); self.length = length; self.encoding = encoding
    def getheader(self, name):
        return self.length if name == 'Content-Length' else self.encoding if name == 'Content-Encoding' else None


def test_exact_caption_and_exact_video_from_same_post_without_secrets_in_repr():
    p = source.parse_widget('https://t.me/s/ExampleNews/102?single=1', page())
    assert p.url == 'https://t.me/ExampleNews/102' and p.post_id == 'ExampleNews/102'
    assert p.caption == CAPTION + '\nЕщё факт. 📰'
    assert p.video_status == 'available' and p.video_url == VIDEO
    assert 'token' not in repr(p) and 'НЕ ИСПОЛНЯТЬ' not in p.caption and 'Соседний' not in p.caption


def test_none_or_ambiguous_video_does_not_choose_unrelated_or_unsafe():
    body = page().decode()
    exact = '<a class="tgme_widget_message_video_player" href="https://t.me/ExampleNews/102"><video src="' + VIDEO + '"></video></a>'
    none = source.parse_widget('https://t.me/ExampleNews/102', body.replace(exact, '').encode())
    assert none.video_status == 'none' and none.video_url is None and CAPTION in none.caption
    other = body.replace(VIDEO, 'https://evil.test/file/v')
    bad = source.parse_widget('https://t.me/ExampleNews/102', other.encode())
    assert bad.video_status == 'ambiguous' and bad.video_url is None
    duplicate = body.replace(exact, exact + exact)
    assert source.parse_widget('https://t.me/ExampleNews/102', duplicate.encode()).video_status == 'ambiguous'
    wrong = body.replace('href="https://t.me/ExampleNews/102"', 'href="https://t.me/ExampleNews/101"')
    assert source.parse_widget('https://t.me/ExampleNews/102', wrong.encode()).video_url is None


def test_invalid_or_ambiguous_post_fails_closed():
    for url in ('http://t.me/ExampleNews/102', 'https://t.me/c/1/2', 'https://evil.test/ExampleNews/102',
                'https://t.me/ExampleNews/102%2f103', 'https://t.me/ExampleNews/0',
                'https://user@t.me/ExampleNews/102', 'https://t.me:444/ExampleNews/102'):
        with pytest.raises(source.SourceError): source.canonical_post(url)
    empty_caption = page().replace((CAPTION + '<br>Ещё факт. <img alt="📰"><script>НЕ ИСПОЛНЯТЬ</script>').encode(), b'')
    for html in (page(post='ExampleNews/103'), page() + page(), empty_caption):
        with pytest.raises(source.SourceError): source.parse_widget('https://t.me/ExampleNews/102', html)


ALBUM = '''<div class="tgme_widget_message" data-post="ExampleNews/300">
  <div class="tgme_widget_message_text js-message_text">Подпись альбома с фактами.</div>
  <div class="tgme_widget_message_grouped_wrap"><div class="tgme_widget_message_grouped">
    <a class="tgme_widget_message_video_player grouped_media_wrap" href="https://t.me/ExampleNews/300?single"><video src="https://cdn4.telesco.pe/file/v"></video></a>
    <a class="tgme_widget_message_photo_wrap grouped_media_wrap" href="https://t.me/ExampleNews/301?single" style="background-image:url('https://cdn4.telesco.pe/file/p.jpg')"></a>
  </div></div></div>'''.encode()


def test_album_item_reads_shared_caption_instead_of_reporting_missing_text():
    # A later album item has no card of its own, so its caption is the album's.
    post = source.parse_widget('https://t.me/ExampleNews/301', ALBUM)
    assert post.post_id == 'ExampleNews/301' and post.caption == 'Подпись альбома с фактами.'
    assert post.video_status == 'none'
    from news_prototype import telegram_post
    assert telegram_post.extract_post(ALBUM, 'ExampleNews/301')[1] == post.caption


def test_album_without_own_caption_still_asks_for_single_mode():
    hollow = ALBUM.replace('<div class="tgme_widget_message_text js-message_text">'
                           'Подпись альбома с фактами.</div>'.encode(), b'')
    assert hollow != ALBUM
    # The album marker tells fetch_post to retry once with ?single=1; no
    # caption is invented from a neighbour or from an empty card.
    with pytest.raises(source._GroupedAttempt):
        source.parse_widget('https://t.me/ExampleNews/301', hollow)


def test_fetch_uses_canonical_exact_widget_and_closes_connection():
    conn = Mock(); data = page()
    with patch.object(probe, 'get_response', return_value=(conn, Response(data, str(len(data))))) as get:
        p = source.fetch_post('https://telegram.me/ExampleNews/102?single=1')
    get.assert_called_once_with('t.me', '/ExampleNews/102?embed=1')
    conn.close.assert_called_once()
    assert p.video_url == VIDEO


def test_html_failure_is_sanitized_and_does_not_download():
    for error in (OSError('private-secret'), probe.ProbeError('secret')):
        with patch.object(probe, 'get_response', side_effect=error):
            with pytest.raises(source.SourceError) as exc: source.fetch_post('https://t.me/ExampleNews/102')
            assert 'secret' not in str(exc.value)
    conn = Mock()
    with patch.object(probe, 'get_response', return_value=(conn, Response(page(), encoding='gzip'))):
        with pytest.raises(source.SourceError): source.fetch_post('https://t.me/ExampleNews/102')
    conn.close.assert_called_once()


def test_successful_bounded_download_and_no_overwrite(tmp_path):
    content = b'\0\0\0\x18ftypisom' + b'x' * 300
    post = source.parse_widget('https://t.me/ExampleNews/102', page())
    conn = Mock()
    with patch.object(probe, 'get_response', return_value=(conn, Response(content, str(len(content))))) as get, \
         patch.object(source, 'inspect', return_value=(53.0, 848, 476, True)):
        result = source.download_video(post, tmp_path / 'assets', ffmpeg='ffmpeg', ffprobe='ffprobe')
        assert result.path.read_bytes() == content and result.sha256 == hashlib.sha256(content).hexdigest()
        assert (result.duration, result.width, result.height, result.has_audio) == (53., 848, 476, True)
        with pytest.raises(FileExistsError): source.download_video(post, tmp_path / 'assets', ffmpeg='ffmpeg', ffprobe='ffprobe')
        get.assert_called_once_with('cdn4.telesco.pe', '/file/correct?token=opaque')
    conn.close.assert_called_once()
    assert not (tmp_path / 'assets/source.partial.mp4').exists()


def test_bad_video_fails_without_final_file_or_partial(tmp_path):
    post = source.parse_widget('https://t.me/ExampleNews/102', page())
    content = b'\0\0\0\x18ftypisom' + b'x' * 128
    cases = [Response(content, str(source.MAX_VIDEO + 1)), Response(content, str(len(content) + 1)),
             Response(b'not an mp4'), Response(content, str(len(content)), encoding='gzip')]
    for i, response in enumerate(cases):
        folder = tmp_path / f'{i}'
        with patch.object(probe, 'get_response', return_value=(Mock(), response)), \
             patch.object(source, 'inspect') as inspect:
            with pytest.raises(source.SourceError): source.download_video(post, folder, ffmpeg='ffmpeg', ffprobe='ffprobe')
            assert not (folder / 'source.mp4').exists() and not (folder / 'source.partial.mp4').exists()
            inspect.assert_not_called()
    folder = tmp_path / 'invalid'
    with patch.object(probe, 'get_response', return_value=(Mock(), Response(content))), \
         patch.object(source, 'inspect', side_effect=source.SourceError('invalid')):
        with pytest.raises(source.SourceError): source.download_video(post, folder, ffmpeg='ffmpeg', ffprobe='ffprobe')
    assert not (folder / 'source.mp4').exists() and not (folder / 'source.partial.mp4').exists()


def test_inspect_rejects_nonfinite_dimensions_and_decode_errors(tmp_path):
    payload = {'format': {'duration': '2.5', 'format_name': 'mov,mp4,m4a,3gp,3g2,mj2'},
               'streams': [{'codec_type': 'video', 'width': 320, 'height': 180}, {'codec_type': 'audio'}]}
    output = Mock(stdout=json.dumps(payload).encode())
    with patch.object(source.subprocess, 'run', side_effect=[output, Mock()]) as cmd:
        assert source.inspect(tmp_path / 'x.mp4', 'ffprobe', 'ffmpeg') == (2.5, 320, 180, True)
        assert '-xerror' in cmd.call_args_list[1].args[0]
    for duration in ('nan', 'inf', '0', '601'):
        payload['format']['duration'] = duration
        with patch.object(source.subprocess, 'run', return_value=Mock(stdout=json.dumps(payload).encode())):
            with pytest.raises(source.SourceError): source.inspect(tmp_path / 'x.mp4', 'ffprobe', 'ffmpeg')
    with patch.object(source.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'secret')):
        with pytest.raises(source.SourceError) as exc: source.inspect(tmp_path / 'x.mp4', 'ffprobe', 'ffmpeg')
        assert 'secret' not in str(exc.value)


def test_import_has_no_third_party_dependencies():
    proc = subprocess.run([sys.executable, '-S', '-c', 'import news_prototype.telegram_public_source'], capture_output=True)
    assert proc.returncode == 0, proc.stderr


def test_caption_matches_existing_telegram_parser():
    from news_prototype import telegram_post
    expected = telegram_post.extract_post(page(), 'ExampleNews/102')[1]
    assert source.parse_widget('https://t.me/ExampleNews/102', page()).caption == expected


def test_media_environment_never_inherits_app_secrets():
    with patch.dict('os.environ', {'GEMINI_TTS_KEYS': 'secret', 'BOT_TOKEN': 'secret',
                                  'BENDER_WORKER_KEY': 'secret', 'CLOUDFLARE_API_TOKEN': 'secret'}):
        env = source.media_env()
    assert 'secret' not in env.values()
    assert set(env).issubset({'PATH', 'LANG', 'LC_ALL', 'TZ'})


def test_real_ffmpeg_complete_decode_with_valid_and_corrupt_source(tmp_path):
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    path = tmp_path / 'video.mp4'
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=24',
                    '-f', 'lavfi', '-i', 'sine=frequency=500:sample_rate=24000', '-t', '1',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(path)], check=True)
    payload = {'format': {'duration': '1', 'format_name': 'mov,mp4,m4a,3gp,3g2,mj2'},
               'streams': [{'codec_type': 'video', 'width': 320, 'height': 180}, {'codec_type': 'audio'}]}
    real_run = subprocess.run
    def run(args, **kwargs):
        if args[0] == 'mock-ffprobe': return Mock(stdout=json.dumps(payload).encode())
        return real_run(args, **kwargs)
    with patch.object(source.subprocess, 'run', side_effect=run):
        assert source.inspect(path, 'mock-ffprobe', ffmpeg) == (1., 320, 180, True)
        corrupt = tmp_path / 'corrupt.mp4'; corrupt.write_bytes(b'\0\0\0\x18ftypisom' + b'x' * 400)
        with pytest.raises(source.SourceError): source.inspect(corrupt, 'mock-ffprobe', ffmpeg)


def test_cli_only_downloads_with_explicit_folder(tmp_path, capsys):
    p = source.parse_widget('https://t.me/ExampleNews/102', page())
    with patch.object(source, 'fetch_post', return_value=p), patch.object(source, 'download_video') as download, \
         patch.object(sys, 'argv', ['telegram_public_source', p.url]):
        assert source.main() == 0
        download.assert_not_called()
    assert 'не скачивалось' in capsys.readouterr().out
    video = source.Video(tmp_path / 'source.mp4', 'a' * 64, 100, 53., 848, 476, True)
    with patch.object(source, 'fetch_post', return_value=p), patch.object(source, 'download_video', return_value=video) as download, \
         patch.object(sys, 'argv', ['telegram_public_source', p.url, '--video-folder', str(tmp_path / 'new')]):
        assert source.main() == 0
        download.assert_called_once_with(p, tmp_path / 'new')
    result = capsys.readouterr().out
    assert 'аудиодорожка: есть' in result and VIDEO not in result


def test_strip_terminal_control_and_preserve_caption():
    unsafe = page().replace(CAPTION.encode(), (CAPTION + '\x1b[31m').encode())
    post = source.parse_widget('https://t.me/ExampleNews/102', unsafe)
    assert '\x1b' not in post.caption and CAPTION in post.caption
