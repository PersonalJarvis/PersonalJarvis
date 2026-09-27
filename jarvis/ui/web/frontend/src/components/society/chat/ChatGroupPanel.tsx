import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Pencil, Trash2, UsersRound } from "lucide-react";
import { useT } from "@/i18n";
import { BrandedSelect } from "@/components/ui/select";
import { deleteSocietyChatGroup, type SocietyChatGroup } from "@/lib/societyChatGroups";
import { createAgentChatStore } from "@/store/agentChat";
import type { SocietyAgent } from "../data";
import { RosterRail } from "../roster/RosterRail";
import { AgentChatPanel } from "./AgentChatPanel";
import { ChatGroupDialog } from "./ChatGroupDialog";

interface Props {
  group: SocietyChatGroup;
  groups: SocietyChatGroup[];
  roster: SocietyAgent[];
  onOpenAgent: (id: string) => void;
  onOpenGroup: (id: string) => void;
  onCreateAgent: () => void;
  onDeleted: () => void;
  onGroupAgents: (sourceId: string, targetId: string) => void;
  onAddAgentToGroup: (agentId: string, groupId: string) => void;
}

export function ChatGroupPanel({
  group, groups, roster, onOpenAgent, onOpenGroup, onCreateAgent, onDeleted,
  onGroupAgents, onAddAgentToGroup,
}: Props) {
  const t = useT();
  const client = useQueryClient();
  const [leftId, setLeftId] = useState(group.members[0] ?? "");
  const [rightId, setRightId] = useState(group.members[1] ?? "");
  const [editing, setEditing] = useState(false);
  const [error, setError] = useState("");
  const leftStore = useMemo(() => createAgentChatStore("society", `group:${group.group_id}:left`), [group.group_id]);
  const rightStore = useMemo(() => createAgentChatStore("society", `group:${group.group_id}:right`), [group.group_id]);

  useEffect(() => () => {
    leftStore.getState().disconnect();
    rightStore.getState().disconnect();
  }, [leftStore, rightStore]);

  const members = group.members
    .map((id) => roster.find((agent) => agent.agentId === id))
    .filter((agent): agent is SocietyAgent => Boolean(agent));
  const left = members.find((agent) => agent.agentId === leftId) ?? members[0];
  const right = members.find((agent) => agent.agentId === rightId && agent.agentId !== left?.agentId)
    ?? members.find((agent) => agent.agentId !== left?.agentId);

  const remove = async () => {
    if (!window.confirm(t("society.groups.delete_confirm"))) return;
    setError("");
    try {
      await deleteSocietyChatGroup(group.group_id);
      await client.invalidateQueries({ queryKey: ["society", "chat-groups"] });
      onDeleted();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  const pane = (side: "left" | "right", agent: SocietyAgent | undefined) => (
    <section key={side} data-testid={`society-group-pane-${side}`} className="flex min-h-0 min-w-0 flex-col bg-background">
      {agent ? <>
        <header className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-2">
          <div className="min-w-0 flex-1 text-xs font-medium text-muted-foreground">
            <span>{t(side === "left" ? "society.groups.left_chat" : "society.groups.right_chat")}</span>
            <BrandedSelect
              ariaLabel={t(side === "left" ? "society.groups.left_chat" : "society.groups.right_chat")}
              testId={`society-group-select-${side}`}
              value={agent.agentId}
              onValueChange={(value) => side === "left" ? setLeftId(value) : setRightId(value)}
              className="mt-1 w-full"
              options={members.filter((member) => member.agentId !== (side === "left" ? right?.agentId : left?.agentId))
                .map((member) => ({ value: member.agentId, label: member.name }))}
            />
          </div>
          <button type="button" onClick={() => onOpenAgent(agent.agentId)}
            className="rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-secondary hover:text-foreground"
            aria-label={t("society.groups.open_individual").replace("{0}", agent.name)}>
            {t("society.groups.open")}
          </button>
        </header>
        <div className="min-h-0 flex-1">
          <AgentChatPanel key={agent.agentId} agent={agent} roster={roster}
            chatStore={side === "left" ? leftStore : rightStore} />
        </div>
      </> : <p className="m-auto p-6 text-center text-sm text-muted-foreground">{t("society.groups.missing_member")}</p>}
    </section>
  );

  return <div className="grid min-h-0 flex-1 grid-cols-[minmax(240px,300px)_minmax(0,1fr)] bg-card" data-testid="society-group-chat">
    <RosterRail agents={roster} groups={groups} activeGroupId={group.group_id} activeAgentId={null}
      onOpen={onOpenAgent} onOpenGroup={onOpenGroup} onCreate={onCreateAgent}
      onGroupAgents={onGroupAgents} onAddAgentToGroup={onAddAgentToGroup}
      loading={false} sample={false} side="left" className="w-full border-0 jarvis-nav-surface" />
    <div className="flex min-h-0 min-w-0 flex-col overflow-hidden border-l border-border bg-background">
      <header className="flex shrink-0 items-center gap-3 border-b border-border px-5 py-3">
        <UsersRound className="h-5 w-5 text-muted-foreground" aria-hidden />
        <div className="min-w-0 flex-1"><h2 className="truncate font-display text-base font-semibold">{group.name}</h2>
          <p className="text-xs text-muted-foreground">{members.length} {t("society.groups.members")}</p>
        </div>
        <button type="button" onClick={() => setEditing(true)} aria-label={t("society.groups.edit")}
          className="rounded-md p-2 text-muted-foreground hover:bg-secondary hover:text-foreground"><Pencil className="h-4 w-4" /></button>
        <button type="button" onClick={() => void remove()} aria-label={t("society.groups.delete")}
          className="rounded-md p-2 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"><Trash2 className="h-4 w-4" /></button>
      </header>
      {error && <p role="alert" className="px-5 py-1 text-sm text-destructive">{error}</p>}
      <div className="grid min-h-0 flex-1 grid-cols-2 divide-x divide-border" data-testid="society-group-split">
        {pane("left", left)}
        {pane("right", right)}
      </div>
    </div>
    {editing && <ChatGroupDialog group={group} agents={roster} onClose={() => setEditing(false)} onSaved={onOpenGroup} />}
  </div>;
}
