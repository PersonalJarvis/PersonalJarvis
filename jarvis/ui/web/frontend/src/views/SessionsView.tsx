/**
 * Transcription view — master-detail layout for voice sessions.
 *
 * Left pane: SessionList (chronological). Right pane: detail with header
 * + turn timeline + click-to-copy. Live updates via the useSessions hook,
 * which reacts to VoiceSessionStarted/Ended bus events.
 */
import { AlertTriangle, Mic } from "lucide-react";
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
      {/* --primary is a fill, never a decorative glyph. */}
      <ViewHeader
        icon={<Mic className="h-4 w-4 text-foreground" />}
        title={t("sessions_view.title")}
        subtitle={t("sessions_view.subtitle")}
      />

      {/* The recorder being switched off is a degraded state, not a failure:
          everything else on this screen still works. So it is a --warning
          glyph on the room's own ground, and NOT the near-white band this
          used to be — a full-bleed region never rises above its room, and a
          status painted in --foreground outshouts every real heading. */}
      {errorMessage && /HTTP 503/.test(errorMessage) && (
        <div className="flex items-start gap-3 border-b border-border px-6 py-4">
          <AlertTriangle
            aria-hidden="true"
            className="mt-0.5 h-4 w-4 shrink-0 text-warning"
          />
          <div className="min-w-0">
            <div className="text-title font-semibold text-foreground-strong">
              {t("sessions_view.recorder_disabled")}
            </div>
            <div className="mt-1 text-meta text-muted-foreground">
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

      <div className="grid min-h-0 flex-1 grid-cols-[320px_1fr]">
        <div className="min-h-0 overflow-hidden border-r border-border">
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
