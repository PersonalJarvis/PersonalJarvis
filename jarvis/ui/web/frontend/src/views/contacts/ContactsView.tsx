import { useCallback, useEffect, useMemo, useState } from "react";
import { Contact as ContactIcon, Loader2, Mic, Plus, Search } from "lucide-react";

import { ViewHeader } from "@/views/ChatsView";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { ContactRow } from "./ContactRow";
import { ContactDetail } from "./ContactDetail";
import { ContactEditDialog } from "./ContactEditDialog";
import { RELATIONSHIPS, relationshipLabel, type Relationship } from "./constants";
import {
  deleteContact,
  getContact,
  listContacts,
  type Contact,
  type ContactSummary,
} from "./api";

/**
 * Contacts — a user-curated address book (master–detail). Left: a searchable,
 * relationship-filterable list grouped by first letter; right: the selected
 * contact's detail with actions. The "Add" button and the edit pencil open the
 * same dialog (create vs. PATCH).
 *
 * Live: the view listens for the `jarvis:contact-changed` window event (the
 * bus `ContactChanged` envelope relayed by useWebSocket), so a contact saved
 * by voice (`contact-upsert`) appears without a manual refresh.
 *
 * Distinct from the read-only "People around you" tab in ProfileView (the
 * auto-extracted Curator list) — this section is fully managed by the user and
 * is what Jarvis resolves names against (Chunk B).
 */
export function ContactsView() {
  const t = useT();
  const [contacts, setContacts] = useState<ContactSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [relFilter, setRelFilter] = useState<Relationship | null>(null);
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const [selected, setSelected] = useState<Contact | null>(null);
  const [dialog, setDialog] = useState<"create" | "edit" | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const loadList = useCallback(async () => {
    setError(null);
    try {
      setContacts(await listContacts());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  // Load the full record whenever the selection changes.
  useEffect(() => {
    if (!selectedSlug) {
      setSelected(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const full = await getContact(selectedSlug);
        if (!cancelled) setSelected(full);
      } catch {
        if (!cancelled) setSelected(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedSlug]);

  // Live refresh: a contact changed somewhere else (voice upsert, CLI, another
  // window). Re-fetch the list; keep the open detail honest too.
  useEffect(() => {
    const onChanged = (event: Event) => {
      const detail = (event as CustomEvent).detail as
        | { action?: string; slug?: string }
        | undefined;
      void loadList();
      if (!detail?.slug || detail.slug !== selectedSlug) return;
      if (detail.action === "deleted") {
        setSelectedSlug(null);
        setSelected(null);
      } else {
        void getContact(detail.slug)
          .then(setSelected)
          .catch(() => setSelected(null));
      }
    };
    window.addEventListener("jarvis:contact-changed", onChanged);
    return () => window.removeEventListener("jarvis:contact-changed", onChanged);
  }, [loadList, selectedSlug]);

  // Relationship counts over the whole book (not the current search) — the
  // chips are a stable map of the book, not of the query.
  const relCounts = useMemo(() => {
    const counts = new Map<Relationship, number>();
    for (const c of contacts) {
      if (c.relationship) {
        counts.set(c.relationship, (counts.get(c.relationship) ?? 0) + 1);
      }
    }
    return counts;
  }, [contacts]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return contacts.filter((c) => {
      if (relFilter && c.relationship !== relFilter) return false;
      if (!q) return true;
      return (
        c.name.toLowerCase().includes(q) ||
        c.aliases.some((a) => a.toLowerCase().includes(q)) ||
        (c.primary_email ?? "").toLowerCase().includes(q) ||
        (c.primary_phone ?? "").toLowerCase().includes(q)
      );
    });
  }, [contacts, query, relFilter]);

  // Group the (already name-sorted) list by first letter; non-letters → "#".
  const groups = useMemo(() => {
    const out: { letter: string; items: ContactSummary[] }[] = [];
    for (const c of filtered) {
      const first = c.name.trim()[0] ?? "#";
      const letter = /\p{L}/u.test(first) ? first.toUpperCase() : "#";
      const last = out[out.length - 1];
      if (last && last.letter === letter) last.items.push(c);
      else out.push({ letter, items: [c] });
    }
    return out;
  }, [filtered]);

  async function handleSaved(saved: Contact) {
    setDialog(null);
    await loadList();
    setSelectedSlug(saved.slug);
    setSelected(saved);
  }

  async function handleConfirmDelete() {
    if (!selected) return;
    const slug = selected.slug;
    setConfirmingDelete(false);
    try {
      await deleteContact(slug);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return;
    }
    setSelectedSlug(null);
    setSelected(null);
    await loadList();
  }

  const hasContacts = contacts.length > 0;

  return (
    <div className="flex h-full flex-col">
      <ViewHeader
        icon={<ContactIcon className="h-4 w-4 text-primary" />}
        title={t("nav.contacts")}
        subtitle={t("contacts.subtitle")}
        right={
          <button
            type="button"
            onClick={() => setDialog("create")}
            className="inline-flex items-center gap-1.5 rounded-md border border-primary/40 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary hover:bg-primary/20"
          >
            <Plus className="h-3.5 w-3.5" />
            {t("contacts.add")}
          </button>
        }
      />

      <div className="flex min-h-0 flex-1">
        {/* Master list */}
        <div className="flex w-[320px] shrink-0 flex-col border-r border-border">
          <div className="space-y-2 border-b border-border p-3">
            <div className="flex items-center gap-2 rounded-md border border-border bg-background/40 px-2.5 py-1.5">
              <Search className="h-3.5 w-3.5 text-muted-foreground" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={t("contacts.search")}
                className="w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground/60"
              />
            </div>
            {hasContacts && (
              <div className="flex flex-wrap gap-1">
                <FilterChip
                  active={relFilter === null}
                  onClick={() => setRelFilter(null)}
                  label={`${t("contacts.filterAll")} · ${contacts.length}`}
                />
                {RELATIONSHIPS.filter((r) => (relCounts.get(r) ?? 0) > 0).map((r) => (
                  <FilterChip
                    key={r}
                    active={relFilter === r}
                    onClick={() => setRelFilter(relFilter === r ? null : r)}
                    label={`${relationshipLabel(t, r)} · ${relCounts.get(r)}`}
                  />
                ))}
              </div>
            )}
          </div>
          <nav className="flex-1 overflow-y-auto scrollbar-jarvis p-2">
            {loading ? (
              <div className="flex items-center justify-center py-10 text-muted-foreground">
                <Loader2 className="h-5 w-5 animate-spin" />
              </div>
            ) : error ? (
              <p className="px-3 py-6 text-center text-sm text-destructive">{error}</p>
            ) : !hasContacts ? (
              <div className="flex flex-col items-center gap-3 px-3 py-8 text-center">
                <ContactIcon className="h-8 w-8 text-muted-foreground/40" />
                <p className="text-sm text-muted-foreground">{t("contacts.empty")}</p>
                <button
                  type="button"
                  onClick={() => setDialog("create")}
                  className="inline-flex items-center gap-1.5 rounded-md border border-primary/40 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary hover:bg-primary/20"
                >
                  <Plus className="h-3.5 w-3.5" />
                  {t("contacts.add")}
                </button>
                <p className="flex items-start gap-1.5 text-xs text-muted-foreground/80">
                  <Mic className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                  {t("contacts.voiceHint")}
                </p>
              </div>
            ) : filtered.length === 0 ? (
              <p className="px-3 py-6 text-center text-sm text-muted-foreground">
                {t("contacts.noMatches")}
              </p>
            ) : (
              <div className="space-y-1">
                {groups.map((group) => (
                  <div key={group.letter}>
                    <div className="px-3 pb-0.5 pt-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/70">
                      {group.letter}
                    </div>
                    <ul className="space-y-0.5">
                      {group.items.map((c) => (
                        <ContactRow
                          key={c.slug}
                          contact={c}
                          active={c.slug === selectedSlug}
                          onClick={() => setSelectedSlug(c.slug)}
                        />
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            )}
          </nav>
        </div>

        {/* Detail */}
        <div className="min-w-0 flex-1">
          {selected ? (
            <ContactDetail
              contact={selected}
              onEdit={() => setDialog("edit")}
              onDelete={() => setConfirmingDelete(true)}
            />
          ) : (
            <div className="flex h-full flex-col items-center justify-center gap-3 text-center text-muted-foreground">
              <ContactIcon className="h-8 w-8 text-muted-foreground/40" />
              <p className="text-sm">{t("contacts.selectHint")}</p>
            </div>
          )}
        </div>
      </div>

      {dialog && (
        <ContactEditDialog
          initial={dialog === "edit" ? selected : null}
          onClose={() => setDialog(null)}
          onSaved={(c) => void handleSaved(c)}
        />
      )}

      {confirmingDelete && selected && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-scrim/50 backdrop-blur-sm"
          onClick={() => setConfirmingDelete(false)}
        >
          <div
            className="w-full max-w-sm rounded-xl border border-border bg-card p-6 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="font-display text-base font-semibold">
              {t("contacts.deleteTitle")}
            </h3>
            <p className="mt-2 text-sm text-muted-foreground">
              {t("contacts.deleteConfirm")} <strong>{selected.name}</strong>?
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setConfirmingDelete(false)}
                className="rounded-md border border-border px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground"
              >
                {t("contacts.cancel")}
              </button>
              <button
                type="button"
                onClick={() => void handleConfirmDelete()}
                className="rounded-md border border-destructive/50 bg-destructive/10 px-3 py-1.5 text-xs font-medium text-destructive hover:bg-destructive/20"
              >
                {t("contacts.delete")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function FilterChip({
  active,
  onClick,
  label,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-full border px-2 py-0.5 text-[10px] font-medium transition-colors",
        active
          ? "border-primary/40 bg-primary/15 text-primary"
          : "border-border text-muted-foreground hover:border-primary/30 hover:text-foreground",
      )}
    >
      {label}
    </button>
  );
}
