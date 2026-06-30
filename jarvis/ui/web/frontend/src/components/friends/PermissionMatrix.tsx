// === F-FRIENDS [F4] · feature/friends-section · ruben-2026-05-01 ===
import { cn } from "@/lib/utils";
import type { StatusProfile } from "@/hooks/useFriends";

/**
 * 3-Radio-Selector fuer das Sharing-Profile eines Friends.
 *
 * Stateless / kontrolliert: ``current`` zeigt den aktuellen Wert,
 * ``onChange`` wird beim Wechsel gerufen. ``disabled`` setzt alle
 * Radios visuell + funktional inaktiv (z.B. waehrend des Mutation-Calls).
 */
const PROFILES: { value: StatusProfile; label: string; subline: string }[] = [
  { value: "minimal", label: "minimal", subline: "Nur online/offline" },
  { value: "standard", label: "standard", subline: "+ Mission-Titel" },
  { value: "detailed", label: "detailed", subline: "+ Jarvis-Agent-Summary" },
];

export function PermissionMatrix({
  friendId,
  current,
  onChange,
  disabled = false,
}: {
  friendId: string;
  current: StatusProfile;
  onChange: (profile: StatusProfile) => void;
  disabled?: boolean;
}) {
  const groupName = `permission-${friendId}`;
  return (
    <div
      role="radiogroup"
      aria-label="Sharing-Profile"
      className="grid gap-2 sm:grid-cols-3"
    >
      {PROFILES.map((p) => {
        const isActive = p.value === current;
        return (
          <label
            key={p.value}
            className={cn(
              "flex cursor-pointer flex-col gap-1 rounded-lg border px-3 py-2 transition-colors",
              isActive
                ? "border-primary/50 bg-primary/10"
                : "border-border bg-card/30 hover:border-border/80",
              disabled && "cursor-not-allowed opacity-60"
            )}
          >
            <div className="flex items-center gap-2">
              <input
                type="radio"
                name={groupName}
                value={p.value}
                checked={isActive}
                disabled={disabled}
                onChange={() => onChange(p.value)}
                className="h-3.5 w-3.5 accent-primary"
              />
              <span className="font-display text-xs font-semibold uppercase tracking-wider text-foreground">
                {p.label}
              </span>
            </div>
            <span className="pl-5 text-[11px] text-muted-foreground">
              {p.subline}
            </span>
          </label>
        );
      })}
    </div>
  );
}
