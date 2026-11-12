import unittest
from video_prototype.stress import apply_stress_marks, apply_ukusi_stress


class StressTests(unittest.TestCase):
    def test_requested_words_and_case(self):
        self.assertEqual(apply_stress_marks('Укуси меня, Бендер! Луначарский ждёт у Луна-парка.'),
                         'Укуси́ меня, Бе́ндер! Луначарский ждёт у Лу́на-парка.')
        self.assertEqual(apply_stress_marks('УКУСИ БЕНДЕРА, ЛУНА ПАРКИ!'),
                         'УКУСИ́ БЕ́НДЕРА, ЛУ́НА ПАРКИ!')
        self.assertEqual(apply_stress_marks('Укусите Укуси укуси'), 'Укуси́те Укуси́ укуси́')

    def test_ukusi_only_does_not_touch_other_words(self):
        self.assertEqual(apply_ukusi_stress('Укуси Бендера у Луна-парка и укусите меня.'),
                         'Укуси́ Бендера у Луна-парка и укуси́те меня.')
        self.assertEqual(apply_ukusi_stress('Укуси́ меня!'), 'Укуси́ меня!')
        self.assertEqual(apply_ukusi_stress('Укусить и укусил'), 'Укусить и укусил')

    def test_avoid_unrelated_words_and_already_stressed(self):
        text = 'На Луне, Луна и парк; укусить, укусил, лунатик, бендеровский.'
        self.assertEqual(apply_stress_marks(text), text)
        marked = 'Укуси́, Бе́ндер и Лу́на-парк.'
        self.assertEqual(apply_stress_marks(marked), marked)
        self.assertEqual(apply_stress_marks(apply_stress_marks('Укуси Бендера у Луна парка')),
                         apply_stress_marks('Укуси Бендера у Луна парка'))

    def test_luna_park_forms_and_separators(self):
        for phrase in ('луна парк', 'луна-парка', 'Луна\u2011парком', 'ЛУНА ПАРКОВ'):
            with self.subTest(phrase=phrase):
                self.assertIn('у́', apply_stress_marks(phrase).lower())
        for phrase in ('Луна-парковый', 'Луна паркет', 'на Луну'):
            with self.subTest(phrase=phrase):
                self.assertEqual(apply_stress_marks(phrase), phrase)
