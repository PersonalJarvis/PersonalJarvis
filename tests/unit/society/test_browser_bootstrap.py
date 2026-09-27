"""Bootstrap downloads retain verified TLS in a frozen application."""

import hashlib
import io
import json
import os
import ssl
import zipfile

import pytest

from jarvis.society.browser import bootstrap


@pytest.mark.parametrize("corrupt", [False, True])
def test_both_downloads_use_verified_certificates_and_validate_the_wheel(
    monkeypatch, tmp_path, corrupt
):
    monkeypatch.setattr(bootstrap.shutil, "which", lambda _: None)
    monkeypatch.setattr(bootstrap.platform, "machine", lambda: "x86_64")
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    target = "uv.exe" if os.name == "nt" else "uv"
    platform_tag = (
        "win_amd64"
        if os.name == "nt"
        else "macosx_x86_64"
        if bootstrap.sys.platform == "darwin"
        else "manylinux_x86_64"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("uv/bin/" + target, b"verified test executable")
    wheel = buffer.getvalue()
    metadata = json.dumps(
        {
            "urls": [
                {
                    "filename": "uv-" + platform_tag + ".whl",
                    "url": "https://files.pythonhosted.org/uv-test.whl",
                    "digests": {
                        "sha256": "0" * 64 if corrupt else hashlib.sha256(wheel).hexdigest()
                    },
                }
            ]
        }
    ).encode()
    contexts = []

    def urlopen(url, *, timeout, context):
        assert timeout > 0
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname
        assert context.cert_store_stats()["x509_ca"] > 0
        contexts.append(context)
        return io.BytesIO(metadata if url.endswith("/json") else wheel)

    monkeypatch.setattr(bootstrap.urllib.request, "urlopen", urlopen)
    if corrupt:
        with pytest.raises(RuntimeError, match="checksum mismatch"):
            bootstrap.ensure_uv(tmp_path)
        assert not (tmp_path / target).exists()
    else:
        assert bootstrap.ensure_uv(tmp_path) == str(tmp_path / target)
        assert (tmp_path / target).read_bytes() == b"verified test executable"
    assert len(contexts) == 2 and contexts[0] is contexts[1]
