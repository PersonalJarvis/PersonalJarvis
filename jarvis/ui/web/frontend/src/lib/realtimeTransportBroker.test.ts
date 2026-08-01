import { afterEach, describe, expect, it, vi } from "vitest";

import {
  buildRealtimeTransportSocketUrl,
  RealtimeTransportBroker,
} from "./realtimeTransportBroker";

class FakeTransport {
  answers: string[] = [];
  close = vi.fn();

  constructor(readonly offer: string) {}

  createOffer = vi.fn(async () => this.offer);
  applyAnswer = vi.fn(async (answer: string) => {
    this.answers.push(answer);
  });

}

class FakeSocket {
  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  close = vi.fn(() => {
    if (this.readyState === 3) return;
    this.readyState = 3;
    this.onclose?.();
  });

  constructor(readonly url: string) {}

  open() {
    this.readyState = 1;
    this.onopen?.();
  }

  send(data: string) {
    this.sent.push(data);
  }

  receive(message: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(message) } as MessageEvent);
  }
}

afterEach(() => vi.unstubAllGlobals());

describe("realtime transport broker", () => {
  it("builds the authenticated socket URL without exposing a session token", () => {
    vi.stubGlobal("window", {
      location: { protocol: "https:", host: "app.example" },
    });
    expect(buildRealtimeTransportSocketUrl("single-use")).toBe(
      "wss://app.example/ws/realtime-transport?ticket=single-use",
    );
  });

  it("keeps an answered peer alive, then publishes a fresh offer after release", async () => {
    vi.stubGlobal("window", {
      location: { protocol: "https:", host: "app.example" },
    });
    const sockets: FakeSocket[] = [];
    const transports: FakeTransport[] = [];
    let nextOffer = 0;
    let nextId = 0;
    const broker = new RealtimeTransportBroker({
      mintTicket: vi.fn(async () => "ticket-1"),
      readDesktopCapability: () => "desktop-only",
      createSocket: (url) => {
        const socket = new FakeSocket(url);
        sockets.push(socket);
        return socket as unknown as WebSocket;
      },
      createTransport: () => {
        const transport = new FakeTransport(`offer-sdp-${++nextOffer}`);
        transports.push(transport);
        return transport;
      },
      createOfferId: () => `offer-${++nextId}`,
    });

    broker.start();
    await vi.waitFor(() => expect(sockets).toHaveLength(1));
    const socket = sockets[0];
    socket.open();
    await vi.waitFor(() => expect(socket.sent).toHaveLength(2));
    expect(JSON.parse(socket.sent[0])).toEqual({
      type: "authenticate",
      desktop_capability: "desktop-only",
    });
    expect(JSON.parse(socket.sent[1])).toEqual({
      type: "offer",
      offer_id: "offer-1",
      webrtc_offer_sdp: "offer-sdp-1",
    });

    socket.receive({
      type: "answer",
      offer_id: "offer-1",
      webrtc_answer_sdp: "answer-sdp-1",
    });
    await vi.waitFor(() => expect(transports[0].answers).toEqual(["answer-sdp-1"]));
    expect(transports[0].close).not.toHaveBeenCalled();

    socket.receive({ type: "release", offer_id: "offer-1" });
    await vi.waitFor(() => expect(socket.sent).toHaveLength(3));
    expect(transports[0].close).toHaveBeenCalledOnce();
    expect(transports).toHaveLength(2);
    expect(JSON.parse(socket.sent[2])).toEqual({
      type: "offer",
      offer_id: "offer-2",
      webrtc_offer_sdp: "offer-sdp-2",
    });

    socket.receive({
      type: "answer",
      offer_id: "offer-2",
      webrtc_answer_sdp: "answer-sdp-2",
    });
    await vi.waitFor(() => expect(transports[1].answers).toEqual(["answer-sdp-2"]));
    expect(transports[1].close).not.toHaveBeenCalled();

    broker.stop();
    expect(transports[1].close).toHaveBeenCalledOnce();
    expect(socket.close).toHaveBeenCalledOnce();
  });

  it("reports an unavailable WebRTC capability without reconnecting forever", async () => {
    vi.stubGlobal("window", {
      location: { protocol: "https:", host: "app.example" },
    });
    const socket = new FakeSocket("wss://app.example/ws/realtime-transport");
    const schedules: Array<() => void> = [];
    const broker = new RealtimeTransportBroker({
      mintTicket: vi.fn(async () => "ticket-1"),
      readDesktopCapability: () => "desktop-only",
      createSocket: () => socket as unknown as WebSocket,
      createTransport: () => new FakeTransport(""),
      schedule: (callback) => {
        schedules.push(callback);
        return 1 as unknown as ReturnType<typeof setTimeout>;
      },
    });

    broker.start();
    await vi.waitFor(() => expect(socket.onopen).not.toBeNull());
    socket.open();
    await vi.waitFor(() => expect(socket.close).toHaveBeenCalledOnce());

    expect(JSON.parse(socket.sent[1])).toEqual({
      type: "unavailable",
      reason: "webrtc_offer_unavailable",
    });
    expect(schedules).toHaveLength(0);
  });
});
