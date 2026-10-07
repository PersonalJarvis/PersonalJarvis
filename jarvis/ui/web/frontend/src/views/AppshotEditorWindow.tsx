import { useCallback, useEffect, useRef, useState } from "react";

import { ToastLayer } from "@/components/ToastLayer";
import { loadLocaleChunk } from "@/i18n";
import { AppshotEditor, type EditorExit } from "@/views/AppshotEditor";

/**
 * The appshot editor as its own desktop window (`?view=appshot-editor&solo=1
 * &appshot=<id>`), opened by a click on the corner card.
 * Nothing of the app is loaded around it.
 *
 * When it closes the appshot goes back into the screen corner
 * (`POST /api/appshot/latest/card`), so it stays at hand — after Save or Done
 * it flies there from where the editor showed it, after Close it slides in;
 * either way it lands at the bottom of the card stack — and then the window
 * closes itself through the shell
 * (`POST /api/window/reattach`), which only hides it: the window stays loaded
 * so the next click opens the editor at once.
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

/**
 * Remember which picture this window is on.
 *
 * The warm window is opened without an id and pointed later in memory. A
 * reload of that address (the blank-window watchdog, a rebuilt bundle) would
 * otherwise come back as the empty gray shell. The address is the backup.
 */
function rememberEditorTarget(id: string): void {
  const url = new URL(window.location.href);
  url.searchParams.delete("appshot");
  if (id) url.searchParams.set("appshot", id);
  const next = `${url.pathname}${url.search}${url.hash}`;
  const current = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  if (next !== current) window.history.replaceState(null, "", next);
}

export interface EditorWindowDeps {
  returnCard: (id: string, flyFrom?: EditorExit["flyFrom"]) => Promise<unknown>;
  closeWindow: () => Promise<unknown>;
}

const browserDeps: EditorWindowDeps = {
  returnCard: (id, flyFrom) =>
    fetch("/api/appshot/latest/card", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(flyFrom ? { id, fly_from: flyFrom } : { id }),
    }),
  closeWindow: () =>
    fetch("/api/window/reattach", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ view: APPSHOT_EDITOR_VIEW }),
    }),
};

declare global {
  interface Window {
    /** The desktop shell points the warm (hidden) editor window at an appshot. */
    __jarvisOpenAppshot?: (id: string) => boolean;
  }
}

export function AppshotEditorWindow({ deps = browserDeps }: { deps?: EditorWindowDeps }) {
  // The window is kept warm and hidden between uses: it may start without an
  // appshot and be pointed at one later (window.__jarvisOpenAppshot), so the
  // editor opens at once instead of booting a fresh window each time.
  const [appshotId, setAppshotId] = useState(() => appshotIdFromUrl(window.location.search));
  const [session, setSession] = useState(0);
  const closing = useRef(false);

  useEffect(() => {
    // Waiting while hidden: have the editor's words ready before the click.
    void loadLocaleChunk("appshot_editor").catch(() => undefined);
    window.__jarvisOpenAppshot = (id: string) => {
      const valid = appshotIdFromUrl(`?appshot=${encodeURIComponent(id)}`);
      if (!valid) return false;
      closing.current = false;
      rememberEditorTarget(valid);
      setAppshotId(valid);
      setSession((n) => n + 1);
      return true;
    };
    return () => {
      delete window.__jarvisOpenAppshot;
    };
  }, []);

  const close = useCallback((exit?: EditorExit) => {
    if (closing.current) return;
    closing.current = true;
    // Back into the corner first: the window must not take the card's
    // request down with it. Then forget the picture, so the hidden window
    // never flashes the previous appshot when it is shown again.
    void deps
      .returnCard(appshotId, exit?.flyFrom)
      .catch(() => undefined)
      .finally(() => {
        setAppshotId("");
        rememberEditorTarget("");
        void deps.closeWindow().catch(() => undefined);
      });
  }, [appshotId, deps]);

  return (
    <div className="fixed inset-0 overflow-hidden bg-popover" data-testid="appshot-editor-window">
      {appshotId && (
        <AppshotEditor key={session} appshotId={appshotId} onClose={close} variant="window" />
      )}
      <ToastLayer />
    </div>
  );
}
