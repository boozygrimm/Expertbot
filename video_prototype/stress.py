"""Experimental Russian stress annotations for the tested Bender voice.

Qwen Base does not guarantee support for combining accents. This is opt-in until
heard and approved; the reference transcript is deliberately left unchanged.
"""
import re

ACUTE = '\u0301'
WORD_START = r'(?<![\w\u0301])'
WORD_END = r'(?![\w\u0301])'
BENDER = re.compile(WORD_START + r'бендер(?:а|у|ом|е)?' + WORD_END, re.IGNORECASE)
UKUSI = re.compile(WORD_START + r'укуси(?:те)?' + WORD_END, re.IGNORECASE)
LUNA_PARK = re.compile(
    WORD_START + r'луна(?=[\s\-\u2011]+парк(?:а|у|ом|е|и|ов|ам|ами|ах)?' + WORD_END + ')',
    re.IGNORECASE,
)


def apply_stress_marks(text):
    """Annotate narrowly scoped words without changing unselected text or case."""
    def bender(match):
        word = match.group(0)
        index = word.lower().find('е') + 1
        return word[:index] + ACUTE + word[index:]

    def ukusi(match):
        word = match.group(0)
        return word[:5] + ACUTE + word[5:]

    def luna(match):
        word = match.group(0)
        return word[:2] + ACUTE + word[2:]

    return LUNA_PARK.sub(luna, UKUSI.sub(ukusi, BENDER.sub(bender, text)))
