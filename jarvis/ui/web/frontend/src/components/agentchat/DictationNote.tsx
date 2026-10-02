import { X } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

import { InlinePermissionNote } from "@/components/permissions/InlinePermissionNote";
import { ShortcutsTip } from "@/components/permissions/ShortcutsTip";
import { useInlinePermission } from "@/hooks/useInlinePermission";
import { useT } from "@/i18n";
import { usePermissionsStore } from "@/store/permissions";

/** The `PERMISSION_FEATURES` token a dictation press is attributed to. */
export const DICTATION_FEATURE = "dictation";

/** How long a plain refusal sentence stays; a permission explanation stays until it is resolved. */
export const DICTATION_NOTE_HOLD_MS = 12_000;
/** "Microphone allowed - press again" waits for the press, so it outlasts the card's 6 s. */
export const DICTATION_ALLOWED_HOLD_MS = 15_000;

/** The refusal tokens that have their own sentence; every other token reads "did not start". */
const REFUSAL_KEYS = new Set([
  "microphone_unavailable",
  "no_stt",
  "already_running",
  "handover_failed",
  "voice_session_active",
  "pipeline_not_running",
]);

/** i18n key of the sentence for a `DictationRefused` token (or a normalised error type). */
export function dictationRefusalKey(reason: string): string {
  return `permissions.inline.dictation.refused.${REFUSAL_KEYS.has(reason) ? reason : "generic"}`;
}

/** The popover is `w-72` (288 px) and never wider than 80 % of the window. */
const POPOVER_WIDTH_PX = 288;
/** Keep this much of the window edge free when deciding which side to open towards. */
const EDGE_PX = 8;

const POPOVER =
  "rounded-lg border border-border bg-popover p-3 text-popover-foreground shadow-float";

/**
 * What the dictation button says when a press did nothing, floating just above
 * it (the same popover position the composer's other menus use).
 *
 * The composer set `dictating` when the person pressed the mic; a refusal
 * (`DictationRefused`, or `ErrorOccurred` of layer `ui.web.dictation`) already
 * reset it in the WS hook and left a note in the permission store. Only the
 * window that was dictating ever has such a note, so a dictation started from
 * another window or a key never makes this popover appear.
 *
 * - A microphone episode (macOS asking, or the person has to act): the shared
 *   inline note (full sentence, the one click to System Settings). The floating
 *   card stays quiet while this is mounted.
 * - After a grant: "Microphone allowed - press again". Nothing is started
 *   retroactively; the press that raised the macOS dialog is over.
 * - Any other refusal: one plain sentence for a few seconds.
 * - Otherwise, once, the shortcuts tip (see `lib/shortcutsTip.ts`).
 */
export function DictationNote({
  originId,
  tip,
  onTipDone,
}: {
  /**
   * This button's id. The refusal state is one store field and a chat grid mounts
   * several composers: only the button that was pressed last shows it.
   */
  originId?: string;
  tip: boolean;
  onTipDone: () => void;
}): ReactNode {
  const t = useT();
  const storedNote = usePermissionsStore((state) => state.dictationNote);
  const origin = usePermissionsStore((state) => state.dictationOrigin);
  const note = originId === undefined || origin === null || origin === originId ? storedNote : null;
  const clearNote = usePermissionsStore((state) => state.clearDictationNote);
  const { episode, resolved, dismiss } = useInlinePermission(DICTATION_FEATURE, false);

  const [expired, setExpired] = useState(false);
  useEffect(() => {
    setExpired(false);
    if (!note) return undefined;
    const timer = window.setTimeout(() => setExpired(true), DICTATION_NOTE_HOLD_MS);
    return () => window.clearTimeout(timer);
  }, [note]);

  const grantedAfter = note !== null && resolved?.granted === true && resolved.ts >= note.ts;
  const permissionNote = note !== null && (episode !== null || grantedAfter);

  const close = () => {
    dismiss();
    clearNote();
  };

  let body: ReactNode = null;
  if (permissionNote) {
    body = (
      <InlinePermissionNote
        feature={DICTATION_FEATURE}
        allowedKey="permissions.inline.dictation.allowed"
        holdMs={DICTATION_ALLOWED_HOLD_MS}
        showAllowed={grantedAfter}
        onDismiss={close}
        className={POPOVER}
        testId="dictation-permission-note"
      />
    );
  } else if (note !== null && !expired) {
    body = (
      <div
        role="status"
        aria-live="polite"
        data-testid="dictation-refused-note"
        data-reason={note.reason}
        className={`${POPOVER} flex items-start gap-2 text-meta`}
      >
        <p className="min-w-0 flex-1 break-words text-foreground">{t(dictationRefusalKey(note.reason))}</p>
        <button
          type="button"
          onClick={clearNote}
          aria-label={t("permissions.inline.dismiss")}
          title={t("permissions.inline.dismiss")}
          className="-mr-1 -mt-1 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <X aria-hidden className="h-3.5 w-3.5" />
        </button>
      </div>
    );
  } else if (tip) {
    body = <ShortcutsTip onDone={onTipDone} className={POPOVER} />;
  }

  // The mic sits at the left of one composer and at the right of the others, so
  // the popover opens towards the side that has room (a left-anchored one ran
  // out of the window beside a right-hand button).
  const box = useRef<HTMLDivElement>(null);
  const [alignRight, setAlignRight] = useState(false);
  const shown = body !== null;
  useLayoutEffect(() => {
    const anchor = box.current?.parentElement;
    if (!shown || !anchor) return undefined;
    const place = () => {
      const rect = anchor.getBoundingClientRect();
      const width = Math.min(POPOVER_WIDTH_PX, window.innerWidth * 0.8);
      const overflowsRight = rect.left + width > window.innerWidth - EDGE_PX;
      const fitsLeft = rect.right - width >= EDGE_PX;
      setAlignRight(overflowsRight && fitsLeft);
    };
    place();
    window.addEventListener("resize", place);
    return () => window.removeEventListener("resize", place);
  }, [shown]);

  if (body === null) return null;
  return (
    <div
      ref={box}
      data-align={alignRight ? "right" : "left"}
      className={`absolute bottom-full z-20 mb-2 w-72 max-w-[80vw] ${alignRight ? "right-0" : "left-0"}`}
    >
      {body}
    </div>
  );
}
