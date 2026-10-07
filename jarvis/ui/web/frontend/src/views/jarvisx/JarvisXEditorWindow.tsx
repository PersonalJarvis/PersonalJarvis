import { useCallback, useEffect, useState } from "react";

import { ToastLayer } from "@/components/ToastLayer";
import { EditContextMenu } from "@/components/EditContextMenu";
import { useDesktopChrome, WindowControls } from "@/components/layout/WindowControls";
import { hydrateUiLanguage } from "@/i18n";
import { JarvisXEditor } from "@/views/jarvisx/JarvisXEditor";

/** The view id the backend opens the editor window with. */
export const JARVISX_EDITOR_VIEW = "jarvisx-editor";

/** Is this document the Jarvis X editor window (`?view=jarvisx-editor`)? */
export function isJarvisXEditorSearch(search: string): boolean {
  return new URLSearchParams(search).get("view") === JARVISX_EDITOR_VIEW;
}

/**
 * The editor as its own desktop window.
 *
 * Mounted by `main.tsx` INSTEAD of the app shell: the editor is not a section,
 * so none of the shell's machinery (voice broker, websocket, onboarding, dock)
 * has a job here, and a solo shell would have mounted the front page's voice
 * broker for an unknown view id. The window keeps its title strip — the
 * toolbar row doubles as the drag handle — and the toast layer.
 */
export function JarvisXEditorWindow() {
  const params = new URLSearchParams(window.location.search);
  const itemId = params.get("item");
  // Verification fallback: a same-origin picture opened without a stored item.
  const devSrc = itemId ? null : params.get("src");
  const chrome = useDesktopChrome();
  const controls = chrome.frameless ? chrome.controls : "none";

  useEffect(() => {
    void hydrateUiLanguage();
    document.title = "Jarvis X";
  }, []);

  const [maximized, setMaximized] = useState(false);

  // Always names THIS window. The shared `chrome.command` derives the target
  // from the section store, which knows nothing about the editor, and a
  // command without a known view lands on the main window.
  const command = useCallback((action: "minimize" | "maximize" | "close") => {
    void fetch("/api/window/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, view: JARVISX_EDITOR_VIEW }),
    })
      .then((res) => (res.ok ? res.json() : null))
      .then((body: { maximized?: unknown } | null) => {
        if (body === null && action === "close") window.close();
        if (body && typeof body.maximized === "boolean") setMaximized(body.maximized);
      })
      .catch(() => {
        if (action === "close") window.close();
      });
  }, []);
  const close = useCallback(() => command("close"), [command]);

  const windowButtons = (side: "leading" | "trailing") =>
    controls === side ? <WindowControls controls={controls} maximized={maximized} onCommand={command} /> : null;

  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden bg-background text-foreground">
      <JarvisXEditor
        itemId={itemId}
        devSrc={devSrc}
        variant="window"
        onClose={close}
        leading={windowButtons("leading")}
        trailing={windowButtons("trailing")}
      />
      <ToastLayer />
      <EditContextMenu />
    </div>
  );
}
