import { useEffect, useId, useRef, useState } from "react";
import { AlertCircle, Check, ChevronDown, Copy, Download, ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { OpenWithDialog } from "@/components/OpenWithDialog";
import { fetchSessionExport, openSessionWith, sessionExportUrl } from "@/components/sessions/api";
import type { SessionDetail } from "@/components/sessions/types";
import { useCapabilities } from "@/hooks/useCapabilities";
import { useOpeners, usePreferredOpener, useSetPreferredOpener } from "@/hooks/useOutputs";
import { buildSessionFilename, mimeFor, robustCopy, saveOrDownload } from "@/lib/clipboard";
import { useT } from "@/i18n";

type Format = "plain" | "markdown" | "json";

export function TranscriptActions({ detail }: { detail: SessionDetail }) {
  const t = useT();
  const panelId = useId();
  const [expanded, setExpanded] = useState(false);
  const [format, setFormat] = useState<Format>("plain");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  const [chooseOpener, setChooseOpener] = useState(false);
  const native = useCapabilities().data?.native_file_actions ?? false;

  async function perform(action: "copy" | "download", selectedFormat: Format) {
    setBusy(true);
    setMessage("");
    setFailed(false);
    try {
      const text = await fetchSessionExport(detail.session.id, selectedFormat);
      if (action === "copy") {
        if (!await robustCopy(text)) throw new Error("Clipboard unavailable");
      } else {
        await saveOrDownload({
          filename: buildSessionFilename(detail.session, detail.turns.find((turn) => turn.user_text)?.user_text ?? "", selectedFormat),
          text, mime: mimeFor(selectedFormat), native,
        });
      }
      setMessage(t(action === "copy" ? "transcription.copied" : "transcription.saved"));
    } catch {
      // Surface a useful recovery message without leaking a provider response.
      setFailed(true);
      setMessage(t(action === "copy" ? "session_detail.copy_failed_clipboard" : "session_detail.download_failed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="transcript-actions">
      <div className="transcript-action-buttons">
        <Button variant="ghost" disabled={busy} onClick={() => void perform("copy", "plain")}><Copy aria-hidden />{t("transcription.copy")}</Button>
        <Button variant="outline" aria-expanded={expanded} aria-controls={panelId} onClick={() => setExpanded(!expanded)}><Download aria-hidden />{t("session_detail.export_label")}<ChevronDown aria-hidden /></Button>
      </div>
      {expanded && (
        <div className="transcript-export" id={panelId}>
          <label htmlFor={`${panelId}-format`}>{t("transcription.format")}</label>
          <select id={`${panelId}-format`} value={format} onChange={(event) => setFormat(event.target.value as Format)}>
            <option value="plain">{t("transcription.plain_text")}</option><option value="markdown">Markdown</option><option value="json">JSON</option>
          </select>
          <Button variant="secondary" disabled={busy} onClick={() => void perform("download", format)}><Download aria-hidden />{t("transcription.download")}</Button>
          <Button variant="ghost" disabled={busy} onClick={() => void perform("copy", format)}><Copy aria-hidden />{t("turn_card.copy")}</Button>
          {native ? (
            <Button variant="ghost" disabled={busy} onClick={() => setChooseOpener(true)}><ExternalLink aria-hidden />{t("transcription.open_with")}</Button>
          ) : (
            <Button asChild variant="ghost"><a href={sessionExportUrl(detail.session.id, format)} target="_blank" rel="noopener noreferrer"><ExternalLink aria-hidden />{t("transcription.open_file")}</a></Button>
          )}
        </div>
      )}
      <p className={failed ? "transcript-feedback is-error" : "transcript-feedback"} role={failed ? "alert" : "status"}>{busy ? t("transcription.preparing") : message && <>{failed ? <AlertCircle aria-hidden /> : <Check aria-hidden />}{message}</>}</p>
      {chooseOpener && <TranscriptOpener sessionId={detail.session.id} format={format} onClose={() => setChooseOpener(false)} onResult={(ok) => { setFailed(!ok); setMessage(t(ok ? "session_detail.opened_in_editor" : "session_detail.open_failed")); }} />}
    </div>
  );
}

function TranscriptOpener({ sessionId, format, onClose, onResult }: {
  sessionId: string; format: Format; onClose: () => void; onResult: (ok: boolean) => void;
}) {
  const openers = useOpeners();
  const preferred = usePreferredOpener();
  const setPreferred = useSetPreferredOpener();
  const autoOpened = useRef(false);

  useEffect(() => {
    if (!preferred.data || autoOpened.current) return;
    autoOpened.current = true;
    const opener = preferred.data;
    onClose();
    void openSessionWith(sessionId, format, opener).then(onResult).catch(() => onResult(false));
  }, [preferred.data, sessionId, format, onClose, onResult]);

  async function pick(opener: string, remember: boolean) {
    onClose();
    try {
      const opened = await openSessionWith(sessionId, format, opener);
      if (opened && remember && preferred.data !== opener) setPreferred.mutate(opener);
      onResult(opened);
    } catch {
      onResult(false);
    }
  }
  return <OpenWithDialog openers={preferred.isLoading ? [] : openers.data ?? []} loading={openers.isLoading || preferred.isLoading} onPick={(opener, remember) => void pick(opener, remember)} onClose={onClose} />;
}
