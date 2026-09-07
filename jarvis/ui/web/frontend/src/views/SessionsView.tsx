import { AlertTriangle, Mic } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useEventStore } from "@/store/events";

import { ViewHeader } from "@/views/ChatsView";
import { SessionDetail } from "@/components/sessions/SessionDetail";
import { SessionList } from "@/components/sessions/SessionList";
import { resolveSelectedSessionId } from "@/components/sessions/sessionSelection";
import { useSessionDetail, useSessions } from "@/hooks/useSessions";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * Below this much width the two columns stop fitting: the app's own navigation
 * rail already takes 240 px, so an 850 px window leaves ~610 px here — a 320 px
 * session rail beside it would push the transcript off screen.
 */
const NARROW_PX = 900;

/**
 * Transcription: a 320 px rail of sessions on the sidebar ground, and the
 * chosen session read as a conversation on the page ground.
 *
 * Narrow containers get the master–detail treatment instead: the rail alone,
 * and the transcript alone once a session is picked, with the way back in its
 * header. Width is measured on this view's own box, not the window — the view
 * can share the window with anything else.
 */
export function SessionsView() {
  const assistantName = useEventStore((s) => s.assistantName);
  const t = useT();
  const sessionsQuery = useSessions();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [narrow, setNarrow] = useState(false);
  // Stacked layout only: whether the transcript is the thing on screen. The
  // selection itself resolves on its own (newest finished session), and that
  // must NOT count as opening a session — a narrow view opens on the list.
  const [detailOpen, setDetailOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const list = sessionsQuery.data;
    if (!list) return;
    setSelectedId((currentId) => resolveSelectedSessionId(list, currentId));
  }, [sessionsQuery.data]);

  useEffect(() => {
    const el = rootRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? 0;
      setNarrow(width > 0 && width < NARROW_PX);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const selectSession = useCallback((id: string) => {
    setSelectedId(id);
    setDetailOpen(true);
  }, []);

  const detailQuery = useSessionDetail(selectedId);

  const errorMessage = sessionsQuery.error
    ? sessionsQuery.error instanceof Error
      ? sessionsQuery.error.message
      : t("sessions_view.unknown_error")
    : null;

  const showList = !narrow || !detailOpen;
  const showDetail = !narrow || detailOpen;

  return (
    <div ref={rootRef} className="flex h-full flex-col">
      <ViewHeader
        icon={<Mic />}
        title={t("sessions_view.title")}
        subtitle={t("sessions_view.subtitle")}
      />

      {/* The recorder being switched off is a degraded state, not a failure:
          everything else on this screen still works, so it is a warning
          callout on the room's own ground. */}
      {errorMessage && /HTTP 503/.test(errorMessage) && (
        <div className="mx-8 mb-4 flex items-start gap-3 rounded-lg border border-warning/20 bg-warning/[0.08] px-4 py-3">
          <AlertTriangle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
          <div className="min-w-0">
            <div className="text-base font-medium text-foreground-strong">
              {t("sessions_view.recorder_disabled")}
            </div>
            <div className="mt-1 text-sm text-muted-foreground">
              {t("sessions_view.recorder_hint_a")}{" "}
              <code className="font-mono">[sessions]</code>{" "}
              {t("sessions_view.recorder_hint_b")}{" "}
              <code className="font-mono">jarvis.toml</code>{" "}
              (<code className="font-mono">enabled = true</code>){" "}
              {t("sessions_view.recorder_hint_c")} {assistantName}.
            </div>
          </div>
        </div>
      )}

      <div className="flex min-h-0 flex-1 border-t border-border">
        {showList && (
          <div
            className={cn(
              "min-h-0 overflow-hidden bg-sidebar",
              narrow ? "w-full" : "w-[320px] shrink-0 border-r border-border",
            )}
          >
            <SessionList
              sessions={sessionsQuery.data ?? []}
              selectedId={selectedId}
              onSelect={selectSession}
              loading={sessionsQuery.isLoading}
            />
          </div>
        )}
        {showDetail && (
          <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
            <SessionDetail
              detail={detailQuery.data}
              loading={detailQuery.isLoading && selectedId !== null}
              error={detailQuery.error as Error | null}
              onBack={narrow ? () => setDetailOpen(false) : undefined}
            />
          </div>
        )}
      </div>
    </div>
  );
}
