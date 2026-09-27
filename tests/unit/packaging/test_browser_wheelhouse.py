"""A packaged native wheel remains tied to the source version and content hash."""

import hashlib

import pytest

from jarvis.society.browser import install
from scripts.prepare_browser_wheelhouse import add_wheel_hash, unbundled_libraries


def test_macho_identity_is_not_an_external_library():
    identity = "@rpath/cryptography.hazmat.bindings._rust.abi3.so"
    links = (
        "_rust.abi3.so:\n"
        f"\t{identity} (compatibility version 0.0.0, current version 0.0.0)\n"
        "\t/usr/lib/libiconv.2.dylib (compatibility version 7.0.0)\n"
        "\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0)\n"
    )
    assert unbundled_libraries(links, f"_rust.abi3.so:\n{identity}\n") == []
    links += "\t@rpath/libssl.3.dylib (compatibility version 3.0.0)\n"
    assert unbundled_libraries(links, f"_rust.abi3.so:\n{identity}\n") == ["@rpath/libssl.3.dylib"]


def test_built_wheel_adds_its_digest_without_changing_other_pins(tmp_path):
    lock = "cryptography==50.0.1 \\\n    --hash=sha256:upstream\nother==1.0\n"
    wheel = tmp_path / "cryptography-50.0.1-cp311-abi3-macosx_15_0_x86_64.whl"
    wheel.write_bytes(b"built native artifact")
    result = add_wheel_hash(lock, wheel)
    assert hashlib.sha256(wheel.read_bytes()).hexdigest() in result
    assert "--hash=sha256:upstream" in result
    assert result.endswith("other==1.0\n")
    assert result.count("cryptography==50.0.1") == 1


def test_wheel_from_another_version_is_rejected(tmp_path):
    wheel = tmp_path / "cryptography-48.0.1-cp311-abi3-macosx_10_9_universal2.whl"
    wheel.write_bytes(b"wrong version")
    with pytest.raises(ValueError, match="locked cryptography version"):
        add_wheel_hash("cryptography==50.0.1 \\\n    --hash=sha256:upstream\n", wheel)


def test_only_frozen_app_uses_bundled_lock_and_wheelhouse(tmp_path, monkeypatch):
    module = tmp_path / "jarvis" / "society" / "browser" / "install.py"
    assets = tmp_path / "jarvis" / "assets" / "browser"
    assets.mkdir(parents=True)
    bundled = assets / "requirements-bundled.lock"
    bundled.write_text("cryptography==50.0.1", encoding="utf-8")
    (assets / "wheels").mkdir()
    monkeypatch.setattr(install, "__file__", str(module))
    monkeypatch.setattr(install.sys, "frozen", False, raising=False)
    assert install.requirements_path() == assets / "requirements.lock"
    assert install.bundled_wheel_args() == []
    monkeypatch.setattr(install.sys, "frozen", True)
    assert install.requirements_path() == bundled
    assert install.bundled_wheel_args() == [
        "--find-links",
        str(assets / "wheels"),
        "--only-binary",
        "cryptography",
    ]
