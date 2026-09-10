"""Provider-neutral media receipts using the existing Markdown event contract.

Only explicitly returned files inside the chat workspace are archived. Remote
URLs stay remote: this code never fetches an arbitrary tool-supplied address.
Binary tool content is removed from the persisted debug output after archiving.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from jarvis.missions.standalone_run import write_marker

MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".svg": "image/svg+xml",
    ".bmp": "image/bmp",
    ".ico": "image/x-icon",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".m4v": "video/mp4",
    ".ogv": "video/ogg",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpeg",
    ".wmv": "video/x-ms-wmv",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
}
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_INLINE_BYTES = 24 * 1024 * 1024
MAX_REFERENCES = 128
_EXT = "|".join(re.escape(ext[1:]) for ext in MEDIA_TYPES)
_LINK = re.compile(r"(!?\[[^\]\n]*\]\()(<[^>\n]+>|[^)\n]+)(\))")
_RAW = re.compile(
    rf"(?<![^\s\"'`(=])(?:https?://|file:///|[A-Za-z]:[/\\]|\./|/)[^\s<>\"'`]+?\.(?:{_EXT})(?:\?[^\s<>\"'`)]+)?(?=$|[\s<>\"'`)])",
    re.I,
)
_BARE = re.compile(rf"(?<![\w:/\\])(?:[\w.-]+[/\\])*[\w.-]+\.(?:{_EXT})(?=$|[\s\"'`),])", re.I)
_QUOTED = re.compile(rf"[\"'`]([^\"'`\r\n]+\.(?:{_EXT}))[\"'`]", re.I)
_MIME_SUFFIX = {mime: ext for ext, mime in reversed(list(MEDIA_TYPES.items()))}
_REF_KEYS = {
    "url",
    "src",
    "uri",
    "path",
    "file",
    "file_path",
    "savedpath",
    "saved_path",
    "image_url",
    "video_url",
    "audio_url",
    "output_file",
    "output_path",
    "image_path",
    "video_path",
}


def media_type(reference: str, hint: str = "") -> str | None:
    if hint in _MIME_SUFFIX:
        return hint
    if reference.startswith("data:"):
        mime = reference[5:].split(";", 1)[0]
        return mime if mime in _MIME_SUFFIX else None
    try:
        normalized = reference.replace("\\", "/")
        path = (
            unquote(urlsplit(normalized).path)
            if normalized.lower().startswith(("http:", "https:", "file:"))
            or normalized.startswith("/api/")
            else normalized
        )
    except ValueError:
        return None
    path = re.sub(r"/(download|raw|view)$", "", path)
    return MEDIA_TYPES.get(Path(path).suffix.lower())


def display_url(url: str, mime: str) -> str:
    if media_type(url):
        return url
    return url + ("&" if "#" in url else "#") + "jarvis-media=" + quote(mime, safe="")


def media_markdown(url: str, mime: str) -> str:
    kind = mime.split("/", 1)[0]
    return ("!" if kind == "image" else "") + f"[{kind}](<{display_url(url, mime)}>)"


def content_markdown(blocks: list[Any], receipts: dict[str, str]) -> str:
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
            continue
        urls: list[str] = []

        def visit(value: Any, urls: list[str] = urls) -> None:
            if isinstance(value, str) and value in receipts and value not in urls:
                urls.append(value)
            elif isinstance(value, dict):
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)

        visit(block)
        parts.extend(media_markdown(url, receipts[url]) for url in urls)
    return "\n\n".join(part for part in parts if part)


class MediaNormalizer:
    def __init__(self, cwd: Path, outputs_root: Path, scope: str) -> None:
        self.cwd = cwd.expanduser().resolve()
        self.outputs_root = outputs_root.resolve()
        self.scope = scope
        self.receipts: dict[str, str] = {}
        self.errors: list[str] = []
        self._resolved: dict[str, str] = {}

    def _remember(self, url: str, mime: str) -> str:
        if len(self.receipts) >= MAX_REFERENCES and url not in self.receipts:
            raise ValueError("Too many media files in one tool result")
        self.receipts[url] = mime
        return url

    def _archive(self, handle: Any, suffix: str, expected: os.stat_result | None = None) -> str:
        self.outputs_root.mkdir(parents=True, exist_ok=True)
        # Staging is outside the deliverable subtree, so the gallery cannot
        # expose a half-written video while it is being copied.
        with tempfile.TemporaryDirectory(prefix=".chat-media-", dir=self.outputs_root) as temp:
            staged = Path(temp) / ("media" + suffix)
            digest = hashlib.sha256(self.scope.encode())
            total = 0
            with staged.open("wb") as target:
                while chunk := handle.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_FILE_BYTES:
                        raise ValueError("Media file exceeds the 512 MiB import limit")
                    digest.update(chunk)
                    target.write(chunk)
            if total == 0:
                raise ValueError("Media file is empty")
            if expected is not None:
                actual = os.fstat(handle.fileno())
                if (expected.st_size, expected.st_mtime_ns, expected.st_ino) != (
                    actual.st_size,
                    actual.st_mtime_ns,
                    actual.st_ino,
                ):
                    raise ValueError("Media file changed while being imported")
            key = digest.hexdigest()[:24]
            run = self.outputs_root / f"chat-media-{key}"
            files = run / "tasks" / "chat" / "artifacts" / "files"
            if run.resolve() != run or not files.resolve().is_relative_to(run):
                raise ValueError("Media archive path leaves its own directory")
            files.mkdir(parents=True, exist_ok=True)
            name = "media" + suffix
            os.replace(staged, files / name)
            write_marker(run, kind="chat_media", title="Chat media")
        return (
            f"/api/outputs/{run.name}/files/tasks/chat/artifacts/files/{name}"
            "/download?disposition=inline"
        )

    def reference(self, raw: str, hint: str = "") -> str:
        ref = raw.strip().removeprefix("<").removesuffix(">")
        if ref in self._resolved:
            return self._resolved[ref]
        mime = media_type(ref, hint)
        if not mime:
            return raw
        try:
            if ref.startswith("data:"):
                header, encoded = ref.split(",", 1)
                if header != f"data:{mime};base64" or len(encoded) > (
                    MAX_INLINE_BYTES * 4 // 3 + 4
                ):
                    raise ValueError("Unsupported or oversized inline media")
                import io

                data = base64.b64decode(encoded, validate=True)
                if len(data) > MAX_INLINE_BYTES:
                    raise ValueError("Inline media exceeds the import limit")
                url = self._archive(io.BytesIO(data), _MIME_SUFFIX[mime])
            elif ref.lower().startswith(("https://", "http://")):
                parsed = urlsplit(ref)
                if (
                    not parsed.hostname
                    or parsed.username
                    or parsed.password
                    or re.search(r"[\x00-\x20<>]", ref)
                ):
                    raise ValueError("Invalid media URL")
                url = ref
            elif ref.startswith("/api/"):
                url = ref
            else:
                if ref.startswith("file:"):
                    parsed = urlsplit(ref)
                    if parsed.netloc:
                        raise ValueError("Network file locations are not chat artifacts")
                    ref = unquote(parsed.path)
                    if re.match(r"^/[A-Za-z]:/", ref):
                        ref = ref[1:]
                if ref.startswith(("\\\\", "//")):
                    raise ValueError("Network file locations are not chat artifacts")
                file = Path(ref)
                file = (file if file.is_absolute() else self.cwd / file).resolve()
                if not file.is_relative_to(self.cwd):
                    raise ValueError("Media file is outside this chat workspace")
                if "%" in ref and not file.exists():
                    decoded = Path(unquote(ref))
                    file = (decoded if decoded.is_absolute() else self.cwd / decoded).resolve()
                    if not file.is_relative_to(self.cwd):
                        raise ValueError("Media file is outside this chat workspace")
                before = file.stat()
                with file.open("rb") as source:
                    url = self._archive(source, file.suffix.lower(), before)
            self._resolved[raw] = self._remember(url, mime)
            return url
        except (OSError, ValueError, binascii.Error) as exc:
            # The original receipt remains inspectable, alongside a visible
            # delivery error. Never replace missing pixels with a success claim.
            self.errors.append(str(exc))
            return raw

    def text(self, text: str) -> str:
        if text.strip().startswith("data:") and media_type(text.strip()):
            return self.reference(text.strip())
        # Code examples are not output receipts. Do not expose a file merely
        # because an instruction block happens to mention its name.
        parts = re.split(r"(```[\s\S]*?```)", text)
        for i in range(0, len(parts), 2):
            part = parts[i]

            def link(match: re.Match[str]) -> str:
                raw = match[2].strip()
                url = self.reference(raw)
                return match[0] if url == raw else match[1] + "<" + url + ">" + match[3]

            part = _LINK.sub(link, part)
            # Scan other explicit URL/path receipts, without changing prose.
            without_links = _LINK.sub("", part)
            for match in _QUOTED.finditer(without_links):
                ref = match[1]
                candidate = (self.cwd / ref).resolve()
                if ref.startswith(("http://", "https://", "file:///")) or (
                    candidate.is_relative_to(self.cwd) and candidate.is_file()
                ):
                    self.reference(ref)
            for match in _RAW.finditer(without_links):
                self.reference(match[0])
            for match in _BARE.finditer(without_links):
                candidate = (self.cwd / match[0]).resolve()
                if candidate.is_relative_to(self.cwd) and candidate.is_file():
                    self.reference(match[0])
            parts[i] = part
        return "".join(parts)

    def structured(self, value: Any, depth: int = 0, inherited_hint: str = "") -> Any:
        if depth > 12:
            return value
        if isinstance(value, list):
            return [self.structured(item, depth + 1, inherited_hint) for item in value]
        if isinstance(value, dict):
            result = dict(value)
            hint = str(
                value.get("mimeType")
                or value.get("mime_type")
                or value.get("media_type")
                or value.get("content_type")
                or inherited_hint
            )
            if not hint:
                hint = {
                    "image": "image/png",
                    "image_url": "image/png",
                    "input_image": "image/png",
                    "image_generation_call": "image/png",
                    "video": "video/mp4",
                    "video_url": "video/mp4",
                    "audio": "audio/mpeg",
                }.get(str(value.get("type")), "")
            hint = hint.split(";", 1)[0].strip().lower()
            binary_keys = ["data", "blob"]
            if value.get("type") == "image_generation_call":
                binary_keys.append("result")
            for binary_key in binary_keys:
                if hint in _MIME_SUFFIX and isinstance(value.get(binary_key), str):
                    uri = self.reference(f"data:{hint};base64,{value[binary_key]}", hint)
                    result.pop(binary_key, None)
                    if not uri.startswith("data:"):
                        result["url"] = uri
                    else:
                        result["media_error"] = "Media import failed"
            if isinstance(value.get("b64_json"), str):
                uri = self.reference("data:image/png;base64," + value["b64_json"], "image/png")
                if not uri.startswith("data:"):
                    result.pop("b64_json", None)
                    result["url"] = uri
            for key, child in list(result.items()):
                if (
                    key in {"image_url", "video_url", "audio_url"}
                    and isinstance(child, dict)
                    and isinstance(child.get("url"), str)
                ):
                    mime = {
                        "image_url": "image/png",
                        "video_url": "video/mp4",
                        "audio_url": "audio/mpeg",
                    }[key]
                    result[key] = {**child, "url": self.reference(child["url"], mime)}
                elif key.lower() in _REF_KEYS and isinstance(child, str):
                    key_hint = {
                        "image_url": "image/png",
                        "video_url": "video/mp4",
                        "audio_url": "audio/mpeg",
                    }.get(key, hint)
                    result[key] = self.reference(child, key_hint)
                elif key not in {"b64_json"}:
                    child_hint = {
                        "images": "image/png",
                        "image": "image/png",
                        "videos": "video/mp4",
                        "video": "video/mp4",
                        "audio": "audio/mpeg",
                    }.get(key, hint if key in {"source", "resource", "result"} else "")
                    if key != "data" or not isinstance(child, str):
                        result[key] = self.structured(child, depth + 1, child_hint)
            return result
        if isinstance(value, str):
            return self.reference(value, inherited_hint) if inherited_hint else self.text(value)
        return value


def normalize_media_event(
    event: dict[str, Any], *, cwd: Path, outputs_root: Path, scope: str
) -> list[dict[str, Any]]:
    kind = event.get("kind")
    payload = event.get("payload") or {}
    if kind not in {"assistant_text", "tool_result", "user_message"} or payload.get("is_error"):
        return [event]
    normalizer = MediaNormalizer(cwd, outputs_root, scope)
    if kind == "user_message":
        from jarvis.agentic_ide.drops import dereference

        attachments = []
        for row in payload.get("attachments") or []:
            if not isinstance(row, dict):
                continue
            receipt = {key: value for key, value in row.items() if key != "reference"}
            ref = dereference(str(row.get("reference") or ""))
            if ref and media_type(ref):
                url = normalizer.reference(ref)
                if url != ref:
                    receipt["url"] = url
            attachments.append(receipt)
        result = (
            [{**event, "payload": {**payload, "attachments": attachments}}]
            if "attachments" in payload
            else [event]
        )
        if normalizer.errors:
            result.append(
                {
                    **event,
                    "kind": "error",
                    "payload": {
                        "message": "Attached media could not be displayed: "
                        + "; ".join(dict.fromkeys(normalizer.errors))
                    },
                }
            )
        return result
    field = "text" if kind == "assistant_text" else "output"
    value = payload.get(field, "")
    if isinstance(value, str):
        try:
            parsed = json.loads(value) if value.lstrip().startswith(("{", "[")) else None
        except ValueError:
            parsed = None
        processed = normalizer.structured(parsed) if parsed is not None else None
        rewritten = (
            json.dumps(processed, ensure_ascii=False)
            if parsed is not None
            else normalizer.text(value)
        )
        content = processed.get("content") if isinstance(processed, dict) else processed
        if (
            kind == "assistant_text"
            and isinstance(content, list)
            and all(isinstance(block, dict) and "type" in block for block in content)
            and (normalizer.receipts or normalizer.errors)
        ):
            rewritten = content_markdown(content, normalizer.receipts)
    else:
        rewritten = json.dumps(normalizer.structured(value), ensure_ascii=False)
    result = [{**event, "payload": {**payload, field: rewritten}}]
    # Explicit Markdown in assistant prose is already rendered in place.
    inline = (
        set(re.findall(r"\]\(<?([^)>]+)>?\)", rewritten)) if kind == "assistant_text" else set()
    )
    if kind == "assistant_text":
        prose = re.sub(r"`[^`]*`", "", rewritten)
        inline.update(
            match[0]
            for match in _RAW.finditer(prose)
            if match[0].startswith(("http://", "https://"))
        )
    for url, mime in normalizer.receipts.items():
        if url in inline or display_url(url, mime) in inline:
            continue
        key = hashlib.sha256((str(payload.get("turn_id")) + url).encode()).hexdigest()[:24]
        markdown = media_markdown(url, mime)
        result.append(
            {
                **event,
                "kind": "assistant_text",
                "payload": {
                    "turn_id": payload.get("turn_id"),
                    "message_id": f"media-{key}",
                    "text": markdown,
                },
            }
        )
    if normalizer.errors:
        result.append(
            {
                **event,
                "kind": "error",
                "payload": {
                    "turn_id": payload.get("turn_id"),
                    "message": "Media could not be displayed: "
                    + "; ".join(dict.fromkeys(normalizer.errors)),
                },
            }
        )
    return result
