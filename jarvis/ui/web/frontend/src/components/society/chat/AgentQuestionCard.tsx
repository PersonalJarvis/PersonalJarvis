import { useEffect, useState } from "react";
import { answerAgentQuestion } from "@/lib/agentChatApi";
import type { NoticeItem } from "@/components/agentchat/reduce";
import { useT } from "@/i18n";

interface Option {
  label: string;
  description: string;
}

export function AgentQuestionCard({ item, sessionId }: { item: NoticeItem; sessionId: string }) {
  const t = useT();
  const data = item.data;
  const questionId = String(data.question_id ?? "");
  const options = (Array.isArray(data.options) ? data.options : []) as Option[];
  const recommended = Number(data.recommended_index ?? 0);
  const deadline = Number(data.deadline_ms ?? 0);
  const [selected, setSelected] = useState<number | null>(recommended);
  const [custom, setCustom] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    if (item.resolved) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [item.resolved]);

  const remaining = Math.max(0, Math.ceil((deadline - now) / 1000));
  const clock = `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, "0")}`;
  const submit = async () => {
    if (selected === null && !custom.trim()) return;
    setBusy(true);
    setError("");
    try {
      await answerAgentQuestion(sessionId, questionId, selected === null
        ? { custom_text: custom.trim() }
        : { selected_index: selected });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("society.chat.question_failed"));
    } finally {
      setBusy(false);
    }
  };

  return <section className="w-full max-w-xl self-start rounded-2xl border border-primary/40 bg-card p-4 text-sm text-foreground shadow-sm" data-testid="agent-question-card">
    <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
      <p className="font-semibold">{t("society.chat.question_title").replace("{0}", item.agentName)}</p>
      {!item.resolved ? <span className="rounded-full bg-secondary px-2 py-0.5 text-xs tabular-nums text-muted-foreground">
        {t("society.chat.question_default_in").replace("{0}", clock)}
      </span> : null}
    </div>
    <p className="mb-3 leading-relaxed">{String(data.question ?? item.text)}</p>
    {item.resolved ? <p className="rounded-lg bg-secondary px-3 py-2 text-xs text-muted-foreground">
      {t(item.resolved === "timeout" ? "society.chat.question_auto_chosen" : "society.chat.question_answered")}: <span className="font-medium text-foreground">{String(data.answer ?? "")}</span>
    </p> : <>
      <fieldset className="space-y-2" disabled={busy}>
        <legend className="sr-only">{t("society.chat.question_choices")}</legend>
        {options.map((option, index) => <label key={`${questionId}-${index}`} className="flex cursor-pointer gap-3 rounded-xl border border-border p-3 hover:bg-secondary/60 has-[:checked]:border-primary has-[:checked]:bg-primary/5">
          <input type="radio" name={`question-${questionId}`} checked={selected === index} onChange={() => { setSelected(index); setCustom(""); }} className="mt-1 accent-[var(--primary)]" />
          <span className="min-w-0 flex-1">
            <span className="flex flex-wrap items-center gap-2 font-medium">
              {option.label}
              {index === recommended ? <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[11px] text-primary">{t("society.chat.question_recommended")}</span> : null}
            </span>
            <span className="mt-0.5 block text-xs leading-relaxed text-muted-foreground">{option.description}</span>
          </span>
        </label>)}
      </fieldset>
      <p className="mt-2 text-xs text-muted-foreground">{t("society.chat.question_why").replace("{0}", String(data.recommendation_reason ?? ""))}</p>
      <label className="mt-3 block text-xs font-medium" htmlFor={`question-custom-${questionId}`}>{t("society.chat.question_custom")}</label>
      <textarea id={`question-custom-${questionId}`} value={custom} maxLength={2000} rows={2}
        onChange={(event) => { setCustom(event.target.value); if (event.target.value) setSelected(null); }}
        placeholder={t("society.chat.question_custom_hint")}
        className="mt-1 w-full resize-y rounded-lg border border-input bg-background px-3 py-2 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring" />
      <div className="mt-3 flex items-center justify-between gap-3">
        <span className="text-xs text-muted-foreground">{t("society.chat.question_default_note")}</span>
        <button type="button" onClick={() => void submit()} disabled={busy || (selected === null && !custom.trim())}
          className="rounded-lg bg-primary px-4 py-2 text-xs font-semibold text-primary-foreground hover:bg-primary/90 disabled:opacity-50">
          {busy ? t("society.chat.question_submitting") : t("society.chat.question_submit")}
        </button>
      </div>
      {error ? <p role="alert" className="mt-2 text-xs text-destructive">{error}</p> : null}
    </>}
  </section>;
}
