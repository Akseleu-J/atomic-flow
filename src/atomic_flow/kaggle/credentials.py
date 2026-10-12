"""Load Kaggle Model Proxy credentials without exposing them in logs or config."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from atomic_flow.config import user_dir
from atomic_flow.errors import ConfigurationError

SECRET_NAMES = {"MODEL_PROXY_API_KEY"}


def secret_file() -> Path:
    """Resolved lazily: the old module-level constant froze ATOMIC_FLOW_HOME at import time."""
    return user_dir() / "kaggle.env"


def parse_env_file(path: Path) -> dict[str, str]:
    """Read a dotenv-style file; never evaluate shell syntax."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ConfigurationError(f"Cannot read Kaggle credential file {path}: {exc}") from exc
    for line in lines:
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        if value.startswith("export "):
            value = value[7:].lstrip()
        name, sep, raw = value.partition("=")
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name.strip()):
            continue
        raw = raw.strip()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {"'", '"'}:
            raw = raw[1:-1]
        values[name.strip()] = raw
    return values


def _expiry(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        numeric = float(raw)
        if numeric > 1_000_000_000:
            return datetime.fromtimestamp(numeric, tz=UTC)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


@dataclass(frozen=True)
class KaggleCredentials:
    url: str
    api_key: str = field(repr=False)
    expires_at: datetime | None
    allowed_models: tuple[str, ...]
    default_model: str
    source: str

    @property
    def expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= datetime.now(UTC)

    @property
    def expires_soon(self) -> bool:
        return self.expires_at is not None and (self.expires_at - datetime.now(UTC)).total_seconds() < 120


def load_credentials(path: Path | None = None, *, required: bool = True) -> KaggleCredentials | None:
    """Environment variables win over the private local env file."""
    env_values = parse_env_file(path or secret_file())
    keys = {"MODEL_PROXY_URL", "MODEL_PROXY_API_KEY", "MODEL_PROXY_EXPIRY_TIME", "LLM_DEFAULT", "LLMS_AVAILABLE"}
    merged = {key: env_values.get(key, "") for key in keys}
    for key in keys:
        if os.getenv(key):
            merged[key] = os.environ[key]
    url, api_key = merged["MODEL_PROXY_URL"].strip(), merged["MODEL_PROXY_API_KEY"].strip()
    if not url or not api_key:
        if required:
            raise ConfigurationError("Kaggle Model Proxy credentials are missing. Run `atomic kaggle auth` or set MODEL_PROXY_URL and MODEL_PROXY_API_KEY.")
        return None
    allowed = tuple(item.strip() for item in merged["LLMS_AVAILABLE"].split(",") if item.strip())
    return KaggleCredentials(url=url, api_key=api_key, expires_at=_expiry(merged["MODEL_PROXY_EXPIRY_TIME"]),
                             allowed_models=allowed, default_model=merged["LLM_DEFAULT"].strip(),
                             source="environment" if os.getenv("MODEL_PROXY_API_KEY") else str(path or secret_file()))


def credential_status(path: Path | None = None) -> dict[str, object]:
    credentials = load_credentials(path, required=False)
    if credentials is None:
        return {"configured": False, "source": None, "expires_at": None, "expired": False, "expires_soon": False,
                "models_count": 0, "models": [], "default_model": None}
    return {"configured": True, "source": credentials.source, "expires_at": credentials.expires_at.isoformat() if credentials.expires_at else None,
            "expired": credentials.expired, "expires_soon": credentials.expires_soon,
            "models_count": len(credentials.allowed_models), "models": list(credentials.allowed_models),
            "default_model": credentials.default_model or None}


def prepare_secret_file(path: Path) -> None:
    """Create the file as 0600 BEFORE the Kaggle CLI writes the key into it (closes the chmod-after race)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)


def protect_secret_file(path: Path) -> None:
    """Restrict credential-file access to the current user where supported."""
    if os.name == "nt":
        try:
            account = subprocess.run(["whoami"], capture_output=True, text=True, check=True, timeout=5).stdout.strip()
            subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{account}:(F)"],
                           capture_output=True, text=True, check=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ConfigurationError(f"Could not restrict access to Kaggle credentials at {path}. Check its Windows ACL manually.") from exc
        return
    try:
        path.chmod(0o600)
    except OSError as exc:
        raise ConfigurationError(f"Could not set private permissions on Kaggle credentials at {path}.") from exc


def sanitize_cli_output(value: str, secret: str = "") -> str:
    if secret:
        value = value.replace(secret, "[REDACTED]")
    value = re.sub(r"(?im)(MODEL_PROXY_API_KEY\s*=\s*)\S+", r"\1[REDACTED]", value)
    value = re.sub(r"(?i)(authorization\s*:\s*bearer\s+)\S+", r"\1[REDACTED]", value)
    return value
