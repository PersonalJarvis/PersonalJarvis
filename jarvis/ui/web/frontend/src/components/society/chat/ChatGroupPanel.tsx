import { useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Pencil, Trash2 } from "lucide-react";
import { useT } from "@/i18n";
import { BrandedSelect } from "@/components/ui/select";
import { deleteSocietyChatGroup, type SocietyChatGroup } from "@/lib/societyChatGroups";
import { createAgentChatStore } from "@/store/agentChat";
import type { SocietyAgent } from "../data";
import { AgentSwatch } from "../AgentSwatch";
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
  const [confirmUngroup, setConfirmUngroup] = useState(false);
  const [removing, setRemoving] = useState(false);
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
    setRemoving(true);
    setError("");
    try {
      await deleteSocietyChatGroup(group.group_id);
      await client.invalidateQueries({ queryKey: ["society", "chat-groups"] });
      onDeleted();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      setRemoving(false);
    }
  };

  const pane = (side: "left" | "right", agent: SocietyAgent | undefined) => (
    <section key={side} data-testid={`society-group-pane-${side}`} className="flex min-h-0 min-w-0 flex-col bg-background">
      {agent ? <>
        <header data-testid={`society-group-identity-${side}`}
          className="flex shrink-0 items-center gap-3 border-b border-border-strong bg-secondary/25 px-4 py-3">
          <span data-testid={`society-group-avatar-${side}`} className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-secondary">
            <AgentSwatch agent={agent} size={38} />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              {t(side === "left" ? "society.groups.left_chat" : "society.groups.right_chat")}
            </p>
            {members.length > 2 ? <BrandedSelect
              ariaLabel={t(side === "left" ? "society.groups.left_chat" : "society.groups.right_chat")}
              testId={`society-group-select-${side}`}
              value={agent.agentId}
              onValueChange={(value) => side === "left" ? setLeftId(value) : setRightId(value)}
              className="mt-1 w-full"
              options={members.filter((member) => member.agentId !== (side === "left" ? right?.agentId : left?.agentId))
                .map((member) => ({ value: member.agentId, label: member.name }))}
            /> : <h2 className="truncate text-base font-semibold text-foreground">{agent.name}</h2>}
            <p className="truncate text-xs text-muted-foreground">{agent.title}</p>
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

  const teamFooter = <div className="shrink-0 border-t border-border bg-sidebar px-3 py-3">
    {confirmUngroup ? <div role="alertdialog" aria-label={t("society.groups.delete")}
      className="space-y-3 text-xs text-foreground">
      <p>{t("society.groups.delete_confirm")}</p>
      <div className="flex justify-end gap-2">
        <button type="button" onClick={() => setConfirmUngroup(false)} disabled={removing}
          className="rounded-md px-2 py-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground">
          {t("society.groups.cancel")}
        </button>
        <button type="button" onClick={() => void remove()} disabled={removing}
          className="rounded-md bg-destructive px-2 py-1.5 font-medium text-destructive-foreground disabled:opacity-50">
          {t("society.groups.delete")}
        </button>
      </div>
    </div> : <div className="flex items-center gap-2">
      <button type="button" onClick={() => setEditing(true)}
        className="inline-flex min-w-0 flex-1 items-center justify-center gap-1 rounded-md border border-border px-2 py-1.5 text-xs text-muted-foreground hover:bg-secondary hover:text-foreground">
        <Pencil className="h-3.5 w-3.5 shrink-0" aria-hidden />
        <span className="truncate">{t("society.groups.edit")}</span>
      </button>
      <button type="button" onClick={() => setConfirmUngroup(true)}
        className="inline-flex shrink-0 items-center gap-1 rounded-md border border-border px-2 py-1.5 text-xs text-muted-foreground hover:bg-destructive/10 hover:text-destructive">
        <Trash2 className="h-3.5 w-3.5" aria-hidden />{t("society.groups.delete")}
      </button>
    </div>}
    {error && <p role="alert" className="mt-2 text-xs text-destructive">{error}</p>}
  </div>;

  return <div className="grid min-h-0 flex-1 grid-cols-[minmax(240px,300px)_minmax(0,1fr)] bg-card" data-testid="society-group-chat">
    <RosterRail agents={roster} groups={groups} activeGroupId={group.group_id} activeAgentId={null}
      onOpen={onOpenAgent} onOpenGroup={onOpenGroup} onCreate={onCreateAgent}
      onGroupAgents={onGroupAgents} onAddAgentToGroup={onAddAgentToGroup}
      loading={false} sample={false} side="left" className="w-full border-0 jarvis-nav-surface"
      footer={teamFooter} />
    <div className="grid min-h-0 min-w-0 grid-cols-2 divide-x-2 divide-border-strong overflow-hidden border-l border-border bg-background"
      data-testid="society-group-split">
      {pane("left", left)}
      {pane("right", right)}
    </div>
    {editing && <ChatGroupDialog group={group} agents={roster} onClose={() => setEditing(false)} onSaved={onOpenGroup} />}
  </div>;
}
