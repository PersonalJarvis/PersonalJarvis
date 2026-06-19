import { Users, Inbox } from "lucide-react";
import { ViewHeader } from "@/views/ChatsView";

export function AgentsView() {
  return (
    <div className="flex h-full flex-col">
      <ViewHeader
        icon={<Users className="h-4 w-4 text-primary" />}
        title="Agent-Team"
        subtitle="Laufende Sub-Agents — Rolle, Status, Output."
      />
      <div className="flex flex-1 items-center justify-center p-8">
        <div className="max-w-md text-center">
          <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl border border-border bg-card/60">
            <Inbox className="h-6 w-6 text-muted-foreground" />
          </div>
          <h3 className="font-display text-lg font-semibold tracking-tight">
            Keine aktiven Agents
          </h3>
          <p className="mt-2 text-sm text-muted-foreground">
            Wenn Jarvis einen Sub-Agent startet, erscheint er hier als
            Live-Kachel mit Streaming-Output und Kill-Button.
          </p>
          <p className="mt-4 text-xs italic text-muted-foreground/70">
            Wird Phase 4 (Harness-Integration) aktiviert.
          </p>
        </div>
      </div>
    </div>
  );
}
