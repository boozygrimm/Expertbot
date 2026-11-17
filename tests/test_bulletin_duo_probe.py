import base64
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
import urllib.error
import uuid
import wave

from news_prototype import bulletin_duo_probe as p


def sample():
    turns = [{'speaker':'Бендер','text':'Проверка первой реплики.'},
             {'speaker':'Киберсенсей','text':'Проверка второй реплики.'}]
    return {'sections': [{'id': 1, 'ticker': 'Первая', 'turns': turns},
                         {'id': 2, 'ticker': 'Вторая', 'turns': turns}]}


def audio():
    out = io.BytesIO()
    with wave.open(out, 'wb') as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(24000); f.writeframes(b'\1\0' * 2400)
    return out.getvalue()


def response(wav=None):
    return {'status':'completed', 'steps':[{'type':'model_output', 'content':[{'type':'audio',
            'mime_type':'audio/wav', 'data':base64.b64encode(wav or audio()).decode()}]}]}


class DuoTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.job=self.root/str(uuid.uuid4()); self.job.mkdir()
        (self.job/'request.json').write_text(json.dumps({'bulletin':sample()},ensure_ascii=False),encoding='utf8')
        self.out=self.root/'probe'

    def test_body_maps_two_approved_turns_and_no_images(self):
        body=p.request_body(p.dialogue_from_job(self.job))
        self.assertEqual(body['model'],p.MODEL); self.assertFalse(body['store'])
        content=body['input'][0]['content']
        self.assertEqual([x['annotations'][0]['speaker'] for x in content],['Bender','Sensei'])
        self.assertEqual(body['generation_config']['speech_config']['speakers'],
                         [{'speaker':'Bender','voice':'Algenib'}, {'speaker':'Sensei','voice':'Iapetus'}])
        self.assertEqual(len(content),2)
        self.assertNotIn('ticker', json.dumps(body))

    def test_response_must_be_complete_single_wav(self):
        data,duration=p.decode_audio(response()); self.assertEqual(data,audio()); self.assertAlmostEqual(duration,.1)
        cases=[{}, {'status':'in_progress','steps':[]}, {'status':'completed','steps':[]},
               response(b'not wave'), {'status':'completed','steps':[{'type':'model_output','content':[
                   {'type':'audio','mime_type':'audio/wav','data':'???'}]}]}]
        for case in cases:
            with self.subTest(case=str(case)[:50]), self.assertRaises(p.ProbeError): p.decode_audio(case)

    def test_exactly_one_request_no_repeat_even_on_failure(self):
        with patch.object(p,'service_keys',return_value=['secret','other']), \
             patch.object(p,'post_once',side_effect=p.ProbeError('failure')) as post:
            with self.assertRaises(p.ProbeError): p.run(self.job,self.out)
            post.assert_called_once(); self.assertEqual(post.call_args.args[0], 'secret')
            with self.assertRaises(FileExistsError): p.run(self.job,self.out)
            post.assert_called_once()
        self.assertTrue((self.out/'request-started').is_file()); self.assertFalse((self.out/'duo.wav').exists())

    def test_success_saves_without_touching_original(self):
        before=(self.job/'request.json').read_bytes()
        with patch.object(p,'service_keys',return_value=['fake']), patch.object(p,'post_once',return_value=(audio(),.1)) as call:
            path,duration=p.run(self.job,self.out)
        self.assertEqual(path.read_bytes(),audio()); self.assertEqual(duration,.1); call.assert_called_once()
        self.assertEqual((self.job/'request.json').read_bytes(),before)

    def test_post_uses_interactions_and_parses_complete_audio(self):
        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *args): self.close()
        opener=Mock(); opener.open.return_value=Response(json.dumps(response()).encode())
        with patch.object(p.urllib.request,'build_opener',return_value=opener) as build:
            wav, duration=p.post_once('fake-secret',p.request_body(p.dialogue_from_job(self.job)))
        self.assertEqual(wav,audio()); self.assertAlmostEqual(duration,.1)
        build.assert_called_once(); self.assertIsInstance(build.call_args.args[0],p.NoRedirect)
        opener.open.assert_called_once()
        req=opener.open.call_args.args[0]
        self.assertEqual(req.full_url,p.API); self.assertEqual(req.get_method(),'POST')
        self.assertEqual(req.get_header('Api-revision'),'2026-05-20')
        self.assertEqual(len(json.loads(req.data)['input'][0]['content']),2)

    def test_secret_and_body_not_exposed_on_http_or_connection_error(self):
        for error in (urllib.error.HTTPError('https://example/?SECRET',429,'SECRET',{},None),
                      urllib.error.URLError('SECRET')):
            opener=Mock(); opener.open.side_effect=error
            with patch.object(p.urllib.request,'build_opener',return_value=opener):
                with self.assertRaises(p.ProbeError) as exc: p.post_once('SECRET',p.request_body(p.dialogue_from_job(self.job)))
            self.assertNotIn('SECRET',str(exc.exception))
            req=opener.open.call_args.args[0]
            self.assertEqual(req.get_header('X-goog-api-key'),'SECRET')
            self.assertNotIn('SECRET',req.full_url)

    def test_no_keys_no_output_and_reject_original_directory(self):
        with patch.object(p,'service_keys',return_value=[]), patch.object(p,'post_once') as post:
            with self.assertRaises(p.ProbeError): p.run(self.job,self.out)
            self.assertFalse(self.out.exists()); post.assert_not_called()
        with patch.object(p,'service_keys',return_value=['key']), patch.object(p,'post_once') as post:
            with self.assertRaises(p.ProbeError): p.run(self.job,self.job/'probe')
            post.assert_not_called()
