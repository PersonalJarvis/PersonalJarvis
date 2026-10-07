import { useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { ArrowRight, Loader2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import type { DictionaryEntry, DictionaryEntryPayload } from "@/hooks/useDictionary";
import { useT } from "@/i18n";
import { VoiceNote } from "@/views/voice/voiceUi";

/**
 * Add or edit one dictionary entry. The "Fix a misrecognition" switch decides
 * the shape: off, a single word the recognizer should know and spell exactly
 * so; on, a "heard as → write it as" pair whose left side may list several
 * variants separated by commas.
 *
 * A form, so Enter in any field saves; Escape and the scrim cancel; closing
 * hands focus back to whatever opened the dialog. Validation speaks only after
 * the first save attempt — an empty form is not an error yet.
 *
 * Portalled to the document. The voice hub is an overflow-constrained flex
 * pane; a fixed, backdrop-filtered modal inside that stacking context made
 * Chromium/WebView spend seconds repainting the whole pane, so the scrim is a
 * plain tint with no blur.
 */
export function DictionaryEntryDialog({
  initial,
  onClose,
  onSave,
}: {
  /** The entry being edited, or null to add a new one. */
  initial: DictionaryEntry | null;
  onClose: () => void;
  onSave: (payload: DictionaryEntryPayload) => Promise<void>;
}) {
  const t = useT();
  const switchId = useId();
  const firstFieldRef = useRef<HTMLInputElement | null>(null);
  const wordRef = useRef<HTMLInputElement | null>(null);
  const misheardRef = useRef<HTMLInputElement | null>(null);

  const [word, setWord] = useState(initial?.word ?? "");
  const [misheardText, setMisheardText] = useState(initial?.misheard.join(", ") ?? "");
  const [isCorrection, setIsCorrection] = useState((initial?.misheard.length ?? 0) > 0);
  const [attempted, setAttempted] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const misheard = isCorrection
    ? misheardText
        .split(",")
        .map((m) => m.trim())
        .filter(Boolean)
    : [];
  const wordMissing = word.trim().length === 0;
  const misheardMissing = isCorrection && misheard.length === 0;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (saving) return;
    setAttempted(true);
    if (misheardMissing) {
      misheardRef.current?.focus();
      return;
    }
    if (wordMissing) {
      wordRef.current?.focus();
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await onSave({ word: word.trim(), misheard });
    } catch (e) {
      setError((e as Error).message);
      setSaving(false);
    }
  }

  const wordField = (
    <Field
      label={isCorrection ? t("dictionary.word_correct_label") : t("dictionary.word_label")}
      error={attempted && wordMissing ? t("dictionary.word_required") : null}
    >
      {(ids) => (
        <Input
          ref={(el) => {
            wordRef.current = el;
            if (!isCorrection) firstFieldRef.current = el;
          }}
          id={ids.input}
          value={word}
          onChange={(e) => setWord(e.target.value)}
          placeholder={
            isCorrection
              ? t("dictionary.word_correct_placeholder")
              : t("dictionary.word_placeholder")
          }
          aria-invalid={attempted && wordMissing ? true : undefined}
          aria-describedby={ids.describedBy}
          autoComplete="off"
          spellCheck={false}
          data-testid="dictionary-word-input"
        />
      )}
    </Field>
  );

  return (
    <Dialog.Root
      open
      onOpenChange={(open) => {
        if (!open && !saving) onClose();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-scrim/50" />
        <Dialog.Content
          data-testid="dictionary-dialog"
          onOpenAutoFocus={(event) => {
            // The first field, not the close button that leads the DOM.
            event.preventDefault();
            firstFieldRef.current?.focus();
          }}
          className="fixed left-1/2 top-1/2 z-50 max-h-[calc(100vh-2rem)] w-[min(30rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-xl border border-border bg-popover p-6 text-popover-foreground shadow-float focus:outline-none"
        >
          <form onSubmit={(e) => void submit(e)} noValidate>
            <div className="flex items-start gap-3">
              <div className="min-w-0 flex-1">
                <Dialog.Title className="text-base font-semibold text-foreground-strong">
                  {initial ? t("dictionary.dialog_edit_title") : t("dictionary.dialog_add_title")}
                </Dialog.Title>
                <Dialog.Description className="mt-1 text-sm text-muted-foreground">
                  {isCorrection
                    ? t("dictionary.correction_hint_on")
                    : t("dictionary.correction_hint_off")}
                </Dialog.Description>
              </div>
              <Dialog.Close asChild>
                <button
                  type="button"
                  aria-label={t("dictionary.cancel")}
                  title={t("dictionary.cancel")}
                  disabled={saving}
                  className="-mr-2 -mt-1 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
                >
                  <X aria-hidden="true" className="h-4 w-4" />
                </button>
              </Dialog.Close>
            </div>

            <div className="mt-5 flex flex-col gap-4">
              <div className="flex items-center justify-between gap-3 rounded-lg bg-secondary px-3 py-2.5">
                <label htmlFor={switchId} className="text-sm font-medium text-foreground">
                  {t("dictionary.correction_toggle")}
                </label>
                <Switch
                  id={switchId}
                  checked={isCorrection}
                  onCheckedChange={setIsCorrection}
                  data-testid="dictionary-correction-toggle"
                />
              </div>

              {isCorrection ? (
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)]">
                  <Field
                    label={t("dictionary.misheard_label")}
                    hint={t("dictionary.misheard_comma_hint")}
                    error={attempted && misheardMissing ? t("dictionary.misheard_required") : null}
                  >
                    {(ids) => (
                      <Input
                        ref={(el) => {
                          misheardRef.current = el;
                          firstFieldRef.current = el;
                        }}
                        id={ids.input}
                        value={misheardText}
                        onChange={(e) => setMisheardText(e.target.value)}
                        placeholder={t("dictionary.misheard_placeholder")}
                        aria-invalid={attempted && misheardMissing ? true : undefined}
                        aria-describedby={ids.describedBy}
                        autoComplete="off"
                        spellCheck={false}
                        data-testid="dictionary-misheard-input"
                      />
                    )}
                  </Field>
                  <ArrowRight
                    aria-hidden="true"
                    className="hidden h-4 w-4 text-muted-foreground sm:mt-9 sm:block"
                  />
                  {wordField}
                </div>
              ) : (
                wordField
              )}

              {error && (
                <VoiceNote tone="error" testId="dictionary-dialog-error">
                  {error}
                </VoiceNote>
              )}
            </div>

            <div className="mt-6 flex flex-wrap justify-end gap-2">
              <Button type="button" size="sm" variant="ghost" onClick={onClose} disabled={saving}>
                {t("dictionary.cancel")}
              </Button>
              <Button type="submit" size="sm" disabled={saving} data-testid="dictionary-save">
                {saving && (
                  <Loader2 aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
                )}
                {initial ? t("dictionary.save") : t("dictionary.add_confirm")}
              </Button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** A labelled field with an optional hint and a validation message under it. */
function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string;
  hint?: string;
  error: string | null;
  children: (ids: { input: string; describedBy: string | undefined }) => ReactNode;
}) {
  const inputId = useId();
  const hintId = useId();
  const errorId = useId();
  const describedBy =
    [error ? errorId : null, hint ? hintId : null].filter(Boolean).join(" ") || undefined;
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <label htmlFor={inputId} className="text-sm font-medium text-foreground">
        {label}
      </label>
      {children({ input: inputId, describedBy })}
      {error && (
        <p id={errorId} className="text-xs text-destructive" data-testid="dictionary-field-error">
          {error}
        </p>
      )}
      {hint && (
        <p id={hintId} className="text-xs text-muted-foreground">
          {hint}
        </p>
      )}
    </div>
  );
}
