"""Ad-hoc Script — druckt 3 Bio-Szenarien fuer den PHASE_B_DONE-Report.

Wird nicht als Test ausgefuehrt (Filename beginnt mit Underscore). Wir
nutzen hier einen Scripted-FakeBrain, weil echte API-Keys im CI nicht
vorausgesetzt werden — die Outputs zeigen, was die _Pipeline_ produziert,
nicht was Claude Opus im Feld schreibt.
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
            memory="User ist Nicht-Coder, arbeitet autonom. Multi-Provider-Brain.",
            soul="Jarvis: Iron-Man-Style, trocken, kurz.",
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
