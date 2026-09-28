import { memo, useEffect, useRef, useState, type FormEvent } from "react";
import { Check, MessageCircleQuestion, Send, Sparkles } from "lucide-react";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import type { QuestionAnswerInput } from "@/lib/agentChatApi";
import { useAgentChat } from "./AgentChatStoreContext";
import type { QuestionState } from "./reduce";

/**
 * An agent's question with prepared answers (jarvis/agent_chat/questions.py).
 *
 * The first option is the agent's recommendation. The card counts down to
 * the moment the backend picks that recommendation on its own, so the person
 * always sees that doing nothing is also an answer.
 */

function useRemaining(expiresMs: number | null, live: boolean): number | null {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!live || expiresMs === null) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [expiresMs, live]);
  return expiresMs === null ? null : Math.max(0, expiresMs - now);
}

function clock(ms: number): string {
  const seconds = Math.ceil(ms / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

export const QuestionCard = memo(function QuestionCard({ question }: { question: QuestionState }) {
  const t = useT();
  const answerQuestion = useAgentChat((s) => s.answerQuestion);
  const open = question.answer === null;
  const remaining = useRemaining(question.expiresMs, open);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [typing, setTyping] = useState(false);
  const [text, setText] = useState("");
  const submitting = useRef(false);

  const send = async (answer: QuestionAnswerInput) => {
    if (submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setError(null);
    try {
      await answerQuestion(question.questionId, answer);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      submitting.current = false;
      setBusy(false);
    }
  };

  const submitText = (event: FormEvent) => {
    event.preventDefault();
    const typed = text.trim();
    if (typed) void send({ text: typed });
  };

  if (!open) return <AnsweredQuestion question={question} />;

  return (
    <div
      role="group"
      aria-label={t("question_card.aria")}
      data-testid="question-card"
      data-state="open"
      className="my-2 w-full max-w-xl space-y-3 rounded-xl border border-border bg-card p-4 text-sm text-foreground shadow-sm"
    >
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <MessageCircleQuestion aria-hidden className="h-4 w-4 text-primary" />
        <span className="font-medium uppercase tracking-wide">{question.header || t("question_card.title")}</span>
      </div>
      <p className="font-medium leading-6 [overflow-wrap:anywhere]">{question.question}</p>
      <div className="space-y-2">
        {question.options.map((option, index) => {
          const recommended = index === 0;
          return (
            <button
              key={option.label}
              type="button"
              disabled={busy}
              onClick={() => void send({ optionIndex: index })}
              data-recommended={recommended ? "true" : "false"}
              className={cn(
                "flex w-full flex-col items-start gap-1 rounded-lg border px-3 py-2.5 text-left transition-colors",
                "hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50",
                "!text-sm",
                recommended ? "border-primary/60 bg-primary/5" : "border-border",
              )}
            >
              <span className="flex w-full flex-wrap items-center gap-2">
                <span className="text-xs tabular-nums text-muted-foreground">{index + 1}.</span>
                <span className="font-medium [overflow-wrap:anywhere]">{option.label}</span>
                {recommended ? (
                  <span className="inline-flex items-center gap-1 rounded-full bg-primary/15 px-2 py-0.5 text-[11px] font-medium text-primary">
                    <Sparkles aria-hidden className="h-3 w-3" />
                    {t("question_card.recommended")}
                  </span>
                ) : null}
              </span>
              {option.description ? (
                <span className="text-xs leading-5 text-muted-foreground [overflow-wrap:anywhere]">{option.description}</span>
              ) : null}
              {recommended && question.recommendationReason ? (
                <span className="text-xs leading-5 text-foreground/80 [overflow-wrap:anywhere]">
                  {t("question_card.why").replace("{reason}", question.recommendationReason)}
                </span>
              ) : null}
            </button>
          );
        })}
      </div>
      {typing ? (
        <form onSubmit={submitText} className="flex items-center gap-2">
          <input
            autoFocus
            value={text}
            onChange={(event) => setText(event.target.value)}
            maxLength={2000}
            placeholder={t("question_card.own_placeholder")}
            aria-label={t("question_card.own_answer")}
            className="min-w-0 flex-1 rounded-md border border-border bg-background px-3 py-1.5 text-sm text-foreground placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
          <button
            type="submit"
            disabled={busy || !text.trim()}
            aria-label={t("question_card.send")}
            className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-xs hover:bg-secondary focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
          >
            <Send aria-hidden className="h-3.5 w-3.5" />
            {t("question_card.send")}
          </button>
        </form>
      ) : (
        <button
          type="button"
          disabled={busy}
          onClick={() => setTyping(true)}
          className="rounded-md px-1 text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline focus-visible:ring-2 focus-visible:ring-ring"
        >
          {t("question_card.own_answer")}
        </button>
      )}
      {remaining !== null ? (
        <p className="text-xs text-muted-foreground" aria-live="off" data-testid="question-countdown">
          {remaining > 0
            ? t("question_card.countdown").replace("{time}", clock(remaining))
            : t("question_card.choosing")}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
});

function AnsweredQuestion({ question }: { question: QuestionState }) {
  const t = useT();
  const answer = question.answer!;
  const stopped = answer.source === "cancelled" || answer.source === "closed";
  const note = answer.source === "timeout"
    ? t("question_card.auto_picked")
    : answer.optionIndex === null && !stopped
      ? t("question_card.own_words")
      : answer.optionIndex === 0
        ? t("question_card.recommended")
        : "";
  return (
    <div
      data-testid="question-card"
      data-state={answer.source}
      className="my-1 w-full max-w-xl rounded-lg border border-border px-3 py-2 text-xs text-muted-foreground"
    >
      <p className="flex items-center gap-1.5 [overflow-wrap:anywhere]">
        <MessageCircleQuestion aria-hidden className="h-3.5 w-3.5 shrink-0" />
        <span>{question.question}</span>
      </p>
      <p className="mt-1 flex flex-wrap items-center gap-1.5 pl-5">
        {stopped ? (
          <span>{t("question_card.unanswered")}</span>
        ) : (
          <>
            <Check aria-hidden className="h-3.5 w-3.5 text-primary" />
            <span className="font-medium text-foreground [overflow-wrap:anywhere]">{answer.text}</span>
            {note ? <span>· {note}</span> : null}
          </>
        )}
      </p>
    </div>
  );
}
