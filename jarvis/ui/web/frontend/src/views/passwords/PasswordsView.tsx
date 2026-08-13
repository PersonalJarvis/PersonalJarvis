import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Check,
  Copy,
  Eye,
  EyeOff,
  KeyRound,
  Loader2,
  Pencil,
  Plus,
  Trash2,
} from "lucide-react";

import { ViewHeader } from "@/views/ChatsView";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import {
  createLogin,
  deleteLogin,
  listLogins,
  revealLogin,
  updateLogin,
  type LoginDraft,
  type LoginStatus,
  type LoginSummary,
} from "./api";

/**
 * Passwords — the logins Jarvis may use to sign in somewhere on the user's
 * behalf. Master–detail: the stored services on the left, the selected one's
 * details and notes on the right.
 *
 * Two things about this screen are load-bearing rather than cosmetic:
 *
 * 1. A password is only ever fetched by an explicit click (`revealLogin`), and
 *    the revealed value is dropped from component state the moment the user
 *    selects something else. Listing never carries one.
 * 2. The notes field is not decoration. It is the text Jarvis reads before a
 *    login — "the code arrives by mail", "use the work account here" — so the
 *    editor gives it real room and says what it is for.
 *
 * Colours come from theme tokens throughout, so the section is correct in light
 * and dark without a second code path.
 */

const STATUS_STYLES: Record<LoginStatus, string> = {
  ok: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  rejected: "bg-destructive/15 text-destructive",
  unknown: "bg-muted text-muted-foreground",
};

function emptyDraft(): LoginDraft {
  return { label: "", domains: [], username: "", password: "", notes: "" };
}

function StatusBadge({ status }: { status: LoginStatus }) {
  const t = useT();
  return (
    <span
      className={cn(
        "rounded-full px-2 py-0.5 text-[11px] font-medium",
        STATUS_STYLES[status],
      )}
    >
      {t(`passwords.status.${status}`)}
    </span>
  );
}

function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={() => {
        void navigator.clipboard.writeText(value).then(() => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1500);
        });
      }}
      className="rounded-md border border-border p-1.5 text-muted-foreground hover:border-primary/40 hover:text-foreground"
    >
      {copied ? (
        <Check className="h-3.5 w-3.5 text-emerald-500" />
      ) : (
        <Copy className="h-3.5 w-3.5" />
      )}
    </button>
  );
}

export function PasswordsView() {
  const t = useT();
  const [logins, setLogins] = useState<LoginSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // The revealed secret lives ONLY here, and only while its own entry is
  // selected. Changing selection or saving drops it (see the effect below).
  const [revealed, setRevealed] = useState<string | null>(null);

  const [editing, setEditing] = useState<string | "new" | null>(null);
  const [draft, setDraft] = useState<LoginDraft>(emptyDraft);

  const selected = useMemo(
    () => logins.find((entry) => entry.service_id === selectedId) ?? null,
    [logins, selectedId],
  );

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const entries = await listLogins();
      setLogins(entries);
      setError(null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Never let a revealed password outlive the entry it belongs to.
  useEffect(() => {
    setRevealed(null);
  }, [selectedId]);

  const startCreate = () => {
    setDraft(emptyDraft());
    setEditing("new");
  };

  const startEdit = (entry: LoginSummary) => {
    setDraft({
      label: entry.label,
      domains: entry.domains,
      username: entry.username,
      // Left blank on purpose: an empty field means "leave the stored password
      // alone", so an edit to the notes cannot silently blank the password.
      password: "",
      notes: entry.notes,
    });
    setEditing(entry.service_id);
  };

  const save = async () => {
    setBusy(true);
    try {
      if (editing === "new") {
        const created = await createLogin(draft);
        setSelectedId(created.service_id);
      } else if (editing) {
        const patch: Partial<LoginDraft> = {
          label: draft.label,
          domains: draft.domains,
          username: draft.username,
          notes: draft.notes,
        };
        if (draft.password) patch.password = draft.password;
        await updateLogin(editing, patch);
      }
      setEditing(null);
      setRevealed(null);
      await refresh();
      setError(null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (entry: LoginSummary) => {
    // t() takes no interpolation parameters here, so the name is appended
    // rather than substituted — the user still sees exactly what is going.
    if (!window.confirm(`${t("passwords.confirm_delete")}\n\n${entry.label}`)) {
      return;
    }
    setBusy(true);
    try {
      await deleteLogin(entry.service_id);
      setSelectedId(null);
      await refresh();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const reveal = async (entry: LoginSummary) => {
    if (revealed !== null) {
      setRevealed(null);
      return;
    }
    try {
      const secrets = await revealLogin(entry.service_id);
      setRevealed(secrets.password);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  };

  return (
    <div className="flex h-full flex-col">
      <ViewHeader
        icon={<KeyRound className="h-4 w-4 text-primary" />}
        title={t("nav.passwords")}
        subtitle={t("passwords.subtitle")}
        right={
          <button
            type="button"
            onClick={startCreate}
            className="inline-flex items-center gap-1.5 rounded-md border border-primary/40 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary hover:bg-primary/20"
          >
            <Plus className="h-3.5 w-3.5" />
            {t("passwords.add")}
          </button>
        }
      />

      {error && (
        <p className="border-b border-border bg-destructive/10 px-4 py-2 text-sm text-destructive">
          {error}
        </p>
      )}

      <div className="flex min-h-0 flex-1">
        <nav className="w-72 shrink-0 overflow-y-auto scrollbar-jarvis border-r border-border p-2">
          {loading ? (
            <div className="flex items-center justify-center py-10 text-muted-foreground">
              <Loader2 className="h-5 w-5 animate-spin" />
            </div>
          ) : logins.length === 0 ? (
            <div className="flex flex-col items-center gap-3 px-3 py-8 text-center">
              <KeyRound className="h-8 w-8 text-muted-foreground/40" />
              <p className="text-sm text-muted-foreground">
                {t("passwords.empty")}
              </p>
            </div>
          ) : (
            <ul className="space-y-1">
              {logins.map((entry) => (
                <li key={entry.service_id}>
                  <button
                    type="button"
                    onClick={() => setSelectedId(entry.service_id)}
                    className={cn(
                      "w-full rounded-md px-3 py-2 text-left hover:bg-muted/60",
                      entry.service_id === selectedId && "bg-muted",
                    )}
                  >
                    <span className="block truncate text-sm font-medium text-foreground">
                      {entry.label}
                    </span>
                    <span className="block truncate text-xs text-muted-foreground">
                      {entry.username || t("passwords.no_username")}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </nav>

        <section className="min-w-0 flex-1 overflow-y-auto scrollbar-jarvis p-6">
          {editing ? (
            <LoginEditor
              draft={draft}
              isNew={editing === "new"}
              busy={busy}
              onChange={setDraft}
              onCancel={() => setEditing(null)}
              onSave={save}
            />
          ) : selected ? (
            <article className="space-y-6">
              <header className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <h2 className="truncate text-lg font-semibold text-foreground">
                    {selected.label}
                  </h2>
                  <p className="mt-1 truncate text-sm text-muted-foreground">
                    {selected.domains.join(", ") || t("passwords.no_domain")}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <StatusBadge status={selected.status} />
                  <button
                    type="button"
                    aria-label={t("passwords.edit")}
                    onClick={() => startEdit(selected)}
                    className="rounded-md border border-border p-1.5 text-muted-foreground hover:border-primary/40 hover:text-foreground"
                  >
                    <Pencil className="h-3.5 w-3.5" />
                  </button>
                  <button
                    type="button"
                    aria-label={t("passwords.delete")}
                    onClick={() => void remove(selected)}
                    className="rounded-md border border-border p-1.5 text-muted-foreground hover:border-destructive/40 hover:text-destructive"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </header>

              <dl className="space-y-3">
                <Field label={t("passwords.username")}>
                  <span className="text-sm text-foreground">
                    {selected.username || "—"}
                  </span>
                  {selected.username && (
                    <CopyButton
                      value={selected.username}
                      label={t("passwords.copy_username")}
                    />
                  )}
                </Field>

                <Field label={t("passwords.password")}>
                  <span className="font-mono text-sm text-foreground">
                    {revealed ?? "••••••••••••"}
                  </span>
                  <button
                    type="button"
                    aria-label={
                      revealed ? t("passwords.hide") : t("passwords.reveal")
                    }
                    onClick={() => void reveal(selected)}
                    className="rounded-md border border-border p-1.5 text-muted-foreground hover:border-primary/40 hover:text-foreground"
                  >
                    {revealed ? (
                      <EyeOff className="h-3.5 w-3.5" />
                    ) : (
                      <Eye className="h-3.5 w-3.5" />
                    )}
                  </button>
                  {revealed && (
                    <CopyButton
                      value={revealed}
                      label={t("passwords.copy_password")}
                    />
                  )}
                </Field>
              </dl>

              <section>
                <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  {t("passwords.notes")}
                </h3>
                <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-foreground">
                  {selected.notes || t("passwords.no_notes")}
                </p>
              </section>
            </article>
          ) : (
            <div className="flex h-full items-center justify-center">
              <p className="max-w-sm text-center text-sm text-muted-foreground">
                {t("passwords.pick_one")}
              </p>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-3">
      <dt className="w-28 shrink-0 text-xs uppercase tracking-wide text-muted-foreground">
        {label}
      </dt>
      <dd className="flex min-w-0 items-center gap-2">{children}</dd>
    </div>
  );
}

function LoginEditor({
  draft,
  isNew,
  busy,
  onChange,
  onCancel,
  onSave,
}: {
  draft: LoginDraft;
  isNew: boolean;
  busy: boolean;
  onChange: (next: LoginDraft) => void;
  onCancel: () => void;
  onSave: () => void;
}) {
  const t = useT();
  const inputClass =
    "w-full rounded-md border border-border bg-background/40 px-3 py-2 text-sm text-foreground outline-none focus:border-primary/50";

  return (
    <form
      className="max-w-xl space-y-4"
      onSubmit={(event) => {
        event.preventDefault();
        onSave();
      }}
    >
      <h2 className="text-lg font-semibold text-foreground">
        {isNew ? t("passwords.add") : t("passwords.edit")}
      </h2>

      <label className="block space-y-1">
        <span className="text-xs uppercase tracking-wide text-muted-foreground">
          {t("passwords.label")}
        </span>
        <input
          className={inputClass}
          value={draft.label}
          onChange={(e) => onChange({ ...draft, label: e.target.value })}
          placeholder="GitHub"
          required
        />
      </label>

      <label className="block space-y-1">
        <span className="text-xs uppercase tracking-wide text-muted-foreground">
          {t("passwords.domains")}
        </span>
        <input
          className={inputClass}
          value={draft.domains.join(", ")}
          onChange={(e) =>
            onChange({
              ...draft,
              domains: e.target.value
                .split(",")
                .map((part) => part.trim())
                .filter(Boolean),
            })
          }
          placeholder="github.com"
        />
        <span className="block text-xs text-muted-foreground">
          {t("passwords.domains_hint")}
        </span>
      </label>

      <label className="block space-y-1">
        <span className="text-xs uppercase tracking-wide text-muted-foreground">
          {t("passwords.username")}
        </span>
        <input
          className={inputClass}
          value={draft.username}
          onChange={(e) => onChange({ ...draft, username: e.target.value })}
          autoComplete="off"
        />
      </label>

      <label className="block space-y-1">
        <span className="text-xs uppercase tracking-wide text-muted-foreground">
          {t("passwords.password")}
        </span>
        <input
          className={inputClass}
          type="password"
          value={draft.password}
          onChange={(e) => onChange({ ...draft, password: e.target.value })}
          autoComplete="new-password"
          placeholder={isNew ? "" : t("passwords.password_unchanged")}
        />
      </label>

      <label className="block space-y-1">
        <span className="text-xs uppercase tracking-wide text-muted-foreground">
          {t("passwords.notes")}
        </span>
        <textarea
          className={cn(inputClass, "min-h-[9rem] resize-y font-mono text-xs")}
          value={draft.notes}
          onChange={(e) => onChange({ ...draft, notes: e.target.value })}
          placeholder={t("passwords.notes_placeholder")}
        />
        <span className="block text-xs text-muted-foreground">
          {t("passwords.notes_hint")}
        </span>
      </label>

      <div className="flex items-center gap-2 pt-2">
        <button
          type="submit"
          disabled={busy || !draft.label.trim()}
          className="inline-flex items-center gap-1.5 rounded-md border border-primary/40 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary hover:bg-primary/20 disabled:opacity-50"
        >
          {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          {t("passwords.save")}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md border border-border px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground"
        >
          {t("passwords.cancel")}
        </button>
      </div>
    </form>
  );
}

export default PasswordsView;
