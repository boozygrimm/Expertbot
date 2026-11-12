"""Narrow pronunciation hints for xVibe; RUAccent handles other words."""
import re

# Do not alter words already containing + or a combining accent.
WORD_START = r'(?<![\w\u0301+])'
WORD_END = r'(?![\w\u0301+])'
BENDER = re.compile(WORD_START + r'бендер(?:а|у|ом|е)?' + WORD_END, re.IGNORECASE)
UKUSI = re.compile(WORD_START + r'укуси(?:те)?' + WORD_END, re.IGNORECASE)


def apply_xvibe_stress(text):
    """Mark tested pronunciation hints; leave the original message untouched."""
    def bender(match):
        word = match.group(0)
        index = word.lower().find('е')
        return word[:index] + '+' + word[index:]

    def ukusi(match):
        word = match.group(0)
        return word[:4] + '+' + word[4:]

    return UKUSI.sub(ukusi, BENDER.sub(bender, text))
