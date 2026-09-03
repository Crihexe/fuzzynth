"""Small explicit corpus pools for bounded, provenance-preserving test runs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import random
import re
import sqlite3


class CorpusError(RuntimeError):
    """An explicit corpus selection is invalid or exceeds local boundaries."""


MAX_SAMPLE_BYTES = 64 * 1024
INDEX_MAX_SAMPLE_BYTES = 48 * 1024
MIN_INDEX_POOL_SAMPLES = 1_000
MAX_WINDOW_SOURCE_BYTES = 120 * 1024
_INDEX_ALLOWED_ENGINES = (
    "V8",
    "WebAssembly/V8",
    "JavaScript engine unclassified",
    "Chromium/JavaScript",
)


@dataclass(frozen=True, slots=True)
class CorpusSample:
    name: str
    sha256: str
    data: bytes
    primary_category: str = "unknown"
    engine_family: str = "unknown"
    syntax_profile: str = "ecmascript"
    contains_exploit_markers: bool = False
    contains_wasm_markers: bool = False
    tags: frozenset[str] = frozenset()
    required_flags: frozenset[str] = frozenset()


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

    @classmethod
    def load_index(cls, index_path: Path) -> CorpusPool:
        """Load a large, statically validated corpus from the preview-v3 index."""

        selected_index = index_path.resolve()
        files_root = selected_index.parent / "files"
        if not selected_index.is_file() or not files_root.is_dir():
            raise CorpusError("corpus index or files directory is unavailable")
        try:
            connection = sqlite3.connect(
                f"file:{selected_index}?mode=ro",
                uri=True,
            )
        except sqlite3.Error as exc:
            raise CorpusError("corpus index cannot be opened read-only") from exc
        try:
            integrity = connection.execute("PRAGMA quick_check").fetchone()
            if integrity != ("ok",):
                raise CorpusError("corpus index integrity check failed")
            build_version = connection.execute(
                "SELECT value FROM build_info WHERE key = 'schema_version'"
            ).fetchone()
            if build_version != ("2",):
                raise CorpusError("unsupported corpus index schema version")
            placeholders = ",".join("?" for _ in _INDEX_ALLOWED_ENGINES)
            rows = connection.execute(
                f"""
                WITH eligible AS (
                  SELECT filename, sha256, size_bytes, normalized_sha256,
                         preserved_original_available,
                         primary_category, engine_family, syntax_profile,
                         contains_exploit_markers, contains_wasm_markers,
                         tags, required_flags,
                         ROW_NUMBER() OVER (
                           PARTITION BY normalized_sha256
                           ORDER BY preserved_original_available DESC,
                                    size_bytes ASC, artifact_id
                         ) AS normalized_rank
                  FROM artifacts
                  WHERE validation_status = 'accepted'
                    AND size_bytes BETWEEN 1 AND ?
                    AND primary_category != 'support_harness'
                    AND engine_family IN ({placeholders})
                )
                SELECT filename, sha256, size_bytes, primary_category,
                       engine_family, syntax_profile, contains_exploit_markers,
                       contains_wasm_markers, tags, required_flags
                FROM eligible
                WHERE normalized_rank = 1
                ORDER BY sha256
                """,
                (INDEX_MAX_SAMPLE_BYTES, *_INDEX_ALLOWED_ENGINES),
            ).fetchall()
        except sqlite3.Error as exc:
            raise CorpusError("corpus index schema or query is invalid") from exc
        finally:
            connection.close()
        if len(rows) < MIN_INDEX_POOL_SAMPLES:
            raise CorpusError("corpus index has fewer than 1000 eligible samples")

        samples: list[CorpusSample] = []
        for (
            filename,
            expected_sha256,
            expected_size,
            primary_category,
            engine_family,
            syntax_profile,
            contains_exploit_markers,
            contains_wasm_markers,
            tags,
            required_flags,
        ) in rows:
            if (
                not isinstance(filename, str)
                or re.fullmatch(r"[A-Za-z0-9._-]{1,128}", filename) is None
                or not filename.endswith(".js")
                or not isinstance(expected_sha256, str)
                or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
                or not isinstance(expected_size, int)
                or not isinstance(primary_category, str)
                or not isinstance(engine_family, str)
                or not isinstance(syntax_profile, str)
                or contains_exploit_markers not in {0, 1}
                or contains_wasm_markers not in {0, 1}
                or not isinstance(tags, str)
                or not isinstance(required_flags, str)
            ):
                raise CorpusError("corpus index contains invalid artifact metadata")
            source = (files_root / filename).resolve()
            if source.parent != files_root.resolve():
                raise CorpusError("corpus index resolves outside its files directory")
            try:
                data = source.read_bytes()
            except OSError as exc:
                raise CorpusError("an indexed corpus file cannot be read") from exc
            if len(data) != expected_size:
                raise CorpusError("an indexed corpus file has an unexpected size")
            actual_sha256 = hashlib.sha256(data).hexdigest()
            if actual_sha256 != expected_sha256:
                raise CorpusError("an indexed corpus file failed SHA-256 validation")
            try:
                data.decode("utf-8", errors="strict")
            except UnicodeError as exc:
                raise CorpusError("an indexed corpus file is not UTF-8") from exc
            samples.append(
                CorpusSample(
                    name=filename,
                    sha256=actual_sha256,
                    data=data,
                    primary_category=primary_category,
                    engine_family=engine_family,
                    syntax_profile=syntax_profile,
                    contains_exploit_markers=bool(contains_exploit_markers),
                    contains_wasm_markers=bool(contains_wasm_markers),
                    tags=frozenset(
                        item.strip().lower()
                        for item in re.split(r"[;,]", tags)
                        if item.strip()
                    ),
                    required_flags=frozenset(
                        item.strip()
                        for item in re.split(r"[;,]", required_flags)
                        if item.strip()
                    ),
                )
            )
        return cls(tuple(samples))

    @staticmethod
    def _stratified_groups(sample: CorpusSample) -> tuple[str, ...]:
        groups: list[str] = []
        if sample.primary_category in {
            "javascript_security_artifact",
            "issue_attachment",
            "issue_inline_reproducer",
            "poc_or_reproducer",
            "exploit",
            "search_discovered_candidate",
        } or sample.contains_exploit_markers:
            groups.append("security")
        if sample.contains_wasm_markers or sample.engine_family == "WebAssembly/V8":
            groups.append("wasm")
        if sample.syntax_profile == "v8_d8_intrinsics" or sample.tags.intersection(
            {"maglev", "turbofan", "turboshaft", "sparkplug", "liftoff"}
        ):
            groups.append("compiler")
        source = sample.data.lower()
        if sample.tags.intersection({"sandbox", "gc", "heap", "shellcode"}) or any(
            marker in source
            for marker in (b"arraybuffer", b"sharedarraybuffer", b"weakref", b"gc(")
        ):
            groups.append("memory")
        if any(
            marker in source
            for marker in (b"sharedarraybuffer", b"atomics.", b"worker(")
        ):
            groups.append("concurrency")
        if sample.primary_category == "regression_test":
            groups.append("regression")
        else:
            groups.append("non_regression")
        return tuple(groups)

    def _select_stratified(
        self,
        *,
        generator: random.Random,
        size: int,
        samples: tuple[CorpusSample, ...],
    ) -> list[CorpusSample]:
        buckets: dict[str, list[CorpusSample]] = {
            name: []
            for name in (
                "security",
                "wasm",
                "compiler",
                "memory",
                "concurrency",
                "regression",
                "non_regression",
            )
        }
        for sample in samples:
            for group in self._stratified_groups(sample):
                buckets[group].append(sample)
        selected: list[CorpusSample] = []
        selected_hashes: set[str] = set()
        for group in buckets:
            candidates = [
                sample
                for sample in buckets[group]
                if sample.sha256 not in selected_hashes
            ]
            if candidates and len(selected) < size:
                choice = generator.choice(candidates)
                selected.append(choice)
                selected_hashes.add(choice.sha256)
        remaining = [
            sample for sample in samples if sample.sha256 not in selected_hashes
        ]
        if len(selected) < size:
            selected.extend(generator.sample(remaining, size - len(selected)))
        generator.shuffle(selected)
        return selected

    def build_window(
        self,
        *,
        seed: int,
        size: int,
        strategy: str = "uniform",
    ) -> bytes:
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError("corpus seed must be an integer")
        if isinstance(size, bool) or not isinstance(size, int) or size < 1:
            raise ValueError("corpus window size must be positive")
        if strategy not in {"uniform", "stratified_v8"}:
            raise ValueError("unsupported corpus window strategy")
        selected_size = min(size, len(self.samples))
        per_sample_limit = MAX_WINDOW_SOURCE_BYTES // selected_size
        eligible = tuple(
            sample for sample in self.samples if len(sample.data) <= per_sample_limit
        )
        if len(eligible) < selected_size:
            raise CorpusError("too few samples fit the bounded corpus window")
        generator = random.Random(seed)
        selected = (
            generator.sample(eligible, selected_size)
            if strategy == "uniform"
            else self._select_stratified(
                generator=generator,
                size=selected_size,
                samples=eligible,
            )
        )
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
