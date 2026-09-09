import { useCallback, useEffect, useRef, useState } from "react";
import { mintWsTicket } from "@/lib/ws";
import { jitteredDelay, requestConnect } from "@/lib/connectBudget";

export interface BrowserViewState {
  connected: boolean;
  ready: boolean;
  manual: boolean;
  running: boolean;
  url: string;
  tabs: Array<{ id: string; url: string }>;
  target: string;
  error: string;
  approval?: { id: string; action: string };
  dialog?: { type: string; message: string };
}
const empty: BrowserViewState = {
  connected: false, ready: false, manual: false, running: false,
  url: "", tabs: [], target: "", error: "",
};

export function useBrowserView(agentId: string) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const socket = useRef<WebSocket | null>(null);
  const [state, setState] = useState<BrowserViewState>(empty);
  useEffect(() => {
    let disposed = false;
    let attempt = 0;
    let cancelConnect = () => {};
    let epoch = 0;
    let generation = "";
    let lastSequence = -1;
    let decodeBusy = false;
    let latestFrame: { data: string; sequence: number } | null = null;
    const abort = new AbortController();
    setState(empty);
    const clearCanvas = () => {
      const el = canvas.current;
      if (el) el.width = 1280;
    };
    clearCanvas();
    const decodeFrame = () => {
      if (decodeBusy || !latestFrame || disposed) return;
      const frame = latestFrame;
      latestFrame = null;
      decodeBusy = true;
      const generation = epoch;
      const image = new Image();
      image.onload = () => {
        const el = canvas.current;
        if (!disposed && generation === epoch && el) {
          if (el.width !== image.width) el.width = image.width;
          if (el.height !== image.height) el.height = image.height;
          el.getContext("2d")?.drawImage(image, 0, 0);
          setState((s) => s.ready ? s : { ...s, ready: true });
        }
        decodeBusy = false;
        decodeFrame();
      };
      image.onerror = () => {
        decodeBusy = false;
        if (!disposed) setState((s) => ({ ...s, error: "Browser image could not be decoded" }));
      };
      image.src = "data:image/jpeg;base64," + frame.data;
    };
    const connect = async () => {
      if (disposed) return;
      try {
        const ticket = await mintWsTicket();
        if (disposed) return;
        const protocol = location.protocol === "https:" ? "wss:" : "ws:";
        const path = "/api/society/agents/" + encodeURIComponent(agentId) + "/browser/live";
        const ws = new WebSocket(protocol + "//" + location.host + path +
          (ticket ? "?ticket=" + encodeURIComponent(ticket) : ""));
        socket.current = ws;
        ws.onopen = () => {
          attempt = 0;
          setState((s) => ({ ...s, connected: true, error: "" }));
        };
        ws.onmessage = (message) => {
          if (disposed || socket.current !== ws) return;
          try {
            const event = JSON.parse(message.data);
            if (event.kind === "frame") {
              if (typeof event.data !== "string" || typeof event.sequence !== "number") return;
              if (event.generation !== generation) {
                generation = event.generation;
                lastSequence = -1;
                epoch++;
              }
              if (event.sequence <= lastSequence) return;
              lastSequence = event.sequence;
              latestFrame = event;
              decodeFrame();
            } else if (event.kind === "state") {
              setState((s) => ({ ...s, manual: event.manual, running: event.running,
                url: event.url, target: event.target, tabs: event.tabs ?? [] }));
            } else if (event.kind === "control") {
              setState((s) => ({ ...s, error: event.ok ? "" : event.error,
                manual: typeof event.manual === "boolean" ? event.manual : s.manual }));
            } else if (event.kind === "approval") {
              setState((s) => ({ ...s, approval: { id: event.id, action: event.action } }));
            } else if (event.kind === "dialog") {
              setState((s) => ({ ...s, dialog: event }));
            } else if (event.kind === "approval_cleared") {
              setState((s) => ({ ...s, approval: undefined }));
            } else if (event.kind === "dialog_cleared") {
              setState((s) => ({ ...s, dialog: undefined }));
            } else if (event.kind === "warning") {
              setState((s) => ({ ...s, error: event.error }));
            }
          } catch {
            setState((s) => ({ ...s, error: "Browser stream returned invalid data" }));
          }
        };
        ws.onclose = () => {
          if (disposed || socket.current !== ws) return;
          epoch++;
          setState((s) => ({ ...s, connected: false, manual: false }));
          cancelConnect = requestConnect(() => void connect(), jitteredDelay(attempt++));
        };
        ws.onerror = () => ws.close();
      } catch (error) {
        if (disposed) return;
        setState((s) => ({ ...s, connected: false,
          error: error instanceof Error ? error.message : "Browser connection failed" }));
        cancelConnect = requestConnect(() => void connect(), jitteredDelay(attempt++));
      }
    };
    void fetch("/api/society/agents/" + encodeURIComponent(agentId) + "/browser/session",
      { method: "POST", signal: abort.signal }).then((response) => {
        if (!response.ok) throw new Error("Browser setup " + response.status);
        if (!disposed) cancelConnect = requestConnect(() => void connect());
      }).catch((error) => {
        if (!disposed) setState((s) => ({ ...s, error: String(error) }));
      });
    return () => {
      disposed = true;
      epoch++;
      abort.abort();
      cancelConnect();
      const ws = socket.current;
      socket.current = null;
      ws?.close();
      clearCanvas();
    };
  }, [agentId]);

  const control = useCallback((op: string, args: Record<string, unknown> = {}) => {
    if (socket.current?.readyState !== WebSocket.OPEN) return;
    socket.current.send(JSON.stringify({ op, args }));
  }, []);
  const approve = useCallback(async (allow: boolean) => {
    if (!state.approval) return;
    const response = await fetch("/api/society/approvals/" + state.approval.id + "/resolve", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approve: allow }),
    });
    if (!response.ok) {
      setState((s) => ({ ...s, error: "Approval " + response.status }));
    } else setState((s) => ({ ...s, approval: undefined }));
  }, [state.approval]);
  return { canvas, state, control, approve };
}
