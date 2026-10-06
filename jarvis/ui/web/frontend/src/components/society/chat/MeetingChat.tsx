import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useT } from "@/i18n";
import { societyDisplayName } from "@/lib/societyDisplayName";
import { useEventStore } from "@/store/events";
import type { SocietyAgent } from "../data";
import type { SocietyChatGroup } from "@/lib/societyChatGroups";

interface MeetingSnapshot {
  messages: { id: string; speaker: string; text: string }[];
  running: boolean;
  room: { state: string; settle_reason: string; next_speaker: string | null } | null;
}

async function request(url: string, text?: string, stop = false): Promise<MeetingSnapshot> {
  const response = await fetch(url, text === undefined && !stop ? undefined : {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: stop ? undefined : JSON.stringify({ text }),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === "string" ? result.detail : `HTTP ${response.status}`);
  return result;
}

export function MeetingChat({ group, roster }: { group: SocietyChatGroup; roster: SocietyAgent[] }) {
  const t = useT();
  const client = useQueryClient();
  const key = ["society", "meeting", group.group_id];
  const url = `/api/society/chat-groups/${encodeURIComponent(group.group_id)}/meeting`;
  // Poll only while a round runs; an idle transcript changes only when someone sends.
  const query = useQuery({
    queryKey: key, queryFn: () => request(url),
    refetchInterval: (current) => current.state.data?.running ? 2000 : false,
  });
  const [text, setText] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const assistantName = useEventStore((s) => s.assistantName);
  const name = (id: string) => {
    if (id === "user") return t("society.meeting.you");
    const agent = roster.find((a) => a.agentId === id);
    return agent ? societyDisplayName(agent, assistantName) : id;
  };
  useEffect(() => { end.current?.scrollIntoView?.({ block: "nearest" }); }, [query.data?.messages.length]);
  const send = async (stop = false) => {
    if (pending || (!stop && !text.trim())) return;
    setPending(true); setError("");
    try {
      const result = await request(stop ? `${url}/stop` : url, stop ? undefined : text.trim(), stop);
      client.setQueryData(key, result);
      if (!stop) setText("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally { setPending(false); }
  };
  const tooMany = group.members.length > 6;
  return <section className="flex min-h-0 min-w-0 flex-1 flex-col bg-background" data-testid="society-meeting">
    <header className="border-b border-border px-4 py-3">
      <h2 className="font-semibold text-foreground">{group.name}</h2>
      <p className="text-xs text-muted-foreground">{group.members.map(name).join(" · ")}</p>
      <p className="mt-2 text-xs text-muted-foreground">{t("society.meeting.description")}</p>
    </header>
    <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4" role="log" aria-label={t("society.meeting.title")}>
      {!query.data?.messages.length && <p className="text-sm text-muted-foreground">{t("society.meeting.empty")}</p>}
      {query.data?.messages.map((message) => <article key={message.id}
        className={`max-w-[90%] rounded-xl border border-border p-3 ${message.speaker === "user" ? "ml-auto bg-secondary" : "bg-card"}`}>
        <p className="mb-1 text-xs font-semibold text-foreground">{name(message.speaker)}</p>
        <p className="whitespace-pre-wrap break-words text-sm text-foreground">{message.text}</p>
      </article>)}
      <div ref={end} />
    </div>
    <form className="space-y-2 border-t border-border p-3" onSubmit={(event) => { event.preventDefault(); void send(); }}>
      {query.data?.running && <p role="status" className="text-xs text-muted-foreground">
        {t("society.meeting.answering").replace("{0}", name(query.data.room?.next_speaker ?? ""))}
      </p>}
      {query.data?.room?.state === "failed" && <p role="alert" className="text-sm text-destructive">{t("society.meeting.failed")}</p>}
      {tooMany && <p role="alert" className="text-sm text-destructive">{t("society.meeting.limit")}</p>}
      {(error || query.error) && <p role="alert" className="text-sm text-destructive">{error || String(query.error)}</p>}
      <label className="sr-only" htmlFor={`meeting-${group.group_id}`}>{t("society.meeting.message")}</label>
      <textarea id={`meeting-${group.group_id}`} value={text} maxLength={8000} rows={3}
        onChange={(event) => setText(event.target.value)} placeholder={t("society.meeting.message")}
        className="w-full resize-none rounded-lg border border-border bg-card p-3 text-sm text-foreground" />
      <div className="flex justify-end gap-2">
        {query.data?.running && <button type="button" disabled={pending} onClick={() => void send(true)}
          className="rounded-md border border-border px-3 py-2 text-sm text-foreground">{t("society.meeting.stop")}</button>}
        <button type="submit" disabled={pending || query.isLoading || query.isError || query.data?.running || tooMany || !text.trim()}
          className="rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground disabled:opacity-50">{t("society.meeting.send")}</button>
      </div>
    </form>
  </section>;
}
