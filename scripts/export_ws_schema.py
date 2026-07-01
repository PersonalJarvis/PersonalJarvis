"""Exports Pydantic models from jarvis.ui.web.schema as JSON schema."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "jarvis/ui/web/frontend/src/schema/ws.json"


def main() -> int:
    try:
        from jarvis.ui.web.schema import (
            WSCommand,
            WSEventEnvelope,
            WSMessageIn,
            WSWelcome,
        )
    except ImportError:
        print("Agent-2's schema.py not there yet. Skip.", file=sys.stderr)
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "WSEventEnvelope": WSEventEnvelope.model_json_schema(),
                "WSMessageIn": WSMessageIn.model_json_schema(),
                "WSCommand": WSCommand.model_json_schema(),
                "WSWelcome": WSWelcome.model_json_schema(),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
