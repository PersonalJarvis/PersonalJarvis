/**
 * Transcription view — master-detail layout for voice sessions.
 *
 * Left pane: SessionList (chronological). Right pane: detail with header
 * + turn timeline + click-to-copy. Live updates via the useSessions hook,
 * which reacts to VoiceSessionStarted/Ended bus events.
 */
import { Mic } from "lucide-react";
import { useEffect, useState } from "react";
import { useEventStore } from "@/store/events";

import { ViewHeader } from "@/views/ChatsView";
import { SessionDetail } from "@/components/sessions/SessionDetail";
import { SessionList } from "@/components/sessions/SessionList";
import { resolveSelectedSessionId } from "@/components/sessions/sessionSelection";
import { useSessionDetail, useSessions } from "@/hooks/useSessions";
import { useT } from "@/i18n";

export function SessionsView() {
  const assistantName = useEventStore((s) => s.assistantName);
  const t = useT();
  const sessionsQuery = useSessions();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Keep selection aligned with the visible list. A running attempt can be
  // selected and then disappear after hangup when the API confirms that it
  // contains no transcript. In that case, move to the newest finished
  // transcript instead of leaving an invisible row selected in the detail
  // pane. Initial selection follows the same rule.
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
        icon={<Mic className="h-4 w-4 text-primary" />}
        title={t("sessions_view.title")}
        subtitle={t("sessions_view.subtitle")}
      />

      {errorMessage && /HTTP 503/.test(errorMessage) && (
        <div className="border-b border-amber-400/30 bg-amber-400/10 px-5 py-3 text-sm text-amber-200">
          <div className="font-medium">{t("sessions_view.recorder_disabled")}</div>
          <div className="mt-0.5 text-xs text-amber-200/80">
            {t("sessions_view.recorder_hint_a")}{" "}
            <code className="font-mono">[sessions]</code>{" "}
            {t("sessions_view.recorder_hint_b")}{" "}
            <code className="font-mono">jarvis.toml</code>{" "}
            (<code className="font-mono">enabled = true</code>){" "}
            {t("sessions_view.recorder_hint_c")} {assistantName}.
          </div>
        </div>
      )}

      <div className="grid min-h-0 flex-1 grid-cols-[320px_1fr]">
        <div className="min-h-0 overflow-hidden border-r border-border bg-card/30 backdrop-blur">
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
