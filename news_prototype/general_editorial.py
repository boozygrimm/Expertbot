"""Transparent, conservative hints for general-news selection.

No model calls and no deletion. A headline is not a verified fact or a full
semantic classification; all results may be wrong and are owner-reviewable.
"""
from __future__ import annotations

from collections import Counter
import re

# Each rule is a family of related keywords. Multiple matches within a family
# count once so repetition or a long post cannot dominate the score.
RULES = {
    'ai': ('нейросет', r'искусственн\w* интеллект', r'языков\w* модел', r'генеративн\w* ии', r'\bllm\b', r'\bchatgpt\b', r'\bmachine learning\b', r'\bartificial intelligence\b', r'\bai\b'),
    'technology': ('кибербезопас', 'хакер', r'программн\w* обеспеч', 'процессор', 'смартфон', 'компьютер', 'микросхем', r'\bsoftware\b', r'\bcybersecurity\b', r'\bsmartphone\b'),
    'internet': (r'\bинтернет\w*\b', r'\bсоцсет\w*\b', r'\bсоциальн\w* сет\w*', r'\bблогер\w*\b', r'\bмессенджер\w*\b', r'\btelegram\b', r'\byoutube\b', r'\btiktok\b', r'\bwhatsapp\b', r'\binstagram\b', r'\bfacebook\b', r'\bdiscord\b', r'\bтелеграм\w*', r'\bватсап\w*', r'\bинстаграм\w*', r'\binternet\b', r'\bsocial media\b'),
    'humor': (r'\bюмор\w*\b', r'\bшутк\w*\b', r'\bмем\w*\b', r'\bкомик(?:и|а|у|ом|е|ов|ами|ах)?\b', r'\bкомеди\w*\b', r'\bhumo[u]?r\b', r'\bmemes?\b', r'\bcomedy\b'),
    'games': ('видеоигр', r'компьютерн\w* игр', 'геймплей', r'игров\w* студи', r'\bsteam\b', r'\bplaystation\b', r'\bxbox\b', r'\bvideo ?game'),
    'cinema': ('фильм', 'кинотеатр', 'кинофестивал', 'сериал', 'режиссёр', 'режиссер', r'\banime\b', r'\bfilm\b', r'\bcinema\b', r'\bmovie\b'),
    'culture': ('музык', 'певец', 'певиц', 'альбом', 'концерт', 'театр', 'спектакл', 'выставк', 'музей', 'литератур', r'\bmusic\b', r'\bconcert\b', r'\bmuseum\b'),
    'science': ('исследован', 'учён', r'учен\w', 'физик', 'химик', 'математик', 'эксперимент', r'\bresearch\b', r'\bscientist', r'\bphysics\b'),
    'health': ('медицин', 'лекарств', 'вакцин', 'пациент', r'клиническ\w* испытан', 'заболеван', 'болезн', r'\bhealth\b', r'\bvaccine', r'\bmedical\b'),
    'space': ('космос', 'космическ', r'спутник\w* орбит', 'телескоп', 'астроном', 'луноход', 'марсоход', r'луна\b', r'марс\b', r'\bnasa\b', r'\bspace\b', r'\bmoon\b', r'\bmars\b'),
    'nature': ('эколог', 'климат', r'лесн\w* пожар', 'океан', 'биоразнообраз', 'загрязнен', 'загрязнён', r'\bclimate\b', r'\becology\b', r'\bocean\b'),
    'animals': ('животн', r'панд\w', r'кошк\w', 'собак', 'птиц', r'\bкит(?:а|ы|ов|ам|ами|е|ом|у)?\b', 'дельфин', r'звер\w', r'\banimals?\b', r'\bdogs?\b', r'\bcats?\b', r'\bwhales?\b', r'\bbirds?\b', r'\bfox(?:es)?\b', r'\bwolv(?:es|erine)\b', r'\blions?\b', r'\btigers?\b', r'\bpenguins?\b', r'\belephants?\b', r'\bwildlife\b', r'\bлисиц\w*', r'\bлиса\b', r'\bмедвед(?:ь|я|и|ей|ям|ями|ях)\b', r'\bволк(?:и|а|ов|ам|ами|ах)?\b', r'\bпингвин\w*', r'\bслон(?:ы|а|ов|ам|ами|ах)?\b'),
    'crypto': ('криптовалют', 'биткоин', 'блокчейн', 'крипторын', r'\bbitcoin\b', r'\bcrypto\b', r'\bblockchain\b', r'\bethereum\b'),
    'business': ('экономик', 'инфляци', 'бизнес', r'бирж\w', 'центробанк', r'процентн\w* ставк', 'инвестиц', r'\beconomy\b', r'\binflation\b', r'\bbusiness\b', r'\binvestment\b'),
    'industry_transport': ('промышленн', 'завод', 'автомобил', 'электромобил', r'железн\w* дорог', 'самолёт', 'самолет', 'транспорт', r'\bautomotive\b', r'\belectric vehicles?\b', r'\bindustry\b'),
    'politics_society': ('политик', r'выбор\w* президент', 'парламент', 'правительств', 'президент', 'общественн', r'\bпутин\w*\b', r'\bкремл\w*\b', r'\bтрамп\w*\b', r'\bзеленск\w*\b', r'\bмид\b', r'\bнато\b', 'госдум', 'спецоперац', r'\bсво\b', r'\belections?\b', r'\bparliament\b', r'\bgovernment\b', r'\bpolitic(?:s|al|ian|ians)\b', r'\bpresident\b', r'\bвоенн\w* (?:действи|конфликт)\w*', r'\bсанкци\w*', r'\bминистр(?:ы|а|ов|ам|ами|ах)?\b'),
    'law_investigations': ('расследован', 'судебн', 'прокуратур', 'законопроект', r'суд\w* дел\w', r'\binvestigation\b', r'\bcourt\b', r'\blawsuit\b'),
    'sport': ('футбол', 'хоккей', 'олимпиад', 'чемпионат', 'теннис', 'спортсмен', r'\bfootball\b', r'\bhockey\b', r'\bolympics\b', r'\btennis\b'),
    'travel_life': ('путешеств', 'туризм', 'турист', 'авиарейс', 'гостиниц', r'бытов\w* техник', r'\btravel\b', r'\btourism\b'),
    'odd': ('курьёз', 'курьез', r'необычн\w* случа', r'необычн\w* находк', r'\bodd news\b', r'\bbizarre\b'),
    'history': ('археолог', r'древн\w* цивилизац', r'историческ\w* артефакт', 'раскопк', r'античн\w* город', r'\barcheolog', r'\barchaeolog', r'\bancient\b'),
}
LABELS = {
    'ai': 'ИИ', 'technology': 'технологии', 'internet': 'Интернет', 'humor': 'Юмор', 'games': 'ИгроМир', 'cinema': 'кино и сериалы',
    'culture': 'музыка и культура', 'science': 'наука', 'health': 'медицина', 'space': 'космос',
    'nature': 'природа и экология', 'animals': 'животные', 'crypto': 'крипта',
    'business': 'экономика и бизнес', 'industry_transport': 'промышленность и транспорт',
    'politics_society': 'политика и общество', 'law_investigations': 'право и расследования',
    'sport': 'спорт', 'travel_life': 'быт и путешествия', 'odd': 'необычные истории',
    'history': 'история и археология', 'general': 'другое',
}
COMPILED = {topic: tuple(re.compile(pattern, re.I | re.UNICODE) for pattern in patterns)
            for topic, patterns in RULES.items()}

# Strong advertising labels only. This is a reason to defer, never proof that a
# post is an advertisement and never a basis for deleting or hiding it.
_AD = re.compile(r'(?iu)(?:^\s*(?:#?реклама|advertisement|sponsored)(?=\s|[:.!—–-]|$)|#реклама\b|\berid\s*[:=])')


def assess(title: str, source_hint: str = 'general') -> dict:
    title = title if isinstance(title, str) else ''
    hint = source_hint if source_hint in LABELS else 'general'
    scores = {topic: sum(bool(pattern.search(title)) for pattern in patterns)
              for topic, patterns in COMPILED.items()}
    maximum = max(scores.values(), default=0)
    # Keep only equally supported strongest categories; a generic science word
    # must not drown out a clear specific category. Multiple ties stay options.
    topics = [topic for topic, score in scores.items() if score and score == maximum]
    return {'topics': topics or [hint], 'basis': 'headline' if topics else 'source_hint',
            'defer_reason': 'advertising_marker' if _AD.search(title) else None}


def political_signal(item: dict) -> bool:
    """Conservative veto signal, even if another topic scored higher.

    Not a semantic classifier; false positives and missed politics are possible.
    """
    if item.get('category_override') == 'politics_society':
        return True
    title = item.get('title') if isinstance(item.get('title'), str) else ''
    if any(pattern.search(title) for pattern in COMPILED['politics_society']):
        return True
    return assess(title, item.get('topic_hint', 'general'))['topics'] == ['politics_society']


def choose_topic(assessment: dict, topic_counts: Counter) -> str:
    topics = assessment.get('topics') or ['general']
    return min(topics, key=lambda topic: (topic_counts[topic], topics.index(topic)))


def assess_item(item: dict) -> dict:
    """An explicit owner category overrides provisional headline/source hints."""
    assessment = assess(item.get('title', ''), item.get('topic_hint', 'general'))
    override = item.get('category_override')
    if isinstance(override, str) and override in LABELS:
        assessment = {**assessment, 'topics': [override], 'basis': 'owner'}
    return assessment
