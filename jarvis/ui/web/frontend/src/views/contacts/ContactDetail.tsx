import { useEffect, useState } from "react";
import {
  ArrowLeft,
  BookOpen,
  Cake,
  Check,
  Copy,
  Link2,
  Loader2,
  Mail,
  MapPin,
  Pencil,
  Phone,
  PhoneCall,
  Star,
  Tag,
  Trash2,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import { relationshipLabel } from "./constants";
import { contactAvatarStyle, contactInitials } from "./avatar";
import { updateContact, type Contact } from "./api";

/**
 * The detail (right) pane for the selected contact — a read view with actions:
 * call via the assistant (telephony), open the mirrored wiki person page,
 * copy any field, mailto/tel links, and the README rendered as Markdown.
 * The parent owns the edit dialog + the actual delete call.
 */

// The outbound route accepts E.164 only ("+" + digits) — never offer a call
// the backend would reject.
const E164_RE = /^\+\d{6,}$/;

// One status probe per app session: whether outbound calls are possible at
// all (twilio installed + configured + enabled). A failed probe means "no".
let telephonyProbe: Promise<boolean> | null = null;
function canPlaceCalls(): Promise<boolean> {
  telephonyProbe ??= fetch("/api/telephony/status")
    .then((res) => (res.ok ? res.json() : null))
    .then(
      (status: { available?: boolean; configured?: boolean; enabled?: boolean } | null) =>
        Boolean(status?.available && status?.configured && status?.enabled),
    )
    .catch(() => false);
  return telephonyProbe;
}

export function ContactDetail({
  contact,
  onEdit,
  onDelete,
  onBack,
  onChanged,
}: {
  contact: Contact;
  onEdit: () => void;
  onDelete: () => void;
  /** Present only in the narrow (stacked) layout: return to the list. */
  onBack?: () => void;
  /** Inform the parent after an in-place PATCH (favorite toggle). */
  onChanged?: (contact: Contact) => void;
}) {
  const t = useT();
  const requestWikiPage = useEventStore((s) => s.requestWikiPage);
  const pushToast = useEventStore((s) => s.pushToast);
  const rel = relationshipLabel(t, contact.relationship);
  const addr = formatAddress(contact);
  const updated = formatUpdated(contact.last_updated);
  const orgLine = [contact.organization, contact.role].filter(Boolean).join(" · ");

  const [callable, setCallable] = useState(false);
  const [confirmCall, setConfirmCall] = useState<string | null>(null);
  const [calling, setCalling] = useState(false);
  const [togglingFav, setTogglingFav] = useState(false);

  async function toggleFavorite() {
    if (togglingFav) return;
    setTogglingFav(true);
    try {
      const next = await updateContact(contact.slug, { favorite: !contact.favorite });
      onChanged?.(next);
    } catch (e) {
      pushToast("error", e instanceof Error ? e.message : String(e));
    } finally {
      setTogglingFav(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    void canPlaceCalls().then((ok) => {
      if (!cancelled) setCallable(ok);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const callPhone = contact.phones.find((p) => E164_RE.test(p)) ?? null;

  async function placeCall(phone: string) {
    setCalling(true);
    try {
      const res = await fetch("/api/telephony/outbound", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ to: phone }),
      });
      const data = (await res.json().catch(() => ({}))) as {
        ok?: boolean;
        error?: string;
      };
      if (res.ok && data.ok) {
        pushToast("success", t("contacts.callStarted"));
      } else {
        pushToast("error", `${t("contacts.callFailed")} ${data.error ?? res.status}`);
      }
    } catch (e) {
      pushToast("error", `${t("contacts.callFailed")} ${e instanceof Error ? e.message : e}`);
    } finally {
      setCalling(false);
      setConfirmCall(null);
    }
  }

  return (
    <div className="flex h-full flex-col overflow-y-auto scrollbar-jarvis">
      <div className="border-b border-border p-6">
        <div className="flex items-start gap-4">
          {onBack && (
            <button
              type="button"
              onClick={onBack}
              aria-label={t("contacts.back")}
              className="mt-1 shrink-0 rounded-md border border-border p-1.5 text-muted-foreground hover:border-primary/40 hover:text-foreground"
            >
              <ArrowLeft className="h-4 w-4" />
            </button>
          )}
          <span
            aria-hidden
            style={contactAvatarStyle(contact.name)}
            className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full text-xl font-semibold"
          >
            {contactInitials(contact.name)}
          </span>
          <div className="min-w-0 flex-1">
            <h3 className="font-display text-lg font-semibold tracking-tight">{contact.name}</h3>
            {orgLine && (
              <p className="truncate text-xs text-muted-foreground">{orgLine}</p>
            )}
            {contact.aliases.length > 0 && (
              <p className="truncate text-xs text-muted-foreground">
                {t("contacts.aliases")}: {contact.aliases.join(", ")}
              </p>
            )}
            <div className="mt-1 flex flex-wrap items-center gap-2">
              {rel && (
                <span className="inline-block rounded-full bg-primary/15 px-2 py-0.5 text-[11px] font-medium text-primary">
                  {rel}
                </span>
              )}
              {updated && (
                <span className="text-[11px] text-muted-foreground">
                  {t("contacts.lastUpdated")}: {updated}
                </span>
              )}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <button
              type="button"
              onClick={() => void toggleFavorite()}
              aria-label={t("contacts.favorite")}
              title={t("contacts.favorite")}
              disabled={togglingFav}
              className={cn(
                "rounded-md border border-border p-1.5 transition-colors disabled:opacity-50",
                contact.favorite
                  ? "border-primary/40 text-primary"
                  : "text-muted-foreground hover:border-primary/40 hover:text-foreground",
              )}
            >
              <Star className={cn("h-3.5 w-3.5", contact.favorite && "fill-current")} />
            </button>
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

        {/* Action row: what you can DO with this person from here. */}
        <div className="mt-4 flex flex-wrap items-center gap-2">
          {callable && callPhone && (
            <button
              type="button"
              onClick={() => setConfirmCall(callPhone)}
              className="inline-flex items-center gap-1.5 rounded-md border border-primary/40 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary hover:bg-primary/20"
            >
              <PhoneCall className="h-3.5 w-3.5" />
              {t("contacts.call")}
            </button>
          )}
          <button
            type="button"
            onClick={() => requestWikiPage(contact.slug)}
            className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs text-muted-foreground hover:border-primary/40 hover:text-foreground"
          >
            <BookOpen className="h-3.5 w-3.5" />
            {t("contacts.openWiki")}
          </button>
        </div>
      </div>

      <div className="space-y-6 p-6">
        {contact.emails.length > 0 && (
          <Field icon={<Mail className="h-4 w-4" />} label={t("contacts.emails")}>
            <ul className="space-y-1">
              {contact.emails.map((e) => (
                <li key={e} className="flex items-center gap-1.5">
                  <a
                    href={`mailto:${e}`}
                    className="text-sm text-primary hover:underline"
                  >
                    {e}
                  </a>
                  <CopyButton value={e} label={t("contacts.copy")} />
                </li>
              ))}
            </ul>
          </Field>
        )}

        {contact.phones.length > 0 && (
          <Field icon={<Phone className="h-4 w-4" />} label={t("contacts.phones")}>
            <ul className="space-y-1">
              {contact.phones.map((p) => (
                <li key={p} className="flex items-center gap-1.5">
                  <a href={`tel:${p}`} className="text-sm text-primary hover:underline">
                    {p}
                  </a>
                  <CopyButton value={p} label={t("contacts.copy")} />
                </li>
              ))}
            </ul>
          </Field>
        )}

        {contact.birthday && (
          <Field icon={<Cake className="h-4 w-4" />} label={t("contacts.birthday")}>
            <p className="text-sm text-foreground">{formatBirthday(contact.birthday)}</p>
          </Field>
        )}

        {contact.urls.length > 0 && (
          <Field icon={<Link2 className="h-4 w-4" />} label={t("contacts.urls")}>
            <ul className="space-y-1">
              {contact.urls.map((u) => (
                <li key={u} className="flex items-center gap-1.5">
                  <a
                    href={u.includes("://") ? u : `https://${u}`}
                    target="_blank"
                    rel="noreferrer"
                    className="truncate text-sm text-primary hover:underline"
                  >
                    {u}
                  </a>
                  <CopyButton value={u} label={t("contacts.copy")} />
                </li>
              ))}
            </ul>
          </Field>
        )}

        {addr && (
          <Field icon={<MapPin className="h-4 w-4" />} label={t("contacts.address")}>
            <div className="flex items-start gap-1.5">
              <p className="whitespace-pre-line text-sm text-foreground">{addr}</p>
              <CopyButton value={addr.replace(/\n/g, ", ")} label={t("contacts.copy")} />
            </div>
          </Field>
        )}

        {contact.tags.length > 0 && (
          <Field icon={<Tag className="h-4 w-4" />} label={t("contacts.tags")}>
            <div className="flex flex-wrap gap-1.5">
              {contact.tags.map((tag) => (
                <span
                  key={tag}
                  className="rounded-full border border-border px-2 py-0.5 text-xs text-muted-foreground"
                >
                  #{tag}
                </span>
              ))}
            </div>
          </Field>
        )}

        {contact.note.trim() && (
          <Field label={t("contacts.readme")}>
            <article className="prose prose-neutral max-w-none text-sm dark:prose-invert prose-a:text-primary prose-code:text-foreground prose-pre:border prose-pre:border-border prose-pre:bg-card/80">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{contact.note.trim()}</ReactMarkdown>
            </article>
          </Field>
        )}
      </div>

      {confirmCall && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-scrim/50 backdrop-blur-sm"
          onClick={() => !calling && setConfirmCall(null)}
        >
          <div
            className="w-full max-w-sm rounded-xl border border-border bg-card p-6 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="font-display text-base font-semibold">
              {t("contacts.callTitle")}
            </h3>
            <p className="mt-2 text-sm text-muted-foreground">
              {t("contacts.callConfirm")
                .replace("{0}", contact.name)
                .replace("{1}", confirmCall)}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">{t("contacts.callNote")}</p>
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                disabled={calling}
                onClick={() => setConfirmCall(null)}
                className="rounded-md border border-border px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground disabled:opacity-50"
              >
                {t("contacts.cancel")}
              </button>
              <button
                type="button"
                disabled={calling}
                onClick={() => void placeCall(confirmCall)}
                className="inline-flex items-center gap-2 rounded-md border border-primary/40 bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary hover:bg-primary/20 disabled:opacity-50"
              >
                {calling && <Loader2 className="h-3 w-3 animate-spin" />}
                {t("contacts.call")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/** Small clipboard button; flips to a check mark for a moment on success. */
function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={() => {
        void navigator.clipboard?.writeText(value).then(() => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1500);
        });
      }}
      className="rounded p-1 text-muted-foreground/60 transition-colors hover:bg-background/60 hover:text-foreground"
    >
      {copied ? (
        <Check className="h-3.5 w-3.5 text-primary" />
      ) : (
        <Copy className="h-3.5 w-3.5" />
      )}
    </button>
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

function formatUpdated(iso: string | null): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function formatBirthday(iso: string): string {
  // Parse as a plain date (no timezone shifts): "1990-04-12" stays April 12.
  const [y, m, d] = iso.split("-").map(Number);
  if (!y || !m || !d) return iso;
  return new Date(y, m - 1, d).toLocaleDateString(undefined, { dateStyle: "long" });
}
