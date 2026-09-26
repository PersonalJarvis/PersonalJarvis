# jarvis/cli_ctl/client.py
"""Thin httpx wrapper that speaks to a Jarvis server with the control key."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urlsplit

import httpx

_TRANSFER_CHUNK = 65536


class _BoundedUpload:
    """httpx multipart file adapter with a fixed, verified byte count."""

    def __init__(self, source: BinaryIO, size: int):
        self.source, self.size, self.sent = source, size, 0

    def fileno(self) -> int:
        return self.source.fileno()

    def tell(self) -> int:
        return self.source.tell()

    def seek(self, offset: int, whence: int = 0) -> int:
        position = self.source.seek(offset, whence)
        self.sent = position
        return position

    def read(self, size: int = -1) -> bytes:
        chunk = self.source.read(min(size, _TRANSFER_CHUNK) if size >= 0 else _TRANSFER_CHUNK)
        self.sent += len(chunk)
        if self.sent > self.size or (not chunk and self.sent != self.size):
            raise ApiError(
                "The upload file changed size during transfer; retry a stable backup.", 400
            )
        return chunk


class ApiError(Exception):
    """A request failed. `status_code` is None for transport-level failures.

    ``base_url`` is set on transport failures so callers can run the
    cause-specific unreachable diagnosis (`jarvis.cli_ctl.doctor`).
    """

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        payload: Any = None,
        base_url: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.payload = payload
        self.base_url = base_url


class JarvisClient:
    def __init__(
        self,
        base_url: str,
        control_key: str | None,
        *,
        timeout: float = 30.0,
        connect_timeout: float = 2.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {}
        if control_key:
            headers["Authorization"] = f"Bearer {control_key}"
        # Split timeouts: a down/absent server must fail in ~2 s (connect),
        # while a legitimately slow endpoint may still stream for `timeout`.
        self._client = httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=httpx.Timeout(timeout, connect=min(connect_timeout, timeout)),
            transport=transport,
        )
        self.base_url = base_url
        self.has_auth = bool(control_key)
        self._connect_timeout = min(connect_timeout, timeout)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        timeout_s: float | None = None,
    ) -> Any:
        try:
            request_kwargs: dict[str, Any] = {"params": params, "json": json}
            if timeout_s is not None:
                bounded_timeout = max(0.1, float(timeout_s))
                request_kwargs["timeout"] = httpx.Timeout(
                    bounded_timeout,
                    connect=min(self._connect_timeout, bounded_timeout),
                )
            resp = self._client.request(method.upper(), path, **request_kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            # Deliberately terse: the CLI layers replace this with the
            # cause-specific doctor.unreachable_message (running-but-booting /
            # crashed-stale-session / not-started / remote-target all get
            # DIFFERENT advice — one canned "start the app" is often wrong).
            raise ApiError(
                f"Jarvis at {self.base_url} is unreachable.",
                None,
                base_url=self.base_url,
            ) from exc
        except httpx.TransportError as exc:
            raise ApiError(
                f"Jarvis at {self.base_url} is unreachable: {exc}",
                None,
                base_url=self.base_url,
            ) from exc

        if resp.status_code >= 400:
            detail: Any
            try:
                body = resp.json()
                detail = body.get("detail", body) if isinstance(body, dict) else body
            except ValueError:
                detail = resp.text
            raise ApiError(
                f"HTTP {resp.status_code}: {detail}",
                resp.status_code,
                payload=detail,
            )

        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    def close(self) -> None:
        self._client.close()

    def _transfer_options(self, path: str, timeout_s: float) -> dict[str, Any]:
        parsed = urlsplit(path)
        if (
            parsed.scheme
            or parsed.netloc
            or not path.startswith("/")
            or path.startswith("//")
            or "\\" in path
            or parsed.fragment
        ):
            raise ApiError("Transfers require a relative API path on the configured server.", 400)
        timeout = max(0.1, float(timeout_s))
        return {
            "timeout": httpx.Timeout(timeout, connect=min(self._connect_timeout, timeout)),
            "follow_redirects": False,
        }

    @staticmethod
    def _transfer_response(response: httpx.Response) -> Any:
        """Bound control/error JSON; file payloads never pass through this parser."""
        content = bytearray()
        for chunk in response.iter_bytes(chunk_size=_TRANSFER_CHUNK):
            content.extend(chunk)
            if len(content) > 1048576:
                raise ApiError("Transfer response exceeds the control-response limit.", 502)
        try:
            payload = json.loads(content) if content else None
        except ValueError:
            # A bounded non-JSON control response is still returned or reported below.
            payload = content.decode("utf-8", errors="replace")
        if not 200 <= response.status_code < 300:
            detail = payload.get("detail", payload) if isinstance(payload, dict) else payload
            raise ApiError(
                f"HTTP {response.status_code}: {str(detail)[:2000]}", response.status_code
            )
        return payload

    def upload_file(
        self,
        path: str,
        source: Path,
        *,
        fields: dict[str, str],
        max_bytes: int,
        timeout_s: float = 300,
    ) -> Any:
        """Stream one bounded regular file as multipart, closing it on every exit."""
        options = self._transfer_options(path, timeout_s)
        try:
            if not source.is_file():
                raise ApiError("Select a regular backup file within the upload limit.", 400)
            with source.open("rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= max_bytes:
                    raise ApiError(
                        "Select a nonempty regular backup file within the upload limit.", 400
                    )
                upload = _BoundedUpload(stream, info.st_size)
                with self._client.stream(
                    "POST",
                    path,
                    data=fields,
                    files={"file": ("swarm-backup.zip", upload, "application/zip")},
                    **options,
                ) as response:
                    result = self._transfer_response(response)
                if upload.sent != info.st_size:
                    raise ApiError("The server did not consume the complete upload.", 502)
                return result
        except httpx.TransportError as error:
            raise ApiError(
                f"Jarvis at {self.base_url} is unreachable: {error}",
                base_url=self.base_url,
            ) from error
        except OSError as error:
            raise ApiError(
                f"Cannot read the backup file: {error.strerror or type(error).__name__}", 400
            ) from error

    @staticmethod
    def download_target(destination: Path, *, overwrite: bool = False) -> Path:
        """Check the user-selected destination without creating or opening it."""
        target = destination.expanduser().absolute()
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ApiError("The output must be a regular file, not a link or directory.", 400)
        if target.exists() and not overwrite:
            raise ApiError("The output file exists; choose another path or use --force.", 409)
        if not target.parent.is_dir():
            raise ApiError("The output directory does not exist.", 400)
        return target

    def download_file(
        self,
        path: str,
        destination: Path,
        *,
        expected_size: int,
        expected_sha256: str | None = None,
        max_bytes: int,
        overwrite: bool = False,
        timeout_s: float = 300,
    ) -> dict[str, str]:
        """Stream and verify into a temporary file; never overwrite without permission."""
        options = self._transfer_options(path, timeout_s)
        target = self.download_target(destination, overwrite=overwrite)
        if type(expected_size) is not int or not 0 < expected_size <= max_bytes:
            raise ApiError("The server returned an invalid backup length.", 502)
        if expected_sha256 is not None and (
            not isinstance(expected_sha256, str)
            or len(expected_sha256) != 64
            or any(char not in "0123456789abcdefABCDEF" for char in expected_sha256)
        ):
            raise ApiError("The server returned an invalid backup hash.", 502)
        temporary: Path | None = None
        created_output = False
        output_identity: tuple[int, int] | None = None
        try:
            with self._client.stream(
                "GET",
                path,
                headers={"Accept-Encoding": "identity"},
                **options,
            ) as response:
                if not 200 <= response.status_code < 300:
                    self._transfer_response(response)
                length = response.headers.get("content-length")
                if length is not None and (
                    not length.isascii()
                    or not length.isdigit()
                    or len(length) > 20
                    or int(length) != expected_size
                ):
                    raise ApiError("Backup response length disagrees with its receipt.", 502)
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise ApiError("Backup download requires an unencoded response.", 502)
                header_hash = response.headers.get("x-content-sha256")
                if header_hash is not None and (
                    len(header_hash) != 64
                    or any(char not in "0123456789abcdefABCDEF" for char in header_hash)
                ):
                    raise ApiError("Backup response contains an invalid hash.", 502)
                digest, size = hashlib.sha256(), 0
                with tempfile.NamedTemporaryFile(
                    prefix=".jarvis-transfer-",
                    suffix=".part",
                    dir=target.parent,
                    delete=False,
                ) as output:
                    temporary = Path(output.name)
                    for chunk in response.iter_raw(chunk_size=_TRANSFER_CHUNK):
                        size += len(chunk)
                        if size > expected_size or size > max_bytes:
                            raise ApiError("Backup response exceeds its declared length.", 502)
                        digest.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                actual_hash = digest.hexdigest()
                if size != expected_size or any(
                    value is not None and actual_hash != value.lower()
                    for value in (expected_sha256, header_hash)
                ):
                    raise ApiError("Backup download failed length or SHA-256 verification.", 502)
            # Recheck after a slow transfer. Exclusive creation also closes the
            # no-overwrite race if another process creates the destination now.
            self.download_target(target, overwrite=overwrite)
            if overwrite:
                os.replace(temporary, target)
            else:
                with target.open("xb") as output:
                    created_output = True
                    info = os.fstat(output.fileno())
                    output_identity = (info.st_dev, info.st_ino)
                    with temporary.open("rb") as source:
                        shutil.copyfileobj(source, output, length=_TRANSFER_CHUNK)
                    output.flush()
                    os.fsync(output.fileno())
            created_output = False
            return {"path": str(target), "size_bytes": str(size), "sha256": actual_hash}
        except httpx.TransportError as error:
            raise ApiError(
                f"Jarvis at {self.base_url} is unreachable: {error}",
                base_url=self.base_url,
            ) from error
        except OSError as error:
            raise ApiError(
                f"Cannot save the backup: {error.strerror or type(error).__name__}", 400
            ) from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            if created_output:
                try:
                    current = target.lstat()
                except FileNotFoundError:
                    pass  # Another process already removed the failed output.
                else:
                    if (current.st_dev, current.st_ino) == output_identity:
                        target.unlink()

    def __enter__(self) -> JarvisClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
