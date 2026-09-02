import { AlertTriangle, Mic } from "lucide-react";
import { useEffect, useState } from "react";
import { useEventStore } from "@/store/events";

import { ViewHeader } from "@/views/ChatsView";
import { SessionDetail } from "@/components/sessions/SessionDetail";
import { SessionList } from "@/components/sessions/SessionList";
import { resolveSelectedSessionId } from "@/components/sessions/sessionSelection";
import { useSessionDetail, useSessions } from "@/hooks/useSessions";
import { useT } from "@/i18n";

/**
 * Transcription: a 320 px rail of sessions on the sidebar ground, and the
 * chosen session read as a conversation on the page ground.
 */
export function SessionsView() {
  const assistantName = useEventStore((s) => s.assistantName);
  const t = useT();
  const sessionsQuery = useSessions();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => {
    const list = sessionsQuery.data;
    if (!list) return;
    setSelectedId((currentId) => resolveSelectedSessionId(list, currentId));
  }, [sessionsQuery.data]);

  const detailQuery = useSessionDetail(selectedId);

  const errorMessage = sessionsQuery.error
    ? sessionsQuery.error instanceof Error
      ? sessionsQuery.error.message
      : t("sessions_view.unknown_error")
    : null;

  return (
    <div className="flex h-full flex-col">
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

      <div className="grid min-h-0 flex-1 grid-cols-[320px_1fr] border-t border-border">
        <div className="min-h-0 overflow-hidden border-r border-border bg-sidebar">
          <SessionList
            sessions={sessionsQuery.data ?? []}
            selectedId={selectedId}
            onSelect={setSelectedId}
            loading={sessionsQuery.isLoading}
          />
        </div>
        <div className="flex min-h-0 min-w-0 flex-col overflow-hidden">
          <SessionDetail
            detail={detailQuery.data}
            loading={detailQuery.isLoading && selectedId !== null}
            error={detailQuery.error as Error | null}
          />
        </div>
      </div>
    </div>
  );
}
