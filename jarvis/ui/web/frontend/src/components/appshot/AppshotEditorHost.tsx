import { Suspense, lazy } from "react";
import * as Dialog from "@radix-ui/react-dialog";

import { useT } from "@/i18n";
import { useAppshotEditor } from "@/store/appshotEditor";

// The editor and its strings load only when someone edits an appshot.
const AppshotEditor = lazy(() =>
  import("@/views/AppshotEditor").then((m) => ({ default: m.AppshotEditor })),
);

/**
 * The one place the appshot editor lives: a full-window dialog over whatever
 * view is open, so a click on the appshot card opens it without navigating
 * anywhere.
 *
 * A Radix dialog, not a plain layer: over the Settings dialog (the Appshots
 * page's "Edit" button) it stacks as the top modal — its clicks are not
 * "outside" Settings, its text box can take focus, and Escape reaches only
 * the editor. Escape and outside presses are left to the editor itself, which
 * cancels a stroke or asks before discarding edits.
 */
function returnToCorner(id: string, flyFrom: [number, number, number, number]) {
  void fetch("/api/appshot/latest/card", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ id, fly_from: flyFrom }),
  }).catch(() => undefined);
}

export function AppshotEditorHost() {
  const t = useT();
  const openId = useAppshotEditor((s) => s.openId);
  const close = useAppshotEditor((s) => s.close);
  const applied = useAppshotEditor((s) => s.applied);

  return (
    <Dialog.Root open={openId !== null} onOpenChange={(open) => !open && close()}>
      <Dialog.Portal>
        <Dialog.Content
          aria-describedby={undefined}
          onEscapeKeyDown={(event) => event.preventDefault()}
          onPointerDownOutside={(event) => event.preventDefault()}
          onInteractOutside={(event) => event.preventDefault()}
          className="fixed inset-0 z-[95] outline-none"
          data-testid="appshot-editor-host"
        >
          <Dialog.Title className="sr-only">{t("appshots.editor_title")}</Dialog.Title>
          {openId !== null && (
            <Suspense fallback={<div className="h-full w-full bg-background" />}>
              <AppshotEditor
                key={openId}
                appshotId={openId}
                onClose={(exit) => {
                  // Saved or used: the picture flies back to the bottom of the card stack.
                  if (exit?.flyFrom) returnToCorner(openId, exit.flyFrom);
                  close();
                }}
                onApplied={applied}
              />
            </Suspense>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
