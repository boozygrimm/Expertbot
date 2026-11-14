import unittest
from unittest.mock import patch
from PIL import Image
from video_prototype import dynamic_sign, random_guest_render
from video_prototype.sign_text import normalize_sign, parse_generated


class SignTextTests(unittest.TestCase):
    def test_normalize_and_bound_sign(self):
        self.assertEqual(normalize_sign(' Гравитация  в отпуске '), 'ГРАВИТАЦИЯ В ОТПУСКЕ')
        for value in (None, '', '😀', 'СЛИШКОМ МНОГО СЛОВ В ФРАЗЕ', 'a'*23,
                      'СТРОКА\n😀', '"КРИВО"', '---', ['test']):
            with self.subTest(value=value):
                self.assertIsNone(normalize_sign(value))

    def test_isolate_speech_and_invalid_sign(self):
        for value, expected in [
            ('{"speech":"Мой ответ.","sign":"КОСМОС В ОТПУСКЕ"}', ('Мой ответ.', 'КОСМОС В ОТПУСКЕ')),
            ('```json\n{"speech":"Привет!","sign":"😀"}\n```', ('Привет!', None)),
            ('{"speech":"Ответ!", "sign":', ('Ответ!', None)),
            ('ОТВЕТ: Мой ответ.\nТАБЛИЧКА: ШУТКА', ('Мой ответ.', None)),
            ('Мой обычный ответ.', ('Мой обычный ответ.', None)),
            ('{"sign":"ШУТКА"}', ('', 'ШУТКА')),
            ('{"sign":', ('', None)),
        ]:
            with self.subTest(value=value):
                self.assertEqual(parse_generated(value), expected)


class DynamicSignTests(unittest.TestCase):
    def test_prepares_progressively_and_protects_hand(self):
        frames, position = dynamic_sign.prepare('ГРАВИТАЦИЯ В ОТПУСКЕ')
        self.assertEqual(len(frames), 19)
        self.assertEqual(position, (79, 228))
        self.assertNotEqual(frames[0].tobytes(), frames[10].tobytes())
        self.assertNotEqual(frames[10].tobytes(), frames[-1].tobytes())
        self.assertEqual(frames[-1].getpixel((94-79, 274-228))[3], 0)
        self.assertEqual(dynamic_sign.index_at(0, len(frames)), 0)
        self.assertEqual(dynamic_sign.index_at(50, len(frames)), 18)
        self.assertEqual(dynamic_sign.index_at(23/24, len(frames), duration=1), 18)

    def test_various_text_and_safe_rejection(self):
        for phrase in ('ХЛЕБ', 'ВЕЧНЫЙ БОСС', 'ЭЙ, РОБОТ!', 'BIG BANG'):
            with self.subTest(phrase=phrase):
                frames, _ = dynamic_sign.prepare(phrase)
                self.assertEqual(len(frames)-1, len(phrase.replace(' ', '')))
        with self.assertRaises(ValueError):
            dynamic_sign.prepare('😀')

    def test_failed_sign_preparation_preserves_ordinary_renderer(self):
        guest = lambda frame, seconds: frame
        with patch.object(random_guest_render, 'quick_levels', return_value=[0] * 24), \
             patch.object(random_guest_render, 'make_effect', return_value=guest), \
             patch.object(random_guest_render.dynamic_sign, 'prepare', side_effect=OSError), \
             patch.object(random_guest_render, 'base_render') as renderer:
            random_guest_render.render('a', 'c', 'o', 'b', 'p', 'portrait', 'out', '319', sign='КОСМОС')
            self.assertIs(renderer.call_args.kwargs['frame_effect'], guest)

    def test_sign_is_added_before_guest_effect(self):
        seen = []
        def guest(frame, seconds):
            seen.append(frame)
            return frame
        with patch.object(random_guest_render, 'quick_levels', return_value=[0] * 24), \
             patch.object(random_guest_render, 'make_effect', return_value=guest), \
             patch.object(random_guest_render, 'base_render') as renderer:
            random_guest_render.render('a', 'c', 'o', 'b', 'p', 'portrait', 'out', '319', sign='КОСМОС')
            fn = renderer.call_args.kwargs['frame_effect']
            raw = bytes(480*480*3)
            first, last = fn(raw, 0), fn(raw, 10)
            self.assertEqual(seen, [first, last])
            self.assertNotEqual(first, last)
