import { useEffect, useMemo, useRef, useState } from "react";
import { FlaskConical, Search } from "lucide-react";

import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { PanelSkeleton } from "@/components/layout/PanelSkeleton";
import { RunDetail } from "@/components/runs/RunDetail";
import { RunList } from "@/components/runs/RunList";
import { useRuns } from "@/hooks/useRuns";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import { ViewHeader } from "@/views/ChatsView";

/**
 * Run Inspector — master–detail over every recorded run.
 *
 * Left: the rail, one row per run, searchable across the utterance, the
 * session id and the capabilities the run touched. Right: the selected run,
 * whose own header and tabs carry the forensic material.
 *
 * The layout measures ITSELF, not the window: this view also runs in a solo
 * window and beside a wide sidebar, and below ~900 px a 320 px rail plus a
 * reading column no longer fit. There it stacks — the rail is the screen, and
 * picking a run replaces it with the detail plus a Back control.
 */

/** Below this container width the master–detail splits into stacked screens. */
const NARROW_PX = 900;

export function RunInspectorView() {
  const t = useT();
  const { data: runs, isError, isLoading } = useRuns();
  const [selected, setSelected] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  // `null` = not measured yet, so nothing auto-selects before the layout is
  // known — on a narrow container an auto-selection hides the list outright.
  const [narrow, setNarrow] = useState<boolean | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = rootRef.current;
    if (!el || typeof ResizeObserver === "undefined") {
      setNarrow(false);
      return;
    }
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? 0;
      if (width > 0) setNarrow(width < NARROW_PX);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // Wide layouts open on the newest run, because the detail pane is on screen
  // anyway and an empty one next to a full list reads as a failure to load.
  useEffect(() => {
    if (narrow !== false || selected !== null) return;
    if (runs && runs.length > 0) setSelected(runs[0].session_id);
  }, [narrow, runs, selected]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q || !runs) return runs ?? [];
    return runs.filter(
      (r) =>
        r.preview.toLowerCase().includes(q) ||
        r.session_id.toLowerCase().includes(q) ||
        r.feature_tags.some((tag) => tag.toLowerCase().includes(q)),
    );
  }, [runs, query]);

  if (isError) {
    // The recorder is off, not broken — a designed state, with the reason.
    return (
      <div className="flex h-full flex-col">
        <ViewHeader
          icon={<FlaskConical />}
          title={t("run_inspector.title")}
          subtitle={t("run_inspector.subtitle")}
        />
        <div className="flex min-h-0 flex-1 items-center justify-center p-8">
          <EmptyState
            icon={<FlaskConical />}
            title={t("run_inspector.unavailable")}
            description={t("run_inspector.unavailable_body")}
          />
        </div>
      </div>
    );
  }

  const hasRuns = (runs?.length ?? 0) > 0;
  const isNarrow = narrow === true;
  const showList = !isNarrow || selected === null;
  const showDetail = !isNarrow || selected !== null;

  return (
    <div ref={rootRef} className="flex h-full flex-col">
      <ViewHeader
        icon={<FlaskConical />}
        title={t("run_inspector.title")}
        subtitle={t("run_inspector.subtitle")}
      />

      <div className="flex min-h-0 flex-1">
        {showList && (
          // A standing column the full height of the section: far too wide to
          // earn --card, so it separates from the detail by taking the rail's
          // own ground rather than by a rule.
          <div
            className={cn(
              "flex min-h-0 shrink-0 flex-col border-r border-border bg-sidebar",
              isNarrow ? "w-full" : "w-[320px]",
            )}
          >
            {hasRuns && (
              <div className="shrink-0 p-3">
                <div className="relative">
                  <Search
                    aria-hidden
                    className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
                  />
                  <Input
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder={t("run_inspector.search")}
                    aria-label={t("run_inspector.search")}
                    data-testid="run-search"
                    className="pl-9 ring-offset-sidebar"
                  />
                </div>
              </div>
            )}
            <nav
              aria-label={t("run_inspector.title")}
              className="min-h-0 flex-1 overflow-y-auto scrollbar-jarvis p-2"
            >
              {isLoading ? (
                // Real rows at their real height, not a spinner in a void.
                <PanelSkeleton
                  rows={7}
                  rowHeight={72}
                  label={t("run_inspector.loading")}
                  className="p-1"
                />
              ) : !hasRuns ? (
                // The stage carries the empty state; the rail says nothing twice.
                null
              ) : filtered.length === 0 ? (
                <p className="px-3 py-8 text-center text-base text-muted-foreground">
                  {t("run_inspector.no_matches")}
                </p>
              ) : (
                <RunList items={filtered} selectedId={selected} onSelect={setSelected} />
              )}
            </nav>
          </div>
        )}

        {showDetail && (
          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            {selected ? (
              <RunDetail
                sessionId={selected}
                onBack={isNarrow ? () => setSelected(null) : undefined}
              />
            ) : !hasRuns && !isLoading ? (
              <div className="flex h-full items-center justify-center p-8">
                <EmptyState
                  icon={<FlaskConical />}
                  title={t("run_inspector.empty_title")}
                  description={t("run_inspector.empty_body")}
                />
              </div>
            ) : (
              <div className="flex h-full items-center justify-center p-8">
                <EmptyState
                  icon={<FlaskConical />}
                  title={t("run_inspector.empty")}
                  description={t("run_inspector.empty_hint")}
                />
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
