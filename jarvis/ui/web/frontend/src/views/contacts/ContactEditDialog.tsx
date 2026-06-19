import { useState } from "react";
import { Loader2, Plus, X } from "lucide-react";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { RELATIONSHIPS, relationshipLabel, type Relationship } from "./constants";
import {
  createContact,
  updateContact,
  type Contact,
  type ContactInput,
} from "./api";

/**
 * Create / edit a contact. `initial === null` → create (POST); otherwise edit
 * (PATCH the existing slug). Non-blocking modal (backdrop click closes), same
 * shape as PairDialog. The README field shows a live word counter (the store is
 * designed for a short ~300-word bio).
 */
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
  const [aliases, setAliases] = useState((initial?.aliases ?? []).join(", "));
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

  const wordCount = note.trim() ? note.trim().split(/\s+/).length : 0;

  async function handleSave() {
    setError(null);
    if (!name.trim()) {
      setError(t("contacts.nameRequired"));
      return;
    }
    const payload: ContactInput = {
      name: name.trim(),
      aliases: aliases
        .split(",")
        .map((a) => a.trim())
        .filter(Boolean),
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

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onClick={onClose}
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
            onClick={onClose}
            aria-label={t("contacts.cancel")}
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
            <input
              value={aliases}
              onChange={(e) => setAliases(e.target.value)}
              className={inputClass}
              placeholder="Chris, Chrissi"
            />
          </Labeled>

          <Labeled label={t("contacts.relationship")}>
            <select
              value={relationship}
              onChange={(e) => setRelationship(e.target.value as Relationship | "")}
              className={inputClass}
            >
              <option value="">—</option>
              {RELATIONSHIPS.map((r) => (
                <option key={r} value={r}>
                  {relationshipLabel(t, r)}
                </option>
              ))}
            </select>
          </Labeled>

          <ListField
            label={t("contacts.emails")}
            values={emails}
            onChange={setEmails}
            placeholder="name@example.com"
            type="email"
            addLabel={t("contacts.addEmail")}
          />

          <ListField
            label={t("contacts.phones")}
            values={phones}
            onChange={setPhones}
            placeholder="+49 151 2345 6789"
            type="tel"
            addLabel={t("contacts.addPhone")}
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
              onClick={onClose}
              className="rounded-md border border-border px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground"
            >
              {t("contacts.cancel")}
            </button>
            <button
              type="button"
              onClick={() => void handleSave()}
              disabled={saving}
              className="inline-flex items-center gap-2 rounded-md border border-primary/40 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary hover:bg-primary/20 disabled:opacity-50"
            >
              {saving && <Loader2 className="h-3 w-3 animate-spin" />}
              {t("contacts.save")}
            </button>
          </div>
        </div>
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
}: {
  label: string;
  values: string[];
  onChange: (next: string[]) => void;
  placeholder: string;
  type: string;
  addLabel: string;
}) {
  return (
    <div className="space-y-1">
      <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      <div className="space-y-2">
        {values.map((value, i) => (
          <div key={i} className="flex gap-2">
            <input
              type={type}
              value={value}
              onChange={(e) => {
                const next = values.slice();
                next[i] = e.target.value;
                onChange(next);
              }}
              className={inputClass}
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
