import { useEffect, useMemo, useRef, useState } from "react";
import { Loader2, Plus, X } from "lucide-react";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { BrandedSelect } from "@/components/ui/select";
import { RELATIONSHIPS, relationshipLabel, type Relationship } from "./constants";
import {
  createContact,
  updateContact,
  type Contact,
  type ContactInput,
} from "./api";

/**
 * Create / edit a contact. `initial === null` → create (POST); otherwise edit
 * (PATCH the existing slug). Non-blocking modal, same shape as PairDialog —
 * but closing with unsaved changes asks before discarding. Aliases are chips
 * (Enter/comma commits, Backspace removes), e-mail/phone fields validate with
 * the same rules the store applies server-side, and a phone that will be
 * normalised shows its stored form up front. Esc closes (guarded),
 * Ctrl/Cmd+Enter saves. The README field keeps its live word counter (the
 * store is designed for a short ~300-word bio).
 */

// Mirrors jarvis/contacts/store.py: _EMAIL_RE and _normalize_phone. Kept in
// sync by hand — these are hints; the server stays the authority.
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

function normalizePhonePreview(raw: string): string | null {
  const s = raw.trim();
  const digits = s.replace(/\D/g, "");
  if (!digits) return null;
  if (s.startsWith("+")) return `+${digits}`;
  if (s.startsWith("00")) return `+${digits.slice(2)}`;
  return digits;
}

export function ContactEditDialog({
  initial,
  onClose,
  onSaved,
}: {
  initial: Contact | null;
  onClose: () => void;
  onSaved: (contact: Contact) => void;
}) {
  const t = useT();
  const [name, setName] = useState(initial?.name ?? "");
  const [aliases, setAliases] = useState<string[]>(initial?.aliases ?? []);
  const [aliasDraft, setAliasDraft] = useState("");
  const [relationship, setRelationship] = useState<Relationship | "">(
    initial?.relationship ?? "",
  );
  const [emails, setEmails] = useState<string[]>(
    initial?.emails?.length ? initial.emails : [""],
  );
  const [phones, setPhones] = useState<string[]>(
    initial?.phones?.length ? initial.phones : [""],
  );
  const [street, setStreet] = useState(initial?.address?.street ?? "");
  const [postal, setPostal] = useState(initial?.address?.postal_code ?? "");
  const [city, setCity] = useState(initial?.address?.city ?? "");
  const [country, setCountry] = useState(initial?.address?.country ?? "");
  const [note, setNote] = useState(initial?.note ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirmDiscard, setConfirmDiscard] = useState(false);

  const wordCount = note.trim() ? note.trim().split(/\s+/).length : 0;

  // Dirty tracking: snapshot the form once on mount, compare on every close
  // request. Cheap (JSON of a dozen small strings) and exact.
  const formSnapshot = JSON.stringify({
    name, aliases, aliasDraft, relationship, emails, phones,
    street, postal, city, country, note,
  });
  const initialSnapshot = useRef(formSnapshot);
  const dirty = formSnapshot !== initialSnapshot.current;

  function requestClose() {
    if (dirty) setConfirmDiscard(true);
    else onClose();
  }

  // Validation hints (the server is the authority; these prevent surprises).
  const emailErrors = useMemo(
    () => emails.map((e) => (e.trim() && !EMAIL_RE.test(e.trim()) ? t("contacts.invalidEmail") : null)),
    [emails, t],
  );
  const phoneErrors = useMemo(
    () =>
      phones.map((p) =>
        p.trim() && normalizePhonePreview(p) === null ? t("contacts.invalidPhone") : null,
      ),
    [phones, t],
  );
  const phoneHints = useMemo(
    () =>
      phones.map((p) => {
        const normalized = normalizePhonePreview(p);
        return normalized && normalized !== p.trim()
          ? `${t("contacts.savedAs")} ${normalized}`
          : null;
      }),
    [phones, t],
  );
  const hasInvalidField =
    emailErrors.some(Boolean) || phoneErrors.some(Boolean);

  function commitAliasDraft(): string[] {
    const value = aliasDraft.trim().replace(/,+$/, "");
    if (!value) return aliases;
    if (aliases.includes(value)) {
      setAliasDraft("");
      return aliases;
    }
    const next = [...aliases, value];
    setAliases(next);
    setAliasDraft("");
    return next;
  }

  async function handleSave() {
    setError(null);
    if (!name.trim()) {
      setError(t("contacts.nameRequired"));
      return;
    }
    if (hasInvalidField || saving) return;
    const payload: ContactInput = {
      name: name.trim(),
      aliases: commitAliasDraft(),
      relationship: relationship === "" ? null : relationship,
      emails: emails.map((e) => e.trim()).filter(Boolean),
      phones: phones.map((p) => p.trim()).filter(Boolean),
      address: {
        street: street.trim() || undefined,
        postal_code: postal.trim() || undefined,
        city: city.trim() || undefined,
        country: country.trim() || undefined,
      },
      note: note.trim(),
    };
    setSaving(true);
    try {
      const saved = initial
        ? await updateContact(initial.slug, payload)
        : await createContact(payload);
      onSaved(saved);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  // Keyboard: Esc closes (guarded — first Esc on a dirty form asks, Esc on
  // the ask keeps editing), Ctrl/Cmd+Enter saves from anywhere in the form.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        if (confirmDiscard) setConfirmDiscard(false);
        else requestClose();
      } else if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        void handleSave();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-scrim/50 backdrop-blur-sm"
      onClick={requestClose}
    >
      <div
        className="max-h-[90vh] w-full max-w-lg overflow-y-auto scrollbar-jarvis rounded-xl border border-border bg-card p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="mb-4 flex items-center gap-3">
          <h3 className="flex-1 font-display text-base font-semibold">
            {initial ? t("contacts.dialogEditTitle") : t("contacts.dialogAddTitle")}
          </h3>
          <button
            type="button"
            onClick={requestClose}
            aria-label={t("contacts.close")}
            className="text-muted-foreground hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="space-y-4">
          <Labeled label={t("contacts.name")}>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              className={inputClass}
              placeholder="Christoph Meyer"
            />
          </Labeled>

          <Labeled label={t("contacts.aliases")}>
            <div
              className={cn(
                inputClass,
                "flex min-h-[2.1rem] flex-wrap items-center gap-1.5 py-1",
              )}
            >
              {aliases.map((alias) => (
                <span
                  key={alias}
                  className="inline-flex items-center gap-1 rounded-full bg-primary/15 px-2 py-0.5 text-xs text-primary"
                >
                  {alias}
                  <button
                    type="button"
                    aria-label={`${t("contacts.delete")} ${alias}`}
                    onClick={() => setAliases(aliases.filter((a) => a !== alias))}
                    className="text-primary/70 hover:text-primary"
                  >
                    <X className="h-3 w-3" />
                  </button>
                </span>
              ))}
              <input
                value={aliasDraft}
                onChange={(e) => {
                  // A comma commits, exactly like Enter — people paste
                  // "Chris, Chrissi" and expect chips.
                  if (e.target.value.includes(",")) {
                    const parts = e.target.value.split(",");
                    const last = parts.pop() ?? "";
                    const committed = parts.map((p) => p.trim()).filter(Boolean);
                    if (committed.length) {
                      setAliases((prev) => [
                        ...prev,
                        ...committed.filter((p) => !prev.includes(p)),
                      ]);
                    }
                    setAliasDraft(last);
                  } else {
                    setAliasDraft(e.target.value);
                  }
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    commitAliasDraft();
                  } else if (e.key === "Backspace" && !aliasDraft && aliases.length) {
                    setAliases(aliases.slice(0, -1));
                  }
                }}
                onBlur={() => commitAliasDraft()}
                placeholder={aliases.length ? "" : t("contacts.addAlias")}
                className="min-w-[6rem] flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground/60"
              />
            </div>
          </Labeled>

          <Labeled label={t("contacts.relationship")}>
            <BrandedSelect
              value={relationship}
              onValueChange={(value) =>
                setRelationship(value as Relationship | "")
              }
              ariaLabel={t("contacts.relationship")}
              className={inputClass}
              options={[
                { value: "", label: "—" },
                ...RELATIONSHIPS.map((item) => ({
                  value: item,
                  label: relationshipLabel(t, item),
                })),
              ]}
            />
          </Labeled>

          <ListField
            label={t("contacts.emails")}
            values={emails}
            onChange={setEmails}
            placeholder="name@example.com"
            type="email"
            addLabel={t("contacts.addEmail")}
            errors={emailErrors}
          />

          <ListField
            label={t("contacts.phones")}
            values={phones}
            onChange={setPhones}
            placeholder="+49 151 2345 6789"
            type="tel"
            addLabel={t("contacts.addPhone")}
            errors={phoneErrors}
            hints={phoneHints}
          />

          <Labeled label={t("contacts.address")}>
            <div className="space-y-2">
              <input
                value={street}
                onChange={(e) => setStreet(e.target.value)}
                className={inputClass}
                placeholder={t("contacts.street")}
              />
              <div className="flex gap-2">
                <input
                  value={postal}
                  onChange={(e) => setPostal(e.target.value)}
                  className={cn(inputClass, "w-1/3")}
                  placeholder={t("contacts.postalCode")}
                />
                <input
                  value={city}
                  onChange={(e) => setCity(e.target.value)}
                  className={cn(inputClass, "flex-1")}
                  placeholder={t("contacts.city")}
                />
              </div>
              <input
                value={country}
                onChange={(e) => setCountry(e.target.value)}
                className={inputClass}
                placeholder={t("contacts.country")}
              />
            </div>
          </Labeled>

          <Labeled
            label={`${t("contacts.readme")} (${wordCount} ${t("contacts.words")})`}
          >
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={5}
              className={cn(inputClass, "resize-y font-sans")}
              placeholder={t("contacts.readmePlaceholder")}
            />
          </Labeled>

          {error && <p className="text-xs text-destructive">{error}</p>}

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={requestClose}
              className="rounded-md border border-border px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground"
            >
              {t("contacts.cancel")}
            </button>
            <button
              type="button"
              onClick={() => void handleSave()}
              disabled={saving || hasInvalidField}
              className="inline-flex items-center gap-2 rounded-md border border-primary/40 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary hover:bg-primary/20 disabled:opacity-50"
            >
              {saving && <Loader2 className="h-3 w-3 animate-spin" />}
              {t("contacts.save")}
            </button>
          </div>
        </div>

        {confirmDiscard && (
          <div
            className="fixed inset-0 z-[60] flex items-center justify-center bg-scrim/60 backdrop-blur-sm"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="w-full max-w-xs rounded-xl border border-border bg-card p-5 shadow-xl">
              <h4 className="font-display text-sm font-semibold">
                {t("contacts.discardTitle")}
              </h4>
              <p className="mt-1.5 text-xs text-muted-foreground">
                {t("contacts.discardBody")}
              </p>
              <div className="mt-4 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setConfirmDiscard(false)}
                  className="rounded-md border border-border px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground"
                >
                  {t("contacts.keepEditing")}
                </button>
                <button
                  type="button"
                  onClick={onClose}
                  className="rounded-md border border-destructive/50 bg-destructive/10 px-3 py-1.5 text-xs font-medium text-destructive hover:bg-destructive/20"
                >
                  {t("contacts.discard")}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

const inputClass =
  "w-full rounded-md border border-border bg-background/40 px-3 py-1.5 text-sm outline-none focus:border-primary/40";

function Labeled({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      {children}
    </label>
  );
}

function ListField({
  label,
  values,
  onChange,
  placeholder,
  type,
  addLabel,
  errors,
  hints,
}: {
  label: string;
  values: string[];
  onChange: (next: string[]) => void;
  placeholder: string;
  type: string;
  addLabel: string;
  errors?: (string | null)[];
  hints?: (string | null)[];
}) {
  return (
    <div className="space-y-1">
      <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      <div className="space-y-2">
        {values.map((value, i) => (
          <div key={i}>
            <div className="flex gap-2">
              <input
                type={type}
                value={value}
                onChange={(e) => {
                  const next = values.slice();
                  next[i] = e.target.value;
                  onChange(next);
                }}
                className={cn(
                  inputClass,
                  errors?.[i] && "border-destructive/50 focus:border-destructive/50",
                )}
                placeholder={placeholder}
              />
              <button
                type="button"
                onClick={() => onChange(values.filter((_, j) => j !== i) || [])}
                aria-label="remove"
                className="shrink-0 rounded-md border border-border px-2 text-muted-foreground hover:border-destructive/50 hover:text-destructive"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
            {errors?.[i] ? (
              <p className="mt-0.5 text-[11px] text-destructive">{errors[i]}</p>
            ) : hints?.[i] ? (
              <p className="mt-0.5 text-[11px] text-muted-foreground">{hints[i]}</p>
            ) : null}
          </div>
        ))}
        <button
          type="button"
          onClick={() => onChange([...values, ""])}
          className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
        >
          <Plus className="h-3.5 w-3.5" />
          {addLabel}
        </button>
      </div>
    </div>
  );
}
