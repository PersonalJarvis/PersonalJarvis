"""Inspect a custom local voice package without starting native model code."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from .models import LocalModelManifest
from .packages import load_manifest, verify_package


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("schema", help="Print the custom-model manifest JSON schema.")
    commands.add_parser(
        "catalog", help="List native model candidates and their declared capabilities."
    )
    inspect = commands.add_parser(
        "inspect", help="Validate a manifest and optionally its weight files."
    )
    inspect.add_argument("manifest", type=Path)
    inspect.add_argument("--weights", type=Path)
    acquire = commands.add_parser("acquire", help="Download or import verified model weights.")
    acquire.add_argument("manifest", type=Path)
    acquire.add_argument("--store", type=Path, required=True)
    acquire.add_argument("--source", type=Path, help="Directory for an own/local model package.")
    args = parser.parse_args(argv)
    if args.command == "schema":
        print(json.dumps(LocalModelManifest.model_json_schema(), indent=2))
        return 0
    from .catalog import catalog_model, native_model_catalog

    if args.command == "catalog":
        print(
            json.dumps(
                {
                    "models": [model.model_dump(mode="json") for model in native_model_catalog()],
                    "runtime_qualified": False,
                },
                indent=2,
            )
        )
        return 0
    try:
        model = catalog_model(str(args.manifest)) or load_manifest(args.manifest)
        if args.command == "acquire":
            from .store import ModelAcquisitionError, acquire_model

            try:
                weights = acquire_model(model, args.store, local_directory=args.source)
            except ModelAcquisitionError as exc:
                print(json.dumps({"error": "acquisition_failed", "detail": str(exc)}))
                return 1
            print(
                json.dumps(
                    {
                        "model": model.id,
                        "weights": str(weights),
                        "package_verified": True,
                        "runtime_qualified": False,
                    },
                    indent=2,
                )
            )
            return 0
        output: dict[str, object] = {
            "model": model.id,
            "manifest_fingerprint": model.fingerprint,
            "download_bytes": model.download_bytes,
            "languages": model.languages,
            "declared_capabilities": sorted(model.capabilities),
            "runtime_qualified": False,
        }
        exit_code = 0
        if args.weights is not None:
            check = verify_package(model, args.weights)
            output["package"] = check.model_dump(mode="json")
            if not check.verified:
                exit_code = 1
        print(json.dumps(output, indent=2))
        return exit_code
    except (OSError, ValueError) as exc:
        # Validation diagnostics omit input values: a pasted secret must not
        # be echoed just because it was put into an unsupported model field.
        detail: object = "The manifest could not be read or validated."
        if isinstance(exc, ValidationError):
            detail = [{"field": list(e["loc"]), "type": e["type"]} for e in exc.errors()]
        print(json.dumps({"error": "invalid_model_package", "detail": detail}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
