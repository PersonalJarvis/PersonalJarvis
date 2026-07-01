"""Ad-hoc script — prints 3 bio scenarios for the PHASE_B_DONE report.

Not run as a test (filename starts with an underscore). We use a
scripted FakeBrain here because real API keys aren't guaranteed to be
available in CI — the outputs show what the _pipeline_ produces, not
what Claude Opus would write in the field.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_profile import (  # type: ignore[import-not-found]
    SCRIPTED_CASUAL_BIO,
    SCRIPTED_EMPTY_MEMORY_BIO,
    SCRIPTED_POWER_USER_BIO,
    FakeBrain,
    _casual_user_db,
    _power_user_db,
)

from jarvis.board.profile import BioGenerator, BioStore, make_resolver_from_brain
from jarvis.board.store import BoardStore


async def _run_sample(tmp: Path, brain_text: str, db_factory, *, memory: str, soul: str) -> None:
    jsonl, db = db_factory(tmp)
    gen = BioGenerator(
        brain_resolver=make_resolver_from_brain(FakeBrain(brain_text)),
        store=BoardStore(db),
        bio_store=BioStore(db),
        jsonl_dir=jsonl,
    )
    result = await gen.generate_bio(memory_text=memory, soul_text=soul, triggered_by="sample")
    print(result["text"])
    print()


async def main() -> None:
    import tempfile

    print("=== A - Power-User (Code-heavy, 14 Tage Daten) ===")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        await _run_sample(
            Path(tmp), SCRIPTED_POWER_USER_BIO, _power_user_db,
            memory="User is a non-coder, works autonomously. Multi-provider brain.",
            soul="Jarvis: lakonisch, trocken, kurz.",
        )

    print("=== B - Casual-User (wenig Daten) ===")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        await _run_sample(
            Path(tmp), SCRIPTED_CASUAL_BIO, _casual_user_db,
            memory="", soul="",
        )

    print("=== C - Edge-Case (leerer MEMORY.md, aber viele Stats) ===")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        await _run_sample(
            Path(tmp), SCRIPTED_EMPTY_MEMORY_BIO, _power_user_db,
            memory="", soul="",
        )


if __name__ == "__main__":
    asyncio.run(main())
