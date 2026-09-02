"""Small explicit corpus pools for bounded, provenance-preserving test runs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import random
import re


class CorpusError(RuntimeError):
    """An explicit corpus selection is invalid or exceeds local boundaries."""


MAX_SAMPLE_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class CorpusSample:
    name: str
    sha256: str
    data: bytes


@dataclass(frozen=True, slots=True)
class CorpusReference:
    name: str
    sha256: str

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "sha256": self.sha256}


_REFERENCE_TAG = re.compile(
    rb'<historical-js-example name="([A-Za-z0-9._-]{1,128})" '
    rb'sha256="([0-9a-f]{64})">'
)


def extract_corpus_references(window: bytes | None) -> tuple[CorpusReference, ...]:
    if not window:
        return ()
    return tuple(
        CorpusReference(name=name.decode("ascii"), sha256=digest.decode("ascii"))
        for name, digest in _REFERENCE_TAG.findall(window)
    )


class CorpusPool:
    def __init__(self, samples: tuple[CorpusSample, ...]):
        if not samples:
            raise CorpusError("at least one corpus sample is required")
        if len({sample.sha256 for sample in samples}) != len(samples):
            raise CorpusError("duplicate corpus samples are not allowed")
        self.samples = samples

    @classmethod
    def load(cls, paths: tuple[Path, ...]) -> CorpusPool:
        samples: list[CorpusSample] = []
        for path in paths:
            selected = path.resolve()
            if selected.suffix.lower() not in {".js", ".mjs", ".cjs"}:
                raise CorpusError("corpus inputs must be explicit JavaScript files")
            if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", selected.name) is None:
                raise CorpusError("corpus input filename is unsafe for provenance markup")
            try:
                data = selected.read_bytes()
            except OSError as exc:
                raise CorpusError("a selected corpus file cannot be read") from exc
            if not data or len(data) > MAX_SAMPLE_BYTES:
                raise CorpusError("a selected corpus sample has an invalid size")
            try:
                data.decode("utf-8", errors="strict")
            except UnicodeError as exc:
                raise CorpusError("a selected corpus sample is not UTF-8") from exc
            samples.append(
                CorpusSample(
                    name=selected.name,
                    sha256=hashlib.sha256(data).hexdigest(),
                    data=data,
                )
            )
        return cls(tuple(samples))

    def build_window(self, *, seed: int, size: int) -> bytes:
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError("corpus seed must be an integer")
        if isinstance(size, bool) or not isinstance(size, int) or size < 1:
            raise ValueError("corpus window size must be positive")
        selected_size = min(size, len(self.samples))
        selected = random.Random(seed).sample(self.samples, selected_size)
        parts = [
            b"CORPUS ROLE: historical reference data only. Do not copy, mutate, ",
            b"combine, or reproduce these programs or their historical defects. ",
            b"Use only broad structural-density cues and design an independent program.\n",
        ]
        for sample in selected:
            parts.extend(
                (
                    (
                        f'<historical-js-example name="{sample.name}" '
                        f'sha256="{sample.sha256}">\n'
                    ).encode(),
                    sample.data,
                    b"\n</historical-js-example>\n",
                )
            )
        return b"".join(parts)
