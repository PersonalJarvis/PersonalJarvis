import { useCallback, useEffect, useState } from "react";

import { robustCopy } from "@/lib/clipboard";

/**
 * The complete outcome vocabulary of one dictation, mirroring
 * `jarvis.dictation.outcomes.DICTATION_OUTCOMES`.
 *
 * This array is the TypeScript half of a cross-layer parity test — the Python
 * tuple and this list must stay set-equal, and every entry must have a
 * `dictation.outcome.{name}` key in every locale. Never render a raw outcome
 * string in the UI; always translate through that key.
 */
export const DICTATION_OUTCOMES = [
  "inserted",
  // A user-recorded paste shortcut went out to the focused window and nothing
  // came back — Jarvis does not paste, it ASKS the app in front to paste, and
  // an app that does not bind that combination simply ignores it. There is
  // nothing to read back, so this deliberately does not claim an insertion.
  // (No quoted words in this block: the parity test scans it with a regex.)
  "paste_sent",
  "clipboard_only",
  "unavailable",
  "chat",
  // Some of the words arrived and some are gone for good: one or more segments
  // failed every retry, so what was delivered is a fragment. Reported instead
  // of a plain success because a session that lost half a minute of speech and
  // still called itself inserted is the bug this value exists to end. The audio
  // is kept, so the row can be run again from the history.
  // (No quoted words in this block: the parity test scans it with a regex.)
  "partial",
  "empty",
  "cancelled",
  "failed",
] as const;

export type DictationOutcome = (typeof DICTATION_OUTCOMES)[number];

/**
 * Why a transcription failed, mirroring
 * `jarvis.speech.stt_failure.STT_FAILURE_REASONS`.
 *
 * The backend stores a reason CODE rather than the provider's own error text,
 * because that text was a Python exception class plus a vendor URL plus a link
 * to an HTTP specification — rendered verbatim under the user's own words, and
 * untranslatable by any locale. Same rule as the outcomes above: never render a
 * raw reason, always translate through `dictation.failure.{name}`.
 *
 * The TypeScript half of a cross-layer parity test; the Python tuple and this
 * list must stay set-equal.
 */
export const STT_FAILURE_REASONS = [
  "rate_limited",
  "no_credit",
  "bad_key",
  "unavailable",
  "rejected",
  "engine_busy",
  "recording_interrupted",
  "no_stt",
  "unknown",
] as const;

export type SttFailureReason = (typeof STT_FAILURE_REASONS)[number];

/**
 * How the wording pass ended, mirroring
 * `jarvis.dictation.polish.POLISH_STATUSES`.
 *
 * Every value except `applied` and `translated` means the RAW transcript was
 * delivered — the pass can only ever be a no-op, never a loss — so none of
 * these is an error the user has to act on, and they are worded that way.
 *
 * One caveat the wording carries: when translation is switched on, a no-op is
 * VISIBLE. The words arrive in the language they were spoken in rather than the
 * chosen one, which is why a fallback status is worth reading on a translated
 * dictation even though it is invisible on a polished one.
 *
 * Same parity contract as the two vocabularies above: the Python tuple and this
 * list must stay set-equal, and every entry needs a
 * `dictation.polish_status.{name}` key in every locale.
 */
export const POLISH_STATUSES = [
  "applied",
  // The dictation was delivered in the configured target language rather than
  // the one it was spoken in. The only status besides applied where the text
  // differs from what was recognized — and the only one where that difference
  // is visible at a glance.
  "translated",
  "unchanged",
  "off",
  "unavailable",
  // Speech recognition runs on this machine, so the wording pass refused to be
  // the step that ships the words off it. Not an error and not a missing key:
  // the raw transcript was delivered, and a cloud family picked deliberately in
  // the dropdown still overrides this.
  // (No quoted words in this block: the parity test scans it with a regex.)
  "local_only",
  "skipped_short",
  "skipped_long",
  "timeout",
  "provider_error",
  "rejected_drift",
] as const;

export type PolishStatus = (typeof POLISH_STATUSES)[number];

/**
 * Why the deterministic filler cleanup did NOT run, mirroring the `reason`
 * vocabulary of `jarvis.dictation.cleanup.CleanupResult` (empty when it did).
 *
 * This one earns its place in the UI: `no_rules` means the cleanup has no
 * filler list for the language that was recognized, which is the case for most
 * of the ~100 languages dictation accepts. Until it was rendered, those users
 * saw the filler-removal switch sitting ON while it had never once run.
 */
export const DICTATION_CLEANUP_REASONS = [
  "disabled",
  "no_rules",
  "ceiling",
  "empty",
  "error",
] as const;

export type DictationCleanupReason = (typeof DICTATION_CLEANUP_REASONS)[number];

const KNOWN_POLISH_STATUSES: ReadonlySet<string> = new Set(POLISH_STATUSES);
const KNOWN_CLEANUP_REASONS: ReadonlySet<string> = new Set(
  DICTATION_CLEANUP_REASONS,
);

/**
 * Translates a polish status through its i18n key, falling back to the raw
 * value for anything this bundle does not know — a newer backend must never
 * crash or blank an older frontend. Takes `t` rather than calling the hook, so
 * it stays usable from any component.
 */
export function polishStatusLabel(
  t: (key: string) => string,
  status: string,
): string {
  return KNOWN_POLISH_STATUSES.has(status)
    ? t(`dictation.polish_status.${status}`)
    : status;
}

/** The cleanup-reason twin of `polishStatusLabel`, same fallback rule. */
export function cleanupReasonLabel(
  t: (key: string) => string,
  reason: string,
): string {
  return KNOWN_CLEANUP_REASONS.has(reason)
    ? t(`dictation.cleanup_reason.${reason}`)
    : reason;
}

/** Languages dictation can be pinned to; `auto` lets the provider decide. */
export type DictationLanguage = "auto" | "de" | "en" | "es";

/**
 * Live state of dictation mode from GET /api/dictation/status.
 *
 * `insertion` is the honest part: on Wayland, on a headless host, or in front
 * of an elevated window the transcript cannot be pasted into another app, and
 * the UI says so up front instead of letting the user discover it by dictating
 * into nothing.
 */
export interface DictationStatus {
  available: boolean;
  active: boolean;
  reason: string;
  hotkey: string;
  /** Hands-free (press once to start, again to stop) combo, "" when unbound. */
  hotkey_toggle?: string;
  mode: string;
  target: string;
  insertion: {
    can_insert: boolean;
    reason: string;
    detail: string;
  };
}

/** One recorded dictation — raw transcript alongside what was inserted. */
export interface DictationEntry {
  id: string;
  created_at: string;
  raw_text: string;
  text: string;
  language: string;
  duration_s: number;
  /**
   * One of DICTATION_OUTCOMES. Typed as a union *or* string on purpose: a
   * newer backend must never crash an older bundle, so an unknown value falls
   * through to a neutral badge instead of a type error.
   */
  outcome: DictationOutcome | string;
  method: string;
  removed_words: number;
  cleanup_reason: string;
  word_count: number;
  /** Soft-deleted: hidden from the default list, still restorable. */
  discarded: boolean;
  /** Audio was kept for this entry, so Restore can transcribe it again. */
  audio_available: boolean;
  error: string | null;
  /**
   * One of POLISH_STATUSES — how the optional wording pass ended for this
   * dictation. Optional because a row written before the pass existed carries
   * no polish fields at all, and an older backend serves none.
   */
  polish_status?: string | null;
  /** The model family that answered, "" when none did. */
  polish_provider?: string | null;
  /** What the pass cost in wall-clock time, for the honest "is this slowing me
   *  down" question. 0 when it never ran. */
  polish_latency_ms?: number | null;
  /** STT providers that produced text, in first-use order (fallback is visible). */
  stt_providers?: string[];
  /** Requested/effective STT model ids used by successful calls. */
  stt_models?: string[];
  /** Provider-reported language tags across the final windows. */
  detected_languages?: string[];
  /** Aggregate wall-clock time spent in STT for this dictation. */
  stt_latency_ms?: number;
  /** Logical preview/final STT calls; provider-internal shape retries are opaque. */
  stt_calls?: number;
  /** Stable failure reason codes observed during this dictation. */
  stt_errors?: string[];
  /** Machine-readable quality-path decisions; never transcript content. */
  stt_audit?: string[];
  /** Stable PCM rate delivered to STT after capture-side resampling. */
  audio_sample_rate_hz?: number;
  /** Whole-recording normalized RMS (0..1). */
  audio_rms?: number;
  /** Share of PCM16 samples at or immediately below full scale. */
  audio_clipping_ratio?: number;
  /** Capture discontinuities inferred from timestamps / queue overflow. */
  audio_dropouts?: number;
  /** Approximate missing audio across timestamp-detected discontinuities. */
  audio_dropout_ms?: number;
}

/**
 * Aggregate dictation numbers from GET /api/dictation/stats.
 *
 * `source` decides the panel's honesty: `"lifetime"` means the never-pruned
 * sidecar answered and the totals really are all-time; `"window"` means they
 * were derived from the rolling history window, and the UI must say so rather
 * than calling a 30-day slice "all time".
 */
export interface DictationStats {
  source: "lifetime" | "window";
  window: { days: number; max_entries: number };
  totals: { dictations: number; words: number; seconds: number; wpm: number };
  today: { dictations: number; words: number };
  streak: { current_days: number; longest_days: number };
  by_day: { date: string; dictations: number; words: number; seconds: number }[];
}

export interface DictationSettings {
  mode: string;
  target: string;
  insert_method: string;
  paste_chord: string;
  paste_delay_ms: number;
  paste_delay_after_ms: number;
  restore_clipboard: boolean;
  remove_fillers: boolean;
  filler_max_removed_fraction: number;
  max_seconds: number;
  partial_interval_s: number;
  segment_seconds: number;
  /** Re-read the complete recording after release; live segments stay preview-only. */
  final_quality_pass: boolean;
  /** Length of one final quality window in seconds. */
  final_window_seconds: number;
  /** Overlap between adjacent final windows in seconds. */
  final_overlap_seconds: number;
  /** Auto-detect within the recording instead of sending a hard language lock. */
  code_switching: boolean;
  history_enabled: boolean;
  history_max_entries: number;
  history_retention_days: number;
  language: DictationLanguage | string;
  keep_failed_audio: boolean;
  audio_retention_days: number;
  audio_max_files: number;
  // The wording pass. `polish` is the master switch; everything below it only
  // matters while it is on. The numeric knobs are here because they are part of
  // the same settings block the backend serves and saves — the UI exposes only
  // the switch and the provider, and touching the rest is a config-file or CLI
  // affair (`jarvis api dictation put-settings`).
  polish: boolean;
  /** "auto" = whichever family the user holds a key for, primary first. */
  polish_provider: string;
  /** "" = the family's own default model. */
  polish_model: string;
  polish_timeout_ms: number;
  polish_max_input_chars: number;
  polish_min_words: number;
  polish_max_output_tokens: number;
  polish_temperature: number;
  polish_drift_max_shrink: number;
  polish_drift_max_growth: number;
  /** neutral | messaging | email — register only, never a licence to change
   *  meaning. */
  polish_style: string;
  /**
   * Also sharpen the WORD CHOICE, not just the writing: a vague placeholder
   * becomes the specific word that was meant, padding collapses into the plain
   * verb. Simple and exact, never ornate.
   *
   * Ships OFF, and unlike `polish_style` this is not a matter of taste — it
   * relaxes the guard that rejects an answer in which an uncommon word
   * vanished, because replacing uncommon words is exactly what it licenses.
   * Applies to a translated dictation too, through the same prompt clause.
   */
  polish_precision: boolean;
  /**
   * Also tidy up the transcripts of ordinary conversations, not just dictation.
   *
   * Never delays a reply: the assistant answers the raw words and the tidied
   * version arrives afterwards, for the transcript view and the session record.
   * What was actually said is always kept alongside. Needs `polish`.
   */
  polish_conversation: boolean;
  /**
   * Deliver every dictation in `translate_target`, whatever language was
   * spoken. Ships OFF: it changes which words come out, not just how they are
   * written, so it is never acquired by an install that did not ask for it.
   */
  translate: boolean;
  /** The language dictations come out in while `translate` is on. No "auto". */
  translate_target: string;
  translate_drift_max_shrink: number;
  translate_drift_max_growth: number;
}

export interface DictationChoices {
  mode: string[];
  target: string[];
  insert_method: string[];
  paste_chord: string[];
  language: string[];
  /**
   * The polish families the backend actually knows, `"auto"` first. Served
   * rather than mirrored on purpose: a hand-kept copy of the family list here
   * is the AP-4 drift trap, and the cost of getting it wrong is a dropdown
   * offering a provider the backend rejects. Optional so an older backend that
   * serves no polish block still parses.
   */
  polish_provider?: string[];
  polish_style?: string[];
  /**
   * Languages a dictation can be delivered in. The recognition list without
   * `auto` — there is nothing to detect on the output side. Optional so an
   * older backend that serves no translate block still parses.
   */
  translate_target?: string[];
}

/**
 * The keys that also accept a RECORDED value, described by the backend rather
 * than mirrored here.
 *
 * The token vocabulary travels over the wire on purpose: a hand-kept copy in
 * the frontend is the AP-4 drift trap, and the cost of getting it wrong is a
 * recorder that happily captures a key the actuator cannot send, which then
 * fails silently at paste time. Everything is optional — an older backend
 * serves no `custom` block at all, and the recorder then simply lets the
 * backend be the judge on save.
 */
export interface DictationCustomField {
  allowed?: boolean;
  /** How tokens are joined ("+"). */
  separator?: string;
  modifiers?: string[];
  keys?: string[];
  /** English sentence describing what the custom value can and cannot do. */
  detail?: string;
}

export interface DictationCustom {
  paste_chord?: DictationCustomField;
}

/** Result of POST /api/dictation/history/{id}/restore. */
export interface DictationRestoreResult {
  ok: boolean;
  entry: DictationEntry;
  retranscribed: boolean;
  detail: string | null;
}

async function unwrap<T>(res: Response): Promise<T> {
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return body as T;
}

/** Result of POST /api/dictation/polish/test — one fixed sample, dry-run. */
export interface DictationPolishTest {
  /** One of POLISH_STATUSES. */
  status: PolishStatus | string;
  /** The family that answered, "" when none did. */
  provider: string;
  model: string;
  latency_ms: number;
  /** Machine cause when a guard or the transport refused, "" when clean. */
  reason: string;
  sample_in: string;
  sample_out: string;
}

/**
 * Runs the fixed sample through the user's live polish configuration.
 *
 * The only way to SEE this feature: when it works it is invisible by design,
 * and when it fails it silently delivers the raw text. Non-destructive — it
 * writes no setting and touches no history — so the UI can offer it as a plain
 * "Test" button next to the switch.
 */
export async function testDictationPolish(): Promise<DictationPolishTest> {
  return unwrap<DictationPolishTest>(
    await fetch("/api/dictation/polish/test", { method: "POST" }),
  );
}

/**
 * Loads dictation status, settings, history and stats, and exposes start/stop,
 * a partial settings save, and the four per-entry actions (copy, discard,
 * restore, hard delete). Mirrors useDictionary's fetch/error/loading shape.
 */
export function useDictation() {
  const [status, setStatus] = useState<DictationStatus | null>(null);
  const [settings, setSettings] = useState<DictationSettings | null>(null);
  const [choices, setChoices] = useState<DictationChoices | null>(null);
  const [custom, setCustom] = useState<DictationCustom | null>(null);
  const [entries, setEntries] = useState<DictationEntry[]>([]);
  const [stats, setStats] = useState<DictationStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetchStatus = useCallback(async () => {
    try {
      const data = await unwrap<DictationStatus>(
        await fetch("/api/dictation/status"),
      );
      setStatus(data);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  const refetchHistory = useCallback(async () => {
    try {
      // Discarded entries stay in the list: they are the ones the Restore
      // button exists for, and filtering them out would make Restore
      // unreachable from the UI that owns it.
      const data = await unwrap<{ entries: DictationEntry[] }>(
        await fetch("/api/dictation/history?limit=50&include_discarded=true"),
      );
      setEntries(data.entries ?? []);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  const refetchStats = useCallback(async () => {
    // Stats are an informational strip. A backend that cannot answer must not
    // blank the whole view with a red error line — the strip just stays away.
    try {
      const data = await unwrap<DictationStats>(
        await fetch("/api/dictation/stats"),
      );
      setStats(data);
    } catch {
      setStats(null);
    }
  }, []);

  const refetch = useCallback(async () => {
    setError(null);
    try {
      const data = await unwrap<{
        settings: DictationSettings;
        choices: DictationChoices;
        custom?: DictationCustom;
      }>(await fetch("/api/dictation/settings"));
      setSettings(data.settings);
      setChoices(data.choices);
      setCustom(data.custom ?? null);
      await Promise.all([refetchStatus(), refetchHistory(), refetchStats()]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [refetchStatus, refetchHistory, refetchStats]);

  useEffect(() => {
    void refetch();
  }, [refetch]);

  const start = useCallback(
    async (target: "auto" | "insert" | "chat" = "auto") => {
      await unwrap(
        await fetch("/api/dictation/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ target }),
        }),
      );
      await refetchStatus();
    },
    [refetchStatus],
  );

  const stop = useCallback(async () => {
    await unwrap(await fetch("/api/dictation/stop", { method: "POST" }));
    await refetchStatus();
    await refetchHistory();
    await refetchStats();
  }, [refetchStatus, refetchHistory, refetchStats]);

  const saveSettings = useCallback(
    async (patch: Partial<DictationSettings>) => {
      const data = await unwrap<{ settings: DictationSettings }>(
        await fetch("/api/dictation/settings", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ...patch, persist: true }),
        }),
      );
      setSettings(data.settings);
      await refetchStatus();
    },
    [refetchStatus],
  );

  /**
   * Soft delete. The entry stays in the list wearing a "Discarded" badge —
   * filtering it out here would strand the Restore button that is the whole
   * point of a recoverable delete.
   */
  const discardEntry = useCallback(async (id: string) => {
    const data = await unwrap<{ entry: DictationEntry }>(
      await fetch(`/api/dictation/history/${encodeURIComponent(id)}/discard`, {
        method: "POST",
      }),
    );
    setEntries((prev) =>
      prev.map((e) =>
        e.id === id ? (data.entry ?? { ...e, discarded: true }) : e,
      ),
    );
  }, []);

  /** Un-discards, and re-transcribes from the kept audio when there is text to win back. */
  const restoreEntry = useCallback(async (id: string) => {
    const data = await unwrap<DictationRestoreResult>(
      await fetch(`/api/dictation/history/${encodeURIComponent(id)}/restore`, {
        method: "POST",
      }),
    );
    setEntries((prev) =>
      prev.map((e) =>
        e.id === id ? (data.entry ?? { ...e, discarded: false }) : e,
      ),
    );
    return data;
  }, []);

  /** Hard delete — gone from disk, audio sidecar included. */
  const deleteEntry = useCallback(async (id: string) => {
    await unwrap(
      await fetch(`/api/dictation/history/${encodeURIComponent(id)}`, {
        method: "DELETE",
      }),
    );
    setEntries((prev) => prev.filter((e) => e.id !== id));
  }, []);

  const clearHistory = useCallback(async () => {
    await unwrap(await fetch("/api/dictation/history", { method: "DELETE" }));
    setEntries([]);
    await refetchStats();
  }, [refetchStats]);

  /**
   * Copies one entry's delivered text (falling back to the raw transcript).
   * Returns false when every clipboard path failed, so the caller can say so
   * instead of claiming a copy that never happened.
   */
  const copyEntry = useCallback(
    async (id: string) => {
      const entry = entries.find((e) => e.id === id);
      if (!entry) return false;
      return robustCopy(entry.text || entry.raw_text);
    },
    [entries],
  );

  return {
    status,
    settings,
    choices,
    custom,
    entries,
    stats,
    loading,
    error,
    start,
    stop,
    saveSettings,
    copyEntry,
    discardEntry,
    restoreEntry,
    deleteEntry,
    clearHistory,
    refetch,
    refetchStatus,
    refetchHistory,
    refetchStats,
  };
}
