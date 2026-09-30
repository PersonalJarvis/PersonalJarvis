import { useState, type MouseEvent } from "react";

export interface PaneMenuRequest { left: number; top: number }

/** Intercept before xterm can consume a right-click or send it to the PTY. */
export function usePaneContextMenu(enabled: boolean) {
  const [request, setRequest] = useState<PaneMenuRequest | null>(null);
  const [sendRightClicks, setSendRightClicks] = useState(false);
  const interceptButton = (event: MouseEvent) => {
    if (enabled && event.button === 2 && (!sendRightClicks || event.shiftKey)) {
      event.preventDefault();
      event.stopPropagation();
    }
  };
  return {
    request,
    sendRightClicks,
    toggleSendRightClicks: () => setSendRightClicks((current) => !current),
    handlers: {
      onPointerDownCapture: interceptButton,
      onPointerUpCapture: interceptButton,
      onMouseDownCapture: interceptButton,
      onMouseUpCapture: interceptButton,
      onContextMenuCapture: (event: MouseEvent) => {
        if (!enabled) return;
        event.preventDefault();
        event.stopPropagation();
        if (!sendRightClicks || event.shiftKey) {
          setRequest({ left: event.clientX, top: event.clientY });
        }
      },
    },
  };
}
