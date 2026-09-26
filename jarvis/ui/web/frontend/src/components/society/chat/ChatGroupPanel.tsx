import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Pencil, Trash2, UsersRound } from "lucide-react";
import { useT } from "@/i18n";
import { deleteSocietyChatGroup, sendSocietyChatGroupMessage, useSocietyChatGroupMessages, type SocietyChatGroup } from "@/lib/societyChatGroups";
import type { SocietyAgent } from "../data";
import { AgentSwatch } from "../AgentSwatch";
import { RosterRail } from "../roster/RosterRail";
import { ChatGroupDialog } from "./ChatGroupDialog";

interface Props {
  group: SocietyChatGroup;
  groups: SocietyChatGroup[];
  roster: SocietyAgent[];
  onOpenAgent: (id: string) => void;
  onOpenGroup: (id: string) => void;
  onCreateAgent: () => void;
  onDeleted: () => void;
}

export function ChatGroupPanel({ group, groups, roster, onOpenAgent, onOpenGroup, onCreateAgent, onDeleted }: Props) {
  const t = useT();
  const client = useQueryClient();
  const { data: messages = [], error: loadError, refetch } = useSocietyChatGroupMessages(group.group_id);
  const [draft, setDraft] = useState("");
  const [recipient, setRecipient] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => bottom.current?.scrollIntoView?.({ block: "end" }), [messages.length]);
  const members = group.members.map((id) => roster.find((agent) => agent.agentId === id)).filter((agent): agent is SocietyAgent => Boolean(agent));
  useEffect(() => {
    if (recipient && !group.members.includes(recipient)) setRecipient("");
  }, [group.members, recipient]);

  const send = async () => {
    const text = draft.trim();
    if (!text || busy) return;
    setBusy(true);
    setError("");
    try {
      const delivery = await sendSocietyChatGroupMessage(group.group_id, text, recipient ? [recipient] : undefined);
      setDraft("");
      if (delivery.skipped.length) {
        const names = delivery.skipped.map((id) => roster.find((agent) => agent.agentId === id)?.name ?? id);
        setError(t("society.groups.skipped").replace("{0}", names.join(", ")));
      }
      await refetch();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };
  const onDraftKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void send();
    }
  };
  const remove = async () => {
    if (!window.confirm(t("society.groups.delete_confirm"))) return;
    setBusy(true);
    try {
      await deleteSocietyChatGroup(group.group_id);
      await client.invalidateQueries({ queryKey: ["society", "chat-groups"] });
      onDeleted();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      setBusy(false);
    }
  };

  return <div className="grid min-h-0 flex-1 grid-cols-[minmax(240px,300px)_minmax(0,1fr)] bg-card" data-testid="society-group-chat">
    <RosterRail agents={roster} groups={groups} activeGroupId={group.group_id} activeAgentId={null}
      onOpen={onOpenAgent} onOpenGroup={onOpenGroup} onCreate={onCreateAgent} loading={false} sample={false}
      side="left" className="w-full border-0 jarvis-nav-surface" />
    <section className="flex min-h-0 flex-col overflow-hidden border-l border-border bg-background" aria-label={group.name}>
      <header className="flex items-center gap-3 border-b border-border px-5 py-3">
        <UsersRound className="h-5 w-5 text-muted-foreground" aria-hidden />
        <div className="min-w-0 flex-1"><h2 className="truncate font-display text-base font-semibold">{group.name}</h2>
          <p className="text-xs text-muted-foreground">{members.length} {t("society.groups.members")}</p>
        </div>
        <button type="button" onClick={() => setEditing(true)} aria-label={t("society.groups.edit")} title={t("society.groups.edit")} className="rounded-md p-2 text-muted-foreground hover:bg-secondary hover:text-foreground"><Pencil className="h-4 w-4" /></button>
        <button type="button" onClick={() => void remove()} aria-label={t("society.groups.delete")} title={t("society.groups.delete")} className="rounded-md p-2 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"><Trash2 className="h-4 w-4" /></button>
      </header>
      <div className="flex flex-wrap gap-1.5 border-b border-border px-5 py-2">
        {members.map((agent) => <button type="button" key={agent.agentId} onClick={() => onOpenAgent(agent.agentId)} className="flex items-center gap-1.5 rounded-full bg-secondary px-2 py-1 text-xs hover:bg-secondary/70">
          <AgentSwatch agent={agent} size={20} />{agent.name}
        </button>)}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4" data-testid="society-group-transcript">
        {messages.length === 0 && <p className="pt-12 text-center text-sm text-muted-foreground">{t("society.groups.empty")}</p>}
        <div className="flex flex-col gap-3">
          {messages.map((message) => {
            const agent = roster.find((item) => item.agentId === message.from_agent);
            const own = message.from_agent === "user";
            return <div key={message.id} className={`flex gap-2 ${own ? "justify-end" : "justify-start"}`}>
              {!own && agent && <AgentSwatch agent={agent} size={28} />}
              <div className={`max-w-[min(48rem,85%)] rounded-2xl px-3.5 py-2.5 ${own ? "bg-primary text-primary-foreground" : "bg-secondary text-foreground"}`}>
                {!own && <p className="mb-1 text-xs font-semibold">{agent?.name ?? message.from_agent}</p>}
                <p className="whitespace-pre-wrap break-words text-sm">{message.text}</p>
                <time className="mt-1 block text-right text-[10px] opacity-60" dateTime={new Date(message.ts_ms).toISOString()}>{new Date(message.ts_ms).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}</time>
              </div>
            </div>;
          })}
        </div>
        <div ref={bottom} />
      </div>
      {(error || loadError) && <p role="alert" className="px-5 py-1 text-sm text-destructive">{error || t("society.groups.error")}</p>}
      <form onSubmit={(event: FormEvent) => { event.preventDefault(); void send(); }} className="border-t border-border p-3">
        <div className="flex items-center gap-2 pb-2 text-xs text-muted-foreground">
          <label htmlFor="group-recipient">{t("society.groups.recipients")}</label>
          <select id="group-recipient" value={recipient} onChange={(event) => setRecipient(event.target.value)} className="rounded-md border border-border bg-background px-2 py-1 text-foreground">
            <option value="">{t("society.groups.everyone")}</option>
            {members.map((agent) => <option key={agent.agentId} value={agent.agentId}>{agent.name}</option>)}
          </select>
        </div>
        <div className="flex items-end gap-2"><textarea value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={onDraftKey} rows={2}
          placeholder={t("society.groups.placeholder")} aria-label={t("society.groups.placeholder")}
          className="min-h-12 flex-1 resize-y rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" />
          <button type="submit" disabled={busy || !draft.trim()} className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50">{t("society.groups.send")}</button>
        </div>
      </form>
    </section>
    {editing && <ChatGroupDialog group={group} agents={roster} onClose={() => setEditing(false)} onSaved={onOpenGroup} />}
  </div>;
}
