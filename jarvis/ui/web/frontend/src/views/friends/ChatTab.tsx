import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

import { ChatThread } from "@/components/friends/ChatThread";
import { FriendListItem } from "@/components/friends/FriendListItem";
import { useFriend, useFriends } from "@/hooks/useFriends";
import { useT } from "@/i18n";

/**
 * Master-detail layout for the chat tab.
 *   Left:  scrollable FriendsList (FriendListItem)
 *   Right: ChatThread for the selected friend
 */
export function ChatTab() {
  const t = useT();
  const friends = useFriends();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const detail = useFriend(selectedId);

  useEffect(() => {
    if (selectedId === null && friends.data && friends.data.length > 0) {
      setSelectedId(friends.data[0].id);
    }
  }, [friends.data, selectedId]);

  return (
    <div className="flex h-full min-h-0 gap-4">
      {/* The friends rail is a standing column, not a card — it takes the rail
          ground and separates from the stage by fill alone. */}
      <aside className="flex w-72 flex-shrink-0 flex-col rounded-lg bg-sidebar p-2">
        {friends.isLoading && (
          <div className="flex items-center justify-center py-6 text-sm text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" /> {t("chat_tab.loading_friends")}
          </div>
        )}
        {friends.isError && (
          <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {t("chat_tab.load_failed")}{" "}
            {(friends.error as Error).message}
          </div>
        )}
        {friends.data && friends.data.length === 0 && (
          <div className="rounded-md border border-dashed border-border/60 px-3 py-4 text-center text-xs text-muted-foreground">
            {t("chat_tab.empty_hint")}{" "}
            <strong>{t("chat_tab.add_friend")}</strong>.
          </div>
        )}
        <div className="mt-1 flex-1 space-y-1 overflow-y-auto scrollbar-jarvis pr-1">
          {friends.data?.map((f) => (
            <FriendListItem
              key={f.id}
              friend={f}
              selected={f.id === selectedId}
              onClick={() => setSelectedId(f.id)}
            />
          ))}
        </div>
      </aside>

      {/* The conversation stage fills the rest of the section, so it stays on
          the page's own ground; the rim is what draws its edge now. */}
      <section className="min-w-0 flex-1 rounded-lg border border-border bg-background">
        {selectedId === null && (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
            {t("chat_tab.select_hint")}
          </div>
        )}
        {selectedId !== null && detail.isLoading && (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" /> {t("common.loading")}
          </div>
        )}
        {selectedId !== null && detail.data && (
          <ChatThread friend={detail.data} />
        )}
      </section>
    </div>
  );
}
