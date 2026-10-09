"""Name-only discovery of published API descriptions. Never accepts a credential."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import re
import time
from difflib import SequenceMatcher
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from jarvis.core.http_guard import public_only_async
from jarvis.core.http_pool import HttpClientPool
from jarvis.marketplace.custom_api import ApiConnectionInfo, ApiDefinition
from jarvis.marketplace.custom_api_openapi import api_auth, api_base_url, compile_openapi

log = logging.getLogger(__name__)
DIRECTORY_URL = "https://api.apis.guru/v2/list.json"
BRAND_DIRECTORY_URL = (
    "https://raw.githubusercontent.com/simple-icons/simple-icons/develop/data/simple-icons.json"
)
_ALIASES = {"11labs": "elevenlabs", "elevenlab": "elevenlabs"}
# A bootstrap address is a discovery hint, not a hand-maintained action list.
# Every operation still comes from the provider's current published document.
_BOOTSTRAP = {
    "elevenlabs": ("ElevenLabs", "elevenlabs.io", "https://api.elevenlabs.io/openapi.json")
}


class ApiDiscoveryError(ValueError):
    def __init__(self, code: str, suggestions: list[str] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.suggestions = suggestions or []


def normalized_name(name: str) -> str:
    value = re.sub(r"\b(api|apis|documentation|official)\b", "", name.lower())
    value = re.sub(r"[^\w]", "", value)
    return _ALIASES.get(value, value)


def same_service_host(host: str, domain: str) -> bool:
    host, domain = host.lower().rstrip("."), domain.lower().rstrip(".")
    return host == domain or host.endswith("." + domain)


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag in {"a", "link"} and values.get("href"):
            self.links.append(str(values["href"]))


class ApiDiscovery:
    def __init__(
        self, *, transport: Any = None, directory: dict[str, Any] | None = None, search: Any = None
    ) -> None:
        self._pool = HttpClientPool(
            timeout_s=15,
            transport=transport,
            client_kwargs={
                "follow_redirects": False,
                "trust_env": False,
                **public_only_async(),
            },
        )
        self._directory = directory
        self._loaded_at = time.monotonic() if directory is not None else 0.0
        self._lock = asyncio.Lock()
        self._search = search
        self._brands: list[dict[str, Any]] | None = None
        self._documents: dict[str, dict[str, Any]] = {}

    async def close(self) -> None:
        await self._pool.aclose()

    async def _fetch(self, url: str, maximum: int = 32 * 1024 * 1024) -> tuple[bytes, str]:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.username or parts.password:
            raise ApiDiscoveryError("unsafe_documentation")
        # All redirects are separately checked before sending a request. No
        # authentication headers, keys or cookies are used for documentation.
        for _ in range(4):
            parts = urlsplit(url)
            if parts.scheme != "https" or parts.username or parts.password:
                raise ApiDiscoveryError("unsafe_documentation")
            async with self._pool.client().stream(
                "GET",
                url,
                headers={"Accept": "application/json, application/yaml, text/html, image/png"},
            ) as response:
                if response.is_redirect and response.headers.get("location"):
                    url = urljoin(url, response.headers["location"])
                    if urlsplit(url).scheme != "https":
                        raise ApiDiscoveryError("unsafe_documentation")
                    continue
                response.raise_for_status()
                data = bytearray()
                async for chunk in response.aiter_bytes(64 * 1024):
                    data.extend(chunk)
                    if len(data) > maximum:
                        raise ApiDiscoveryError("documentation_too_large")
                return bytes(data), url
        raise ApiDiscoveryError("documentation_unavailable")

    async def _catalog(self) -> dict[str, Any]:
        async with self._lock:
            if self._directory is not None and time.monotonic() - self._loaded_at < 6 * 3600:
                return self._directory
            raw, _ = await self._fetch(DIRECTORY_URL, 16 * 1024 * 1024)
            result = await asyncio.to_thread(json.loads, raw)
            if not isinstance(result, dict):
                raise ApiDiscoveryError("documentation_unavailable")
            self._directory, self._loaded_at = result, time.monotonic()
            return result

    @staticmethod
    def _match(name: str, directory: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        target = normalized_name(name)
        scored = []
        for service, entry in directory.items():
            version = entry.get("versions", {}).get(entry.get("preferred"), {})
            info = version.get("info", {})
            domain, _, suffix = service.partition(":")
            labels = [info.get("title", ""), service, domain.split(".")[0] + " " + suffix]
            names = [normalized_name(str(v)) for v in labels]
            score = max(
                (1.0 if target == value else SequenceMatcher(None, target, value).ratio())
                for value in names
            )
            if score >= 0.78:
                scored.append((score, service, version))
        scored.sort(key=lambda item: (-item[0], item[1]))
        if not scored:
            return None
        if scored[0][0] < 1 or (len(scored) > 1 and scored[0][0] - scored[1][0] < 0.08):
            raise ApiDiscoveryError(
                "ambiguous_service",
                [str(v[2].get("info", {}).get("title", v[1])) for v in scored[:5]],
            )
        return scored[0][1], scored[0][2]

    async def _search_document(self, name: str) -> tuple[str, str, str]:
        """Discover an unlisted provider from its own public documentation."""
        target = normalized_name(name)
        try:
            if self._brands is None:
                raw, _ = await self._fetch(BRAND_DIRECTORY_URL, 2 * 1024 * 1024)
                self._brands = json.loads(raw)
            matches = [
                b
                for b in self._brands
                if target
                in {
                    normalized_name(str(b.get("title", ""))),
                    *[normalized_name(v) for v in b.get("aliases", {}).get("aka", [])],
                }
            ]
            if len(matches) == 1:
                brand = matches[0]
                host = urlsplit(str(brand.get("source", ""))).hostname or ""
                for prefix in ("www.", "docs.", "developers.", "brand."):
                    host = host.removeprefix(prefix)
                if host and host not in {
                    "github.com",
                    "raw.githubusercontent.com",
                    "wikipedia.org",
                    "commons.wikimedia.org",
                }:
                    found = await self._published_document(host)
                    if found:
                        return str(brand["title"]), host, found
        except (httpx.HTTPError, ValueError):
            log.debug("Custom API brand directory discovery was unavailable")
        if self._search is None:
            from jarvis.plugins.tool.search_backends import run_search

            found = await run_search(
                f'"{name}" official OpenAPI swagger API documentation',
                6,
                client=self._pool.client(),
                apifare_key="",
            )
            rows = found.results
        else:
            rows = await self._search(name)
        domain_input = urlsplit(name if "://" in name else "https://" + name).hostname or ""
        candidates: list[tuple[str, str]] = []
        for row in rows:
            url = row.get("url") or row.get("href") or ""
            host = urlsplit(url).hostname or ""
            if urlsplit(url).scheme != "https":
                continue
            # A search result alone never chooses an unrelated credential host.
            # An official domain must identify the requested service itself.
            labels = host.split(".")
            # Match the service's registrable label, never an arbitrary
            # subdomain such as requested-service.someone-else.example.
            suffix_size = (
                2
                if ".".join(labels[-2:])
                in {
                    "co.uk",
                    "com.au",
                    "co.nz",
                    "co.jp",
                    "com.br",
                    "co.in",
                }
                else 1
            )
            brand_index = len(labels) - suffix_size - 1
            if brand_index >= 0 and normalized_name(labels[brand_index]) == target:
                domain = ".".join(labels[brand_index:])
            elif "." in domain_input and same_service_host(host, domain_input):
                domain = domain_input
            else:
                continue
            candidates.append((url, domain))
        if not candidates:
            raise ApiDiscoveryError("service_not_found")
        domains = {domain for _, domain in candidates}
        if len(domains) != 1:
            raise ApiDiscoveryError("ambiguous_service", sorted(domains))
        for page, domain in candidates[:3]:
            try:
                raw, actual = await self._fetch(page, 3 * 1024 * 1024)
                document = self._parse(raw)
                if document:
                    self._documents[actual] = document
                    return name, domain, actual
                links = _Links()
                links.feed(raw.decode("utf-8", errors="replace"))
                for href in links.links:
                    if re.search(
                        r"(?:openapi|swagger)[^?#]*\.(?:json|ya?ml)(?:[?#]|$)", href, re.I
                    ):
                        return name, domain, urljoin(actual, href)
            except (httpx.HTTPError, ValueError):
                log.debug("Custom API public documentation candidate was unavailable")
        raise ApiDiscoveryError("documentation_not_found")

    async def _published_document(self, domain: str) -> str | None:
        """Check public documentation conventions on a catalog-identified domain."""
        for path in ("/openapi.json", "/openapi.yaml", "/openapi.yml", "/llms.txt"):
            try:
                raw, actual = await self._fetch(f"https://{domain}{path}")
                document = await asyncio.to_thread(self._parse, raw)
                if document:
                    self._documents[actual] = document
                    return actual
                if path == "/llms.txt":
                    links = re.findall(
                        r"https://[^\s)<>\"']+", raw.decode("utf-8", errors="replace")
                    )
                    for link in links:
                        if re.search(
                            r"(?:openapi|swagger).*\.(?:json|ya?ml)(?:[?#]|$)", link, re.I
                        ):
                            return link
            except (httpx.HTTPError, ValueError):
                log.debug("Custom API conventional documentation address was unavailable")
        try:
            raw, actual = await self._fetch(f"https://api.{domain}/openapi.json")
            document = await asyncio.to_thread(self._parse, raw)
            if document:
                self._documents[actual] = document
                return actual
        except (httpx.HTTPError, ValueError):
            log.debug("Custom API API-host documentation address was unavailable")
        return None

    @staticmethod
    def _parse(raw: bytes) -> dict[str, Any] | None:
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            log.debug("Custom API documentation is not JSON; checking YAML")
            try:
                import yaml

                value = yaml.safe_load(raw)
            except (ValueError, yaml.YAMLError):
                log.debug("Custom API documentation candidate is not an OpenAPI document")
                return None
        if (
            isinstance(value, dict)
            and (value.get("openapi") or value.get("swagger"))
            and isinstance(value.get("paths"), dict)
        ):
            return value
        return None

    async def _logo(self, domain: str, image_url: str = "") -> str:
        for url in [image_url, f"https://{domain}/favicon.ico"]:
            if not url:
                continue
            try:
                raw, _ = await self._fetch(url, 512 * 1024)
                return await asyncio.to_thread(self._png, raw)
            except (httpx.HTTPError, ValueError, OSError):
                log.debug("Custom API brand artwork candidate was unavailable")
        return ""

    @staticmethod
    def _png(raw: bytes) -> str:
        from PIL import Image

        with Image.open(io.BytesIO(raw)) as picture:
            if picture.width > 2048 or picture.height > 2048:
                raise ValueError("Artwork is too large")
            picture.thumbnail((96, 96))
            out = io.BytesIO()
            picture.convert("RGBA").save(out, format="PNG")
        return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode("ascii")

    async def resolve(self, name: str) -> ApiDefinition:
        """Resolve a name to validated metadata. Credentials cannot enter this path."""
        name = name.strip()
        if not name or len(name) > 100 or any(ord(c) < 32 for c in name):
            raise ApiDiscoveryError("invalid_service_name")
        async with asyncio.timeout(60):
            normalized = normalized_name(name)
            logo_url = ""
            if normalized in _BOOTSTRAP:
                title, domain, spec_url = _BOOTSTRAP[normalized]
                service_id = domain
            else:
                try:
                    match = self._match(name, await self._catalog())
                except httpx.HTTPError:
                    log.warning("Custom API directory unavailable; checking public documentation")
                    match = None
                if match:
                    service_id, version = match
                    domain = service_id.partition(":")[0]
                    info = version.get("info", {})
                    title = re.sub(
                        r"\s+(API\s*)?(Documentation|Reference)$",
                        "",
                        str(info.get("title") or name),
                        flags=re.I,
                    )
                    origins = info.get("x-origin", [])
                    if isinstance(origins, dict):
                        origins = [origins]
                    origin = next(
                        (
                            v.get("url")
                            for v in origins
                            if v.get("format") in {"openapi", "swagger"}
                            and str(v.get("url", "")).startswith("https://")
                        ),
                        None,
                    )
                    spec_url = origin or version.get("swaggerUrl", "")
                    logo_url = str(info.get("x-logo", {}).get("url", ""))
                else:
                    title, domain, spec_url = await self._search_document(name)
                    service_id = domain
            document = self._documents.pop(spec_url, None)
            actual_url = spec_url
            if document is None:
                raw, actual_url = await self._fetch(spec_url)
                document = await asyncio.to_thread(self._parse, raw)
            if document is None:
                raise ApiDiscoveryError("documentation_not_found")
            base_url = api_base_url(document, actual_url)
            if not same_service_host(urlsplit(base_url).hostname or "", domain):
                raise ApiDiscoveryError("service_address_mismatch")
            auth = api_auth(document)
            actions, categories, omitted = await asyncio.to_thread(
                compile_openapi, document, auth, base_url
            )
            logo = "" if normalized == "elevenlabs" else await self._logo(domain, logo_url)
            return ApiDefinition(
                name=title[:100],
                description=", ".join(categories)[:2000],
                base_url=base_url,
                auth=auth,
                actions=actions,
                enabled=True,
                connection=ApiConnectionInfo(
                    service_id=service_id,
                    website=f"https://{domain}",
                    spec_url=actual_url,
                    brand_id=domain.split(".")[0],
                    logo_data=logo,
                    categories=categories,
                    omitted_operations=omitted,
                ),
            )
