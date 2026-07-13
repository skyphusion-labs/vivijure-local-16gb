"""R2Config endpoint selection: Cloudflare R2 by default, an S3 override for the self-host path.

No network and no real boto3: `R2` defers `import boto3` (and `botocore.client`) to construction, so a
fake captures the exact kwargs the client is built with. This proves the `R2_S3_ENDPOINT` seam without
touching a store, the same way the rest of the CPU suite injects fakes.
"""
from __future__ import annotations

import sys
import types

from vivijure_local.core.r2 import R2, R2Config


def _base_env(monkeypatch, **extra):
    monkeypatch.setenv("R2_ACCOUNT_ID", "acct123")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "sk")
    monkeypatch.setenv("R2_BUCKET", "vivijure")
    for k, v in extra.items():
        monkeypatch.setenv(k, v)


def test_endpoint_unset_derives_the_cloudflare_r2_url(monkeypatch):
    monkeypatch.delenv("R2_S3_ENDPOINT", raising=False)
    _base_env(monkeypatch)
    cfg = R2Config.from_env()
    assert cfg.s3_endpoint is None
    assert cfg.endpoint_url == "https://acct123.r2.cloudflarestorage.com"


def test_endpoint_override_is_honored_when_set(monkeypatch):
    _base_env(monkeypatch, R2_S3_ENDPOINT="https://minio.local:9000")
    cfg = R2Config.from_env()
    assert cfg.s3_endpoint == "https://minio.local:9000"
    # The override wins over the account-id-derived Cloudflare URL.
    assert cfg.endpoint_url == "https://minio.local:9000"


def test_blank_endpoint_is_treated_as_unset(monkeypatch):
    _base_env(monkeypatch, R2_S3_ENDPOINT="")
    cfg = R2Config.from_env()
    assert cfg.s3_endpoint is None
    assert cfg.endpoint_url == "https://acct123.r2.cloudflarestorage.com"


class _FakeClient:
    pass


class _FakeBoto3:
    def __init__(self):
        self.kwargs = None

    def client(self, service, **kwargs):
        self.kwargs = {"service": service, **kwargs}
        return _FakeClient()


class _FakeBotocoreConfig:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def _install_fake_boto3(monkeypatch):
    fake = _FakeBoto3()
    monkeypatch.setitem(sys.modules, "boto3", fake)
    botocore = types.ModuleType("botocore")
    botocore_client = types.ModuleType("botocore.client")
    botocore_client.Config = _FakeBotocoreConfig
    botocore.client = botocore_client
    monkeypatch.setitem(sys.modules, "botocore", botocore)
    monkeypatch.setitem(sys.modules, "botocore.client", botocore_client)
    return fake


def test_custom_endpoint_applies_path_style_addressing(monkeypatch):
    fake = _install_fake_boto3(monkeypatch)
    cfg = R2Config(
        account_id="acct123",
        access_key_id="ak",
        secret_access_key="sk",
        bucket="vivijure",
        s3_endpoint="https://minio.local:9000",
    )
    R2(cfg)
    assert fake.kwargs["endpoint_url"] == "https://minio.local:9000"
    client_config = fake.kwargs["config"]
    assert isinstance(client_config, _FakeBotocoreConfig)
    assert client_config.kwargs["s3"] == {"addressing_style": "path"}


def test_default_endpoint_leaves_addressing_untouched(monkeypatch):
    fake = _install_fake_boto3(monkeypatch)
    cfg = R2Config(
        account_id="acct123",
        access_key_id="ak",
        secret_access_key="sk",
        bucket="vivijure",
    )
    R2(cfg)
    # The working Cloudflare R2 path passes no botocore Config (boto3's default addressing).
    assert fake.kwargs["endpoint_url"] == "https://acct123.r2.cloudflarestorage.com"
    assert fake.kwargs["config"] is None
