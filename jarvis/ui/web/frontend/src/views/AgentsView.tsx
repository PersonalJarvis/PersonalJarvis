import { Users, Inbox } from "lucide-react";
import { ViewHeader } from "@/views/ChatsView";
import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";

export function AgentsView() {
  const t = useT();
  const assistantName = useEventStore((s) => s.assistantName);
  return (
    <div className="flex h-full flex-col">
      <ViewHeader
        icon={<Users className="h-4 w-4 text-primary" />}
        title="Agent-Team"
        subtitle={t("agents_view.subtitle")}
      />
      <div className="flex flex-1 items-center justify-center p-8">
        <div className="max-w-md text-center">
          <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl border border-border bg-card/60">
            <Inbox className="h-6 w-6 text-muted-foreground" />
          </div>
          <h3 className="font-display text-lg font-semibold tracking-tight">
            {t("agents_view.empty_title")}
          </h3>
          <p className="mt-2 text-sm text-muted-foreground">
            {t("agents_view.empty_body_a")} {assistantName}{" "}
            {t("agents_view.empty_body_b")}
          </p>
          <p className="mt-4 text-xs italic text-muted-foreground/70">
            {t("agents_view.phase4_notice")}
          </p>
        </div>
      </div>
    </div>
  );
}
