import { lazy, Suspense, useEffect, type ReactNode } from "react";
import { useT } from "@/i18n";
import { useRoutineNavigation } from "./routineNavigation";

const RoutineChat = lazy(() => import("../card/RoutineChat"));

export function RoutineChatHost({ agentId, onOpen, children }: {
  agentId: string; onOpen: () => void; children: ReactNode;
}) {
  const t = useT();
  const target = useRoutineNavigation((state) => state.target);
  const close = useRoutineNavigation((state) => state.close);
  const selected = target?.agentId === agentId ? target : null;
  useEffect(() => { if (selected) onOpen(); }, [selected, onOpen]);
  useEffect(() => () => {
    if (useRoutineNavigation.getState().target?.agentId === agentId) close();
  }, [close, agentId]);
  return selected ? <Suspense fallback={<p role="status" className="p-4 text-sm">{t("tasks_view.loading_details")}</p>}>
    <RoutineChat key={`${selected.sessionId}:${selected.legacy?.messageId ?? selected.timestamp}`} target={selected} onClose={close} />
  </Suspense> : children;
}
