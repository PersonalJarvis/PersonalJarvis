// === F-FRIENDS [F2] · feature/friends-section · alex-2026-04-30 ===
import { useState } from "react";
import { Plus, Users } from "lucide-react";

import { ViewHeader } from "@/views/ChatsView";
import { AddFriendMenu } from "@/components/friends/AddFriendMenu";
import { ChatTab } from "@/views/friends/ChatTab";
import { StatusTab } from "@/views/friends/StatusTab";
import { cn } from "@/lib/utils";
import { useT } from "@/i18n";

/**
 * Phase F2 — zwei Tabs auf diesem Branch:
 *   Chat    -> Master-Detail mit ChatThread (Telegram in F2, Federation-DM in F3).
 *   Status  -> F4-Stub fuer Per-Friend-Sharing-Permissions.
 *
 * Der Federation-Feed-Tab existiert in spaeteren Branches (Phase 5+).
 * Auf skills-brain-integration ohne ihn aufgebaut, damit keine fehlenden
 * Imports ts-Fehler werfen.
 */
type Tab = "chat" | "status";

export function FriendsView() {
  const t = useT();
  const [tab, setTab] = useState<Tab>("chat");
  const [addOpen, setAddOpen] = useState(false);

  return (
    <div className="flex h-full flex-col">
      <ViewHeader
        icon={<Users className="h-4 w-4 text-primary" />}
        title="Friends"
        subtitle={t("friends_view.subtitle")}
        right={
          <button
            type="button"
            onClick={() => setAddOpen(true)}
            className="inline-flex items-center gap-1 rounded-md border border-primary/40 bg-primary/10 px-2.5 py-1 text-[11px] text-primary hover:bg-primary/20"
          >
            <Plus className="h-3 w-3" /> {t("add_friend_menu.title")}
          </button>
        }
      />

      <div className="flex flex-shrink-0 gap-1 border-b border-border px-6 py-2">
        <TabButton active={tab === "chat"} onClick={() => setTab("chat")}>
          Chat
        </TabButton>
        <TabButton active={tab === "status"} onClick={() => setTab("status")}>
          Status
        </TabButton>
      </div>

      <div className="flex-1 min-h-0 overflow-hidden p-6">
        {tab === "chat" && <ChatTab />}
        {tab === "status" && <StatusTab />}
      </div>

      <AddFriendMenu
        open={addOpen}
        onClose={() => setAddOpen(false)}
        onPairOpen={() => {
          // Pair-Dialog existiert auf diesem Branch noch nicht (kommt mit
          // Federation in spaeteren Branches). No-Op statt Crash.
          setAddOpen(false);
        }}
      />
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-md px-3 py-1 text-xs uppercase tracking-wider transition-colors",
        active
          ? "bg-primary/15 text-primary"
          : "text-muted-foreground hover:text-foreground"
      )}
    >
      {children}
    </button>
  );
}
