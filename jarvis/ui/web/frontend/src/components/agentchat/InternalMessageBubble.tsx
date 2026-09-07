import type { InternalMessageItem } from "./reduce";
import { useT } from "@/i18n";

/** Shared by the lead's timeline and the society's canonical agent chat. */
export function InternalMessageBubble({ item }: { item: InternalMessageItem }) {
  const t = useT();
  return (
    <div data-testid="agent-message-internal" data-message-id={item.id}
      className="max-w-[85%] self-start rounded-lg border border-border bg-card px-4 py-3 text-reading">
      <div className="mb-1 flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
        <span className="font-medium text-foreground">{item.message.sender_name}</span>
        <span>{t("agent_chat.internal_message")}</span>
        <span aria-live="polite">{t(`agent_chat.delivery_${item.message.status}`)}</span>
      </div>
      <div className="whitespace-pre-wrap">{item.message.text}</div>
      {item.message.error && <div className="text-destructive">{item.message.error}</div>}
    </div>
  );
}
