/**
 * What a click on the Plugin Docks opens: every plugin installed in this app,
 * grouped by family, as a drawer over the island (one-viewer doctrine — the
 * world keeps living behind it). Data: `/api/plugins`, the entry-point
 * catalog the backend already serves. The drawer is app chrome, so it wears
 * the theme tokens; only the family swatches echo the building's bay colours.
 */
import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { ExternalLink, X } from "lucide-react";

import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";

/** Entry-point group → family key, label key and the bay colour it wears. */
const FAMILIES: ReadonlyArray<{ group: string; key: string; color: string }> = [
  { group: "jarvis.brain", key: "brain", color: "#9b5de5" },
  { group: "jarvis.tool", key: "tool", color: "#2ec4b6" },
  { group: "jarvis.stt", key: "stt", color: "#ff6f61" },
  { group: "jarvis.tts", key: "tts", color: "#ffb703" },
  { group: "jarvis.channel", key: "channel", color: "#06d6a0" },
  { group: "jarvis.realtime", key: "realtime", color: "#4cc9f0" },
  { group: "jarvis.wakeword", key: "wakeword", color: "#c9c2b2" },
  { group: "jarvis.harness", key: "harness", color: "#8b8f9c" },
];

async function fetchPlugins(): Promise<Record<string, string[]>> {
  const res = await fetch("/api/plugins");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return (await res.json()) as Record<string, string[]>;
}

export function PluginStoreDrawer({ onClose }: { onClose: () => void }) {
  const t = useT();
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const plugins = useQuery({ queryKey: ["plugins", "catalog"], queryFn: fetchPlugins, staleTime: 60_000 });

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const total = plugins.data ? Object.values(plugins.data).reduce((n, v) => n + v.length, 0) : 0;

  return (
    <aside
      className="absolute inset-y-3 right-3 z-30 flex w-[340px] max-w-[85%] flex-col overflow-hidden rounded-lg border border-border bg-popover text-foreground shadow-float"
      role="dialog"
      aria-label={t("society.world.drawer_plugins_title")}
    >
      <header className="flex items-start justify-between gap-3 border-b border-border px-4 py-3">
        <div className="min-w-0">
          <h2 className="font-display text-base font-semibold tracking-tight">{t("society.world.drawer_plugins_title")}</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">{t("society.world.drawer_plugins_hint")}</p>
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
        {plugins.isLoading && <p className="text-sm text-muted-foreground">{t("society.world.drawer_loading")}</p>}
        {plugins.isError && <p className="text-sm text-destructive">{String(plugins.error)}</p>}
        {plugins.data && total === 0 && <p className="text-sm text-muted-foreground">{t("society.world.drawer_empty")}</p>}
        {plugins.data &&
          FAMILIES.filter((f) => (plugins.data?.[f.group]?.length ?? 0) > 0).map((f) => {
            const names = [...(plugins.data?.[f.group] ?? [])].sort();
            return (
              <section key={f.group} className="mb-4">
                <h3 className="mb-1.5 flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: f.color }} aria-hidden />
                  {t(`society.world.plugin_group_${f.key}`)}
                  <span className="tabular-nums">{names.length}</span>
                </h3>
                <ul className="flex flex-wrap gap-1.5">
                  {names.map((n) => (
                    <li key={n} className="rounded-md bg-secondary px-2 py-1 font-mono text-xs text-foreground">
                      {n}
                    </li>
                  ))}
                </ul>
              </section>
            );
          })}
      </div>
      <footer className="border-t border-border px-4 py-3">
        <button
          type="button"
          onClick={() => setActiveSection("plugins")}
          className="inline-flex h-8 items-center gap-2 rounded-md bg-secondary px-3 text-sm font-medium text-foreground hover:bg-muted"
        >
          <ExternalLink size={14} />
          {t("society.world.drawer_open_section")}
        </button>
      </footer>
    </aside>
  );
}
