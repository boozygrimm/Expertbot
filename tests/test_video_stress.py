import unittest
from video_prototype.stress import apply_xvibe_stress


class StressTests(unittest.TestCase):
    def test_marks_tested_words_in_xvibe_notation(self):
        self.assertEqual(apply_xvibe_stress('Укуси, Бендер! Луна-парк ждёт.'),
                         'Укус+и, Б+ендер! Луна-парк ждёт.')
        self.assertEqual(apply_xvibe_stress('УКУСИТЕ БЕНДЕРА и Бендеру'),
                         'УКУС+ИТЕ Б+ЕНДЕРА и Б+ендеру')

    def test_preserves_other_words_and_existing_marks(self):
        text = 'Укус+и, Б+ендер, укуси́ Бе́ндер, укусить и бендеровский.'
        self.assertEqual(apply_xvibe_stress(text), text)
        source = 'Укуси Бендера и укуси́ Бе́ндер'
        self.assertEqual(apply_xvibe_stress(apply_xvibe_stress(source)), apply_xvibe_stress(source))
