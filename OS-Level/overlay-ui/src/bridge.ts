// Typed wrapper around qwebchannel.js. Qt provides qwebchannel.js out of
// the box via QWebEngineScript / qrc:///qtwebchannel/qwebchannel.js — we
// load it in the HTML head as <script src=...>. This is just the promise
// wrapper + the subscribe helpers so the renderer never touches globals
// directly.

import { StateChangeSchema, StateNameSchema, type StateName } from "./schema";

const QWEBCHANNEL_URL = "qrc:///qtwebchannel/qwebchannel.js";

/**
 * qwebchannel.js is delivered by the Qt runtime from the qrc resource
 * path. We load it lazily via DOM script insertion so Vite doesn't
 * need to resolve the HTML (otherwise it triggers a build warning
 * "qrc:/// can't be bundled").
 */
function loadQWebChannelScript(): Promise<void> {
  if (typeof globalThis.QWebChannel === "function") {
    return Promise.resolve();
  }
  return new Promise((resolve, reject) => {
    const existing = document.querySelector<HTMLScriptElement>(
      `script[src="${QWEBCHANNEL_URL}"]`,
    );
    if (existing !== null) {
      existing.addEventListener("load", () => resolve(), { once: true });
      existing.addEventListener(
        "error",
        () => reject(new Error("qwebchannel.js script failed to load")),
        { once: true },
      );
      return;
    }
    const script = document.createElement("script");
    script.src = QWEBCHANNEL_URL;
    script.async = false;
    script.addEventListener("load", () => resolve(), { once: true });
    script.addEventListener(
      "error",
      () => reject(new Error("qwebchannel.js script failed to load")),
      { once: true },
    );
    document.head.appendChild(script);
  });
}

// Minimal type for what QWebChannel exposes.
interface StateBridgeQObject {
  // Qt-Signal -> {connect(callback): void}
  stateChanged: {
    connect: (cb: (oldState: string, nextState: string, reason: string) => void) => void;
    disconnect?: (cb: (oldState: string, nextState: string, reason: string) => void) => void;
  };
  // Qt-Slot -> Promise<string>
  currentState: (cb: (s: string) => void) => void;
}

interface EffectsBridgeQObject {
  clickEvent: {
    connect: (cb: (x: number, y: number, monitorIdx: number, button: string) => void) => void;
  };
  cursorMoved: {
    connect: (cb: (x: number, y: number) => void) => void;
  };
  actionStarted: {
    connect: (cb: (kind: string, durationHintMs: number) => void) => void;
  };
  actionEnded: {
    connect: (cb: () => void) => void;
  };
}

interface QtWebChannelTransport {
  send: (data: string) => void;
  onmessage?: ((event: MessageEvent) => void) | null;
}

interface QtGlobals {
  webChannelTransport: QtWebChannelTransport;
}

declare global {
  // qwebchannel.js setzt window.qt + window.QWebChannel.
  // eslint-disable-next-line no-var
  var qt: QtGlobals | undefined;
  // eslint-disable-next-line no-var
  var QWebChannel: new (
    transport: QtWebChannelTransport,
    callback: (channel: { objects: Record<string, unknown> }) => void,
  ) => unknown;
}

export interface ClickEventData {
  x: number;
  y: number;
  monitorIdx: number;
  button: string;
}

export interface OverlayBridge {
  currentState(): Promise<StateName>;
  onStateChange(handler: (change: { old: StateName; next: StateName; reason: string }) => void): void;
  /** Phase 9.5 — becomes non-null once the effectsBridge is registered. */
  onClickEvent(handler: (ev: ClickEventData) => void): void;
  onCursorMoved(handler: (x: number, y: number) => void): void;
  onActionStarted(handler: (kind: string, durationHintMs: number) => void): void;
  onActionEnded(handler: () => void): void;
}

/**
 * Connect to QWebChannel and resolve with a typed bridge.
 *
 * Called from main.ts. If qwebchannel.js hasn't loaded yet (a race in
 * pre-build dev mode), the wrapper briefly polls for
 * `window.qt.webChannelTransport`.
 *
 * @remarks
 * Re-Connect after teardown is not supported in v1. The bridge is
 * connected once at boot and lives until the WebView is destroyed.
 * Subscribers cannot be removed individually — the underlying
 * QWebChannel-Signal `stateBridge.stateChanged.disconnect` is
 * optional in our typed wrapper because it's never invoked. If
 * Hot-Reload-Cycles in dev-mode become a debugging pain, expose
 * a `teardownBridge()` function that calls `disconnect` on all
 * registered handlers.
 */
export async function connectBridge(timeoutMs = 5000): Promise<OverlayBridge> {
  // Lazy load qwebchannel.js — this way Vite ignores the qrc:/// URL.
  await loadQWebChannelScript();
  return new Promise((resolve, reject) => {
    const start = performance.now();

    const tryConnect = (): void => {
      const qt = globalThis.qt;
      if (!qt || !qt.webChannelTransport) {
        if (performance.now() - start > timeoutMs) {
          reject(new Error("QWebChannel transport not available within timeout"));
          return;
        }
        setTimeout(tryConnect, 50);
        return;
      }
      // eslint-disable-next-line @typescript-eslint/no-unused-expressions
      new globalThis.QWebChannel(qt.webChannelTransport, (channel) => {
        const obj = channel.objects["stateBridge"];
        if (!obj) {
          reject(new Error("stateBridge not registered on QWebChannel"));
          return;
        }
        const stateBridge = obj as StateBridgeQObject;
        const effectsBridge = channel.objects["effectsBridge"] as
          | EffectsBridgeQObject
          | undefined;

        resolve({
          currentState() {
            return new Promise<StateName>((res) => {
              stateBridge.currentState((value) => {
                res(StateNameSchema.parse(value));
              });
            });
          },
          onStateChange(handler) {
            stateBridge.stateChanged.connect((oldState, nextState, reason) => {
              const parsed = StateChangeSchema.safeParse({
                old: oldState,
                next: nextState,
                reason,
              });
              if (!parsed.success) {
                console.warn("StateChange validation failed", parsed.error.issues);
                return;
              }
              handler(parsed.data);
            });
          },
          onClickEvent(handler) {
            if (effectsBridge === undefined) return;
            effectsBridge.clickEvent.connect((x, y, monitorIdx, button) => {
              handler({ x, y, monitorIdx, button });
            });
          },
          onCursorMoved(handler) {
            if (effectsBridge === undefined) return;
            effectsBridge.cursorMoved.connect((x, y) => {
              handler(x, y);
            });
          },
          onActionStarted(handler) {
            if (effectsBridge === undefined) return;
            effectsBridge.actionStarted.connect((kind, dur) => {
              handler(kind, dur);
            });
          },
          onActionEnded(handler) {
            if (effectsBridge === undefined) return;
            effectsBridge.actionEnded.connect(() => {
              handler();
            });
          },
        });
      });
    };

    tryConnect();
  });
}
