    """Which stores actually existed — an empty section is explainable."""
    index: IndexStatus
    """Whether the coding-CLI numbers are final yet, and how far they are."""


class DayRow(BaseModel):
    """One calendar day of spend, already broken down.

    The section shows ONE of these per day rather than one row per session:
    a session row carries the timestamp of its FIRST call, so a long morning
    run sorts below a short one started later and the day's real work reads
    as if it never happened. A day has no such ambiguity.
    """

    date: str
    """Local ``YYYY-MM-DD`` — the day as the person's own clock drew it."""
    since_ms: int
    until_ms: int
    totals: Totals
    by_model: list[Bucket]
    by_provider: list[Bucket]
    by_role: list[Bucket]
    by_surface: list[Bucket]


class DailyLedger(BaseModel):
    days: list[DayRow]
    currency: Currency


class EntryRow(BaseModel):
    ts_ms: int
    surface: str
    role: str
    provider: str
    model: str
    tokens_in: int
    tokens_out: int
    tokens_cached: int
    tokens_total: int
    cost_usd: float
    price_source: str
    ref_id: str
    label: str
    account_id: str = ""


class EntriesPage(BaseModel):
    items: list[EntryRow]
    total: int
    limit: int
    offset: int


class RateRow(BaseModel):
    model: str
    input_usd_per_mtok: float | None