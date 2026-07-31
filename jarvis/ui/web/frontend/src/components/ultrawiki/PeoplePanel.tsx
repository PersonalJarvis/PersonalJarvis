/**
 * People — who the knowledge base thinks exists, and what it knows about them.
 *
 * Explore answers "what topics run through my life". This answers the other
 * half: the same corpus seen through PEOPLE. One person is usually scattered
 * across a dozen spellings, two e-mail addresses and an address-book entry,
 * and the identity layer's whole job is folding those back into one. This
 * view is where that becomes visible — and correctable.
 *
 * Layout: a list on the left, one thing at a time on the right.
 *
 *   ┌───────────┬──────────────────────────────┐
 *   │ people    │  the decision queue          │
 *   │ (18 rem)  │  ── or one person's profile  │
 *   └───────────┴──────────────────────────────┘
 *
 * The right column opens on the QUEUE, not on an empty profile pane. Merge
 * proposals are the only thing here that goes stale by being ignored: an
 * unanswered "are these the same person?" leaves the knowledge base holding
 * one human as two, and every later answer inherits the split. A profile, by
 * contrast, is only interesting once you have picked someone.
 *
 * A profile is two halves, because the store holds two different kinds of
 * knowledge about a person: the standing FACTS (the identifiers they are
 * known by) and what HAPPENED (the episodic events they took part in). Under
 * both sits the merge history, which is the only place an over-eager merge
 * can be taken back.
 */
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  ArrowLeft,
  CalendarClock,
  ExternalLink,
  Loader2,
  Search,
  Undo2,
  UserPlus,
  Users,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import {
  IDENTITY_QUERY_KEY,
  MergeQueuePanel,
} from "@/components/ultrawiki/MergeQueuePanel";
import {
  ULTRAWIKI_IDENTIFIER_KINDS,
  fetchIdentityQueue,
  fetchPeople,
  fetchPerson,
  fetchPersonEvents,
  identityErrorMessage,
  seedIdentities,
  undoableMerges,
  unmergeIdentity,
  type UltraWikiIdentifier,
  type UltraWikiMergeRecord,
  type UltraWikiPerson,
  type UltraWikiPersonEvent,
  type UltraWikiPersonRow,
} from "@/lib/ultrawikiIdentityApi";

/** One request covers any realistic address book; typing filters in place. */
const PEOPLE_LIMIT = 500;
/** Long enough that a stray letter does not fire a request of its own. */
const IDENTIFIER_SEARCH_MIN = 2;
const EVENT_LIMIT = 25;

export interface PeoplePanelProps {
  /** The way out of an empty knowledge base — no source, nobody to know. */
  onOpenSources?: () => void;
}

export function PeoplePanel({ onOpenSources }: PeoplePanelProps): JSX.Element {
  const t = useT();
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<number | null>(null);
  const [pane, setPane] = useState<"queue" | "profile">("queue");

  const peopleQuery = useQuery({
    queryKey: [...IDENTITY_QUERY_KEY, "people"],
    queryFn: () => fetchPeople({ limit: PEOPLE_LIMIT }),
    staleTime: 30_000,
  });

  const queueQuery = useQuery({
    queryKey: [...IDENTITY_QUERY_KEY, "queue"],
    queryFn: () => fetchIdentityQueue({ status: "pending", limit: 100 }),
    staleTime: 15_000,
  });

  const people = peopleQuery.data?.people ?? [];
  const needle = search.trim().toLowerCase();

  const localHits = useMemo(() => {
    if (!needle) return people;
    return people.filter((person) =>
      person.display_name.toLowerCase().includes(needle),
    );
  }, [people, needle]);

  // A name search runs locally and stays instant. Searching by e-mail or
  // phone number cannot: identifiers are not in the list payload, and loading
  // every identifier of every person to make them searchable would be a far
  // bigger cost than one request on the rare miss. So the server is only
  // asked once the local list has genuinely nothing.
  const identifierQuery = useQuery({
    queryKey: [...IDENTITY_QUERY_KEY, "people", "search", needle],
    queryFn: () => fetchPeople({ q: needle, limit: 200 }),
    enabled:
      needle.length >= IDENTIFIER_SEARCH_MIN &&
      localHits.length === 0 &&
      !peopleQuery.isLoading,
    staleTime: 30_000,
  });

  const identifierHits = identifierQuery.data?.people ?? [];
  const byIdentifier = localHits.length === 0 && identifierHits.length > 0;
  const rows = byIdentifier ? identifierHits : localHits;

  const seedMutation = useMutation({
    mutationFn: seedIdentities,
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: IDENTITY_QUERY_KEY }),
  });

  const proposals = queueQuery.data?.proposals ?? [];
  const counts = peopleQuery.data?.counts ?? {};

  const openPerson = (entityId: number) => {
    setSelected(entityId);
    setPane("profile");
  };

  if (peopleQuery.isLoading) {
    return (
      <div
        data-testid="people-loading"
        className="flex flex-1 items-center justify-center p-6 text-sm text-muted-foreground"
      >
        {t("ultrawiki.people.loading")}
      </div>
    );
  }

  if (peopleQuery.isError) {
    return (
      <div className="flex flex-1 items-center justify-center p-6">
        <p
          role="alert"
          data-testid="people-error"
          className="max-w-md rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive"
        >
          {t("ultrawiki.people.error")}
        </p>
      </div>
    );
  }

  return (
    // h-full rather than flex-1, and min-w-0 on every column: the same two
    // rules the Explore workspace needs, for the same two reasons (an inner
    // list must scroll instead of stretching the page, and no child may set
    // the section's minimum width).
    <div className="flex h-full min-h-0 min-w-0 flex-col" data-testid="people-panel">
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
        <div className="relative min-w-[12rem] flex-1">
          <Search
            className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
            aria-hidden
          />
          <input
            data-testid="people-search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={t("ultrawiki.people.search_placeholder")}
            aria-label={t("ultrawiki.people.search_placeholder")}
            className="h-8 w-full rounded-lg border border-border bg-background/60 pl-8 pr-2.5 text-xs outline-none transition-colors focus-visible:border-primary/50 focus-visible:ring-1 focus-visible:ring-primary/40"
          />
        </div>

        <button
          type="button"
          data-testid="people-open-queue"
          onClick={() => setPane("queue")}
          data-active={pane === "queue" ? "true" : "false"}
          className={cn(
            "inline-flex shrink-0 items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[11px] transition-colors",
            pane === "queue"
              ? "border-primary/50 bg-primary/10 text-foreground"
              : "border-border text-muted-foreground hover:text-foreground",
          )}
        >
          <Users className="h-3.5 w-3.5" aria-hidden />
          {proposals.length > 0
            ? t("ultrawiki.people.queue_open").replace(
                "{0}",
                String(proposals.length),
              )
            : t("ultrawiki.people.queue_tab")}
        </button>

        <button
          type="button"
          data-testid="people-seed"
          onClick={() => seedMutation.mutate()}
          disabled={seedMutation.isPending}
          title={t("ultrawiki.people.seed_hint")}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-[11px] text-muted-foreground transition-colors hover:text-foreground disabled:opacity-60"
        >
          {seedMutation.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          ) : (
            <UserPlus className="h-3.5 w-3.5" aria-hidden />
          )}
          {seedMutation.isPending
            ? t("ultrawiki.people.seeding")
            : t("ultrawiki.people.seed")}
        </button>
      </div>

      {seedMutation.data && (
        <p
          data-testid="people-seed-result"
          className="border-b border-border bg-muted/20 px-4 py-1.5 text-[11px] tabular-nums text-muted-foreground"
        >
          {t("ultrawiki.people.seed_result")
            .replace("{0}", String(seedMutation.data.report.created))
            .replace("{1}", String(seedMutation.data.report.linked))
            .replace("{2}", String(seedMutation.data.report.merged))
            .replace("{3}", String(seedMutation.data.report.queued))}
        </p>
      )}

      {seedMutation.error && (
        <p
          role="alert"
          data-testid="people-seed-error"
          className="border-b border-border bg-destructive/10 px-4 py-1.5 text-[11px] text-destructive"
        >
          {t("ultrawiki.people.action_failed").replace(
            "{0}",
            identityErrorMessage(seedMutation.error),
          )}
        </p>
      )}

      <div className="flex min-h-0 min-w-0 flex-1 flex-col lg:flex-row">
        <aside className="flex min-h-0 min-w-0 shrink-0 flex-col border-b border-border lg:w-[18rem] lg:border-b-0 lg:border-r">
          <p className="shrink-0 px-4 pb-1.5 pt-2 text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
            {t("ultrawiki.people.count").replace("{0}", String(rows.length))}
          </p>

          <div className="scrollbar-jarvis min-h-0 flex-1 overflow-y-auto px-1.5 pb-2 max-lg:max-h-56">
            {byIdentifier && (
              <p
                data-testid="people-by-identifier"
                className="px-2.5 py-1.5 text-[11px] text-muted-foreground"
              >
                {t("ultrawiki.people.found_by_identifier")}
              </p>
            )}

            {rows.length === 0 && needle !== "" && !identifierQuery.isFetching && (
              <p
                data-testid="people-no-search-hits"
                className="px-2.5 py-3 text-xs text-muted-foreground"
              >
                {t("ultrawiki.people.no_search_hits").replace("{0}", search.trim())}
              </p>
            )}

            {rows.length === 0 && needle === "" && (
              <div
                data-testid="people-empty"
                className="px-2.5 py-3 text-xs text-muted-foreground"
              >
                <p>{t("ultrawiki.people.empty")}</p>
                {onOpenSources && (
                  <button
                    type="button"
                    data-testid="people-empty-sources"
                    onClick={onOpenSources}
                    className="mt-1 underline underline-offset-2 hover:text-foreground"
                  >
                    {t("ultrawiki.panel.tab_sources")}
                  </button>
                )}
              </div>
            )}

            {rows.map((person) => (
              <PersonRow
                key={person.id}
                person={person}
                active={person.id === selected && pane === "profile"}
                onSelect={() => openPerson(person.id)}
              />
            ))}
          </div>

          <p
            data-testid="people-counts"
            className="shrink-0 border-t border-border px-4 py-1.5 text-[10px] tabular-nums text-muted-foreground"
          >
            {t("ultrawiki.people.counts_line")
              .replace("{0}", String(counts.people ?? 0))
              .replace("{1}", String(counts.identifiers ?? 0))
              .replace("{2}", String(counts.merges ?? 0))}
          </p>
        </aside>

        <section className="scrollbar-jarvis min-h-0 min-w-0 flex-1 overflow-y-auto">
          {pane === "queue" ? (
            <div className="space-y-2.5 p-3.5" data-testid="people-queue-pane">
              <p className="text-[11px] leading-relaxed text-muted-foreground">
                {t("ultrawiki.people.queue_intro")}
              </p>
              {queueQuery.isError ? (
                <p
                  role="alert"
                  data-testid="people-queue-error"
                  className="rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive"
                >
                  {t("ultrawiki.people.action_failed").replace(
                    "{0}",
                    identityErrorMessage(queueQuery.error),
                  )}
                </p>
              ) : (
                <MergeQueuePanel
                  proposals={proposals}
                  onOpenPerson={openPerson}
                />
              )}
            </div>
          ) : selected === null ? (
            <p
              data-testid="people-pick-hint"
              className="p-6 text-xs text-muted-foreground"
            >
              {t("ultrawiki.people.pick_hint")}
            </p>
          ) : (
            <PersonProfile
              entityId={selected}
              onOpenPerson={openPerson}
              onBack={() => setPane("queue")}
            />
          )}
        </section>
      </div>
    </div>
  );
}

function PersonRow({
  person,
  active,
  onSelect,
}: {
  person: UltraWikiPersonRow;
  active: boolean;
  onSelect: () => void;
}): JSX.Element {
  const t = useT();
  return (
    <button
      type="button"
      onClick={onSelect}
      data-testid={`person-row-${person.id}`}
      data-active={active ? "true" : "false"}
      className={cn(
        "block w-full rounded-lg px-2.5 py-1.5 text-left transition-colors",
        active
          ? "bg-primary/10 ring-1 ring-inset ring-primary/25"
          : "hover:bg-muted/60",
      )}
    >
      <span className="flex items-baseline justify-between gap-2">
        <span
          className={cn(
            "truncate text-xs",
            active ? "text-primary" : "text-foreground",
          )}
        >
          {person.display_name}
        </span>
        <span
          className="shrink-0 text-[11px] tabular-nums text-muted-foreground"
          title={t("ultrawiki.people.identifiers").replace(
            "{0}",
            String(person.identifier_count),
          )}
        >
          {person.identifier_count}
        </span>
      </span>
    </button>
  );
}

// ---------------------------------------------------------------------------
// One person
// ---------------------------------------------------------------------------

function PersonProfile({
  entityId,
  onOpenPerson,
  onBack,
}: {
  entityId: number;
  onOpenPerson: (id: number) => void;
  onBack: () => void;
}): JSX.Element {
  const t = useT();

  const personQuery = useQuery({
    queryKey: [...IDENTITY_QUERY_KEY, "person", entityId],
    queryFn: () => fetchPerson(entityId),
    staleTime: 15_000,
  });

  // Their timeline is a SEPARATE layer and a separate request on purpose: a
  // corpus with no event extraction yet still has a working profile, and a
  // failure here degrades to one muted line instead of taking the identity
  // half of the page down with it.
  const eventsQuery = useQuery({
    queryKey: [...IDENTITY_QUERY_KEY, "events", entityId],
    queryFn: () => fetchPersonEvents(entityId, { limit: EVENT_LIMIT }),
    staleTime: 30_000,
    retry: false,
  });

  if (personQuery.isLoading) {
    return (
      <p
        data-testid="person-loading"
        className="p-6 text-xs text-muted-foreground"
      >
        {t("ultrawiki.people.loading")}
      </p>
    );
  }

  if (personQuery.isError || !personQuery.data) {
    return (
      <p
        role="alert"
        data-testid="person-error"
        className="m-4 rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive"
      >
        {t("ultrawiki.people.action_failed").replace(
          "{0}",
          identityErrorMessage(personQuery.error),
        )}
      </p>
    );
  }

  const { person, forwarded } = personQuery.data;
  const events = eventsQuery.data?.events ?? [];

  return (
    <div data-testid={`person-profile-${person.id}`} className="pb-6">
      <header className="flex items-start gap-2 border-b border-border px-4 py-3">
        <button
          type="button"
          data-testid="person-back"
          onClick={onBack}
          aria-label={t("ultrawiki.people.back")}
          title={t("ultrawiki.people.back")}
          className="mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded text-muted-foreground transition-colors hover:text-primary"
        >
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
        </button>
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-sm text-foreground">
            {person.display_name}
          </h3>
          <p className="mt-0.5 text-[11px] tabular-nums text-muted-foreground">
            {t("ultrawiki.people.identifiers").replace(
              "{0}",
              String(person.identifiers.length),
            )}
            {person.merged_from.length > 0 && (
              <>
                {" · "}
                {t("ultrawiki.people.folded_count").replace(
                  "{0}",
                  String(person.merged_from.length),
                )}
              </>
            )}
          </p>
          {forwarded && (
            <p
              data-testid="person-forwarded"
              className="mt-1 text-[11px] text-muted-foreground"
            >
              {t("ultrawiki.people.forwarded").replace(
                "{0}",
                String(person.requested_id),
              )}
            </p>
          )}
        </div>
      </header>

      <FactsSection identifiers={person.identifiers} />

      <EventsSection
        events={events}
        total={eventsQuery.data?.total ?? 0}
        failed={eventsQuery.isError}
        loading={eventsQuery.isLoading}
      />

      {person.pending_proposals.length > 0 && (
        <section className="border-t border-border px-4 py-3">
          <h4 className="text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
            {t("ultrawiki.people.open_questions")}
          </h4>
          <MergeQueuePanel
            className="mt-2"
            proposals={person.pending_proposals}
            onOpenPerson={onOpenPerson}
          />
        </section>
      )}

      <MergeHistory person={person} />
    </div>
  );
}

/**
 * The standing facts — what this person is KNOWN BY.
 *
 * Grouped by kind rather than listed flat, because the kinds are not equally
 * interesting: an e-mail address is why two identities merged, a name variant
 * is usually just spelling. Grouping puts the deterministic ones together.
 */
function FactsSection({
  identifiers,
}: {
  identifiers: UltraWikiIdentifier[];
}): JSX.Element {
  const t = useT();
  const groups = useMemo(() => {
    const byKind = new Map<string, string[]>();
    for (const item of identifiers) {
      const label = item.display_value || item.value;
      if (!label) continue;
      const bucket = byKind.get(item.kind);
      if (bucket) bucket.push(label);
      else byKind.set(item.kind, [label]);
    }
    return ULTRAWIKI_IDENTIFIER_KINDS.filter((kind) => byKind.has(kind)).map(
      (kind) => ({ kind, values: byKind.get(kind) as string[] }),
    );
  }, [identifiers]);

  return (
    <section className="border-b border-border px-4 py-3" data-testid="person-facts">
      <h4 className="text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
        {t("ultrawiki.people.facts_title")}
      </h4>
      {groups.length === 0 ? (
        <p className="mt-1 text-xs text-muted-foreground">
          {t("ultrawiki.people.no_facts")}
        </p>
      ) : (
        <dl className="mt-2 space-y-1.5">
          {groups.map((group) => (
            <div
              key={group.kind}
              data-testid={`person-facts-${group.kind}`}
              className="flex flex-wrap items-baseline gap-x-2 gap-y-1"
            >
              <dt className="w-24 shrink-0 text-[11px] text-muted-foreground">
                {t(`ultrawiki.people.facts_${group.kind}`)}
              </dt>
              <dd className="flex min-w-0 flex-wrap gap-1.5">
                {group.values.map((value) => (
                  <span
                    key={value}
                    className="rounded-full border border-border px-2 py-0.5 text-[11px] text-foreground"
                  >
                    {value}
                  </span>
                ))}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  );
}

/** What HAPPENED — the episodic half of a profile. */
function EventsSection({
  events,
  total,
  failed,
  loading,
}: {
  events: UltraWikiPersonEvent[];
  total: number;
  failed: boolean;
  loading: boolean;
}): JSX.Element {
  const t = useT();
  return (
    <section className="border-b border-border px-4 py-3" data-testid="person-events">
      <h4 className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
        <CalendarClock className="h-3 w-3" aria-hidden />
        {t("ultrawiki.people.events_title")}
      </h4>

      {loading && (
        <p className="mt-1 text-xs text-muted-foreground">
          {t("ultrawiki.people.loading")}
        </p>
      )}

      {!loading && failed && (
        <p
          data-testid="person-events-failed"
          className="mt-1 flex items-center gap-1.5 text-[11px] text-muted-foreground"
        >
          <AlertCircle className="h-3 w-3 shrink-0" aria-hidden />
          {t("ultrawiki.people.events_failed")}
        </p>
      )}

      {!loading && !failed && events.length === 0 && (
        <p className="mt-1 text-xs text-muted-foreground">
          {t("ultrawiki.people.no_events")}
        </p>
      )}

      {events.length > 0 && (
        <ol className="mt-2 space-y-1.5">
          {events.map((event) => (
            <li
              key={event.id}
              data-testid={`person-event-${event.id}`}
              className="rounded-lg border border-border/70 bg-card/40 px-2.5 py-2"
            >
              <p className="flex flex-wrap items-baseline gap-x-2">
                <span className="text-[11px] tabular-nums text-muted-foreground">
                  {event.date_label || event.occurred_at.slice(0, 10)}
                </span>
                <span className="min-w-0 flex-1 text-xs text-foreground">
                  {event.title}
                </span>
              </p>
              {event.summary && (
                <p className="mt-0.5 line-clamp-2 text-[11px] leading-relaxed text-muted-foreground">
                  {event.summary}
                </p>
              )}
              <p className="mt-1 flex flex-wrap items-center gap-x-2 text-[10px] text-muted-foreground">
                {event.place && (
                  <span>
                    {t("ultrawiki.people.event_place").replace("{0}", event.place)}
                  </span>
                )}
                {event.permalink && (
                  <a
                    href={event.permalink}
                    data-testid={`person-event-link-${event.id}`}
                    className="inline-flex items-center gap-0.5 transition-colors hover:text-primary"
                  >
                    <ExternalLink className="h-2.5 w-2.5" aria-hidden />
                    {t("ultrawiki.people.open_source")}
                  </a>
                )}
              </p>
            </li>
          ))}
        </ol>
      )}

      {events.length > 0 && total > events.length && (
        <p
          data-testid="person-events-more"
          className="mt-1.5 text-[10px] tabular-nums text-muted-foreground"
        >
          {t("ultrawiki.people.events_shown")
            .replace("{0}", String(events.length))
            .replace("{1}", String(total))}
        </p>
      )}
    </section>
  );
}

/**
 * The merge history — and the undo it exists for.
 *
 * Every merge stays on the record, undone or not: "these two were once one
 * person" is a fact about the knowledge base even after the split. Only a
 * merge that still STANDS gets an undo button, and the store refuses one that
 * a later merge shadows, naming the merge to undo first — that sentence is
 * printed as it arrives.
 */
function MergeHistory({ person }: { person: UltraWikiPerson }): JSX.Element | null {
  const t = useT();
  const merges = person.merges ?? [];
  const standing = undoableMerges(person);
  if (merges.length === 0) return null;

  const names = new Map<number, string>(
    person.merged_from.map((entry) => [entry.id, entry.display_name]),
  );

  return (
    <section className="px-4 py-3" data-testid="person-merges">
      <h4 className="text-[10px] uppercase tracking-[0.14em] text-muted-foreground">
        {t("ultrawiki.people.merges_title")}
      </h4>
      <p className="mt-1 text-[11px] text-muted-foreground">
        {standing.length > 0
          ? t("ultrawiki.people.merges_hint")
          : t("ultrawiki.people.merges_all_undone")}
      </p>
      <ul className="mt-2 space-y-1.5">
        {merges.map((merge) => (
          <MergeRow
            key={merge.id}
            merge={merge}
            loserName={names.get(merge.loser_id) ?? ""}
          />
        ))}
      </ul>
    </section>
  );
}

function MergeRow({
  merge,
  loserName,
}: {
  merge: UltraWikiMergeRecord;
  loserName: string;
}): JSX.Element {
  const t = useT();
  const queryClient = useQueryClient();
  const undoMutation = useMutation({
    mutationFn: () => unmergeIdentity(merge.id),
    onSuccess: () =>
      void queryClient.invalidateQueries({ queryKey: IDENTITY_QUERY_KEY }),
  });

  const undone = Boolean(merge.undone_at);
  const who = loserName || `#${merge.loser_id}`;

  return (
    <li
      data-testid={`person-merge-${merge.id}`}
      data-undone={undone ? "true" : "false"}
      className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-border/70 bg-card/40 px-2.5 py-1.5"
    >
      <span className="min-w-0 flex-1 text-[11px] text-foreground">
        {t("ultrawiki.people.merge_line").replace("{0}", who)}
        <span className="ml-1.5 text-muted-foreground">
          {merge.merged_at.slice(0, 10)}
          {merge.tier && ` · ${t(`ultrawiki.people.tier_${merge.tier}`)}`}
        </span>
      </span>

      {undone ? (
        <span
          data-testid={`person-merge-undone-${merge.id}`}
          className="shrink-0 text-[10px] uppercase tracking-wider text-muted-foreground"
        >
          {t("ultrawiki.people.merge_undone")}
        </span>
      ) : (
        <button
          type="button"
          data-testid={`person-merge-undo-${merge.id}`}
          onClick={() => undoMutation.mutate()}
          disabled={undoMutation.isPending}
          className="inline-flex shrink-0 items-center gap-1 rounded-md border border-border px-2 py-0.5 text-[10px] text-muted-foreground transition-colors hover:text-foreground disabled:opacity-60"
        >
          {undoMutation.isPending ? (
            <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
          ) : (
            <Undo2 className="h-3 w-3" aria-hidden />
          )}
          {t("ultrawiki.people.undo_merge")}
        </button>
      )}

      {undoMutation.error && (
        <p
          role="alert"
          data-testid={`person-merge-error-${merge.id}`}
          className="w-full text-[11px] text-destructive"
        >
          {t("ultrawiki.people.action_failed").replace(
            "{0}",
            identityErrorMessage(undoMutation.error),
          )}
        </p>
      )}
    </li>
  );
}

export default PeoplePanel;
