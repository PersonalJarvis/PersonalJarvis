import * as Dialog from "@radix-ui/react-dialog";
import { Loader2, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";

/**
 * The confirmation in front of "Delete all": unlike the per-row trash icon,
 * clearing the history removes every entry and its saved audio at once, with
 * no discarded state to restore from.
 *
 * Cancel takes the initial focus, so a stray Enter keeps the history. Closing
 * returns focus to the button that opened the dialog.
 */
export function DictationClearDialog({
  open,
  busy,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const t = useT();
  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        if (!next && !busy) onCancel();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-scrim/50" />
        <Dialog.Content
          data-testid="dictation-clear-dialog"
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            (event.currentTarget as HTMLElement)
              .querySelector<HTMLElement>("[data-clear-cancel]")
              ?.focus();
          }}
          className="fixed left-1/2 top-1/2 z-50 w-[min(26rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-xl border border-border bg-popover p-6 text-popover-foreground shadow-float focus:outline-none"
        >
          <div className="flex items-start gap-4">
            <span
              aria-hidden="true"
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-destructive/10 text-destructive [&>svg]:h-4 [&>svg]:w-4"
            >
              <Trash2 />
            </span>
            <div className="min-w-0 flex-1">
              <Dialog.Title className="text-base font-semibold text-foreground-strong">
                {t("dictation.clear_confirm_title")}
              </Dialog.Title>
              <Dialog.Description className="mt-1 text-sm text-muted-foreground">
                {t("dictation.clear_confirm_body")}
              </Dialog.Description>
            </div>
          </div>
          <div className="mt-6 flex flex-wrap justify-end gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={onCancel}
              data-clear-cancel
              data-testid="dictation-clear-cancel"
            >
              {t("dictation.cancel")}
            </Button>
            <Button
              size="sm"
              variant="destructive"
              disabled={busy}
              onClick={onConfirm}
              data-testid="dictation-clear-confirm"
            >
              {busy && (
                <Loader2 aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
              )}
              {t("dictation.clear_history")}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
