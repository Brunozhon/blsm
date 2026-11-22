from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    host: str
    port: int
    allowed_origins: tuple[str, ...]
    admin_password: str | None
    frontend_dir: Path | None = None

    @classmethod
    def from_env(cls) -> Settings:
        data_dir = Path(os.getenv("BLSM_DATA_DIR", "./data")).expanduser().resolve()
        frontend_value = os.getenv("BLSM_FRONTEND_DIR")
        frontend_dir = (
            Path(frontend_value).expanduser().resolve() if frontend_value else None
        )
        origins = tuple(
            value.strip()
            for value in os.getenv(
                "BLSM_ALLOWED_ORIGINS",
                "http://localhost:5173,http://127.0.0.1:5173",
            ).split(",")
            if value.strip()
        )
        return cls(
            data_dir=data_dir,
            host=os.getenv("BLSM_HOST", "0.0.0.0"),
            port=int(os.getenv("BLSM_PORT", "8080")),
            allowed_origins=origins,
            admin_password=os.getenv("BLSM_ADMIN_PASSWORD"),
            frontend_dir=frontend_dir,
        )

    @property
    def database_path(self) -> Path:
        return self.data_dir / "blsm.db"

    @property
    def servers_dir(self) -> Path:
        return self.data_dir / "servers"

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.servers_dir.mkdir(parents=True, exist_ok=True, mode=0o700)


def read_or_create_secret(data_dir: Path) -> bytes:
    path = data_dir / ".session-secret"
    if path.exists():
        return path.read_bytes()
    secret = secrets.token_bytes(32)
    path.write_bytes(secret)
    path.chmod(0o600)
    return secret
