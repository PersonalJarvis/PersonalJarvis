import { Mail, MapPin, Pencil, Phone, Trash2 } from "lucide-react";
import { useT } from "@/i18n";
import { relationshipLabel } from "./constants";
import type { Contact } from "./api";

/** The detail (right) pane for the selected contact. Read view + edit/delete
 *  affordances; the parent owns the edit dialog + the actual delete call. */
export function ContactDetail({
  contact,
  onEdit,
  onDelete,
}: {
  contact: Contact;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const t = useT();
  const rel = relationshipLabel(t, contact.relationship);
  const addr = formatAddress(contact);

  return (
    <div className="flex h-full flex-col overflow-y-auto scrollbar-jarvis">
      <div className="flex items-start gap-4 border-b border-border p-6">
        <span
          aria-hidden
          className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full border border-border bg-secondary/50 text-lg font-semibold uppercase text-primary"
        >
          {contact.name.trim()[0]?.toUpperCase() ?? "?"}
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="font-display text-lg font-semibold tracking-tight">{contact.name}</h3>
          {contact.aliases.length > 0 && (
            <p className="truncate text-xs text-muted-foreground">
              {t("contacts.aliases")}: {contact.aliases.join(", ")}
            </p>
          )}
          {rel && (
            <span className="mt-1 inline-block rounded-full bg-primary/15 px-2 py-0.5 text-[11px] font-medium text-primary">
              {rel}
            </span>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={onEdit}
            aria-label={t("contacts.edit")}
            className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground"
          >
            <Pencil className="h-3.5 w-3.5" />
            {t("contacts.edit")}
          </button>
          <button
            type="button"
            onClick={onDelete}
            aria-label={t("contacts.delete")}
            className="inline-flex items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-xs text-muted-foreground transition-colors hover:border-destructive/50 hover:text-destructive"
          >
            <Trash2 className="h-3.5 w-3.5" />
            {t("contacts.delete")}
          </button>
        </div>
      </div>

      <div className="space-y-6 p-6">
        {contact.emails.length > 0 && (
          <Field icon={<Mail className="h-4 w-4" />} label={t("contacts.emails")}>
            <ul className="space-y-1">
              {contact.emails.map((e) => (
                <li key={e}>
                  <a
                    href={`mailto:${e}`}
                    className="text-sm text-primary hover:underline"
                  >
                    {e}
                  </a>
                </li>
              ))}
            </ul>
          </Field>
        )}

        {contact.phones.length > 0 && (
          <Field icon={<Phone className="h-4 w-4" />} label={t("contacts.phones")}>
            <ul className="space-y-1">
              {contact.phones.map((p) => (
                <li key={p}>
                  <a href={`tel:${p}`} className="text-sm text-primary hover:underline">
                    {p}
                  </a>
                </li>
              ))}
            </ul>
          </Field>
        )}

        {addr && (
          <Field icon={<MapPin className="h-4 w-4" />} label={t("contacts.address")}>
            <p className="whitespace-pre-line text-sm text-foreground">{addr}</p>
          </Field>
        )}

        {contact.note.trim() && (
          <Field label={t("contacts.readme")}>
            <p className="whitespace-pre-line text-sm leading-relaxed text-foreground">
              {contact.note.trim()}
            </p>
          </Field>
        )}
      </div>
    </div>
  );
}

function Field({
  icon,
  label,
  children,
}: {
  icon?: React.ReactNode;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <section>
      <div className="mb-1.5 flex items-center gap-2 text-[10px] uppercase tracking-wider text-muted-foreground">
        {icon}
        {label}
      </div>
      {children}
    </section>
  );
}

function formatAddress(contact: Contact): string {
  const a = contact.address ?? {};
  const line1 = a.street ?? "";
  const line2 = [a.postal_code, a.city].filter(Boolean).join(" ");
  const line3 = a.country ?? "";
  return [line1, line2, line3].filter((s) => s && s.trim()).join("\n");
}
