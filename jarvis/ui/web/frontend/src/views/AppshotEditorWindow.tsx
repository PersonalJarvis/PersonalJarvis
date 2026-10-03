import { useCallback, useRef } from "react";

import { ToastLayer } from "@/components/ToastLayer";
import { AppshotEditor } from "@/views/AppshotEditor";

/**
 * The appshot editor as its own desktop window (`?view=appshot-editor&solo=1
 * &appshot=<id>`), opened by a click on the corner card — CleanShot X's
 * editor window. Nothing of the app is loaded around it.
 *
 * When it closes — Done or Close — the appshot slides back into the screen
 * corner (`POST /api/appshot/latest/card`), so it stays at hand, and then the
 * window closes itself through the shell (`POST /api/window/reattach`).
 */

export const APPSHOT_EDITOR_VIEW = "appshot-editor";

/** The appshot this window edits, from its URL; empty when none is named. */
export function appshotIdFromUrl(search: string): string {
  const id = new URLSearchParams(search).get("appshot") ?? "";
  return /^[0-9a-f]{8,64}$/.test(id) ? id : "";
}

/** Is this document the editor window rather than the app? */
export function isAppshotEditorWindow(search: string): boolean {
  return new URLSearchParams(search).get("view") === APPSHOT_EDITOR_VIEW;
}

export interface EditorWindowDeps {
  returnCard: () => Promise<unknown>;
  closeWindow: () => Promise<unknown>;
}

const browserDeps: EditorWindowDeps = {
  returnCard: () => fetch("/api/appshot/latest/card", { method: "POST" }),
  closeWindow: () =>
    fetch("/api/window/reattach", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ view: APPSHOT_EDITOR_VIEW }),
    }),
};

export function AppshotEditorWindow({ deps = browserDeps }: { deps?: EditorWindowDeps }) {
  const appshotId = appshotIdFromUrl(window.location.search);
  const closing = useRef(false);

  const close = useCallback(() => {
    if (closing.current) return;
    closing.current = true;
    // Back into the corner first: the window must not take the card's
    // request down with it.
    void deps
      .returnCard()
      .catch(() => undefined)
      .finally(() => {
        void deps.closeWindow().catch(() => undefined);
      });
  }, [deps]);

  return (
    <div className="h-screen w-screen overflow-hidden bg-popover" data-testid="appshot-editor-window">
      <AppshotEditor appshotId={appshotId || "missing"} onClose={close} variant="window" />
      <ToastLayer />
    </div>
  );
}
