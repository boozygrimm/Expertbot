import unittest

from video_prototype.tera_speech import apply_overrides, split_text


class TeraTextTests(unittest.TestCase):
    def test_override_after_auto_stress_preserves_case_and_others(self):
        source = '<ru>Бенд+ер, бенд+ера, УК+УСИТЕ, м+ешки, отпол+ировать, здор+ово.</ru>'
        expected = '<ru>Б+ендер, б+ендера, УКУС+ИТЕ, мешк+и, отполиров+ать, здор+ово.</ru>'
        self.assertEqual(apply_overrides(source), expected)
        self.assertEqual(apply_overrides(expected), expected)

    def test_not_substrings_or_different_forms(self):
        text = 'бендеровский укуси́ укусить мешкина отполировали'
        self.assertEqual(apply_overrides(text), text)

    def test_split_by_sentence_and_keep_all_words(self):
        text = 'Начало. ' + 'длинно ' * 30 + 'Конец. Ещё предложение!'
        result = split_text(text, 100)
        self.assertTrue(all(0 < len(part) <= 100 for part in result))
        self.assertEqual(' '.join(result), ' '.join(text.split()))

    def test_repeated_sentences_do_not_confuse_boundaries(self):
        text = 'Одна фраза. Одна фраза. Конец.'
        parts = split_text(text, 23)
        self.assertEqual(' '.join(parts), text)
        self.assertTrue(all(len(part) <= 23 for part in parts))

    def test_balances_sentence_chunks(self):
        text = 'А' * 175 + '. ' + 'Б' * 100 + '. ' + 'В' * 170 + '.'
        parts = split_text(text, 300)
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[0], 'А' * 175 + '.')
        self.assertEqual(' '.join(parts), text)

    def test_oversized_word_is_rejected(self):
        with self.assertRaises(ValueError):
            split_text('x' * 301)

    def test_escape_model_tags(self):
        self.assertEqual(split_text('Текст <en>тест</en>'), ['Текст enтест/en'])

class TeraSynthesisTests(unittest.TestCase):
    def test_synthesis_uses_auto_stress_before_overrides_and_inserts_pause(self):
        import sys
        from types import SimpleNamespace
        from unittest.mock import patch
        import numpy as np
        from video_prototype import tera_speech

        class Model:
            def __init__(self):
                self.inputs = []
            def normalize_text(self, text):
                self.inputs.append(('normalize', text))
                return text.replace('Бендер', 'Бенд+ер').replace('другое', 'друг+ое')
            def generate_speech(self, text, **kwargs):
                self.inputs.append(('generate', text, kwargs))
                return np.ones(4410, dtype=np.float32)
            def save_wav(self, path, audio):
                self.path, self.audio = path, audio

        model = Model()
        loader = SimpleNamespace(from_pretrained=lambda *args, **kwargs: model)
        with patch.dict(sys.modules, {'transformers': SimpleNamespace(AutoModel=loader)}), \
             patch.object(tera_speech, 'split_text', return_value=['Я Бендер.', 'другое']):
            tera_speech.synthesize('Текст', '/tmp/out.wav')
        first, second = [x for x in model.inputs if x[0] == 'generate']
        self.assertEqual(first[1], '<ru>Я Б+ендер.</ru>')
        self.assertEqual(second[1], '<ru>друг+ое</ru>')
        self.assertEqual(first[2]['voice'], 'ru_m1')
        self.assertEqual(first[2]['duration_scale'], 1.2)
        self.assertEqual(len(model.audio), 4410 * 2 + 11025)
        self.assertEqual(model.path, '/tmp/out.wav')


if __name__ == '__main__':
    unittest.main()
