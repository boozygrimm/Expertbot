"""Internal experimental building block for a future controlled retry.

NOT wired to the worker, bot, or a CLI. The caller must first obtain explicit
owner consent, prevent concurrent work, durably reserve exactly one attempt,
reconcile ambiguous outcomes, and validate the source's provenance. This module
alone does none of that and must not be used as a production retry procedure.
"""
from __future__ import annotations

from pathlib import Path
import hashlib
import os
import stat
import tempfile

from news_prototype import bulletin_recovery_inspect, story_audio


def prepare_copy(job: Path, output: Path, ffmpeg: str, keys: list[str]):
    """Copy and recheck a failed snapshot, then synthesize only missing turns.

    The snapshot lives in a separate temporary directory; original job files
    remain untouched. This may call Gemini and consumes quota. A new output
    directory is required and retained on failure. No renderer or sender runs.
    The caller is responsible for persistent replay and concurrency fencing.
    """
    job, output = Path(job), Path(output)
    if output.resolve().is_relative_to(job.resolve()):
        raise bulletin_recovery_inspect.InspectionError('Папка результата находится внутри исходной задачи')
    initial = bulletin_recovery_inspect.inspect(job)
    if initial.complete_pairs < 1:
        raise bulletin_recovery_inspect.InspectionError('Нет сохранённых пар аудио')
    # The temporary job is revalidated after copying, rather than trusting
    # names, counts or externally supplied bytes. Snapshot is private and the
    # normal audio generator gets only bytes from this validated snapshot.
    with tempfile.TemporaryDirectory(prefix='bulletin-recovery-snapshot-') as tmp:
        snapshot = Path(tmp) / initial.request_id
        snapshot.mkdir(mode=0o700)
        work = snapshot / 'work'; work.mkdir(mode=0o700)
        paths = ['failed', 'request.json', 'work/script.json']
        paths += [f'work/{i}-{kind}.wav' for i in range(1, initial.complete_pairs + 1)
                  for kind in ('raw', 'normalized')]
        def read_original(name):
            source = job / name
            # Never follow a final symlink or read an unbounded file. The
            # operator must also ensure no untrusted process controls parents.
            fd = os.open(source, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            with os.fdopen(fd, 'rb') as f:
                info = os.fstat(f.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 6 * 1024 * 1024:
                    raise bulletin_recovery_inspect.InspectionError('Необычный файл снимка')
                data = f.read(6 * 1024 * 1024 + 1)
                if len(data) != info.st_size:
                    raise bulletin_recovery_inspect.InspectionError('Снимок изменился')
                return data
        hashes = {}
        total_bytes = 0
        for name in paths:
            data = read_original(name)
            total_bytes += len(data)
            if total_bytes > 64 * 1024 * 1024:
                raise bulletin_recovery_inspect.InspectionError('Снимок слишком большой')
            hashes[name] = hashlib.sha256(data).digest()
            with (snapshot / name).open('xb') as target:
                target.write(data)
        result = bulletin_recovery_inspect.inspect(snapshot)
        if (result != initial or bulletin_recovery_inspect.inspect(job) != initial or
                any(hashlib.sha256(read_original(name)).digest() != hashes[name] for name in paths)):
            raise bulletin_recovery_inspect.InspectionError('Снимок изменился')
        from news_prototype.bulletin_recovery_inspect import _read_json
        script = _read_json(work / 'script.json', 100_000)
        pairs = tuple(((work / f'{i}-raw.wav').read_bytes(),
                       (work / f'{i}-normalized.wav').read_bytes())
                      for i in range(1, initial.complete_pairs + 1))
        return story_audio._prepare(script, output, ffmpeg, keys, pairs)
