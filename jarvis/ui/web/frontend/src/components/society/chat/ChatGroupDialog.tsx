import { useState, type FormEvent } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { useQueryClient } from "@tanstack/react-query";
import { useT } from "@/i18n";
import { createSocietyChatGroup, updateSocietyChatGroup, type SocietyChatGroup } from "@/lib/societyChatGroups";
import type { SocietyAgent } from "../data";
import { AgentSwatch } from "../AgentSwatch";

interface Props {
  group?: SocietyChatGroup;
  agents: SocietyAgent[];
  onClose: () => void;
  onSaved: (groupId: string) => void;
}

export function ChatGroupDialog({ group, agents, onClose, onSaved }: Props) {
  const t = useT();
  const client = useQueryClient();
  const [name, setName] = useState(group?.name ?? "");
  const [members, setMembers] = useState<string[]>(group?.members.filter((id) =>
    agents.some((agent) => agent.agentId === id && agent.lifecycle !== "archived")) ?? []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const choices = agents.filter((agent) => agent.tier !== "lead" && agent.lifecycle !== "archived");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim() || members.length < 2) return;
    setBusy(true);
    setError("");
    try {
      const saved = group
        ? await updateSocietyChatGroup(group.group_id, name.trim(), members)
        : await createSocietyChatGroup(name.trim(), members);
      await client.invalidateQueries({ queryKey: ["society", "chat-groups"] });
      onSaved(saved.group_id);
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  return <Dialog.Root open onOpenChange={(open) => { if (!open) onClose(); }}>
    <Dialog.Portal>
      <Dialog.Overlay className="fixed inset-0 z-[150] bg-scrim/60" />
      <Dialog.Content className="fixed left-1/2 top-1/2 z-[151] flex max-h-[80vh] w-[min(28rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 flex-col rounded-xl border border-border bg-card p-5 text-foreground shadow-xl">
        <Dialog.Title className="font-display text-lg font-semibold">{t(group ? "society.groups.edit" : "society.groups.create")}</Dialog.Title>
        <Dialog.Description className="mt-1 text-sm text-muted-foreground">{t("society.groups.description")}</Dialog.Description>
        <form onSubmit={(event) => void submit(event)} className="mt-4 flex min-h-0 flex-col gap-3">
          <label className="text-sm font-medium">{t("society.groups.name")}
            <input autoFocus maxLength={80} value={name} onChange={(event) => setName(event.target.value)}
              className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-foreground" />
          </label>
          <p className="text-sm font-medium">{t("society.groups.members")} ({members.length})</p>
          <div className="min-h-0 overflow-y-auto rounded-md border border-border p-1">
            {choices.map((agent) => <label key={agent.agentId} className="flex cursor-pointer items-center gap-3 rounded-md px-2 py-1.5 hover:bg-secondary">
              <input type="checkbox" checked={members.includes(agent.agentId)} onChange={() => setMembers((current) => current.includes(agent.agentId) ? current.filter((id) => id !== agent.agentId) : [...current, agent.agentId])} />
              <AgentSwatch agent={agent} size={28} />
              <span className="min-w-0 truncate text-sm">{agent.name}</span>
            </label>)}
          </div>
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          <div className="flex justify-end gap-2">
            <button type="button" onClick={onClose} className="rounded-md px-3 py-2 text-sm hover:bg-secondary">{t("society.groups.cancel")}</button>
            <button type="submit" disabled={busy || !name.trim() || members.length < 2}
              className="rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground disabled:opacity-50">{t("society.groups.save")}</button>
          </div>
        </form>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
