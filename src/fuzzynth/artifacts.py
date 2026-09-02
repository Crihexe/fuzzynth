"""Content-addressed byte storage for exact generation and execution evidence."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import tempfile


class ArtifactIntegrityError(RuntimeError):
    """Stored bytes do not match their content address."""


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    sha256: str
    size: int
    relative_path: str


class ArtifactStore:
    """Store immutable bytes once and return a stable SHA-256 reference."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    def _path(self, digest: str) -> Path:
        return self.root / digest[:2] / digest[2:]

    @staticmethod
    def _digest(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def put(self, data: bytes) -> ArtifactRef:
        if not isinstance(data, bytes):
            raise TypeError("artifacts must be exact bytes")
        digest = self._digest(data)
        destination = self._path(digest)
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

        if destination.exists():
            self._verify_path(destination, digest, len(data))
        else:
            temporary_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    prefix=".incoming-",
                    dir=destination.parent,
                    delete=False,
                ) as stream:
                    temporary_path = Path(stream.name)
                    os.chmod(stream.fileno(), 0o600)
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(temporary_path, destination)
                except FileExistsError:
                    self._verify_path(destination, digest, len(data))
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)

        return ArtifactRef(
            sha256=digest,
            size=len(data),
            relative_path=str(destination.relative_to(self.root)),
        )

    def read(self, reference: ArtifactRef) -> bytes:
        if reference.relative_path != str(
            Path(reference.sha256[:2]) / reference.sha256[2:]
        ):
            raise ArtifactIntegrityError("artifact reference path is not canonical")
        path = self.root / reference.relative_path
        data = path.read_bytes()
        if len(data) != reference.size or self._digest(data) != reference.sha256:
            raise ArtifactIntegrityError("artifact bytes failed integrity verification")
        return data

    @staticmethod
    def _verify_path(path: Path, digest: str, expected_size: int) -> None:
        stat = path.stat()
        if not path.is_file() or stat.st_size != expected_size:
            raise ArtifactIntegrityError("existing artifact has invalid type or size")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != digest:
            raise ArtifactIntegrityError("existing artifact failed hash verification")
