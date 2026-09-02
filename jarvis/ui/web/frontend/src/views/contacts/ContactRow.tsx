import { Star } from "lucide-react";

import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { IdentityAvatar } from "@/components/identity/IdentityAvatar";
import { relationshipLabel } from "./constants";
import type { ContactSummary } from "./api";

/** One row in the master (left) list of the Contacts master–detail view. */
export function ContactRow({
  contact,
  active,
  onClick,
}: {
  contact: ContactSummary;
  active: boolean;
  onClick: () => void;
}) {
  const t = useT();
  const rel = relationshipLabel(t, contact.relationship);
  const subtitle = contact.primary_email ?? contact.primary_phone ?? "";
  return (
    <li>
      {/* Selection is a full-width fill on the whole row, one step UP the
          ladder. The old pair — a --background fill (darker than the rail it
          sits in) plus a hover that darkened further — inverted both rules. */}
      <button
        type="button"
        onClick={onClick}
        className={cn(
          "group flex min-h-12 w-full items-center gap-3 rounded-md px-3 py-1.5 text-left transition-colors",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          active ? "jarvis-nav-active bg-secondary" : "hover:bg-secondary",
        )}
      >
        <IdentityAvatar name={contact.name} />
        <span className="flex min-w-0 flex-1 flex-col">
          <span
            className={cn(
              "truncate text-base font-medium",
              active ? "text-foreground-strong" : "text-foreground",
            )}
          >
            {contact.name}
          </span>
          {subtitle && (
            <span className="truncate text-sm text-muted-foreground">{subtitle}</span>
          )}
        </span>
        {contact.favorite && (
          <Star aria-hidden className="h-3.5 w-3.5 shrink-0 fill-current text-foreground" />
        )}
        {rel && (
          <span className="inline-flex h-6 items-center rounded-md border border-border bg-secondary px-2 text-xs font-medium text-muted-foreground">
            {rel}
          </span>
        )}
      </button>
    </li>
  );
}
