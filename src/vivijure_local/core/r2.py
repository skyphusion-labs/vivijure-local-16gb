"""Minimal R2 (S3-compatible) object I/O for the local backend.

The local backend SHARES the studio's `vivijure` R2 bucket (the same seam own-gpu uses): it reads the
keyframe by key and writes the finished clip by key, so the module worker never downloads or re-uploads.
This is the only credential the backend holds (the SECURITY boundary: one R2 key, control-plane-trusted
input), mirroring vivijure-backend's harness/r2.py.

The store is Cloudflare R2 by default (the endpoint is derived from the account id). Point it at any
S3-compatible store (MinIO, another provider) for the self-host path by setting `R2_S3_ENDPOINT`, the
same first-class config var the studio exposes; the account id then only names the bucket owner. This
mirrors the studio: the endpoint is an identifier, not a secret.

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
    s3_endpoint: str | None = None

    @classmethod
    def from_env(cls) -> "R2Config":
        """Read the R2 creds from the environment. Presence-checked (a missing value fails loud at
        startup, before the socket binds), never echoed. `R2_S3_ENDPOINT` is OPTIONAL: unset means the
        Cloudflare R2 endpoint derived from the account id (today's behavior); set it to a MinIO / other
        S3 endpoint for the self-host path."""
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
            s3_endpoint=os.environ.get("R2_S3_ENDPOINT") or None,
        )

    @property
    def endpoint_url(self) -> str:
        """The S3 endpoint to talk to: the `R2_S3_ENDPOINT` override when set, else the Cloudflare R2
        endpoint derived from the account id (backward-compatible: unset == today)."""
        if self.s3_endpoint:
            return self.s3_endpoint
        return f"https://{self.account_id}.r2.cloudflarestorage.com"


class R2:
    """A thin get_file / put_file wrapper over an S3 client pointed at R2. Created once per process."""

    def __init__(self, config: R2Config) -> None:
        import boto3  # deferred: only the GPU runtime image carries boto3

        self.config = config
        client_config = None
        if config.s3_endpoint:
            # A custom S3 endpoint (MinIO / self-host) needs path-style addressing: MinIO does not
            # resolve the virtual-hosted `bucket.host` form. Cloudflare R2 (the unset default) keeps
            # boto3's default addressing, so the working CF path is untouched.
            from botocore.client import Config as BotocoreConfig

            client_config = BotocoreConfig(s3={"addressing_style": "path"})
        self._client = boto3.client(
            "s3",
            endpoint_url=config.endpoint_url,
            aws_access_key_id=config.access_key_id,
            aws_secret_access_key=config.secret_access_key,
            region_name="auto",
            config=client_config,
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
