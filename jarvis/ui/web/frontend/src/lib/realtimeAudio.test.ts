import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
const wsFakes = vi.hoisted(() => ({ mintWsTicket: vi.fn(async () => null) }));
vi.mock("./ws", () => ({ mintWsTicket: wsFakes.mintWsTicket }));

import {
  BrowserSpeechFallback,
  browserRealtimeSupportIssue,
  buildAudioSocketUrl,
  RealtimeAudioClient,
  StreamingPcm16Resampler,
} from "./realtimeAudio";

class FakePort {
  onmessage: ((event: MessageEvent) => void) | null = null;
  postMessage = vi.fn();
}

class FakeAudioNode {
  static instances: FakeAudioNode[] = [];
  port = new FakePort();
  connect = vi.fn(() => this);
  disconnect = vi.fn();

  constructor() {
    FakeAudioNode.instances.push(this);
  }
}

class FakeAudioContext {
  sampleRate = 48_000;
  destination = {} as AudioDestinationNode;
  audioWorklet = { addModule: vi.fn(async () => undefined) };
  resume = vi.fn(async () => undefined);
  close = vi.fn(async () => undefined);
  createMediaStreamSource = vi.fn(() => new FakeAudioNode());
  createGain = vi.fn(() => Object.assign(new FakeAudioNode(), { gain: { value: 1 } }));
}

class FakePeerConnection {
  static instances: FakePeerConnection[] = [];
  iceGatheringState: RTCIceGatheringState = "complete";
  localDescription: RTCSessionDescription | null = null;
  remoteDescriptions: RTCSessionDescriptionInit[] = [];
  addTransceiver = vi.fn();
  createDataChannel = vi.fn();
  close = vi.fn();
  addEventListener = vi.fn();
  removeEventListener = vi.fn();
  ontrack: ((event: RTCTrackEvent) => void) | null = null;

  constructor() {
    FakePeerConnection.instances.push(this);
  }

  createOffer = vi.fn(async () => ({ type: "offer" as const, sdp: "offer-sdp" }));
  setLocalDescription = vi.fn(async (description: RTCSessionDescriptionInit) => {
    this.localDescription = description as RTCSessionDescription;
  });
  setRemoteDescription = vi.fn(async (description: RTCSessionDescriptionInit) => {
    this.remoteDescriptions.push(description);
  });
}

class FakeWebSocket {
  static OPEN = 1;
  static instances: FakeWebSocket[] = [];
  readyState = 0;
  binaryType = "";
  sent: unknown[] = [];
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;

  constructor(readonly url: string) {
    FakeWebSocket.instances.push(this);
  }

  send(data: unknown) {
    this.sent.push(data);
  }

  open() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  receive(message: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(message) } as MessageEvent);
  }

  receiveBinary(data: ArrayBuffer) {
    this.onmessage?.({ data } as MessageEvent);
  }

  close = vi.fn(() => {
    if (this.readyState === 3) return;
    this.readyState = 3;
    this.onclose?.({ code: 1000, reason: "" } as CloseEvent);
  });
}

function installVoiceBrowserFakes() {
  const track = { stop: vi.fn() };
  vi.stubGlobal("navigator", {
    mediaDevices: { getUserMedia: vi.fn(async () => ({ getTracks: () => [track] })) },
  });
  vi.stubGlobal("AudioContext", FakeAudioContext);
  vi.stubGlobal("AudioWorkletNode", FakeAudioNode);
  vi.stubGlobal("RTCPeerConnection", FakePeerConnection);
  vi.stubGlobal("WebSocket", FakeWebSocket);
  return { track };
}

describe("realtime audio client", () => {
  beforeEach(() => {
    vi.stubGlobal("window", {
      location: { protocol: "https:", host: "app.example", hostname: "app.example" },
      __JARVIS_TOKEN: "tok",
      isSecureContext: true,
      setTimeout: globalThis.setTimeout,
      clearTimeout: globalThis.clearTimeout,
    });
    FakePeerConnection.instances = [];
    FakeWebSocket.instances = [];
    FakeAudioNode.instances = [];
    wsFakes.mintWsTicket.mockClear();
  });

  afterEach(() => vi.unstubAllGlobals());

  it("builds a token-free wss /ws/audio URL", () => {
    expect(buildAudioSocketUrl()).toBe("wss://app.example/ws/audio");
  });

  it("builds a token-free localhost audio URL", () => {
    vi.stubGlobal("window", {
      location: {
        protocol: "http:",
        host: "localhost:47821",
        hostname: "localhost",
      },
    });
    expect(buildAudioSocketUrl()).toBe("ws://localhost:47821/ws/audio");
  });

  it("carries a one-time handshake ticket when one was minted (BUG-065)", () => {
    // The long-lived session token must still never appear in a socket URL;
    // only the consumable short-TTL ticket may ride along for WebKit engines.
    expect(buildAudioSocketUrl("one-time-abc")).toBe(
      "wss://app.example/ws/audio?ticket=one-time-abc",
    );
    expect(buildAudioSocketUrl(null)).toBe("wss://app.example/ws/audio");
  });

  it("rejects browser microphone capture outside a secure context", () => {
    vi.stubGlobal("window", { isSecureContext: false });

    expect(browserRealtimeSupportIssue()).toBe("secure_context");
  });

  it("reports missing microphone and AudioWorklet capabilities separately", () => {
    vi.stubGlobal("window", { isSecureContext: true });
    vi.stubGlobal("navigator", {});
    expect(browserRealtimeSupportIssue()).toBe("microphone_unavailable");

    vi.stubGlobal("navigator", { mediaDevices: { getUserMedia: vi.fn() } });
    vi.stubGlobal("AudioContext", undefined);
    vi.stubGlobal("AudioWorkletNode", undefined);
    expect(browserRealtimeSupportIssue()).toBe("audio_worklet_unavailable");
  });

  it("resamples provider PCM from 24 kHz to a 48 kHz AudioContext", () => {
    const input = Int16Array.from({ length: 2_400 }, (_, i) => i - 1_200);
    const output = new Int16Array(
      new StreamingPcm16Resampler(24_000, 48_000).process(input.buffer),
    );

    expect(output.length).toBeGreaterThanOrEqual(4_798);
    expect(output.length).toBeLessThanOrEqual(4_800);
  });

  it("keeps interpolation continuous across WebSocket frame boundaries", () => {
    const input = Int16Array.from({ length: 2_400 }, (_, i) => i * 4 - 4_800);
    const whole = new Int16Array(
      new StreamingPcm16Resampler(24_000, 48_000).process(input.buffer),
    );
    const streamed = new StreamingPcm16Resampler(24_000, 48_000);
    const first = new Int16Array(streamed.process(input.slice(0, 1_200).buffer));
    const second = new Int16Array(streamed.process(input.slice(1_200).buffer));

    expect([...first, ...second]).toEqual([...whole]);
  });

  it("sends a WebRTC offer and applies the matching answer while PCM stays active", async () => {
    const { track } = installVoiceBrowserFakes();
    const client = new RealtimeAudioClient({}, { requiresWebRtcOffer: true });
    const connecting = client.connect();

    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];
    socket.open();
    const start = JSON.parse(String(socket.sent[0])) as Record<string, unknown>;
    expect(start).toMatchObject({
      type: "audio_start",
      sample_rate: 48_000,
      webrtc_offer_sdp: "offer-sdp",
    });
    expect(FakePeerConnection.instances[0].addTransceiver).toHaveBeenCalledWith(
      "audio",
      { direction: "recvonly" },
    );
    expect(FakePeerConnection.instances[0].createDataChannel).toHaveBeenCalledWith(
      "oai-events",
    );

    socket.receive({
      type: "audio_ready",
      output_sample_rate: 24_000,
      webrtc_answer_sdp: "answer-sdp",
    });
    await connecting;
    expect(FakePeerConnection.instances[0].remoteDescriptions).toEqual([
      { type: "answer", sdp: "answer-sdp" },
    ]);

    await client.disconnect();
    expect(FakePeerConnection.instances[0].close).toHaveBeenCalledOnce();
    expect(track.stop).toHaveBeenCalledOnce();
  });

  it("replays the opening spoken during a slow subscription start", async () => {
    // A cold subscription transport spends 15-25 s coming up. Captured PCM was
    // DISCARDED for that whole window, so the user's first sentence vanished —
    // and because that transport drives its own turn detection, nothing ever
    // asked for a repeat. The desktop already retains and replays the opening.
    installVoiceBrowserFakes();
    const client = new RealtimeAudioClient({}, { startBudgetMs: 45_000 });
    const connecting = client.connect();

    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];
    socket.open();

    const capture = FakeAudioNode.instances.find((node) => node.port.onmessage);
    expect(capture).toBeDefined();
    const opening = new Uint8Array([1, 2, 3, 4]).buffer;
    capture!.port.onmessage!({ data: opening } as MessageEvent);

    const binaries = () => socket.sent.filter((item) => item instanceof ArrayBuffer);
    expect(binaries()).toHaveLength(0);

    socket.receive({ type: "audio_ready", output_sample_rate: 24_000 });
    await connecting;

    expect(binaries()).toEqual([opening]);
    await client.disconnect();
  });

  it("drops a retained opening that never reached a ready socket", async () => {
    installVoiceBrowserFakes();
    const client = new RealtimeAudioClient({}, {});
    const connecting = client.connect();

    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];
    socket.open();
    const capture = FakeAudioNode.instances.find((node) => node.port.onmessage);
    capture!.port.onmessage!({ data: new Uint8Array([9]).buffer } as MessageEvent);

    socket.close();
    await expect(connecting).rejects.toBeInstanceOf(Error);
    await client.disconnect();

    expect(socket.sent.filter((item) => item instanceof ArrayBuffer)).toHaveLength(0);
  });

  it("keeps RTP detached and plays scrubbed subscription sideband PCM", async () => {
    const createAudio = vi.fn();
    vi.stubGlobal("Audio", createAudio);
    installVoiceBrowserFakes();
    const onAudio = vi.fn();
    const client = new RealtimeAudioClient({ onAudio }, { requiresWebRtcOffer: true });
    const connecting = client.connect();

    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];
    socket.open();
    socket.receive({
      type: "audio_ready",
      output_sample_rate: 24_000,
      webrtc_answer_sdp: "answer-sdp",
    });
    await connecting;

    expect(FakePeerConnection.instances[0].ontrack).toBeNull();
    expect(createAudio).not.toHaveBeenCalled();
    socket.receiveBinary(new Int16Array([1, 2, 3]).buffer);
    expect(onAudio).toHaveBeenCalledOnce();

    await client.disconnect();
  });

  it("gathers subscription ICE in parallel with microphone setup", async () => {
    installVoiceBrowserFakes();
    const track = { stop: vi.fn() };
    let releaseCapture: ((stream: MediaStream) => void) | undefined;
    vi.stubGlobal("navigator", {
      mediaDevices: {
        getUserMedia: vi.fn(
          () =>
            new Promise<MediaStream>((resolve) => {
              releaseCapture = resolve;
            }),
        ),
      },
    });
    const client = new RealtimeAudioClient({}, { requiresWebRtcOffer: true });
    const connecting = client.connect();

    await vi.waitFor(() => expect(FakePeerConnection.instances).toHaveLength(1));
    await vi.waitFor(() => expect(releaseCapture).toBeTypeOf("function"));
    expect(FakeWebSocket.instances).toHaveLength(0);
    releaseCapture?.({ getTracks: () => [track] } as unknown as MediaStream);
    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];
    socket.open();
    socket.receive({
      type: "audio_ready",
      output_sample_rate: 24_000,
      webrtc_answer_sdp: "answer-sdp",
    });

    await connecting;
    await client.disconnect();
  });

  it("fails closed before opening a socket when subscription WebRTC is missing", async () => {
    const { track } = installVoiceBrowserFakes();
    vi.stubGlobal("RTCPeerConnection", undefined);
    const client = new RealtimeAudioClient({}, { requiresWebRtcOffer: true });

    await expect(client.connect()).rejects.toThrow(
      "Subscription Realtime requires WebRTC support",
    );
    expect(FakeWebSocket.instances).toHaveLength(0);
    expect(track.stop).toHaveBeenCalledOnce();
  });

  it("rejects subscription readiness when the provider omits its WebRTC answer", async () => {
    installVoiceBrowserFakes();
    const client = new RealtimeAudioClient({}, { requiresWebRtcOffer: true });
    const outcome = client.connect().catch((error: unknown) => error);

    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];
    socket.open();
    socket.receive({ type: "audio_ready", output_sample_rate: 24_000 });

    await expect(outcome).resolves.toMatchObject({
      message: "Subscription Realtime did not return a WebRTC answer",
    });
    expect(socket.close).toHaveBeenCalled();
    expect(FakePeerConnection.instances[0].close).toHaveBeenCalled();
  });

  it("rejects subscription readiness when the provider answer is invalid", async () => {
    installVoiceBrowserFakes();
    const client = new RealtimeAudioClient({}, { requiresWebRtcOffer: true });
    const outcome = client.connect().catch((error: unknown) => error);

    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];
    socket.open();
    FakePeerConnection.instances[0].setRemoteDescription.mockRejectedValueOnce(
      new Error("invalid SDP"),
    );
    socket.receive({
      type: "audio_ready",
      output_sample_rate: 24_000,
      webrtc_answer_sdp: "bad-answer",
    });

    await expect(outcome).resolves.toMatchObject({
      message: "Subscription Realtime returned an invalid WebRTC answer",
    });
    expect(socket.close).toHaveBeenCalled();
    expect(FakePeerConnection.instances[0].close).toHaveBeenCalled();
  });

  it("keeps an API primary active when WebRTC was prepared only for a fallback", async () => {
    installVoiceBrowserFakes();
    const client = new RealtimeAudioClient({}, { requiresWebRtcOffer: true });
    const connecting = client.connect();

    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];
    socket.open();
    expect(JSON.parse(String(socket.sent[0])).webrtc_offer_sdp).toBe("offer-sdp");
    socket.receive({
      type: "audio_ready",
      provider: "api-primary",
      output_sample_rate: 24_000,
      requires_webrtc_answer: false,
    });

    await connecting;
    expect(FakePeerConnection.instances[0].remoteDescriptions).toEqual([]);
    expect(FakePeerConnection.instances[0].close).toHaveBeenCalledOnce();
    await client.disconnect();
  });

  it("adds no WebRTC handshake work for API Realtime providers", async () => {
    installVoiceBrowserFakes();
    const client = new RealtimeAudioClient();
    const connecting = client.connect();

    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    expect(FakePeerConnection.instances).toHaveLength(0);
    const socket = FakeWebSocket.instances[0];
    socket.open();
    expect(JSON.parse(String(socket.sent[0])).webrtc_offer_sdp).toBeUndefined();
    socket.receive({ type: "audio_ready", output_sample_rate: 24_000 });
    await connecting;
    await client.disconnect();
  });

  it("keeps API-backed PCM voice available when an unsolicited answer is invalid", async () => {
    installVoiceBrowserFakes();
    const statuses: string[] = [];
    const client = new RealtimeAudioClient({
      onStatus: (status) => statuses.push(status),
    });
    const connecting = client.connect();

    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];
    socket.open();
    socket.receive({
      type: "audio_ready",
      output_sample_rate: 24_000,
      webrtc_answer_sdp: "unsolicited-answer",
    });

    await connecting;
    expect(statuses).toContain("webrtc_transport_unavailable");
    await client.disconnect();
  });

  it("speaks a server-approved fallback with language and volume", () => {
    const utterances: SpeechSynthesisUtterance[] = [];
    const synthesis = {
      cancel: vi.fn(),
      speak: vi.fn((utterance: SpeechSynthesisUtterance) => utterances.push(utterance)),
    };
    const createUtterance = (text: string) =>
      ({ text, lang: "", volume: 1, onstart: null, onend: null, onerror: null }) as unknown as
      SpeechSynthesisUtterance;
    const controller = new BrowserSpeechFallback(synthesis, createUtterance);
    const started = vi.fn();
    const finished = vi.fn();

    expect(controller.speak("Hola", "es-ES", 0.4, { onStart: started, onFinish: finished })).toBe(
      true,
    );
    expect(utterances[0].lang).toBe("es-ES");
    expect(utterances[0].volume).toBe(0.4);
    utterances[0].onstart?.(new Event("start") as SpeechSynthesisEvent);
    utterances[0].onend?.(new Event("end") as SpeechSynthesisEvent);
    expect(started).toHaveBeenCalledOnce();
    expect(finished).toHaveBeenCalledWith("ended");
  });

  it("fails honestly when the browser has no speech service", () => {
    const finished = vi.fn();
    const controller = new BrowserSpeechFallback(null, null);

    expect(controller.speak("Answer", "en-US", 1, { onFinish: finished })).toBe(false);
    expect(finished).toHaveBeenCalledWith("unavailable");
  });

  it("ignores a stale completion after a newer fallback starts", () => {
    const utterances: SpeechSynthesisUtterance[] = [];
    const synthesis = {
      cancel: vi.fn(),
      speak: (utterance: SpeechSynthesisUtterance) => utterances.push(utterance),
    };
    const createUtterance = (text: string) =>
      ({ text, lang: "", volume: 1, onstart: null, onend: null, onerror: null }) as unknown as
      SpeechSynthesisUtterance;
    const controller = new BrowserSpeechFallback(synthesis, createUtterance);
    const first = vi.fn();
    const second = vi.fn();

    controller.speak("First", "en-US", 1, { onFinish: first });
    controller.speak("Second", "en-US", 1, { onFinish: second });
    utterances[0].onend?.(new Event("end") as SpeechSynthesisEvent);
    utterances[1].onend?.(new Event("end") as SpeechSynthesisEvent);

    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledWith("ended");
  });
});
