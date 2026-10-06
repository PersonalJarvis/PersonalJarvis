import { useLayoutEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Send, Square } from "lucide-react";
import { ComposerChipField, type ComposerChipFieldHandle } from "@/components/agentchat/ComposerChipField";
import { DictationButton } from "@/components/agentchat/DictationButton";
import { useComposerDictation } from "@/components/agentchat/useComposerDictation";
import { ScrollToEndButton } from "@/components/ui/scroll-to-end-button";
import { useStickToBottom } from "@/hooks/useStickToBottom";
import { useT } from "@/i18n";
import { societyDisplayName } from "@/lib/societyDisplayName";
import {
  sendSocietyMeeting,
  stopSocietyMeeting,
  useSocietyMeeting,
  type SocietyChatGroup,
  type SocietyMeeting,
} from "@/lib/societyChatGroups";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";
import type { SocietyAgent } from "../data";
import { AgentSwatch } from "../AgentSwatch";
import { CHAT_MEASURE, Prose, UserBubble } from "./AgentChatPanel";

/** The server's room cap (jarvis/society/rooms.py MAX_MEMBERS), pinned by a parity test. */
export const MEETING_MAX_MEMBERS = 6;

/**
 * The group's shared meeting, drawn with the same pieces as an agent's own
 * chat: the person's bubble, each member's reply as prose under its face, and
 * the one composer. A message gives every member one turn, in order; reading
 * the transcript never starts one.
 */
export function MeetingChat({ group, roster }: { group: SocietyChatGroup; roster: SocietyAgent[] }) {
  const t = useT();
  const client = useQueryClient();
  const assistantName = useEventStore((s) => s.assistantName);
  const query = useSocietyMeeting(group.group_id);
  const meeting = query.data;
  const [value, setValue] = useState("");
  const [pending, setPending] = useState(false);
  const [problem, setProblem] = useState("");
  const fieldRef = useRef<ComposerChipFieldHandle>(null);
  const { rootRef, contentRef, atEnd, jumpToEnd, follow } = useStickToBottom();
  useLayoutEffect(follow, [follow, meeting?.messages.length, meeting?.running]);

  const agentOf = (id: string) => roster.find((agent) => agent.agentId === id);
  const nameOf = (id: string) => {
    const agent = agentOf(id);
    return agent ? societyDisplayName(agent, assistantName) : id;
  };
  const members = group.members.map(agentOf).filter((agent): agent is SocietyAgent => Boolean(agent));
  const tooMany = group.members.length > MEETING_MAX_MEMBERS;
  const running = Boolean(meeting?.running);
  const canSend = !pending && !running && !tooMany && !query.isLoading && !query.isError;

  const settle = (next: SocietyMeeting) => client.setQueryData(["society", "meeting", group.group_id], next);
  const submit = async () => {
    const draft = fieldRef.current?.getDraft().text ?? value;
    const text = draft.trim();
    if (!text || !canSend) return;
    setPending(true);
    setProblem("");
    try {
      settle(await sendSocietyMeeting(group.group_id, text));
      // Only clear what was sent; words typed meanwhile stay.
      if ((fieldRef.current?.getDraft().text ?? value) === draft) {
        setValue("");
        fieldRef.current?.clear();
      }
    } catch (err) {
      setProblem(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(false);
    }
  };
  const stop = async () => {
    setPending(true);
    setProblem("");
    try {
      settle(await stopSocietyMeeting(group.group_id));
    } catch (err) {
      setProblem(err instanceof Error ? err.message : String(err));
    } finally {
      setPending(false);
    }
  };
  const dictation = useComposerDictation((next) => {
    const text = typeof next === "function" ? next(value) : next;
    setValue(text);
    fieldRef.current?.setText(text);
  }, () => void submit());

  const messages = meeting?.messages ?? [];
  const failed = meeting?.room?.state === "failed";
  const alert = problem || (query.error ? String(query.error) : "")
    || (tooMany ? t("society.meeting.limit") : "") || (failed ? t("society.meeting.failed") : "");

  return <section className="flex min-h-0 min-w-0 flex-1 flex-col bg-background" data-testid="society-meeting">
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div ref={rootRef} className="chat-scroller min-h-0 flex-1 overflow-y-auto px-4 py-5 sm:px-6"
        role="log" aria-label={t("society.meeting.title")}>
        <div ref={contentRef} className={cn(CHAT_MEASURE, "flex flex-col gap-3")}>
          {messages.length === 0 && !query.isLoading ? (
            <div className="flex flex-col items-center gap-3 py-12 text-center">
              <div className="flex -space-x-2">
                {members.map((agent) => <AgentSwatch key={agent.agentId} agent={agent} size={40}
                  className="rounded-full ring-2 ring-background" />)}
              </div>
              <p className="text-sm font-medium text-foreground">{group.name}</p>
              <p className="max-w-[44ch] text-xs text-muted-foreground">{t("society.meeting.description")}</p>
              <p className="max-w-[44ch] text-xs text-muted-foreground">{t("society.meeting.empty")}</p>
            </div>
          ) : null}
          {messages.map((message) => message.speaker === "user" ? (
            <UserBubble key={message.id}
              item={{ type: "user", id: message.id, text: message.text, attachments: [], tsMs: 0 }} />
          ) : (
            <article key={message.id} className="flex min-w-0 flex-col gap-1.5 self-start">
              <header className="flex items-center gap-2">
                {agentOf(message.speaker) ? <AgentSwatch agent={agentOf(message.speaker)!} size={22} /> : null}
                <span className="text-xs font-medium text-foreground">{nameOf(message.speaker)}</span>
              </header>
              <Prose text={message.text} />
            </article>
          ))}
          {running ? (
            <p role="status" className="self-start py-1 text-xs text-muted-foreground">
              {t("society.meeting.answering").replace("{0}", nameOf(meeting?.room?.next_speaker ?? ""))}
            </p>
          ) : null}
        </div>
      </div>
      {!atEnd && <ScrollToEndButton onClick={jumpToEnd} testId="meeting-scroll-end" className="top-auto bottom-3" />}
    </div>
    <div className="shrink-0 px-4 pb-4 pt-2 sm:px-6">
      {alert ? <p role="alert" className={cn(CHAT_MEASURE, "mb-1 px-1 text-xs text-destructive")}>{alert}</p> : null}
      <div className={cn(
        CHAT_MEASURE,
        "relative flex items-end gap-1 rounded-[24px] border border-border bg-secondary px-2 py-1.5 focus-within:border-border-strong",
        dictation.dictating && "border-success/35 focus-within:border-success/50",
      )}>
        <ComposerChipField
          ref={fieldRef}
          placeholder={dictation.dictating ? t("chats_view.dictation_listening") : t("society.meeting.message")}
          onSubmit={() => (dictation.dictating ? dictation.stopAndSend() : void submit())}
          onDraftChange={(draft) => setValue(draft.text)}
          className="max-h-[180px] pl-2"
        />
        <DictationButton
          dictating={dictation.dictating}
          onToggle={dictation.toggle}
          startLabel={t("society.chat.record")}
          stopLabel={t("society.chat.stop_recording")}
          shape="round"
        />
        {running ? (
          <button type="button" onClick={() => void stop()} disabled={pending}
            aria-label={t("society.meeting.stop")} title={t("society.meeting.stop")}
            className="flex h-8 w-8 items-center justify-center rounded-full bg-foreground text-background transition-colors hover:bg-foreground/90 disabled:opacity-40">
            <Square className="h-3.5 w-3.5" aria-hidden />
          </button>
        ) : (
          <button type="button" onClick={() => (dictation.dictating ? dictation.stopAndSend() : void submit())}
            disabled={!canSend || (!value.trim() && !dictation.dictating)}
            aria-label={t("society.meeting.send")} title={t("society.meeting.send")}
            className="flex h-8 w-8 items-center justify-center rounded-full bg-primary text-primary-foreground disabled:opacity-40">
            <Send className="h-3.5 w-3.5" aria-hidden />
          </button>
        )}
      </div>
    </div>
  </section>;
}
