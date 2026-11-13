"""TeraTTS synthesis for the experimental video worker, run in its own venv."""
import argparse
import re

MODEL_ID = 'TeraSpace/TeraTTSv2'
MODEL_REVISION = '4c3bf2eb99c148cb657af10bd3bf717befb714c8'
MAX_CHUNK = 200


def split_text(text, limit=MAX_CHUNK):
    """Prefer sentence boundaries, falling back to words for long sentences."""
    text = re.sub(r'\s+', ' ', text).strip()
    if not text or limit < 1:
        raise ValueError('Пустой текст или некорректная длина части')
    # Tags are model syntax, not spoken text. Do not let an LLM introduce them.
    text = text.replace('<', '').replace('>', '').strip()
    if not text:
        raise ValueError('Пустой текст после удаления тегов')
    sentences = re.split(r'(?<=[.!?…])\s+', text)
    units = []
    for sentence in sentences:
        if len(sentence) <= limit:
            units.append(sentence)
            continue
        current = ''
        for word in sentence.split():
            if len(word) > limit:
                raise ValueError('Слово слишком длинное для синтеза')
            if current and len(current) + 1 + len(word) > limit:
                units.append(current)
                current = ''
            current = (current + ' ' + word).strip()
        if current:
            units.append(current)

    # Minimize the number of chunks, then prefer evenly sized chunks so a
    # long answer does not become one huge part followed by a tiny tail.
    best = [(0, 0, [])]
    for end in range(1, len(units) + 1):
        choices = []
        for start in range(end - 1, -1, -1):
            chunk = ' '.join(units[start:end])
            if len(chunk) > limit:
                break
            count, squared, chunks = best[start]
            choices.append((count + 1, squared + len(chunk) ** 2, chunks + [chunk]))
        best.append(min(choices, key=lambda choice: (choice[0], choice[1])))
    return best[-1][2]


def apply_overrides(text):
    """Override tested words after RUAccent, leaving other markers intact."""
    # Each +? accepts RUAccent's prior marker before any letter.
    rules = (
        # Stress the first part only for the compound, not the moon itself.
        (r'\+?л\+?у\+?н\+?а(?=\s*[-‑–—]\s*\+?п\+?а\+?р\+?к(?:\+?а|\+?у|\+?е|\+?о\+?м|\+?и|\+?о\+?в|\+?а\+?м|\+?а\+?м\+?и|\+?а\+?х)?(?![\w+\u0301]))', 1),
        (r'\+?б\+?е\+?н\+?д\+?е\+?р(?:\+?а|\+?у|\+?о\+?м|\+?е)?', 1),
        (r'\+?у\+?к\+?у\+?с\+?и(?:\+?т\+?е)?', 4),
        (r'\+?м\+?е\+?ш\+?к\+?и', 4),
        (r'\+?о\+?т\+?п\+?о\+?л\+?и\+?р\+?о\+?в\+?а\+?т\+?ь', 9),
    )
    for pattern, accent_index in rules:
        def replace(match):
            word = match.group().replace('+', '')
            return word[:accent_index] + '+' + word[accent_index:]
        text = re.sub(r'(?<![\w+\u0301])' + pattern + r'(?![\w+\u0301])', replace, text, flags=re.IGNORECASE)
    # Pronounce this compound together, as preferred in listening tests.
    # Keep the already assigned stress markers and restrict the match to park forms.
    text = re.sub(
        r'(?<![\w+\u0301])(л\+уна)\s*[-‑–—]\s*'
        r'(\+?п\+?а\+?р\+?к(?:\+?а|\+?у|\+?е|\+?о\+?м|\+?и|\+?о\+?в|\+?а\+?м|\+?а\+?м\+?и|\+?а\+?х)?)(?![\w+\u0301])',
        r'\1\2', text, flags=re.IGNORECASE,
    )
    return text


def synthesize(text, output, *, voice='ru_m1', duration_scale=1.1, seed=42):
    import numpy as np
    from transformers import AutoModel

    parts = split_text(text)
    model = AutoModel.from_pretrained(
        MODEL_ID, revision=MODEL_REVISION, trust_remote_code=True,
        provider='CPUExecutionProvider', threads=4, local_files_only=True,
    )
    audio = []
    for part in parts:
        # Normalize first: supplying + beforehand disables auto stress for the
        # entire <ru> span in this release of TeraTTS.
        normalized = model.normalize_text('<ru>' + part + '</ru>')
        normalized = apply_overrides(normalized)
        if audio:
            audio.append(np.zeros(11025, dtype=np.float32))  # 250 ms at 44.1 kHz
        audio.append(model.generate_speech(normalized, voice=voice, duration_scale=duration_scale, seed=seed))
    result = np.concatenate(audio)
    if not 0 < len(result) < 60 * 44100:
        raise ValueError('Аудио должно быть короче 60 секунд')
    model.save_wav(str(output), result)
    print(f'Озвучка готова: {len(parts)} частей, {len(result) / 44100:.1f} с', flush=True)


def main():
    parser = argparse.ArgumentParser(description='Синтез TeraTTS для видеокружка')
    parser.add_argument('--text', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    synthesize(args.text, args.output, seed=args.seed)


if __name__ == '__main__':
    main()
