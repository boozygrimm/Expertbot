from typing import Awaitable, Callable


def cleanup_music_query(user_query: str) -> str:
    cleaned = " ".join((user_query or "").strip().split())
    if not cleaned:
        return ""

    lowered = f" {cleaned.lower()} "
    for marker in (
        " включи ",
        " найди ",
        " поставь ",
        " please ",
        " оригинал ",
        " original ",
        " official audio ",
        " official ",
        " audio ",
        " трек ",
        " песня ",
        " песню ",
    ):
        lowered = lowered.replace(marker, " ")
    return " ".join(lowered.split())


def build_normalize_music_prompt(cleaned_query: str) -> str:
    return (
        "Ты нормализуешь музыкальные поисковые запросы для SoundCloud/YouTube поиска.\n"
        "Преобразуй кривой, разговорный или транслитный запрос в короткий, чистый запрос для поиска ОРИГИНАЛЬНОГО трека.\n"
        "Правила:\n"
        "1. Убери мусорные слова: включи, найди, поставь, песню, трек, please и т.п.\n"
        "2. Если запрос написан русским транслитом английской песни, аккуратно восстанови латиницу.\n"
        "3. Старайся вернуть 'artist title' или просто 'title'.\n"
        "4. Не добавляй слова remix, cover, live, slowed, reverb, nightcore, karaoke, edit, version, instrumental, lyrics, audio, official, video, если пользователь их явно не просил.\n"
        "5. Не объясняй ничего. Верни только итоговый поисковый запрос одной строкой.\n\n"
        f"Запрос: {cleaned_query}"
    )


async def normalize_music_query(
    user_query: str,
    try_openrouter_fn: Callable[..., Awaitable[tuple[str | None, int]]],
) -> str:
    cleaned = cleanup_music_query(user_query)
    if not cleaned:
        return ""

    prompt = build_normalize_music_prompt(cleaned)
    res, status = await try_openrouter_fn(
        prompt,
        model="google/gemma-3-27b-it:free",
        max_tokens=64,
    )
    if status == 200 and res:
        normalized = " ".join(res.strip().splitlines()[0].split())
        normalized = normalized.strip(" '\"`")
        if normalized:
            return normalized

    return cleaned
