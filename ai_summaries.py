import asyncio
import random
from typing import Awaitable, Callable


def split_text_into_chunks(text: str, chunk_limit: int = 22000) -> list[str]:
    lines = text.split("\n")
    chunks: list[str] = []
    current_chunk = ""
    for line in lines:
        if len(current_chunk) + len(line) > chunk_limit:
            chunks.append(current_chunk.strip())
            current_chunk = line + "\n"
        else:
            current_chunk += line + "\n"
    if current_chunk:
        chunks.append(current_chunk)
    return chunks


async def collect_chunk_summaries(
    chunks: list[str],
    build_prompt: Callable[[str], str],
    summarize_chunk: Callable[[str], Awaitable[str]],
    sleep_range: tuple[float, float],
) -> list[str]:
    summaries: list[str] = []
    for chunk in chunks:
        prompt = build_prompt(chunk)
        summary = await summarize_chunk(prompt)
        if summary:
            summaries.append(summary)
        await asyncio.sleep(sleep_range[0] if sleep_range[0] == sleep_range[1] else random.uniform(*sleep_range))
    return summaries
