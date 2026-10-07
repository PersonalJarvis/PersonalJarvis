import { useMemo, useRef, useState } from "react";
import { ArrowRight, AudioLines, RefreshCw, Search, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useSessions } from "@/hooks/useSessions";
import { fill, useT, useUiLanguage } from "@/i18n";
import { useEventStore } from "@/store/events";
import type { SessionListItem } from "@/components/sessions/types";
import { TranscriptReader, TranscriptState } from "@/components/transcription/TranscriptReader";
import { elapsedTime } from "@/components/transcription/transcript";
import "@/components/transcription/transcription.css";

/** A conversation index opens into a full reading page at every window size. */
export function SessionsView() {
  const t = useT();
  const locale = useUiLanguage();
  const sessions = useSessions();
  const [selected, setSelected] = useState<SessionListItem | null>(null);
  const [search, setSearch] = useState("");
  const lastOpened = useRef<HTMLButtonElement | null>(null);
  const setSection = useEventStore((state) => state.setActiveSection);
  const groups = useMemo(() => {
    const byDay = new Map<string, SessionListItem[]>();
    for (const session of [...(sessions.data ?? [])].sort((a, b) => b.started_ms - a.started_ms)) {
      if (search.trim() && !session.preview.toLocaleLowerCase(locale).includes(search.trim().toLocaleLowerCase(locale))) continue;
      const day = new Date(session.started_ms).toLocaleDateString(locale, { weekday: "long", month: "long", day: "numeric", year: "numeric" });
      const list = byDay.get(day) ?? [];
      list.push(session);
      byDay.set(day, list);
    }
    return [...byDay];
  }, [sessions.data, search, locale]);
  const disabledRecorder = sessions.error instanceof Error && /HTTP 503/.test(sessions.error.message);

  function back() {
    setSelected(null);
    requestAnimationFrame(() => lastOpened.current?.focus());
  }

  return (
    <section className="transcription-workspace" aria-label={t("sessions_view.title")}>
      <div className="transcription-library" hidden={selected !== null}>
        <div className="transcription-library-inner">
          <header className="transcription-masthead">
            <p className="transcription-eyebrow"><AudioLines aria-hidden />{t("transcription.archive")}</p>
            <h1>{t("sessions_view.title")}</h1>
            <p className="transcription-intro">{t("transcription.intro")}</p>
          </header>
          <div className="transcription-library-tools">
            <div className="transcription-search">
              <Search aria-hidden />
              <input aria-label={t("transcription.search_sessions")} placeholder={t("transcription.search_sessions")} value={search} onChange={(event) => setSearch(event.target.value)} type="search" />
              {search && <button type="button" aria-label={t("transcription.clear_search")} onClick={() => setSearch("")}><X aria-hidden /></button>}
            </div>
            <Button variant="ghost" disabled={sessions.isFetching} onClick={() => void sessions.refetch()} aria-label={t("transcription.refresh")}><RefreshCw aria-hidden className={sessions.isFetching ? "motion-safe:animate-spin" : undefined} /><span>{t("transcription.refresh")}</span></Button>
          </div>
          {sessions.error && <TranscriptState kind="error" title={t(disabledRecorder ? "transcription.recorder_off" : "transcription.list_error")} body={t(disabledRecorder ? "transcription.recorder_help" : "transcription.retry_help")} retry={() => void sessions.refetch()} />}
          {sessions.isLoading ? <TranscriptState kind="loading" title={t("transcription.loading_library")} /> : groups.length > 0 ? (
            <>
              <p className="transcription-library-count" role="status">{fill(t(search.trim() ? "transcription.results" : "transcription.recent_count"), { count: groups.reduce((count, [, list]) => count + list.length, 0) })}</p>
              {groups.map(([day, list]) => (
                <section className="transcription-day" key={day} aria-label={day}>
                  <h2>{day}</h2>
                  <ul>{list.map((session) => (
                    <li key={session.id}>
                      <button className="transcription-session" type="button" onClick={(event) => { lastOpened.current = event.currentTarget; setSelected(session); }}>
                        <time className="transcription-session-time" dateTime={new Date(session.started_ms).toISOString()}>{new Date(session.started_ms).toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" })}</time>
                        <span className="transcription-session-main">
                          <span className="transcription-session-title">{session.preview || t("transcription.untitled")}</span>
                          <span className="transcription-session-meta">
                            {session.ended_ms === null ? <span className="transcription-live">{t("transcription.in_progress")}</span> : session.duration_s !== null ? <span>{elapsedTime(session.duration_s * 1000)}</span> : null}
                            <span>{fill(t("transcription.exchanges"), { count: session.turn_count })}</span>
                            {session.language && <span>{session.language.toLocaleUpperCase(locale)}</span>}
                          </span>
                        </span>
                        <ArrowRight aria-hidden className="transcription-session-arrow" />
                      </button>
                    </li>
                  ))}</ul>
                </section>
              ))}
            </>
          ) : !sessions.error && (
            <TranscriptState title={t(search.trim() ? "transcription.no_matches" : "transcription.empty_title")} body={t(search.trim() ? "transcription.search_help" : "transcription.empty_body")}>
              {search.trim() ? <Button variant="outline" onClick={() => setSearch("")}>{t("transcription.clear_search")}</Button> : <Button variant="outline" onClick={() => setSection("chats")}>{t("transcription.open_chat")}<ArrowRight aria-hidden /></Button>}
            </TranscriptState>
          )}
        </div>
      </div>
      {selected && <TranscriptReader key={selected.id} session={selected} onBack={back} />}
    </section>
  );
}
