/**
 * "Runs on" — where new agents run: this computer, or one of the connected
 * computers (a VPS, a local VM; see the Computers section). On a computer the
 * agents live in tmux there and keep working while this PC is off; the folder
 * is copied there first (new files that look like secrets stay here).
 */
import { useEffect } from "react";
import { Laptop, Server } from "lucide-react";
import { useComputerChoiceList } from "@/hooks/useComputers";
import { useEventStore } from "@/store/events";
import { cn } from "@/lib/utils";

const RUN_ON_KEY = "jarvis.agenticIde.runOn.";

/** The computer last chosen for a project's new workspaces; storage may be blocked. */
export function storedRunOn(projectId: string | undefined): string | null {
  if (!projectId) return null;
  try { return localStorage.getItem(RUN_ON_KEY + projectId) || null; } catch { return null; }
}
export function storeRunOn(projectId: string | undefined, computerId: string | null): void {
  if (!projectId) return;
  try {
    if (computerId) localStorage.setItem(RUN_ON_KEY + projectId, computerId);
    else localStorage.removeItem(RUN_ON_KEY + projectId);
  } catch { /* a convenience only */ }
}

function detailFor(status: string): string {
  if (status === "online") return "Keeps running while this PC is off";
  if (status === "unknown") return "Not checked yet";
  return "Not reachable right now";
}

export function RunOnPicker({
  value,
  onChange,
  disabled,
  hideWhenNone = false,
}: {
  value: string | null;
  onChange: (computerId: string | null) => void;
  disabled?: boolean;
  /** Show nothing (instead of a "Connect a server" hint) when no computer is connected. */
  hideWhenNone?: boolean;
}) {
  const { computers, loaded } = useComputerChoiceList();
  const setActiveSection = useEventStore((state) => state.setActiveSection);
  const usable = computers.filter((computer) => computer.health.status !== "provisioning");
  const options = [
    { id: null as string | null, name: "This computer", detail: "Stops when this PC sleeps or shuts down", online: true, icon: Laptop },
    ...usable.map((computer) => ({
      id: computer.id as string | null,
      name: computer.name,
      detail: detailFor(computer.health.status),
      online: computer.health.status === "online",
      icon: Server,
    })),
  ];
  // A remembered computer that has been removed since falls back to this PC,
  // rather than leaving no choice selected and failing on create.
  const missing = loaded && value !== null && !usable.some((computer) => computer.id === value);
  useEffect(() => { if (missing) onChange(null); }, [missing, onChange]);

  if (hideWhenNone && usable.length === 0) return null;
  const chosen = usable.find((computer) => computer.id === value);
  return (
    <fieldset data-testid="ide-run-on" disabled={disabled}>
      <legend className="text-xs font-medium text-muted-foreground">Runs on</legend>
      <div role="radiogroup" aria-label="Runs on" className="mt-2 grid gap-2 sm:grid-cols-2">
        {options.map((option) => {
          const active = option.id === value;
          const Icon = option.icon;
          return (
            <button
              key={option.id ?? "local"}
              type="button"
              role="radio"
              aria-checked={active}
              data-testid={`ide-run-on-${option.id ?? "local"}`}
              onClick={() => onChange(option.id)}
              className={cn(
                "flex items-start gap-3 rounded-lg border px-3 py-2.5 text-left transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50",
                active ? "border-accent bg-accent-soft" : "border-border hover:border-border-strong",
              )}
            >
              <Icon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
              <span className="min-w-0">
                <span className="flex items-center gap-2 text-sm font-medium text-foreground">
                  <span className="truncate">{option.name}</span>
                  {option.id && (
                    <span
                      aria-hidden="true"
                      className={cn("h-1.5 w-1.5 shrink-0 rounded-full", option.online ? "bg-success" : "bg-foreground-faint")}
                    />
                  )}
                </span>
                <span className="block text-xs text-muted-foreground">{option.detail}</span>
              </span>
            </button>
          );
        })}
      </div>
      {chosen && (
        <p data-testid="ide-run-on-note" className="mt-2 text-xs text-muted-foreground">
          The folder is copied to {chosen.name} first, uncommitted changes included. Files like .env
          and private keys stay on this PC. The coding CLI must be installed there.
        </p>
      )}
      {usable.length === 0 && (
        <p className="mt-2 text-xs text-muted-foreground">
          Want agents that keep working while this PC is off?{" "}
          <button type="button" onClick={() => setActiveSection("computers")}
            className="font-medium text-accent underline-offset-4 hover:underline">
            Connect a server
          </button>
        </p>
      )}
    </fieldset>
  );
}
