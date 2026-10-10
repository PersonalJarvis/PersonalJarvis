import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { AlertCircle, ArrowLeft, AudioLines, Copy, Download, Loader2, RefreshCw, Search, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { SessionListItem } from "@/components/sessions/types";
import { useSessionDetail } from "@/hooks/useSessions";
import { useCapabilities } from "@/hooks/useCapabilities";
import { fill, useT } from "@/i18n";
import { useRunLocale } from "@/components/runs/format";
import { useEventStore } from "@/store/events";
import { robustCopy, saveOrDownload } from "@/lib/clipboard";
import { TranscriptActions } from "./TranscriptActions";
import { elapsedTime, exchangeText, passageText, transcriptExchanges, type TranscriptExchange } from "./transcript";

export function TranscriptState({ kind = "empty", title, body, retry, children }: {
  kind?: "empty" | "loading" | "error"; title: string; body?: string; retry?: () => void; children?: ReactNode;
}) {
  const t = useT();
  const Icon = kind === "error" ? AlertCircle : kind === "loading" ? Loader2 : AudioLines;
  return <div className={`transcription-state is-${kind}`} role={kind === "error" ? "alert" : "status"}>
    <Icon aria-hidden className={kind === "loading" ? "motion-safe:animate-spin" : undefined} />
    <h2>{title}</h2>{body && <p>{body}</p>}
    {retry && <Button variant="outline" onClick={retry}><RefreshCw aria-hidden />{t("transcription.retry")}</Button>}{children}
  </div>;
}

export function TranscriptReader({ session, onBack }: { session: SessionListItem; onBack: () => void }) {
  const t = useT();
  const locale = useRunLocale();
  const detail = useSessionDetail(session.id);
  const [search, setSearch] = useState("");
  const [original, setOriginal] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const exchanges = useMemo(() => detail.data ? transcriptExchanges(detail.data) : [], [detail.data]);
  const term = search.trim().toLocaleLowerCase(locale);
  const visible = useMemo(() => term ? exchanges.filter((exchange) => exchange.passages.some((p) => passageText(p, original).toLocaleLowerCase(locale).includes(term))) : exchanges, [exchanges, term, original, locale]);
  const hasPolished = exchanges.some((exchange) => exchange.passages.some((p) => p.original));
  const current = detail.data?.session ?? session;
  const finished = current.ended_ms !== null;

  useEffect(() => { heading.current?.focus(); }, []);

  return <div className="transcription-reader">
    <div className="transcription-reader-bar">
      <Button variant="ghost" onClick={onBack}><ArrowLeft aria-hidden />{t("transcription.all_conversations")}</Button>
      <span>{t("sessions_view.title")}</span>
    </div>
    <div className="transcription-page">
      <header className="transcription-document-header">
        <p className="transcription-eyebrow">{new Date(current.started_ms).toLocaleDateString(locale, { day: "numeric", month: "long", year: "numeric" })}<span aria-hidden> / </span>{new Date(current.started_ms).toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" })}</p>
        <h1 ref={heading} tabIndex={-1}>{session.preview || t("transcription.untitled")}</h1>
        <div className="transcription-document-meta">
          <span className={!finished ? "transcription-live" : undefined}>{t(finished ? "transcription.saved_conversation" : "transcription.in_progress")}</span>
          {finished && <span>{elapsedTime(current.ended_ms! - current.started_ms)}</span>}
          <span>{fill(t("transcription.exchanges"), { count: current.turn_count })}</span>
        </div>
        {detail.data && <TranscriptActions detail={detail.data} />}
      </header>
      {detail.isLoading ? <TranscriptState kind="loading" title={t("session_detail.loading")} /> : detail.error ? (
        <TranscriptState kind="error" title={t("session_detail.load_error")} body={t("transcription.retry_help")} retry={() => void detail.refetch()} />
      ) : <>
        {!finished && <p className="transcription-recording-note" role="status">{t("transcription.live_help")}<Button variant="ghost" disabled={detail.isFetching} onClick={() => void detail.refetch()}><RefreshCw aria-hidden />{t("transcription.refresh")}</Button></p>}
        {exchanges.length > 0 && <div className="transcription-reading-tools">
          <div className="transcription-search">
            <Search aria-hidden /><input type="search" aria-label={t("transcription.find")} placeholder={t("transcription.find")} value={search} onChange={(event) => setSearch(event.target.value)} />
            {search && <button type="button" aria-label={t("transcription.clear_search")} onClick={() => setSearch("")}><X aria-hidden /></button>}
          </div>
          {hasPolished && <label className="transcription-original"><input type="checkbox" checked={original} onChange={(event) => setOriginal(event.target.checked)} />{t("transcription.original_wording")}</label>}
        </div>}
        {term && <p className="transcription-find-count" role="status">{fill(t("transcription.results"), { count: visible.length })}</p>}
        {visible.length ? <div className="transcription-passages" aria-label={t("transcription.transcript")}>
          {visible.map((exchange) => <Exchange key={exchange.id} exchange={exchange} original={original} search={search.trim()} started={current.started_ms} />)}
          <p className="transcription-end"><span />{t(finished ? "transcription.end" : "transcription.latest")}<span /></p>
        </div> : <TranscriptState title={t(term ? "transcription.no_matches" : "transcription.no_speech")} body={t(term ? "transcription.find_help" : "transcription.no_speech_help")}>
          {term && <Button variant="outline" onClick={() => setSearch("")}>{t("transcription.clear_search")}</Button>}
        </TranscriptState>}
      </>}
    </div>
  </div>;
}

function Exchange({ exchange, original, search, started }: { exchange: TranscriptExchange; original: boolean; search: string; started: number }) {
  const t = useT();
  const assistant = useEventStore((state) => state.assistantName);
  const native = useCapabilities().data?.native_file_actions ?? false;
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  async function exportExchange(download: boolean) {
    setBusy(true);
    setFeedback("");
    setFailed(false);
    try {
      const text = exchangeText(exchange, original, t("session_detail.you"), assistant);
      if (download) await saveOrDownload({ filename: `voice-excerpt-${Math.floor(started)}-${exchange.number ?? "spoken"}.txt`, text, mime: "text/plain;charset=utf-8", native });
      else if (!await robustCopy(text)) throw new Error("Clipboard unavailable");
      setFeedback(t(download ? "transcription.saved" : "transcription.copied"));
    } catch {
      setFailed(true);
      setFeedback(t(download ? "session_detail.download_failed" : "session_detail.copy_failed_clipboard"));
    } finally { setBusy(false); }
  }
  return <article className="transcription-exchange">
    <div className="transcription-exchange-rule">
      <span>{exchange.number === null ? t("transcription.spoken_output") : fill(t("transcription.exchange"), { number: String(exchange.number).padStart(2, "0") })}</span>
      <div>
        <Button variant="ghost" size="icon" disabled={busy} aria-label={t("turn_card.copy_turn")} title={t("turn_card.copy_turn")} onClick={() => void exportExchange(false)}><Copy aria-hidden /></Button>
        <Button variant="ghost" size="icon" disabled={busy} aria-label={t("turn_card.download_turn")} title={t("turn_card.download_turn")} onClick={() => void exportExchange(true)}><Download aria-hidden /></Button>
      </div>
    </div>
    {exchange.passages.map((passage) => <div className="transcription-passage" key={passage.id}>
      <div className="transcription-speaker" data-speaker={passage.speaker}>
        <span>{passage.speaker === "you" ? t("session_detail.you") : assistant}</span>
        <time>{elapsedTime(passage.at - started)}</time>
      </div>
      <div className="transcription-words">
        {passage.original && !original && <span className="transcription-wording-note">{t("session_turn.polished")}</span>}
        {passage.pending && <span className="transcription-pending">{t("transcription.pending")}</span>}
        {passage.kind && passage.kind !== "reply" && <span className="transcription-wording-note">{t(`transcription.spoken_${passage.kind}`) === `transcription.spoken_${passage.kind}` ? t("transcription.spoken_output") : t(`transcription.spoken_${passage.kind}`)}</span>}
        <p><Highlight text={passageText(passage, original)} term={search} /></p>
      </div>
    </div>)}
    <p className={failed ? "transcript-feedback is-error" : "transcript-feedback"} role={failed ? "alert" : "status"}>{feedback}</p>
  </article>;
}

function Highlight({ text, term }: { text: string; term: string }) {
  if (!term) return <>{text}</>;
  const escaped = term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return <>{text.split(new RegExp(`(${escaped})`, "gi")).map((part, index) => index % 2 ? <mark key={index}>{part}</mark> : part)}</>;
}
