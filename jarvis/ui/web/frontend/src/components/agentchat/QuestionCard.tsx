import { memo, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { Check, CornerDownLeft, MessageCircleQuestion, PenLine, X } from "lucide-react";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";
import type { QuestionAnswerInput } from "@/lib/agentChatApi";
import { useAgentChat } from "./AgentChatStoreContext";
import type { QuestionAnswerState, QuestionState } from "./reduce";

/**
 * An agent's question card (jarvis/agent_chat/questions.py).
 *
 * A short series the person answers one at a time ("question 1 of 3"). Each
 * question lists its prepared answers as lettered rows, the agent's
 * recommendation first. The close button leaves the open questions to the
 * agent, and a countdown shows when that happens on its own — doing nothing
 * is an answer too, so a workflow never waits forever.
 */

const LETTERS = "ABCD";

function useRemaining(expiresMs: number | null, live: boolean): number | null {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!live || expiresMs === null) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [expiresMs, live]);
  return expiresMs === null ? null : Math.max(0, expiresMs - now);
}

function clock(ms: number): string {
  const seconds = Math.ceil(ms / 1000);
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function useTitle(question: QuestionState): string {
  const t = useT();
  const name = question.asker || t("question_card.fallback_agent");
  return question.questions.length > 1
    ? t("question_card.title_many").replace("{agent}", name).replace("{count}", String(question.questions.length))
    : t("question_card.title_one").replace("{agent}", name);
}

export const QuestionCard = memo(function QuestionCard({ question }: { question: QuestionState }) {
  if (question.closed) return <AnsweredCard question={question} />;
  return <OpenCard question={question} />;
});

function OpenCard({ question }: { question: QuestionState }) {
  const t = useT();
  const title = useTitle(question);
  const answerQuestion = useAgentChat((s) => s.answerQuestion);
  const skipQuestion = useAgentChat((s) => s.skipQuestion);
  const total = question.questions.length;
  const current = Math.max(0, question.answers.findIndex((a) => a === null));
  const item = question.questions[current];
  const remaining = useRemaining(question.expiresMs, true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [typing, setTyping] = useState(false);
  const [text, setText] = useState("");
  const submitting = useRef(false);

  // A new step starts clean: the server confirmed the previous answer.
  useEffect(() => {
    setBusy(false);
    setTyping(false);
    setText("");
    submitting.current = false;
  }, [current]);

  const run = async (action: () => Promise<void>) => {
    if (submitting.current) return;
    submitting.current = true;
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      submitting.current = false;
      setBusy(false);
    }
  };
  const answer = (input: QuestionAnswerInput) => run(() => answerQuestion(question.questionId, current, input));
  const skip = () => run(() => skipQuestion(question.questionId));

  const submitText = (event: FormEvent) => {
    event.preventDefault();
    const typed = text.trim();
    if (typed) void answer({ text: typed });
  };

  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (typing || busy || event.ctrlKey || event.metaKey || event.altKey) return;
    const index = LETTERS.indexOf(event.key.toUpperCase());
    if (event.key.length === 1 && index >= 0 && index < item.options.length) {
      event.preventDefault();
      void answer({ optionIndex: index });
    }
  };

  return (
    <section
      role="group"
      aria-label={title}
      tabIndex={-1}
      onKeyDown={onKeyDown}
      data-testid="question-card"
      data-state="open"
      className="my-2 w-full max-w-2xl rounded-2xl border border-border bg-card p-5 text-foreground shadow-sm focus:outline-none"
    >
      <header className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <h3 className="text-lg font-semibold leading-7 [overflow-wrap:anywhere]">{title}</h3>
          <p className="mt-1 text-base leading-6 text-muted-foreground [overflow-wrap:anywhere]">{item.question}</p>
        </div>
        {total > 1 ? (
          <span className="mt-1.5 shrink-0 text-xs tabular-nums text-muted-foreground" data-testid="question-step">
            {t("question_card.step").replace("{current}", String(current + 1)).replace("{total}", String(total))}
          </span>
        ) : null}
        <button
          type="button"
          onClick={() => void skip()}
          disabled={busy}
          aria-label={t("question_card.close")}
          title={t("question_card.close")}
          className="-mr-1 -mt-0.5 shrink-0 rounded-md p-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
        >
          <X aria-hidden className="h-5 w-5" />
        </button>
      </header>

      {total > 1 ? (
        <div className="mt-3 flex gap-1" aria-hidden>
          {question.questions.map((_, i) => (
            <span key={i} className={cn("h-1 flex-1 rounded-full", i <= current ? "bg-primary" : "bg-muted")} />
          ))}
        </div>
      ) : null}

      <div className="mt-4 overflow-hidden rounded-xl border border-border">
        {item.options.map((option, index) => (
          <button
            key={option.label}
            type="button"
            disabled={busy}
            onClick={() => void answer({ optionIndex: index })}
            data-recommended={index === 0 ? "true" : "false"}
            className="flex w-full items-center gap-4 border-b border-border px-4 py-3 text-left !text-base transition-colors hover:bg-secondary focus-visible:bg-secondary focus-visible:outline-none disabled:opacity-60"
          >
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-md bg-muted !text-sm font-medium text-muted-foreground">
              {LETTERS[index]}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block [overflow-wrap:anywhere]">{option.label}</span>
              {option.description ? (
                <span className="mt-0.5 block !text-xs leading-5 text-muted-foreground [overflow-wrap:anywhere]">
                  {option.description}
                </span>
              ) : null}
            </span>
            {index === 0 ? (
              <span className="shrink-0 rounded-full border border-primary/40 px-2 py-0.5 !text-[11px] font-medium text-primary">
                {t("question_card.recommended")}
              </span>
            ) : null}
          </button>
        ))}
        {typing ? (
          <form onSubmit={submitText} className="flex items-center gap-4 px-4 py-2.5">
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-md bg-muted text-muted-foreground">
              <PenLine aria-hidden className="h-4 w-4" />
            </span>
            <input
              autoFocus
              value={text}
              onChange={(event) => setText(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Escape") setTyping(false);
              }}
              maxLength={2000}
              placeholder={t("question_card.own_placeholder")}
              aria-label={t("question_card.own_answer")}
              className="min-w-0 flex-1 bg-transparent text-base text-foreground placeholder:text-muted-foreground focus-visible:outline-none"
            />
            <button
              type="submit"
              disabled={busy || !text.trim()}
              aria-label={t("question_card.send")}
              title={t("question_card.send")}
              className="shrink-0 rounded-md p-1.5 text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40"
            >
              <CornerDownLeft aria-hidden className="h-4 w-4" />
            </button>
          </form>
        ) : (
          <button
            type="button"
            disabled={busy}
            onClick={() => setTyping(true)}
            className="flex w-full items-center gap-4 px-4 py-3 text-left !text-base text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:bg-secondary focus-visible:outline-none disabled:opacity-60"
          >
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-md bg-muted">
              <PenLine aria-hidden className="h-4 w-4" />
            </span>
            <span>{t("question_card.own_answer")}</span>
          </button>
        )}
      </div>

      <footer className="mt-3 space-y-1 text-xs leading-5 text-muted-foreground">
        {item.recommendationReason ? (
          <p className="[overflow-wrap:anywhere]">
            {t("question_card.why").replace("{reason}", item.recommendationReason)}
          </p>
        ) : null}
        {remaining !== null ? (
          <p aria-live="off" data-testid="question-countdown">
            {remaining > 0
              ? t("question_card.countdown").replace("{time}", clock(remaining))
              : t("question_card.choosing")}
          </p>
        ) : null}
        {error ? (
          <p role="alert" className="text-destructive">
            {error}
          </p>
        ) : null}
      </footer>
    </section>
  );
}

function AnsweredCard({ question }: { question: QuestionState }) {
  const title = useTitle(question);
  return (
    <div
      data-testid="question-card"
      data-state={question.answers.map((a) => a?.source ?? "closed").join(" ")}
      className="my-1 w-full max-w-2xl rounded-xl border border-border px-4 py-3 text-sm text-muted-foreground"
    >
      <p className="flex items-center gap-2 text-xs">
        <MessageCircleQuestion aria-hidden className="h-3.5 w-3.5 shrink-0" />
        <span>{title}</span>
      </p>
      <ul className="mt-2 space-y-2">
        {question.questions.map((item, i) => (
          <li key={i} className="[overflow-wrap:anywhere]">
            <p>{item.question}</p>
            <AnswerLine answer={question.answers[i]} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function AnswerLine({ answer }: { answer: QuestionAnswerState | null }) {
  const t = useT();
  if (!answer || answer.source === "cancelled" || answer.source === "closed") {
    return <p className="mt-0.5 text-xs">{t("question_card.unanswered")}</p>;
  }
  const note =
    answer.source === "timeout"
      ? t("question_card.auto_picked")
      : answer.source === "skipped"
        ? t("question_card.left_to_agent")
        : answer.optionIndex === null
          ? t("question_card.own_words")
          : answer.optionIndex === 0
            ? t("question_card.recommended")
            : "";
  return (
    <p className="mt-0.5 flex flex-wrap items-center gap-1.5">
      <Check aria-hidden className="h-3.5 w-3.5 text-primary" />
      <span className="font-medium text-foreground">{answer.text}</span>
      {note ? <span className="text-xs">· {note}</span> : null}
    </p>
  );
}
