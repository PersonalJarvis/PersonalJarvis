import { useCallback, useEffect, useMemo, useState } from "react";
import { Contact as ContactIcon, Loader2, Plus, Search } from "lucide-react";

import { ViewHeader } from "@/views/ChatsView";
import { useT } from "@/i18n";
import { ContactRow } from "./ContactRow";
import { ContactDetail } from "./ContactDetail";
import { ContactEditDialog } from "./ContactEditDialog";
import {
  deleteContact,
  getContact,
  listContacts,
  type Contact,
  type ContactSummary,
} from "./api";

/**
 * Contacts — a user-curated address book (master–detail). Left: a searchable
 * list of contacts; right: the selected contact's detail with edit/delete. The
 * "Add" button and the edit pencil open the same dialog (create vs. PATCH).
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

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return contacts;
    return contacts.filter(
      (c) =>
        c.name.toLowerCase().includes(q) ||
        c.aliases.some((a) => a.toLowerCase().includes(q)),
    );
  }, [contacts, query]);

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
          <div className="border-b border-border p-3">
            <div className="flex items-center gap-2 rounded-md border border-border bg-background/40 px-2.5 py-1.5">
              <Search className="h-3.5 w-3.5 text-muted-foreground" />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={t("contacts.search")}
                className="w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground/60"
              />
            </div>
          </div>
          <nav className="flex-1 overflow-y-auto scrollbar-jarvis p-2">
            {loading ? (
              <div className="flex items-center justify-center py-10 text-muted-foreground">
                <Loader2 className="h-5 w-5 animate-spin" />
              </div>
            ) : error ? (
              <p className="px-3 py-6 text-center text-sm text-destructive">{error}</p>
            ) : filtered.length === 0 ? (
              <p className="px-3 py-6 text-center text-sm text-muted-foreground">
                {query ? t("contacts.noMatches") : t("contacts.empty")}
              </p>
            ) : (
              <ul className="space-y-0.5">
                {filtered.map((c) => (
                  <ContactRow
                    key={c.slug}
                    contact={c}
                    active={c.slug === selectedSlug}
                    onClick={() => setSelectedSlug(c.slug)}
                  />
                ))}
              </ul>
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
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
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
