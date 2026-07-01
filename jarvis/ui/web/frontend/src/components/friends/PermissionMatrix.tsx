// === F-FRIENDS [F4] · feature/friends-section · ruben-2026-05-01 ===
import { cn } from "@/lib/utils";
import type { StatusProfile } from "@/hooks/useFriends";

/**
 * 3-radio selector for a friend's sharing profile.
 *
 * Stateless / controlled: ``current`` shows the current value, ``onChange``
 * is called on change. ``disabled`` makes all radios visually and
 * functionally inactive (e.g. during the mutation call).
 */
const PROFILES: { value: StatusProfile; label: string; subline: string }[] = [
  { value: "minimal", label: "minimal", subline: "Online/offline only" },
  { value: "standard", label: "standard", subline: "+ mission title" },
  { value: "detailed", label: "detailed", subline: "+ Jarvis-Agent summary" },
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
