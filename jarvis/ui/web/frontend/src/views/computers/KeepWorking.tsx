/**
 * "Keep working when this PC closes" — the one opt-in that makes closing the
 * app safe for running coding agents: on quit, every IDE workspace with an
 * agent running on this machine moves to the chosen computer (folder,
 * uncommitted edits and conversation included) and carries on there in tmux.
 * Windows computers are not offered: without tmux their agents stop with the
 * app's connection.
 */
import { useEffect, useState } from "react";
import { Loader2, MoonStar } from "lucide-react";
import { Panel } from "@/components/extensions/primitives";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import type { Computer } from "@/lib/computersApi";

const ENDPOINT = "/api/agentic-ide/offload-on-quit";

export function KeepWorking({ computers }: { computers: Computer[] }) {
  const t = useT();
  const [value, setValue] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    void fetch(ENDPOINT)
      .then((res) => (res.ok ? res.json() : null))
      .then((body: { computer_id?: string | null } | null) => {
        if (!alive) return;
        setValue(body?.computer_id ?? null);
        setLoaded(true);
      })
      .catch(() => {
        if (alive) setLoaded(true);
      });
    return () => {
      alive = false;
    };
  }, []);

  async function choose(next: string | null) {
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(ENDPOINT, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ computer_id: next }),
      });
      if (!res.ok) throw new Error(`${res.status}`);
      setValue(next);
    } catch {
      setError(t("computers.keep_failed"));
    } finally {
      setSaving(false);
    }
  }

  // A Windows computer has no tmux: agents moved there at quit would stop with
  // this PC's connection, so it is no target (the backend refuses it as well).
  const isWindows = (computer: Computer) => computer.facts?.os_id === "windows";
  const hasWindows = computers.some(isWindows);
  const options = [
    { id: null as string | null, label: t("computers.keep_off") },
    ...computers
      .filter((computer) => computer.health.status !== "provisioning" && !isWindows(computer))
      .map((computer) => ({ id: computer.id as string | null, label: computer.name })),
  ];

  return (
    <Panel className="p-5">
      <div className="flex items-start gap-3">
        <MoonStar className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-base font-semibold text-foreground-strong">{t("computers.keep_title")}</span>
            {saving && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" aria-hidden />}
          </div>
          <p className="mt-0.5 text-sm text-muted-foreground">{t("computers.keep_body")}</p>
          <div role="radiogroup" aria-label={t("computers.keep_title")} className="mt-4 flex flex-wrap gap-1.5" data-testid="computers-keep-working">
            {options.map((option) => {
              const active = loaded && option.id === value;
              return (
                <button
                  key={option.id ?? "off"}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  disabled={!loaded || saving}
                  onClick={() => void choose(option.id)}
                  className={cn(
                    "h-8 rounded-md border px-3 text-sm font-medium transition-colors",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60",
                    active
                      ? "border-accent bg-accent-soft text-foreground-strong"
                      : "border-border text-muted-foreground hover:border-border-strong hover:text-foreground",
                  )}
                >
                  {option.label}
                </button>
              );
            })}
          </div>
          {hasWindows && <p className="mt-2 text-xs text-muted-foreground">{t("computers.keep_windows_note")}</p>}
          {error && <p role="alert" className="mt-2 text-sm text-destructive">{error}</p>}
        </div>
      </div>
    </Panel>
  );
}
