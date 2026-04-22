import asyncio
from aiogram import Router
from ddgs import DDGS
from ai_logic import generate_with_retry

router = Router()

def _fetch_ddgs_raw(query: str) -> list:
    """Получает сырые данные из DuckDuckGo без форматирования"""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
            return results
    except Exception as e:
        print(f"DDGS Error: {e}")
        return []

async def smart_web_search(query: str) -> str:
    """Ищет инфу и прогоняет ее через ИИ для красивого ответа"""
    raw_results = await asyncio.to_thread(_fetch_ddgs_raw, query)
    
    if not raw_results:
        return "❌ Я поискал в сети, но ничего толкового не нашел."

    # Собираем сырые данные в строку для ИИ
    context = ""
    for idx, res in enumerate(raw_results, 1):
        context += f"[{idx}] {res.get('title', '')}\nТекст: {res.get('body', '')}\n\n"

    prompt = (
        f"Пользователь попросил найти информацию по запросу: «{query}».\n"
        f"Вот свежие данные из поисковика:\n{context}\n"
        "Твоя задача: Изучи эти данные и дай пользователю развернутый, понятный и точный ответ. "
        "Пиши естественно, как умный помощник. Если в данных нет ответа, так и скажи."
    )
    
    # Используем твою функцию из ai_logic (сначала Groq, потом Gemini)
    ai_answer = await generate_with_retry(prompt)
    
    return ai_answer if ai_answer else "❌ Нашел информацию, но не смог ее обработать (ИИ недоступен)."
    