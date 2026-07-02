"""Minimal R2 (S3-compatible) object I/O for the local backend.

The local backend SHARES the studio's `vivijure` R2 bucket (the same seam own-gpu uses): it reads the
keyframe by key and writes the finished clip by key, so the module worker never downloads or re-uploads.
This is the only credential the backend holds (the SECURITY boundary: one R2 key, control-plane-trusted
input), mirroring vivijure-backend's harness/r2.py.

boto3 is imported lazily so the rest of the package stays importable on a CPU box without the GPU image's
deps; the tests inject a fake store and never touch this module.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class R2Config:
    account_id: str
    access_key_id: str
    secret_access_key: str
    bucket: str

    @classmethod
    def from_env(cls) -> "R2Config":
        """Read the R2 creds from the environment. Presence-checked (a missing value fails loud at
        startup, before the socket binds), never echoed."""
        missing = [
            k for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")
            if not os.environ.get(k)
        ]
        if missing:
            raise RuntimeError(f"R2 not configured: missing {', '.join(missing)}")
        return cls(
            account_id=os.environ["R2_ACCOUNT_ID"],
            access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            bucket=os.environ["R2_BUCKET"],
        )

    @property
    def endpoint_url(self) -> str:
        return f"https://{self.account_id}.r2.cloudflarestorage.com"


class R2:
    """A thin get_file / put_file wrapper over an S3 client pointed at R2. Created once per process."""

    def __init__(self, config: R2Config) -> None:
        import boto3  # deferred: only the GPU runtime image carries boto3

        self.config = config
        self._client = boto3.client(
            "s3",
            endpoint_url=config.endpoint_url,
            aws_access_key_id=config.access_key_id,
            aws_secret_access_key=config.secret_access_key,
            region_name="auto",
        )

    def get_file(self, key: str, dest: Path) -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._client.download_file(self.config.bucket, key, str(dest))
        return dest

    def put_file(self, src: Path, key: str, *, content_type: str | None = None) -> str:
        extra = {"ContentType": content_type} if content_type else {}
        self._client.upload_file(str(Path(src)), self.config.bucket, key, ExtraArgs=extra or None)
        return key

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.config.bucket, Key=key)
            return True
        except Exception:
            return False
