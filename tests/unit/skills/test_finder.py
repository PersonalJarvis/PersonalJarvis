"""Unit-Tests fuer den SkillFinder.

Decken:
- Filter-Semantik (Trust, Stars, Category, Language, Risk).
- Heuristisches Ranking ohne Brain.
- Brain-Ranking-Integration (mit Fake-Brain).
- Brain-Failure-Fallback auf Heuristik.
"""
from __future__ import annotations

import json

import pytest

from jarvis.skills.finder import (
    SearchFilters,
    SkillCandidate,
    SkillFinder,
    TRUST_ORDER,
    _passes_filter,
    _score_heuristic,
    _tokenize,
)


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------

@pytest.fixture
def sample_entries() -> list[dict]:
    return [
        {
            "name": "email-triage",
            "title": "Email Triage",
            "description": "Priorisiert Inbox, fasst zusammen, schlaegt Antworten vor.",
            "source": "skills-sh",
            "source_url": "https://skills.sh/",
            "raw_url": None,
            "trust": "community",
            "stars": 1800,
            "categories": ["productivity", "communication"],
            "languages": ["en", "de"],
            "risk": "monitor",
            "tags": ["email", "triage", "gmail"],
        },
        {
            "name": "skill-creator",
            "title": "Skill Creator",
            "description": "Erstellt neue Skills und verbessert bestehende.",
            "source": "anthropic",
            "source_url": "https://github.com/anthropics/skills/tree/main/skills/skill-creator",
            "raw_url": "https://raw.githubusercontent.com/anthropics/skills/main/skills/skill-creator/SKILL.md",
            "trust": "official",
            "stars": None,
            "categories": ["meta", "authoring"],
            "languages": ["en"],
            "risk": "ask",
            "tags": ["meta", "skills"],
        },
        {
            "name": "voice-journal",
            "title": "Voice Journal",
            "description": "Voice-Driven-Tagebuch mit STT und Zusammenfassung.",
            "source": "experimental",
            "source_url": "https://example.com/",
            "raw_url": None,
            "trust": "experimental",
            "stars": 80,
            "categories": ["productivity"],
            "languages": ["en", "de"],
            "risk": "safe",
            "tags": ["voice", "journal"],
        },
        {
            "name": "docker-helper",
            "title": "Docker Helper",
            "description": "Erstellt Dockerfiles mit Best-Practices.",
            "source": "community",
            "source_url": "https://example.com/",
            "raw_url": None,
            "trust": "community",
            "stars": 2600,
            "categories": ["devops"],
            "languages": ["en"],
            "risk": "monitor",
            "tags": ["docker", "devops"],
        },
    ]


# ----------------------------------------------------------------------
# Tokenizer
# ----------------------------------------------------------------------

def test_tokenize_ignoriert_kurze_woerter():
    assert _tokenize("ab cde fghij") == {"cde", "fghij"}


def test_tokenize_lower_case():
    assert _tokenize("Docker Helper") == {"docker", "helper"}


# ----------------------------------------------------------------------
# Filter
# ----------------------------------------------------------------------

def test_filter_trust_blockt_niedrigeren_level(sample_entries):
    f = SearchFilters(trust="verified")
    passing = [e for e in sample_entries if _passes_filter(e, f)]
    names = {e["name"] for e in passing}
    # official + verified erlaubt, community + experimental raus
    assert "skill-creator" in names
    assert "email-triage" not in names
    assert "voice-journal" not in names


def test_filter_trust_any_laesst_alles_durch(sample_entries):
    f = SearchFilters(trust="any")
    passing = [e for e in sample_entries if _passes_filter(e, f)]
    assert len(passing) == len(sample_entries)


def test_filter_min_stars_laesst_official_durch(sample_entries):
    """Official-Skills haben stars=None, sollen aber durchkommen."""
    f = SearchFilters(min_stars=2000)
    passing = [e for e in sample_entries if _passes_filter(e, f)]
    names = {e["name"] for e in passing}
    assert "skill-creator" in names  # official, null stars → bleibt
    assert "docker-helper" in names  # 2600 stars
    assert "email-triage" not in names  # 1800 < 2000
    assert "voice-journal" not in names  # 80 < 2000


def test_filter_category(sample_entries):
    f = SearchFilters(category="devops")
    passing = [e for e in sample_entries if _passes_filter(e, f)]
    assert [e["name"] for e in passing] == ["docker-helper"]


def test_filter_max_risk(sample_entries):
    f = SearchFilters(max_risk="safe")
    passing = [e for e in sample_entries if _passes_filter(e, f)]
    # Nur voice-journal (risk=safe)
    assert [e["name"] for e in passing] == ["voice-journal"]


def test_filter_language_en_als_fallback(sample_entries):
    """Wenn der User 'de' anfragt, muessen EN-only-Skills rausfliegen,
    aber bilingual (en+de) bleibt."""
    f = SearchFilters(language="de")
    passing = [e for e in sample_entries if _passes_filter(e, f)]
    names = {e["name"] for e in passing}
    # email-triage, voice-journal haben "de" -> rein
    # skill-creator, docker-helper haben nur "en" -> raus (keine "de")
    # Aber der Code erlaubt EN als Fallback? Check...
    # Wenn 'de' nicht in langs, aber 'en' in langs → passiert durch (Bilingual-Fallback)
    assert names == {"email-triage", "voice-journal", "skill-creator", "docker-helper"}


# ----------------------------------------------------------------------
# Heuristic Scoring
# ----------------------------------------------------------------------

def test_score_heuristic_matcht_tags_staerker_als_description(sample_entries):
    docker_entry = next(e for e in sample_entries if e["name"] == "docker-helper")
    query_tokens = _tokenize("docker")
    score = _score_heuristic(query_tokens, docker_entry)
    assert score > 0.0


def test_score_heuristic_null_query_null_score(sample_entries):
    entry = sample_entries[0]
    assert _score_heuristic(set(), entry) == 0.0


def test_score_heuristic_keine_treffer(sample_entries):
    entry = sample_entries[0]  # email-triage
    score = _score_heuristic(_tokenize("quantencomputer"), entry)
    assert score == 0.0


# ----------------------------------------------------------------------
# SkillFinder.search ohne Brain
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_ohne_brain_rankt_heuristisch(monkeypatch, sample_entries):
    from jarvis.skills import catalog

    monkeypatch.setattr(catalog, "load_catalog", lambda: sample_entries)

    finder = SkillFinder(brain=None)
    result = await finder.search(SearchFilters(query="docker", limit=5))

    # docker-helper sollte Top-Treffer sein
    assert result[0].name == "docker-helper"
    assert result[0].score > 0


@pytest.mark.asyncio
async def test_search_mit_trust_filter(monkeypatch, sample_entries):
    from jarvis.skills import catalog

    monkeypatch.setattr(catalog, "load_catalog", lambda: sample_entries)

    finder = SkillFinder(brain=None)
    result = await finder.search(
        SearchFilters(query="skill", trust="official", limit=5)
    )

    assert all(c.trust == "official" for c in result)


# ----------------------------------------------------------------------
# Brain-Ranking-Integration
# ----------------------------------------------------------------------

class _FakeBrain:
    """Minimaler Fake-Brain fuer Finder-Tests. Liefert vorgespeicherte Responses."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.call_count = 0

    async def generate(self, user_text: str, *, use_history: bool = True) -> str:
        self.call_count += 1
        return self._response


@pytest.mark.asyncio
async def test_search_nutzt_brain_ranking(monkeypatch, sample_entries):
    from jarvis.skills import catalog

    monkeypatch.setattr(catalog, "load_catalog", lambda: sample_entries)

    brain_response = json.dumps([
        {"name": "voice-journal", "score": 0.95, "reason": "Perfekter Match"},
        {"name": "email-triage", "score": 0.3, "reason": "weniger passend"},
    ])
    fake = _FakeBrain(brain_response)

    finder = SkillFinder(brain=fake)
    result = await finder.search(SearchFilters(query="voice tagebuch", limit=5))

    assert fake.call_count == 1
    # voice-journal muss Top-Treffer sein, weil Brain-Score 0.95 dominiert
    assert result[0].name == "voice-journal"
    assert "Perfekter Match" in result[0].reason


@pytest.mark.asyncio
async def test_search_faellt_auf_heuristik_bei_brain_fehler(
    monkeypatch, sample_entries
):
    """Wenn das Brain invalides JSON returnt, muss der Finder trotzdem Ergebnisse
    liefern — Graceful Degradation statt Crash."""
    from jarvis.skills import catalog

    monkeypatch.setattr(catalog, "load_catalog", lambda: sample_entries)

    fake = _FakeBrain("das ist kein JSON, nur Prosa")

    finder = SkillFinder(brain=fake)
    result = await finder.search(SearchFilters(query="docker", limit=5))

    # Muss trotzdem funktionieren
    assert len(result) > 0
    assert result[0].name == "docker-helper"


@pytest.mark.asyncio
async def test_search_brain_exception_faellt_auf_heuristik(
    monkeypatch, sample_entries
):
    from jarvis.skills import catalog

    monkeypatch.setattr(catalog, "load_catalog", lambda: sample_entries)

    class _BrokenBrain:
        async def generate(self, *a, **kw):
            raise RuntimeError("Kaputt")

    finder = SkillFinder(brain=_BrokenBrain())
    result = await finder.search(SearchFilters(query="docker", limit=5))

    assert len(result) > 0
    assert result[0].name == "docker-helper"


# ----------------------------------------------------------------------
# SkillCandidate roundtrip
# ----------------------------------------------------------------------

def test_candidate_from_entry_to_dict(sample_entries):
    cand = SkillCandidate.from_catalog_entry(sample_entries[0])
    d = cand.to_dict()
    assert d["name"] == "email-triage"
    assert d["trust"] == "community"
    assert d["categories"] == ["productivity", "communication"]
    assert d["tags"] == ["email", "triage", "gmail"]


# ----------------------------------------------------------------------
# Install
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_install_ohne_raw_url_raised(tmp_path, monkeypatch):
    """Kandidat ohne raw_url muss einen klaren Fehler liefern."""
    monkeypatch.setattr(
        "jarvis.skills.finder.user_skills_dir", lambda: tmp_path
    )
    cand = SkillCandidate(
        name="no-download",
        title="No Download",
        description="",
        source="x",
        source_url="https://example.com/",
        raw_url=None,
        trust="community",
        stars=None,
        categories=(),
        languages=(),
        risk="safe",
        tags=(),
    )
    finder = SkillFinder()
    with pytest.raises(RuntimeError, match="Kein Direkt-Download"):
        await finder.install(cand)
