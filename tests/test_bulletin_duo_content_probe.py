import base64
import io
import json
import tempfile
from pathlib import Path
import urllib.error
import uuid
import wave
from unittest.mock import Mock, patch
import pytest
from news_prototype import bulletin_duo_content_probe as p


def wav():
    output=io.BytesIO()
    with wave.open(output,'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(b'\1\0'*2400)
    return output.getvalue()


def bulletin():
    turns=[{'speaker':'Бендер','text':'Первый говорит.'},{'speaker':'Киберсенсей','text':'Второй отвечает.'}]
    return {'sections':[{'id':1,'ticker':'Пример','turns':turns},
                        {'id':2,'ticker':'Ещё','turns':turns}]}


class Response(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self,*args): self.close()


def test_payload_has_two_annotated_parts_and_voice_assignment():
    parts=bulletin()['sections'][0]['turns']; body=p.body_for(parts)
    assert [x['speechMetadata']['speaker'] for x in body['contents'][0]['parts']]==['Bender','Sensei']
    voices=body['generationConfig']['speechConfig']['multiSpeakerVoiceConfig']['speakerVoiceConfigs']
    assert [x['voiceConfig']['prebuiltVoiceConfig']['voiceName'] for x in voices]==['Algenib','Puck']
    assert 'ticker' not in json.dumps(body)


def test_response_api_adapter_uses_correct_schema_and_hides_http_error():
    output={'candidates':[{'finishReason':'STOP','content':{'parts':[{'inlineData':{
        'mimeType':'audio/wav','data':base64.b64encode(wav()).decode()}}]}}]}
    opener=Mock(); opener.open.return_value=Response(json.dumps(output).encode())
    body=p.body_for(bulletin()['sections'][0]['turns'])
    with patch.object(p.urllib.request,'build_opener',return_value=opener):
        data, seconds=p.request_one('secret',body,3)
    assert data==wav() and seconds==.1
    assert opener.open.call_args.args[0].full_url==p.API
    assert opener.open.call_args.kwargs['timeout']==3
    opener.open.side_effect=urllib.error.HTTPError('https://x/?secret',429,'secret',{},None)
    with patch.object(p.urllib.request,'build_opener',return_value=opener):
        with pytest.raises(p.gemini_tts.TTSFailure,match='HTTP 429') as raised:
            p.request_one('secret',body,3)
    assert 'secret' not in str(raised.value)


def test_rotation_then_one_delivery_and_no_duplicate():
    with tempfile.TemporaryDirectory() as tmp:
        base=Path(tmp); job=base/str(uuid.uuid4());job.mkdir()
        original=json.dumps({'bulletin':bulletin()},ensure_ascii=False)
        (job/'request.json').write_text(original,encoding='utf8');out=base/'probe'
        initial=p.gemini_tts.request_one
        with patch.object(p,'read_worker_env',return_value={}), patch.object(p,'worker_keys',return_value=['a','b']), \
             patch.object(p,'request_one',side_effect=[p.gemini_tts.TTSFailure('HTTP 429'),(wav(),.1)]) as request, \
             patch.object(p,'send_once',return_value='sent') as send:
            audio, result=p.run(job,out)
            assert result=='sent' and audio.read_bytes()==wav()
            assert [c.args[0] for c in request.call_args_list]==['a','b']
            send.assert_called_once()
            with pytest.raises(FileExistsError): p.run(job,out)
            assert request.call_count==2; send.assert_called_once()
        assert p.gemini_tts.request_one is initial
        assert (job/'request.json').read_text(encoding='utf8')==original


def test_send_is_one_shot_and_uncertain_does_not_retry():
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);audio=root/'duo.wav';audio.write_bytes(wav())
        opener=Mock(); opener.open.side_effect=OSError('secret')
        with patch.object(p,'read_settings',return_value={'BOT_TOKEN':'secret','ADMIN_ID':'123'}), \
             patch.dict(p.os.environ,{},clear=True), patch.object(p.subprocess,'run',side_effect=OSError()), \
             patch.object(p.urllib.request,'build_opener',return_value=opener):
            assert 'не подтверждена' in p.send_once(audio,root,{})
            assert 'уже начиналась' in p.send_once(audio,root,{})
        opener.open.assert_called_once()
        assert b'secret' not in opener.open.call_args.args[0].data


def test_fake_end_to_end_two_turns_rotation_and_telegram_without_network():
    with tempfile.TemporaryDirectory() as tmp:
        base=Path(tmp); job=base/str(uuid.uuid4());job.mkdir()
        (job/'request.json').write_text(json.dumps({'bulletin':bulletin()},ensure_ascii=False),encoding='utf8')
        recorded=[]
        response={'candidates':[{'finishReason':'STOP','content':{'parts':[{'inlineData':{
            'mimeType':'audio/wav','data':base64.b64encode(wav()).decode()}}]}}]}
        class FakeOpener:
            def open(self, request, timeout):
                recorded.append(request)
                if request.full_url==p.API:
                    if len(recorded)==1:
                        raise urllib.error.HTTPError('https://example',429,'quota',{},None)
                    return Response(json.dumps(response).encode())
                if request.full_url.endswith('/sendAudio'):
                    return Response(b'{"ok":true,"result":{"message_id":42}}')
                raise AssertionError('unexpected network')
        convert=Mock(returncode=0,stdout=b'fake-mp3')
        with patch.object(p,'read_worker_env',return_value={'GEMINI_TTS_KEYS':'a,b'}), \
             patch.object(p,'read_settings',return_value={'BOT_TOKEN':'secret','ADMIN_ID':'123'}), \
             patch.dict(p.os.environ,{},clear=True), \
             patch.object(p.subprocess,'run',return_value=convert), \
             patch.object(p.urllib.request,'build_opener',side_effect=lambda *_: FakeOpener()):
            output, delivery=p.run(job,base/'probe')
        assert output.read_bytes()==wav()
        assert delivery=='Telegram подтвердил отправку.'
        assert len(recorded)==3
        assert [request.get_header('X-goog-api-key') for request in recorded[:2]]==['a','b']
        assert [part['speechMetadata']['speaker'] for part in json.loads(recorded[1].data)['contents'][0]['parts']]==['Bender','Sensei']
        assert b'fake-mp3' in recorded[2].data
        assert (base/'probe'/'telegram-send-started').is_file()
