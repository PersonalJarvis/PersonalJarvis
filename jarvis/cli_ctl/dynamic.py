"""Build a Click command tree at runtime from an OpenAPI document (Approach A).

One Click Group per OpenAPI tag; one Command per operation. The command's
callback issues the HTTP request through an injected `runner(method, path,
params, body)` callable so the tree is testable without a live server.
"""
from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Callable
from typing import Any

import click

_log = logging.getLogger(__name__)

Runner = Callable[[str, str, dict[str, Any], Any], Any]

_HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
_CLICK_TYPE = {
    "integer": click.INT,
    "number": click.FLOAT,
    "boolean": click.BOOL,
    "string": click.STRING,
}


def _clean_name(operation_id: str, method: str, path: str) -> str:
    # FastAPI builds operationIds as re.sub(r"\W", "_", <func><path>) + "_<method>"
    # (e.g. func "list_clis" + path "/api/clis" -> "list_clis_api_clis_get").
    # Recover the bare function name by stripping the trailing "_<method>" and
    # then the munged path, so the command reads "list-clis", not the full mangle.
    name = operation_id or ""
    if name.endswith(f"_{method}"):
        name = name[: -len(method) - 1]
    munged_path = re.sub(r"\W", "_", path)
    if munged_path and name.endswith(munged_path):
        name = name[: -len(munged_path)]
    name = name.strip("_").replace("_", "-")
    return name or f"{method}-" + re.sub(r"\W+", "-", path).strip("-")


def _option_for(param: dict[str, Any]) -> click.Option:
    schema = param.get("schema", {})
    if schema.get("enum"):
        ptype: click.ParamType = click.Choice([str(v) for v in schema["enum"]])
    else:
        ptype = _CLICK_TYPE.get(schema.get("type", "string"), click.STRING)
    return click.Option(
        [f"--{param['name']}"],
        type=ptype,
        required=bool(param.get("required", False)),
        help=param.get("description", ""),
    )


def _build_command(
    path: str, method: str, op: dict[str, Any], runner: Runner
) -> click.Command:
    parameters = op.get("parameters", [])
    path_names = {p["name"] for p in parameters if p.get("in") == "path"}
    params: list[click.Parameter] = [_option_for(p) for p in parameters]
    has_body = "requestBody" in op
    if has_body:
        params.append(
            click.Option(
                ["--json-body"],
                help="Request body as JSON ('-' reads stdin).",
                required=bool(op["requestBody"].get("required", False)),
            )
        )

    def callback(**kwargs: Any) -> None:
        body = None
        raw = kwargs.pop("json_body", None)
        if raw is not None:
            body = json.load(sys.stdin) if raw == "-" else json.loads(raw)
        url_path = path
        query: dict[str, Any] = {}
        for key, value in kwargs.items():
            if value is None:
                continue
            if key in path_names:
                url_path = url_path.replace("{" + key + "}", str(value))
            else:
                query[key] = value
        result = runner(method, url_path, query, body)
        # Local import avoids a load-time cycle with __main__.
        from jarvis.cli_ctl import render
        from jarvis.cli_ctl.__main__ import as_json

        render.emit(result, as_json=as_json())

    return click.Command(
        name=_clean_name(op.get("operationId", ""), method, path),
        params=params,
        callback=callback,
        help=op.get("summary") or op.get("description") or "",
        short_help=op.get("summary", ""),
    )


def build_api_group(spec: dict[str, Any], runner: Runner) -> click.Group:
    """Return a Click `api` group: one sub-group per tag, command per op."""
    root = click.Group("api", help="Auto-generated command per live API endpoint.")
    by_tag: dict[str, click.Group] = {}
    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method, op in item.items():
            if method.lower() not in _HTTP_METHODS or not isinstance(op, dict):
                continue
            tag = (op.get("tags") or ["default"])[0]
            sub = by_tag.setdefault(
                tag, click.Group(tag, help=f"Operations tagged '{tag}'.")
            )
            try:
                sub.add_command(_build_command(path, method.lower(), op, runner))
            except Exception as exc:  # noqa: S112 - one malformed op must not kill the tree
                _log.debug("skipped malformed op %s %s: %s", method, path, exc)
                continue
    for sub in by_tag.values():
        root.add_command(sub)
    return root
