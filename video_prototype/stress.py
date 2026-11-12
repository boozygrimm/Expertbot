"""Experimental Russian stress annotations for the tested Bender voice.

Qwen Base does not guarantee support for combining accents. Only the user-approved
укуси experiment is enabled for worker jobs; the reference transcript is unchanged.
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


def apply_ukusi_stress(text):
    """Mark only укуси/укусите, preserving case and already marked words."""
    def ukusi(match):
        word = match.group(0)
        return word[:5] + ACUTE + word[5:]

    return UKUSI.sub(ukusi, text)


def apply_stress_marks(text):
    """Annotate narrowly scoped words without changing unselected text or case."""
    def bender(match):
        word = match.group(0)
        index = word.lower().find('е') + 1
        return word[:index] + ACUTE + word[index:]

    def luna(match):
        word = match.group(0)
        return word[:2] + ACUTE + word[2:]

    return LUNA_PARK.sub(luna, apply_ukusi_stress(BENDER.sub(bender, text)))
