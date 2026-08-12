import { Star } from "lucide-react";

import { cn } from "@/lib/utils";
import { useT } from "@/i18n";
import { relationshipLabel } from "./constants";
import { contactAvatarStyle, contactInitials } from "./avatar";
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
      <button
        type="button"
        onClick={onClick}
        className={cn(
          "group flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left transition-colors",
          active
            ? "bg-background text-foreground shadow-[inset_2px_0_0_hsl(var(--primary))]"
            : "text-muted-foreground hover:bg-background/60 hover:text-foreground",
        )}
      >
        <span
          aria-hidden
          style={contactAvatarStyle(contact.name)}
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-semibold"
        >
          {contactInitials(contact.name)}
        </span>
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="truncate text-sm font-medium text-foreground">{contact.name}</span>
          {subtitle && (
            <span className="truncate text-xs text-muted-foreground">{subtitle}</span>
          )}
        </span>
        {contact.favorite && (
          <Star aria-hidden className="h-3 w-3 shrink-0 fill-current text-primary/70" />
        )}
        {rel && (
          <span className="rounded-full bg-primary/15 px-2 py-0.5 text-[10px] font-medium text-primary">
            {rel}
          </span>
        )}
      </button>
    </li>
  );
}
