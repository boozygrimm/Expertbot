import base64
import io
import json
from pathlib import Path
import tempfile
import urllib.error
from unittest.mock import Mock, patch
import wave
import imageio_ffmpeg
import pytest
from news_prototype import bulletin_dialogue_audio as t, podcast_video_trial
from video_prototype import gemini_tts


def payload(count=2):
    return {'sections':[{'id':i+1,'ticker':f'Новость {i+1}','turns':[
        {'speaker':'Бендер','text':f'Текст новости {i+1}.'},
        {'speaker':'Киберсенсей','text':'Комментарий.'}]} for i in range(count)]}


def wav(seconds=1, silent=False):
    buf=io.BytesIO()
    with wave.open(buf,'wb') as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(24000)
        f.writeframes((b'\0\0' if silent else b'\1\0') * 24000 * seconds)
    return buf.getvalue()


def response(audio=None):
    return {'candidates':[{'finishReason':'STOP','content':{'parts':[{'inlineData':{
        'mimeType':'audio/wav','data':base64.b64encode(wav() if audio is None else audio).decode()}}]}}]}


def test_request_carries_one_complete_news_and_approved_voice_pair():
    item=payload()['sections'][0]; body=t.request_body(item)
    assert len(body['contents'][0]['parts'])==2
    assert [x['speechMetadata']['speaker'] for x in body['contents'][0]['parts']]==['Bender','Sensei']
    voices=body['generationConfig']['speechConfig']['multiSpeakerVoiceConfig']['speakerVoiceConfigs']
    assert [x['voiceConfig']['prebuiltVoiceConfig']['voiceName'] for x in voices]==['Algenib','Puck']
    assert 'Новость 2' not in json.dumps(body,ensure_ascii=False)


def test_audio_validation_accepts_long_item_without_truncation_and_rejects_bad_data():
    assert t.decode_audio(response(wav(65)))[1]==65
    for value in ({},response(b'broken'),response(wav(1,True)),{'candidates':[{'finishReason':'MAX_TOKENS'}]}):
        with pytest.raises(gemini_tts.TTSFailure): t.decode_audio(value)


def test_request_rejects_http_without_leaking_key():
    opener=Mock(); opener.open.side_effect=urllib.error.HTTPError('https://example/?secret',429,'secret',{},None)
    with patch.object(t.urllib.request,'build_opener',return_value=opener):
        with pytest.raises(gemini_tts.TTSFailure) as ex: t.request_one('secret',t.request_body(payload()['sections'][0]),2)
    assert str(ex.value)=='HTTP 429'
    assert 'secret' not in opener.open.call_args.args[0].full_url


@pytest.mark.parametrize('count',[2,5,10,20])
def test_prepare_one_generation_per_news_including_multi_turn(count):
    with tempfile.TemporaryDirectory() as tmp:
        calls=[]
        def fake_request(key, body, timeout):
            calls.append((key,body)); return wav(),1.0
        with patch.object(t,'request_one',side_effect=fake_request):
            audio, parts=t.prepare(payload(count),Path(tmp)/'work',imageio_ffmpeg.get_ffmpeg_exe(),['a','b'])
        assert len(calls)==count and len(parts)==count
        assert [len(body['contents'][0]['parts']) for _,body in calls]==[2]*count
        assert [key for key,_ in calls]==['a' if i%2==0 else 'b' for i in range(count)]
        _, samples=podcast_video_trial.read_wav(audio)
        assert len(samples)==24000*count+round(24000*.350)*(count-1)


def test_key_rotation_on_error_no_repeat_of_old_news():
    with tempfile.TemporaryDirectory() as tmp:
        calls=[]
        def fake_request(key, body, timeout):
            calls.append(key)
            if len(calls)==2: raise gemini_tts.TTSFailure('HTTP 429')
            return wav(),1
        with patch.object(t,'request_one',side_effect=fake_request):
            _,parts=t.prepare(payload(),Path(tmp)/'work',imageio_ffmpeg.get_ffmpeg_exe(),['a','b'])
        assert calls==['a','b','a'] and len(parts)==2


def test_failure_preserves_completed_news_and_no_manifest_or_replay():
    with tempfile.TemporaryDirectory() as tmp:
        folder=Path(tmp)/'work'
        def request(key,body,timeout):
            if '2' in body['contents'][0]['parts'][0]['text']: raise gemini_tts.TTSFailure('failed')
            return wav(),1
        with patch.object(t,'request_one',side_effect=request) as call:
            with pytest.raises(gemini_tts.TTSFailure): t.prepare(payload(),folder,imageio_ffmpeg.get_ffmpeg_exe(),['a'])
            assert (folder/'1-normalized.wav').is_file() and not (folder/'dialogue.wav').exists()
            before=call.call_count
            with pytest.raises(FileExistsError): t.prepare(payload(),folder,imageio_ffmpeg.get_ffmpeg_exe(),['a'])
            assert call.call_count==before
