"""M5 guard: the legacy Agents slot stays migrated onto the Society surface."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "jarvis" / "ui" / "web" / "frontend" / "src"


def test_agents_deep_link_mounts_society_and_legacy_view_stays_removed() -> None:
    main_view = (FRONTEND / "components" / "layout" / "MainView.tsx").read_text(
        encoding="utf-8"
    )
    events = (FRONTEND / "store" / "events.ts").read_text(encoding="utf-8")
    society_view = (FRONTEND / "views" / "society" / "SocietyView.tsx").read_text(
        encoding="utf-8"
    )

    assert 'import("@/views/society/SocietyView")' in main_view
    assert re.search(
        r'case\s+"agents":\s*return\s+<SocietyView\s*/>',
        main_view,
    )
    assert '"agents",' in events
    assert 'import { SocietyLedger }' in society_view
    assert re.search(r'mode\s*===\s*"ledger".*?<SocietyLedger\s*/>', society_view, re.S)
    assert not (FRONTEND / "views" / "AgentsView.tsx").exists()
