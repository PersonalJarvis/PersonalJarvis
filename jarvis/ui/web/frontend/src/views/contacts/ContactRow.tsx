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
          "group flex w-full items-center gap-row rounded-md px-3 py-2 text-left transition-colors",
          active ? "bg-secondary" : "hover:bg-secondary",
        )}
      >
        <IdentityAvatar name={contact.name} />
        <span className="flex min-w-0 flex-1 flex-col">
          <span
            className={cn(
              "truncate text-body",
              active ? "text-foreground-strong" : "text-foreground",
            )}
          >
            {contact.name}
          </span>
          {subtitle && (
            <span className="truncate text-meta text-muted-foreground">{subtitle}</span>
          )}
        </span>
        {contact.favorite && (
          <Star aria-hidden className="h-3.5 w-3.5 shrink-0 fill-current text-foreground" />
        )}
        {rel && (
          <span className="rounded-full bg-popover px-2 py-0.5 text-micro text-muted-foreground">
            {rel}
          </span>
        )}
      </button>
    </li>
  );
}
