"""Fail-closed loading for Fuzzynth's external provider credentials."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import stat
from urllib.parse import urlsplit, urlunsplit


DEFAULT_CREDENTIALS_PATH = Path("/root/fuzzynth_openai_credentials")
OFFICIAL_OPENAI_BASE_URL = "https://api.openai.com/v1"


class CredentialError(RuntimeError):
    """A credentials failure whose message is safe to display."""


@dataclass(frozen=True, slots=True)
class ProviderCredentials:
    """One provider endpoint and its non-displayable API key."""

    name: str
    base_url: str
    api_key: str = field(repr=False)

    def safe_status(self) -> dict[str, str]:
        return {
            "name": self.name,
            "base_url": "configured",
            "api_key": "configured",
        }


@dataclass(frozen=True, slots=True)
class CredentialStore:
    alternate: ProviderCredentials
    official: ProviderCredentials

    def safe_status(self) -> list[dict[str, str]]:
        return [self.alternate.safe_status(), self.official.safe_status()]


def _read_env_file(path: Path) -> dict[str, str]:
    try:
        metadata = path.stat()
    except OSError as exc:
        raise CredentialError(f"credentials file is unavailable: {path}") from exc

    mode = stat.S_IMODE(metadata.st_mode)
    if mode & 0o077:
        raise CredentialError(
            f"credentials file must not be group/world accessible: {path}"
        )

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise CredentialError(f"credentials file cannot be read: {path}") from exc

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise CredentialError(f"invalid entry at {path}:{line_number}")

        key, value = line.split("=", maxsplit=1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if not key or not value:
            raise CredentialError(f"empty entry at {path}:{line_number}")
        values[key] = value

    return values


def _required(values: dict[str, str], name: str) -> str:
    value = values.get(name, "")
    if not value:
        raise CredentialError(f"credentials file is missing required field {name}")
    return value


def _normalize_https_url(value: str, field_name: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise CredentialError(f"{field_name} must be an absolute HTTPS URL")
    if parsed.username or parsed.password:
        raise CredentialError(f"{field_name} must not contain URL credentials")
    if parsed.query or parsed.fragment:
        raise CredentialError(f"{field_name} must not contain query or fragment data")

    normalized_path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, normalized_path, "", ""))


def load_credentials(path: Path | None = None) -> CredentialStore:
    """Load both providers without environment fallback or cross-routing keys."""

    selected_path = path or Path(
        os.environ.get("FUZZYNTH_OPENAI_CREDENTIALS", str(DEFAULT_CREDENTIALS_PATH))
    )
    values = _read_env_file(selected_path)

    alternate = ProviderCredentials(
        name="alternate",
        base_url=_normalize_https_url(
            _required(values, "ALTERNATE_OPENAI_BASE_URL"),
            "ALTERNATE_OPENAI_BASE_URL",
        ),
        api_key=_required(values, "ALTERNATE_OPENAI_API_KEY"),
    )
    official = ProviderCredentials(
        name="official",
        base_url=OFFICIAL_OPENAI_BASE_URL,
        api_key=_required(values, "OPENAI_API_KEY"),
    )
    return CredentialStore(alternate=alternate, official=official)
