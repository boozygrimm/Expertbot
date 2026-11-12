"""Qwen voice-cloning adapter. Run with the Qwen virtual environment."""
import argparse
from pathlib import Path
from time import perf_counter


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--text', required=True)
    parser.add_argument('--reference', required=True)
    parser.add_argument('--reference-text', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--model', default='Qwen/Qwen3-TTS-12Hz-0.6B-Base')
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--max-new-tokens', type=int, default=768)
    parser.add_argument('--seed', type=int)
    args = parser.parse_args()

    started = perf_counter()
    print('TTS: загрузка библиотек...', flush=True)
    # These packages live in the existing Qwen venv, not in the bot environment.
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel

    print(f'TTS: библиотеки загружены за {perf_counter() - started:.1f} с', flush=True)
    torch.set_num_threads(args.threads)
    if args.seed is not None:
        import random
        import numpy as np
        random.seed(args.seed)
        np.random.seed(args.seed % (2 ** 32))
        torch.manual_seed(args.seed)
    started = perf_counter()
    print('TTS: загрузка модели...', flush=True)
    model = Qwen3TTSModel.from_pretrained(
        args.model, device_map='cpu', dtype=torch.bfloat16, attn_implementation='sdpa'
    )
    print(f'TTS: модель загружена за {perf_counter() - started:.1f} с', flush=True)
    started = perf_counter()
    print(f'TTS: синтез (максимум {args.max_new_tokens} токенов)...', flush=True)
    audio, sample_rate = model.generate_voice_clone(
        text=args.text, language='Russian', ref_audio=args.reference,
        ref_text=args.reference_text, max_new_tokens=args.max_new_tokens,
    )
    print(f'TTS: синтез завершён за {perf_counter() - started:.1f} с', flush=True)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    sf.write(args.output, audio[0], sample_rate, subtype='PCM_16')
    print(f'Озвучка готова: {args.output}', flush=True)


if __name__ == '__main__':
    main()
