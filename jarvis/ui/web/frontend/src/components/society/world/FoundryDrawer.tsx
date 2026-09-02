/**
 * What a click on the Agent Foundry opens: the island's own front door to
 * agent creation. The building is where new agents come from, so its drawer
 * is where you make one — the creator opens straight from here, and closing
 * it puts you back on the island in time to watch the figure walk out.
 *
 * A drawer over the world, never a page navigation (one-viewer doctrine): the
 * island keeps living behind it. App chrome, so it wears the theme tokens;
 * only the portal swatch echoes the building's cyan.
 */
import { Suspense, lazy, useCallback, useEffect, useMemo, useState } from "react";
import { Factory, Plus, X } from "lucide-react";

import { fill, useT, useUiLanguage } from "@/i18n";
import { AgentSwatch } from "../AgentSwatch";
import { useSocietyRoster, type SocietyAgent } from "../data";

const CreateAgentDialog = lazy(() =>
  import("../create/CreateAgentDialog").then((m) => ({ default: m.CreateAgentDialog })),
);

/** How many of the newest agents the drawer lists. */
const RECENT_LIMIT = 6;

function useRelativeTime(): (ms: number) => string {
  const language = useUiLanguage();
  const rtf = useMemo(() => new Intl.RelativeTimeFormat(language, { numeric: "auto" }), [language]);
  return useCallback(
    (ms: number) => {
      const seconds = Math.round((ms - Date.now()) / 1000);
      const abs = Math.abs(seconds);
      if (abs < 60) return rtf.format(Math.round(seconds), "second");
      if (abs < 3600) return rtf.format(Math.round(seconds / 60), "minute");
      if (abs < 86_400) return rtf.format(Math.round(seconds / 3600), "hour");
      return rtf.format(Math.round(seconds / 86_400), "day");
    },
    [rtf],
  );
}

export function FoundryDrawer({
  onClose,
  onSelectAgent,
}: {
  onClose: () => void;
  /** Opens an agent's model card, the same as clicking its figure. */
  onSelectAgent?: (agentId: string) => void;
}) {
  const t = useT();
  const roster = useSocietyRoster();
  const relative = useRelativeTime();
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Escape closes the creator first, then the drawer.
      if (e.key === "Escape" && !creating) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, creating]);

  const agents: SocietyAgent[] = useMemo(() => roster.data?.agents ?? [], [roster.data]);
  const recent = useMemo(
    () => [...agents].sort((a, b) => b.createdMs - a.createdMs).slice(0, RECENT_LIMIT),
    [agents],
  );

  const created = useCallback(
    (agentId: string) => {
      setCreating(false);
      // Get out of the way — the new figure is walking out of the portal now.
      onClose();
      onSelectAgent?.(agentId);
    },
    [onClose, onSelectAgent],
  );

  return (
    <>
      <aside
        className="absolute inset-y-3 right-3 z-30 flex w-[340px] max-w-[85%] flex-col overflow-hidden rounded-lg border border-border bg-popover text-foreground shadow-float"
        role="dialog"
        aria-label={t("society.world.drawer_foundry_title")}
      >
        <header className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
          <div className="min-w-0">
            <h2 className="flex items-center gap-2 font-display text-base font-semibold tracking-tight">
              <span
                className="inline-block h-2.5 w-2.5 rounded-sm"
                style={{ background: "#4cc9f0" }}
                aria-hidden
              />
              {t("society.world.drawer_foundry_title")}
            </h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              {t("society.world.drawer_foundry_hint")}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("society.world.drawer_close")}
            className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <X size={16} />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
          <button
            type="button"
            onClick={() => setCreating(true)}
            className="flex w-full items-center justify-center gap-2 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:opacity-90"
          >
            <Plus size={15} />
            {t("society.world.foundry_create")}
          </button>
          <p className="mt-2 flex items-center gap-1.5 text-xs text-muted-foreground">
            <Factory size={13} aria-hidden />
            {fill(t("society.world.foundry_population"), { count: agents.length })}
          </p>

          <h3 className="mb-1.5 mt-4 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {t("society.world.foundry_recent")}
          </h3>
          {roster.isLoading && (
            <p className="text-sm text-muted-foreground">{t("society.world.drawer_loading")}</p>
          )}
          {!roster.isLoading && recent.length === 0 && (
            <p className="text-sm text-muted-foreground">{t("society.world.foundry_none")}</p>
          )}
          <ul className="flex flex-col gap-1">
            {recent.map((a) => (
              <li key={a.agentId}>
                <button
                  type="button"
                  onClick={() => onSelectAgent?.(a.agentId)}
                  className="flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left hover:bg-muted"
                >
                  <AgentSwatch agent={a} size={28} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium">{a.name}</span>
                    <span className="block truncate text-xs text-muted-foreground">
                      {a.title || a.providerLabel}
                    </span>
                  </span>
                  <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                    {relative(a.createdMs)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>

        <footer className="border-t border-border px-4 py-3">
          <p className="text-xs text-muted-foreground">{t("society.world.foundry_walkout")}</p>
        </footer>
      </aside>
      {creating && (
        <Suspense fallback={null}>
          <CreateAgentDialog open onClose={() => setCreating(false)} onCreated={created} />
        </Suspense>
      )}
    </>
  );
}
