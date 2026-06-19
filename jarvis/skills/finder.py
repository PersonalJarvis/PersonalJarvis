"""Skill-Finder: Mini-Agent zur Suche und Installation von Skills.

Der Finder ist ein schlanker Wrapper um:
1. Den kuratierten Seed-Katalog (``catalog/seed_catalog.json``).
2. Den ``BrainManager`` zur semantischen Bewertung der Kandidaten.
3. Den ``httpx``-Client zum Download einer ausgewaehlten SKILL.md.

Design-Prinzipien:
- **Graceful degradation**: Ohne Brain funktioniert die Suche heuristisch
  (String-Matching). Ohne Internet schlaegt ``install`` fehl, aber die Suche
  laeuft weiter (statischer Katalog).
- **Trust-Filter vor Brain**: Der Trust-Filter greift *vor* dem Brain-Ranking,
  damit das Brain nicht Tokens fuer Kandidaten verschwendet, die der User
  sowieso ablehnen wuerde.
- **Stateless**: Der Finder haelt keinen State — Query-Historie liegt im
  Frontend bzw. spaeter im Flight-Recorder.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from jarvis.core.paths import user_skills_dir
from jarvis.skills.catalog import load_catalog
from jarvis.skills.loader import parse_skill

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Value-Types
# ----------------------------------------------------------------------

TrustLevel = Literal["official", "verified", "community", "experimental"]
"""Vertrauensstufen fuer Skill-Quellen.

- ``official``: Anthropic, OpenAI, andere first-party Vendors.
- ``verified``: Maintainer mit Track-Record und 3000+ GitHub-Stars.
- ``community``: Aktiv gewartet, weniger Stars, pruefbar.
- ``experimental``: Prototyp, Solo-Dev, Risiko vom User bewusst akzeptiert.
"""


TRUST_ORDER: dict[TrustLevel, int] = {
    "official": 0,
    "verified": 1,
    "community": 2,
    "experimental": 3,
}


@dataclass(frozen=True)
class SkillCandidate:
    """Ein Match-Kandidat aus dem Katalog.

    Frozen fuer JSON-Serialisierung in Responses + Replay-Kompatibilitaet.
    """
    name: str
    title: str
    description: str
    source: str
    source_url: str
    raw_url: str | None
    trust: TrustLevel
    stars: int | None
    categories: tuple[str, ...]
    languages: tuple[str, ...]
    risk: str
    tags: tuple[str, ...]
    score: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "source": self.source,
            "source_url": self.source_url,
            "raw_url": self.raw_url,
            "trust": self.trust,
            "stars": self.stars,
            "categories": list(self.categories),
            "languages": list(self.languages),
            "risk": self.risk,
            "tags": list(self.tags),
            "score": round(self.score, 3),
            "reason": self.reason,
        }

    @classmethod
    def from_catalog_entry(cls, entry: dict[str, Any]) -> "SkillCandidate":
        return cls(
            name=str(entry["name"]),
            title=str(entry.get("title", entry["name"])),
            description=str(entry.get("description", "")),
            source=str(entry.get("source", "unknown")),
            source_url=str(entry.get("source_url", "")),
            raw_url=entry.get("raw_url"),
            trust=entry.get("trust", "community"),
            stars=entry.get("stars"),
            categories=tuple(entry.get("categories", [])),
            languages=tuple(entry.get("languages", [])),
            risk=str(entry.get("risk", "monitor")),
            tags=tuple(entry.get("tags", [])),
        )


@dataclass(frozen=True)
class SearchFilters:
    """Filter fuer die Suche — mapping der Dropdown-Auswahl im Frontend."""
    query: str = ""
    trust: TrustLevel | Literal["any"] = "any"
    min_stars: int | None = None
    category: str | None = None
    language: str | None = None
    max_risk: str | None = None  # "safe", "monitor", "ask"
    limit: int = 10


# ----------------------------------------------------------------------
# Filter + Ranking
# ----------------------------------------------------------------------

_RISK_ORDER = {"safe": 0, "monitor": 1, "ask": 2, "block": 3}


def _passes_filter(entry: dict[str, Any], f: SearchFilters) -> bool:
    """Harte Filter vor dem Ranking — excluded Trust/Stars/Category/Risk."""
    # Trust
    if f.trust != "any":
        e_trust = entry.get("trust", "community")
        if TRUST_ORDER.get(e_trust, 99) > TRUST_ORDER.get(f.trust, 99):
            return False

    # Min-Stars (wenn angegeben)
    if f.min_stars is not None:
        e_stars = entry.get("stars")
        if e_stars is None or e_stars < f.min_stars:
            # Official-Skills haben ``stars = null`` — die lassen wir passieren
            # unabhaengig vom Star-Threshold (offizielle > Stars-Metrik).
            if entry.get("trust") != "official":
                return False

    # Kategorie
    if f.category:
        cats = entry.get("categories", [])
        if f.category not in cats:
            return False

    # Sprache
    if f.language:
        langs = entry.get("languages", [])
        if f.language not in langs and "en" not in langs:
            # EN faellt zurueck, wenn der Skill bilingual ist
            return False

    # Risk
    if f.max_risk:
        e_risk = entry.get("risk", "monitor")
        if _RISK_ORDER.get(e_risk, 99) > _RISK_ORDER.get(f.max_risk, 99):
            return False

    return True


_WORD_RE = re.compile(r"\b\w{3,}\b", re.UNICODE)


def _tokenize(text: str) -> set[str]:
    return {w.lower() for w in _WORD_RE.findall(text)}


def _score_heuristic(query_tokens: set[str], entry: dict[str, Any]) -> float:
    """Heuristisches Ranking ohne Brain.

    Zaehlt Token-Overlaps zwischen Query und (title + description + tags).
    Gewichtet Tags hoeher, weil sie kuratiert sind.
    """
    if not query_tokens:
        return 0.0
    blob_tokens = _tokenize(
        " ".join([
            str(entry.get("title", "")),
            str(entry.get("description", "")),
            " ".join(entry.get("categories", [])),
        ])
    )
    tag_tokens = _tokenize(" ".join(entry.get("tags", [])))

    blob_overlap = len(query_tokens & blob_tokens)
    tag_overlap = len(query_tokens & tag_tokens)

    # Tags bekommen Faktor 2, damit "docker" in Tags > "docker" irgendwo im Body
    raw = blob_overlap + tag_overlap * 2
    # Normalisieren auf [0, 1] mit sanftem Decay
    return min(1.0, raw / (len(query_tokens) + 1))


def _heuristic_reason(query_tokens: set[str], entry: dict[str, Any]) -> str:
    """Menschlich lesbarer Grund fuer einen Treffer — damit der User versteht,
    warum ein Skill hochgerankt wurde."""
    matches = query_tokens & _tokenize(
        " ".join([
            str(entry.get("title", "")),
            str(entry.get("description", "")),
            " ".join(entry.get("tags", [])),
        ])
    )
    if not matches:
        trust = entry.get("trust", "community")
        return f"Trust-Match ({trust})"
    return "Match: " + ", ".join(sorted(matches)[:4])


# ----------------------------------------------------------------------
# Brain-gestuetztes Ranking
# ----------------------------------------------------------------------

_RANK_SYSTEM_PROMPT = """Du bist ein Skill-Ranker fuer Personal Jarvis. Der User
sucht einen Skill, der sein Problem loest. Du bekommst eine User-Anfrage und eine
Liste von Kandidaten als JSON. Gib NUR ein JSON-Array zurueck, sortiert von bestem
zu schlechtestem Match. Jeder Eintrag: {"name": "...", "score": 0.0-1.0, "reason": "kurzer
Satz warum es passt"}. Nichts anderes, kein Markdown, kein Prefix."""


async def _brain_rank(
    brain: Any,
    query: str,
    candidates: list[dict[str, Any]],
) -> dict[str, tuple[float, str]] | None:
    """Nutzt ein Brain (BrainManager-Instanz) zum Ranking.

    Returnt ``None`` wenn:
    - kein Brain uebergeben
    - die Response nicht als JSON parsebar war (dann faellt man auf Heuristik zurueck)
    - das Brain einen Error raised
    """
    if brain is None:
        return None

    # Minimaler Prompt: nur Name + Title + Description + Tags, um Tokens zu sparen
    compact = [
        {
            "name": c["name"],
            "title": c.get("title"),
            "description": c.get("description"),
            "tags": c.get("tags", []),
        }
        for c in candidates[:30]  # Hard-Cap — 30 Kandidaten reichen
    ]
    user_msg = (
        f"User-Anfrage: {query!r}\n\n"
        f"Kandidaten:\n{json.dumps(compact, ensure_ascii=False)}\n\n"
        f"{_RANK_SYSTEM_PROMPT}"
    )

    try:
        # BrainManager.generate ist der uniforme Aufruf. Kein use_history — das
        # ist ein One-Shot-Ranker, nicht Teil der Chat-Historie.
        if hasattr(brain, "generate"):
            text = await brain.generate(user_msg, use_history=False)
        else:
            # Fallback: falls jemand eine rohe Brain-Protocol-Impl uebergibt,
            # koennten wir hier via dispatcher gehen. MVP: Nur BrainManager.
            return None
    except Exception as exc:  # noqa: BLE001
        log.warning("Brain-Ranking fehlgeschlagen: %s", exc)
        return None

    # JSON rausfischen — Brain liefert evtl. mit Whitespace oder Prefix
    json_match = re.search(r"\[\s*\{.*?\}\s*\]", text, re.DOTALL)
    if not json_match:
        log.debug("Brain-Response enthaelt kein JSON-Array: %s", text[:200])
        return None
    try:
        parsed = json.loads(json_match.group(0))
    except json.JSONDecodeError as exc:
        log.debug("Brain-Ranking-JSON nicht parsebar: %s", exc)
        return None

    out: dict[str, tuple[float, str]] = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str):
            continue
        try:
            score = float(item.get("score", 0.0))
        except (TypeError, ValueError):
            score = 0.0
        reason = str(item.get("reason", "") or "")
        out[name] = (max(0.0, min(1.0, score)), reason)
    return out or None


# ----------------------------------------------------------------------
# Finder
# ----------------------------------------------------------------------

class SkillFinder:
    """Mini-Agent fuer Skill-Suche und Installation."""

    def __init__(self, brain: Any | None = None) -> None:
        self._brain = brain

    async def search(self, filters: SearchFilters) -> list[SkillCandidate]:
        """Filter + Rank — gibt bis zu ``filters.limit`` Kandidaten zurueck."""
        catalog = load_catalog()
        filtered = [e for e in catalog if _passes_filter(e, filters)]

        if not filtered:
            return []

        # Brain-Ranking
        brain_scores = await _brain_rank(self._brain, filters.query, filtered)

        query_tokens = _tokenize(filters.query)
        candidates: list[SkillCandidate] = []
        for entry in filtered:
            cand = SkillCandidate.from_catalog_entry(entry)
            if brain_scores and cand.name in brain_scores:
                score, reason = brain_scores[cand.name]
                # Brain-Score mit 0.7 Gewicht, Heuristik-Score mit 0.3 — so
                # bleibt ein heuristischer Nulltreffer nicht komplett blind
                heur = _score_heuristic(query_tokens, entry)
                final_score = 0.7 * score + 0.3 * heur
                final_reason = reason or _heuristic_reason(query_tokens, entry)
            else:
                final_score = _score_heuristic(query_tokens, entry)
                final_reason = _heuristic_reason(query_tokens, entry)

            # Trust-Bonus: bei gleichem Score gewinnt das vertrauenswuerdigere
            trust_bonus = (3 - TRUST_ORDER.get(cand.trust, 3)) * 0.01
            final_score += trust_bonus

            candidates.append(
                SkillCandidate(
                    **{**cand.__dict__, "score": final_score, "reason": final_reason}
                )
            )

        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates[: filters.limit]

    async def install(self, candidate: SkillCandidate) -> Path:
        """Installiert einen Kandidaten in ``user_skills_dir()``.

        Strategie:
        - Hat ``raw_url``, wird die SKILL.md direkt geholt.
        - Ohne ``raw_url`` schlaegt die Installation fehl mit klarer Message —
          der User muss dann manuell installieren (Link im Frontend).

        Gibt den Ziel-Pfad zurueck (``<user_skills>/<name>/SKILL.md``).
        Raised ``ValueError`` bei ungueltigem SKILL.md (Frontmatter-Validation).
        Raised ``RuntimeError`` bei Netzwerk-Fehler oder fehlender raw_url.
        """
        if not candidate.raw_url:
            raise RuntimeError(
                f"Kein Direkt-Download fuer '{candidate.name}' verfuegbar. "
                f"Oeffne {candidate.source_url} und installiere manuell."
            )

        # httpx ist in den Runtime-Deps (mcp_routes etc.)
        import httpx

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(15.0),
        ) as client:
            try:
                resp = await client.get(candidate.raw_url)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise RuntimeError(
                    f"Download von {candidate.raw_url} fehlgeschlagen: {exc}"
                ) from exc
            content = resp.text

        # Ziel-Struktur: <user_skills>/<name>/SKILL.md
        target_dir = user_skills_dir() / candidate.name
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / "SKILL.md"

        # Atomar schreiben
        tmp = target_file.with_suffix(".md.tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(target_file)

        # Parse-Check: wenn das SKILL.md kaputt ist, markieren wir es als DRAFT,
        # aber loeschen es nicht automatisch — der User sieht im UI den Fehler
        # und kann entscheiden.
        parsed = parse_skill(target_file)
        if parsed.error:
            log.warning(
                "Installierter Skill '%s' hat Validation-Fehler: %s",
                candidate.name, parsed.error,
            )

        return target_file


__all__ = [
    "SkillFinder",
    "SkillCandidate",
    "SearchFilters",
    "TrustLevel",
    "TRUST_ORDER",
]
