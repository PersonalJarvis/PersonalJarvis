"""Keep an https-only download https-only, redirect after redirect.

Several places fetch a URL that came from the community registry: the index
itself, a skill's ``SKILL.md``, a wallpaper's bytes, a plugin's files. Each of
them checks the URL is ``https://`` before fetching, and each says why in a
comment — the fetch runs on the user's machine, so a plaintext or internal
address would let a published entry aim the backend at the loopback API, the
router's admin page, or a cloud metadata endpoint.

That check covers the FIRST url only. With ``follow_redirects=True`` the
server on the other end picks the next one, and a publisher who controls
``https://their-host/skill.md`` controls its ``Location`` header too. So the
guard has to travel with the request rather than stand in front of it.

Usage — one keyword argument on the client::

    async with httpx.AsyncClient(follow_redirects=True, **https_only_async()) as c:
        resp = await c.get(url)

    with httpx.Client(follow_redirects=True, **https_only()) as c:
        resp = c.get(url)

A redirect to anything but https raises :class:`InsecureRedirect`, which is an
``httpx.HTTPError`` — the exception every one of these call sites already
handles as "the download failed".
"""

from __future__ import annotations

from typing import Any

import httpx

__all__ = ["InsecureRedirect", "https_only", "https_only_async"]


class InsecureRedirect(httpx.HTTPError):
    """A redirect tried to leave https.

    Subclasses ``httpx.HTTPError`` on purpose: every download this guards is
    already wrapped in ``except httpx.HTTPError``, so a refused redirect
    surfaces as the ordinary "could not fetch" the caller knows how to report.
    """


def _check(response: httpx.Response) -> None:
    if not response.has_redirect_location:
        return
    location = response.headers.get("location")
    if not location:
        return
    # Resolved against the current URL: a relative Location keeps the scheme it
    # already had, and only an absolute one can change it.
    target = response.url.join(location)
    if target.scheme != "https":
        raise InsecureRedirect(f"refusing a non-https redirect from {response.url} to {target}")


async def _check_async(response: httpx.Response) -> None:
    _check(response)


def https_only() -> dict[str, Any]:
    """Client kwargs that refuse any redirect leaving https (sync client)."""
    return {"event_hooks": {"response": [_check]}}


def https_only_async() -> dict[str, Any]:
    """The same, for ``httpx.AsyncClient`` — its hooks must be awaitable."""
    return {"event_hooks": {"response": [_check_async]}}
