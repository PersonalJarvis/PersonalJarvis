import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Bot,
  Check,
  Copy,
  Eye,
  EyeOff,
  KeyRound,
  Loader2,
  Lock,
  Pencil,
  Plus,
  ShieldCheck,
  Trash2,
  UserRound,
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
  type LoginOwner,
  type LoginStatus,
  type LoginSummary,
} from "./api";

/**
 * Passwords — the sign-ins Jarvis may use on the user's behalf. Master–detail:
 * the stored services on the left (grouped by whose account each one is), the
 * selected one's details on the right.
 *
 * Three things about this screen are load-bearing rather than cosmetic:
 *
 * 1. TRUST IS STATED, NOT IMPLIED. The neutral right pane is a "how this
 *    works" panel — where the secret lives, what the AI can and cannot see,
 *    and that the first sign-in asks for confirmation — and the editor repeats
 *    the disclosure right above the input, so nobody types a password without
 *    having been told the assistant will gain access to it. A password screen
 *    that stays silent about this reads as untrustworthy, and rightly so.
 * 2. A password is only ever fetched by an explicit click (`revealLogin`), and
 *    the revealed value is dropped from component state the moment the user
 *    selects something else. Listing never carries one.
 * 3. The notes field is not decoration. It is the text Jarvis reads before a
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
  return {
    label: "",
    domains: [],
    username: "",
    password: "",
    notes: "",
    totp_secret: "",
    owner: "user",
  };
}

function formatWhen(iso: string | null): string | null {
  if (!iso) return null;
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed.toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
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

function OwnerBadge({ owner }: { owner: LoginOwner }) {
  const t = useT();
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
      {owner === "agent" ? (
        <Bot className="h-3 w-3" />
      ) : (
        <UserRound className="h-3 w-3" />
      )}
      {t(`passwords.owner_badge_${owner}`)}
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

/**
 * The honest explanation of the section, shown whenever nothing is selected —
 * so it is the FIRST thing a new user reads, before any password exists. Three
 * facts, each true of the actual implementation: keychain storage
 * (`jarvis.logins.store` writes only through the OS keyring), what the AI sees
 * (summaries without secrets; injection fills SECRET() placeholders
 * out-of-process and scrubs the output), and the first-use confirmation
 * (`needs_confirmation` keys off the entry's own status).
 */
function TrustPanel({
  showAdd,
  onAdd,
}: {
  showAdd: boolean;
  onAdd: () => void;
}) {
  const t = useT();
  const points: { icon: React.ReactNode; title: string; body: string }[] = [
    {
      icon: <Lock className="h-4 w-4" />,
      title: t("passwords.trust.storage_title"),
      body: t("passwords.trust.storage_body"),
    },
    {
      icon: <Eye className="h-4 w-4" />,
      title: t("passwords.trust.ai_title"),
      body: t("passwords.trust.ai_body"),
    },
    {
      icon: <ShieldCheck className="h-4 w-4" />,
      title: t("passwords.trust.control_title"),
      body: t("passwords.trust.control_body"),
    },
  ];
  return (
    <div className="mx-auto flex h-full max-w-md flex-col justify-center gap-6 py-8">
      <div className="space-y-2 text-center">
        <ShieldCheck className="mx-auto h-8 w-8 text-primary" />
        <h2 className="text-base font-semibold text-foreground">
          {t("passwords.trust.title")}
        </h2>
        <p className="text-sm leading-relaxed text-muted-foreground">
          {t("passwords.trust.intro")}
        </p>
      </div>
      <ul className="space-y-4">
        {points.map((point) => (
          <li key={point.title} className="flex items-start gap-3">
            <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-border bg-secondary/50 text-primary">
              {point.icon}
            </span>
            <div className="min-w-0">
              <h3 className="text-sm font-medium text-foreground">
                {point.title}
              </h3>
              <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
                {point.body}
              </p>
            </div>
          </li>
        ))}
      </ul>
      {showAdd ? (
        <button
          type="button"
          onClick={onAdd}
          className="mx-auto inline-flex items-center gap-1.5 rounded-md border border-primary/40 bg-primary/10 px-4 py-2 text-sm font-medium text-primary hover:bg-primary/20"
        >
          <Plus className="h-4 w-4" />
          {t("passwords.add")}
        </button>
      ) : (
        <p className="text-center text-xs text-muted-foreground">
          {t("passwords.pick_one")}
        </p>
      )}
    </div>
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

  // Grouped by whose account each entry is. Group headings appear only once
  // the assistant holds accounts of its own — a list that is all the user's
  // needs no label saying so.
  const userLogins = useMemo(
    () => logins.filter((entry) => entry.owner !== "agent"),
    [logins],
  );
  const agentLogins = useMemo(
    () => logins.filter((entry) => entry.owner === "agent"),
    [logins],
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
      totp_secret: "",
      owner: entry.owner,
    });
    setEditing(entry.service_id);
  };

  const save = async () => {
    setBusy(true);
    try {
      if (editing === "new") {
        const created = await createLogin({
          ...draft,
          // Omitted rather than sent empty: the server stores exactly what it
          // gets, and "no TOTP" must stay "no TOTP", not become "".
          totp_secret: draft.totp_secret || undefined,
        });
        setSelectedId(created.service_id);
      } else if (editing) {
        const patch: Partial<LoginDraft> = {
          label: draft.label,
          domains: draft.domains,
          username: draft.username,
          notes: draft.notes,
          owner: draft.owner,
        };
        // Blank means "keep what is stored" for both secrets — the server
        // treats an omitted field as untouched.
        if (draft.password) patch.password = draft.password;
        if (draft.totp_secret) patch.totp_secret = draft.totp_secret;
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

  const renderGroup = (entries: LoginSummary[], heading: string | null) => (
    <>
      {heading && (
        <h3 className="px-3 pb-1 pt-3 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
          {heading}
        </h3>
      )}
      <ul className="space-y-1">
        {entries.map((entry) => (
          <li key={entry.service_id}>
            <button
              type="button"
              onClick={() => setSelectedId(entry.service_id)}
              className={cn(
                "w-full rounded-md px-3 py-2 text-left hover:bg-muted/60",
                entry.service_id === selectedId && "bg-muted",
              )}
            >
              <span className="flex items-center gap-2">
                <span className="block min-w-0 flex-1 truncate text-sm font-medium text-foreground">
                  {entry.label}
                </span>
                {entry.status === "rejected" && (
                  <span
                    className="h-1.5 w-1.5 shrink-0 rounded-full bg-destructive"
                    title={t("passwords.status.rejected")}
                  />
                )}
              </span>
              <span className="block truncate text-xs text-muted-foreground">
                {entry.username || t("passwords.no_username")}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </>
  );

  const grouped = agentLogins.length > 0;

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
          ) : grouped ? (
            <>
              {userLogins.length > 0 &&
                renderGroup(userLogins, t("passwords.owner_group_user"))}
              {renderGroup(agentLogins, t("passwords.owner_group_agent"))}
            </>
          ) : (
            renderGroup(userLogins, null)
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
              <header className="space-y-2">
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0">
                    <h2 className="truncate text-lg font-semibold text-foreground">
                      {selected.label}
                    </h2>
                    <p className="mt-1 truncate text-sm text-muted-foreground">
                      {selected.domains.join(", ") || t("passwords.no_domain")}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <OwnerBadge owner={selected.owner} />
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
                </div>
                {/* The status badge alone says "Working" without saying what
                    that means for the user — this line does. */}
                <p className="text-xs leading-relaxed text-muted-foreground">
                  {t(`passwords.status_hint.${selected.status}`)}
                  {selected.status === "ok" &&
                    formatWhen(selected.last_used_at) !== null &&
                    ` ${t("passwords.last_used")}: ${formatWhen(selected.last_used_at)}.`}
                </p>
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

                {selected.has_totp && (
                  <Field label={t("passwords.totp_row")}>
                    <span className="text-sm text-muted-foreground">
                      {t("passwords.stored_hidden")}
                    </span>
                  </Field>
                )}

                {/* Additional secrets by NAME only — the values never reach
                    this client (see api.ts). Saying they exist is what keeps
                    the record honest about what the vault holds. */}
                {selected.secret_names.map((name) => (
                  <Field key={name} label={name}>
                    <span className="text-sm text-muted-foreground">
                      {t("passwords.stored_hidden")}
                    </span>
                  </Field>
                ))}

                {Object.entries(selected.fields).map(([name, value]) => (
                  <Field key={name} label={name}>
                    <span className="min-w-0 truncate text-sm text-foreground">
                      {value}
                    </span>
                    <CopyButton value={value} label={name} />
                  </Field>
                ))}
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
            <TrustPanel showAdd={logins.length === 0} onAdd={startCreate} />
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

function EditorSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <fieldset className="space-y-3 rounded-lg border border-border p-4">
      <legend className="px-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {title}
      </legend>
      {children}
    </fieldset>
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
  const owners: LoginOwner[] = ["user", "agent"];

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

      {/* The disclosure, at the moment it matters: BEFORE anything is typed.
          The trust panel says the same at more length, but the editor cannot
          rely on the user having read it. */}
      <div className="flex items-start gap-2.5 rounded-md border border-primary/25 bg-primary/5 px-3 py-2.5">
        <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <p className="text-xs leading-relaxed text-muted-foreground">
          {t("passwords.editor_disclosure")}
        </p>
      </div>

      <EditorSection title={t("passwords.section_service")}>
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

        <div className="space-y-1">
          <span className="block text-xs uppercase tracking-wide text-muted-foreground">
            {t("passwords.owner")}
          </span>
          <div className="grid grid-cols-2 gap-2" role="radiogroup">
            {owners.map((owner) => (
              <button
                key={owner}
                type="button"
                role="radio"
                aria-checked={(draft.owner ?? "user") === owner}
                onClick={() => onChange({ ...draft, owner })}
                className={cn(
                  "rounded-md border px-3 py-2 text-left",
                  (draft.owner ?? "user") === owner
                    ? "border-primary/50 bg-primary/10"
                    : "border-border hover:border-primary/30",
                )}
              >
                <span className="flex items-center gap-1.5 text-sm font-medium text-foreground">
                  {owner === "agent" ? (
                    <Bot className="h-3.5 w-3.5" />
                  ) : (
                    <UserRound className="h-3.5 w-3.5" />
                  )}
                  {t(`passwords.owner_${owner}`)}
                </span>
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  {t(`passwords.owner_${owner}_hint`)}
                </span>
              </button>
            ))}
          </div>
        </div>
      </EditorSection>

      <EditorSection title={t("passwords.section_credentials")}>
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
            {t("passwords.totp")}
          </span>
          <input
            className={inputClass}
            type="password"
            value={draft.totp_secret ?? ""}
            onChange={(e) =>
              onChange({ ...draft, totp_secret: e.target.value })
            }
            autoComplete="off"
            placeholder={isNew ? "" : t("passwords.totp_unchanged")}
          />
          <span className="block text-xs text-muted-foreground">
            {t("passwords.totp_hint")}
          </span>
        </label>
      </EditorSection>

      <EditorSection title={t("passwords.section_notes")}>
        <label className="block space-y-1">
          <textarea
            className={cn(inputClass, "min-h-[9rem] resize-y font-mono text-xs")}
            value={draft.notes}
            onChange={(e) => onChange({ ...draft, notes: e.target.value })}
            placeholder={t("passwords.notes_placeholder")}
            aria-label={t("passwords.notes")}
          />
          <span className="block text-xs text-muted-foreground">
            {t("passwords.notes_hint")}
          </span>
        </label>
      </EditorSection>

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
