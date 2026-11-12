"""Qwen voice-cloning adapter. Run with the Qwen virtual environment."""
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--text', required=True)
    parser.add_argument('--reference', required=True)
    parser.add_argument('--reference-text', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--model', default='Qwen/Qwen3-TTS-12Hz-0.6B-Base')
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--max-new-tokens', type=int, default=768)
    args = parser.parse_args()

    # These packages live in the existing Qwen venv, not in the bot environment.
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel

    torch.set_num_threads(args.threads)
    model = Qwen3TTSModel.from_pretrained(
        args.model, device_map='cpu', dtype=torch.bfloat16, attn_implementation='sdpa'
    )
    audio, sample_rate = model.generate_voice_clone(
        text=args.text, language='Russian', ref_audio=args.reference,
        ref_text=args.reference_text, max_new_tokens=args.max_new_tokens,
    )
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    sf.write(args.output, audio[0], sample_rate, subtype='PCM_16')
    print(f'Озвучка готова: {args.output}', flush=True)


if __name__ == '__main__':
    main()
