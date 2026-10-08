import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
const wsFakes = vi.hoisted(() => ({ mintWsTicket: vi.fn(async () => null) }));
vi.mock("./ws", () => ({ mintWsTicket: wsFakes.mintWsTicket }));

import {
  BrowserSpeechFallback,
  browserRealtimeSupportIssue,
  buildAudioSocketUrl,
  RealtimeAudioClient,
  RealtimeWebRtcTransport,
  StreamingPcm16Resampler,
} from "./realtimeAudio";
import { TimedPcmQueue } from "./playbackTimeline";
import { readTimedSpeechPlayback } from "./speechPlayback";
import { BURST, requestConnect, resetConnectBudgetForTests } from "./connectBudget";

class FakePort {
  onmessage: ((event: MessageEvent) => void) | null = null;
  postMessage = vi.fn();
}

class FakeAudioNode {
  static instances: FakeAudioNode[] = [];
  port = new FakePort();
  connect = vi.fn(() => this);
  disconnect = vi.fn();

  constructor(_context?: unknown, readonly name?: string) {
    FakeAudioNode.instances.push(this);
  }
}

class FakeAudioContext {
  static voices: Array<{ start: ReturnType<typeof vi.fn>; stop: ReturnType<typeof vi.fn>; disconnect: ReturnType<typeof vi.fn> }> = [];
  state = "running";
  currentTime = 0;
  static instances: FakeAudioContext[] = [];
  outputTime = 0;
  getOutputTimestamp = () => ({ contextTime: this.outputTime, performanceTime: 1 });
  constructor() { FakeAudioContext.instances.push(this); }
  sampleRate = 48_000;
  destination = {} as AudioDestinationNode;
  audioWorklet = { addModule: vi.fn(async () => undefined) };
  resume = vi.fn(async () => undefined);
  close = vi.fn(async () => undefined);
  createMediaStreamSource = vi.fn(() => new FakeAudioNode());
  createGain = vi.fn(() => Object.assign(new FakeAudioNode(), { gain: {
    value: 1, setValueAtTime: vi.fn(), linearRampToValueAtTime: vi.fn(), exponentialRampToValueAtTime: vi.fn(),
  } }));
  createOscillator = vi.fn(() => {
    const voice = { frequency: { value: 0 }, connect: vi.fn(), disconnect: vi.fn(),
      start: vi.fn(), stop: vi.fn(), onended: null };
    FakeAudioContext.voices.push(voice);
    return voice;
  });
  createMediaStreamDestination = vi.fn(() => Object.assign(new FakeAudioNode(), {
    stream: { getAudioTracks: () => ["buffered-track"], getTracks: () => [] },
  }));
}

class FakePeerConnection {
  static instances: FakePeerConnection[] = [];
  static offerSdp = "offer-sdp";
  iceGatheringState: RTCIceGatheringState = "complete";
  connectionState: RTCPeerConnectionState = "new";
  localDescription: RTCSessionDescription | null = null;
  remoteDescriptions: RTCSessionDescriptionInit[] = [];
  addTransceiver = vi.fn();
  addTrack = vi.fn();
  channel = Object.assign(new EventTarget(), { close: vi.fn() });
  createDataChannel = vi.fn(() => this.channel);
  close = vi.fn();
  events = new EventTarget();
  addEventListener = vi.fn(this.events.addEventListener.bind(this.events));
  removeEventListener = vi.fn(this.events.removeEventListener.bind(this.events));
  ontrack: ((event: RTCTrackEvent) => void) | null = null;

  constructor() {
    FakePeerConnection.instances.push(this);
  }

  createOffer = vi.fn(async () => ({ type: "offer" as const, sdp: FakePeerConnection.offerSdp }));
  setLocalDescription = vi.fn(async (description: RTCSessionDescriptionInit) => {
    this.localDescription = description as RTCSessionDescription;
  });
  setRemoteDescription = vi.fn(async (description: RTCSessionDescriptionInit) => {
    this.remoteDescriptions.push(description);
    this.connectionState = "connected";
    this.events.dispatchEvent(new Event("connectionstatechange"));
    this.channel.dispatchEvent(new MessageEvent("message", { data: '{"type":"session.started"}' }));
  });
}

class FakeWebSocket {
  static OPEN = 1;
  static instances: FakeWebSocket[] = [];
  readyState = 0;
  bufferedAmount = 0;
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
    mediaDevices: { getUserMedia: vi.fn(async () => ({ getTracks: () => [track], getAudioTracks: () => [track] })) },
  });
  vi.stubGlobal("AudioContext", FakeAudioContext);
  vi.stubGlobal("AudioWorkletNode", FakeAudioNode);
  vi.stubGlobal("RTCPeerConnection", FakePeerConnection);
  vi.stubGlobal("WebSocket", FakeWebSocket);
  return { track };
}

describe("realtime audio client", () => {
  it.each([
    { sound_effects: false }, { sound_effects: undefined },
    { input_muted: true }, { output_muted: true }, { output_volume: 0 },
  ])("respects readiness cue mute %j", async (state) => {
    installVoiceBrowserFakes();
    const client = new RealtimeAudioClient();
    const connecting = client.connect();
    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];
    socket.open();
    socket.receive({ type: "audio_ready", sound_effects: true, ...state });
    await connecting;
    expect(FakeAudioContext.voices).toHaveLength(0);
    await client.disconnect();
  });

  it("plays once and cancels the cue on output mute without stopping the microphone", async () => {
    const { track } = installVoiceBrowserFakes();
    const client = new RealtimeAudioClient();
    const connecting = client.connect();
    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];
    socket.open();
    socket.receive({ type: "audio_ready", sound_effects: true });
    await connecting;
    expect(FakeAudioContext.voices).toHaveLength(2);
    socket.receive({ type: "output_state", muted: true, revision: 1 });
    expect(FakeAudioContext.voices.every(voice => voice.disconnect.mock.calls.length === 1)).toBe(true);
    expect(track.stop).not.toHaveBeenCalled();
    socket.receive({ type: "reconnecting" });
    socket.receive({ type: "audio_ready", sound_effects: true, output_muted: false, output_revision: 2 });
    await Promise.resolve();
    await Promise.resolve();
    expect(FakeAudioContext.voices).toHaveLength(2);
    await client.disconnect();
  });

  it("plays timed source audio once and highlights only at the device clock", async () => {
    installVoiceBrowserFakes();
    const speakers: { muted: boolean }[] = [];
    vi.stubGlobal("Audio", class {
      muted = false;
      play = async () => undefined;
      pause = () => undefined;
      constructor() { speakers.push(this); }
    });
    const client = new RealtimeAudioClient({}, { browserAudio: true, requiresWebRtcOffer: true });
    const connecting = client.connect();
    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];
    socket.open();
    socket.receive({ type: "audio_transport", webrtc_answer_sdp: "answer", output_transport: "timed_pcm" });
    socket.receive({ type: "audio_ready", webrtc_answer_sdp: "answer", output_transport: "timed_pcm", output_sample_rate: 24000 });
    await connecting;
    const context = FakeAudioContext.instances.at(-1)!;
    const playback = FakeAudioNode.instances.find(node => node.name === "pcm-playback")!;
    const state = playback.port.postMessage.mock.calls.map(([m]) => m).filter(m => m.type === "output_state").at(-1);
    expect(state.muted).toBe(false);
    expect(speakers[0].muted).toBe(true); // RTP can never double the PCM output.
    socket.receive({ type: "speech_timing", epoch: 0, line_id: "wire-test", text: "First second",
      char_start: 0, char_end: 12, start_ms: 1000, end_ms: 2000 });
    socket.receive({ type: "audio_timed", epoch: 0, start_ms: 1000, end_ms: 2000,
      sample_rate: 24000, audio: Buffer.from(new Int16Array(24000).fill(1000).buffer).toString("base64") });
    expect(readTimedSpeechPlayback("wire-test")?.chars).toBe(0);
    const packet = playback.port.postMessage.mock.calls.map(([m]) => m).find(m => m.type === "pcm");
    const queue = new TimedPcmQueue(48000);
    queue.enqueue(new Int16Array(packet.data), packet);
    const spans = queue.render(new Float32Array(48000), 5);
    const report = (intervals: typeof spans = []) => playback.port.onmessage?.({ data: {
      type: "playback", generation: state.generation, spans: intervals,
    } } as MessageEvent);
    report(spans);
    expect(readTimedSpeechPlayback("wire-test")?.chars).toBe(0);
    context.outputTime = 5.05;
    report();
    expect(readTimedSpeechPlayback("wire-test")?.chars).toBe(5);
    socket.receive({ type: "turn_complete" });
    expect(readTimedSpeechPlayback("wire-test")?.chars).toBe(5);
    socket.receive({ type: "audio_clear", epoch: 1 });
    const flushes = playback.port.postMessage.mock.calls.length;
    socket.receive({ type: "tts_cancel", epoch: 0 });
    expect(playback.port.postMessage.mock.calls).toHaveLength(flushes);
    context.outputTime = 20;
    report(spans); // A delayed worklet message from the cancelled generation.
    expect(readTimedSpeechPlayback("wire-test")?.chars).toBe(5);
    socket.receive({ type: "speech_timing", epoch: 0, line_id: "stale-wire", text: "Stale",
      char_start: 0, char_end: 5, start_ms: 1000, end_ms: 2000 });
    expect(readTimedSpeechPlayback("stale-wire")).toBeNull();
    socket.receive({ type: "audio_closed" });
    await client.disconnect();
    const reconnecting = client.connect();
    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(2));
    const freshSocket = FakeWebSocket.instances[1];
    freshSocket.open();
    freshSocket.receive({ type: "audio_ready", webrtc_answer_sdp: "new-answer",
      output_transport: "timed_pcm", output_sample_rate: 24000 });
    await reconnecting;
    freshSocket.receive({ type: "speech_timing", epoch: 0, line_id: "new-call", text: "New",
      char_start: 0, char_end: 3, start_ms: 0, end_ms: 1000 });
    freshSocket.receive({ type: "audio_timed", epoch: 0, start_ms: 0, end_ms: 1000,
      sample_rate: 24000, audio: Buffer.from(new Int16Array(24000).buffer).toString("base64") });
    expect(readTimedSpeechPlayback("new-call")?.chars).toBe(0);
    const newPlayer = FakeAudioNode.instances.filter(node => node.name === "pcm-playback").at(-1)!;
    expect(newPlayer.port.postMessage.mock.calls.some(([m]) => m.type === "pcm")).toBe(true);
    freshSocket.receive({ type: "audio_closed" });
    await client.disconnect();
  });
  it("does not consume the media deadline while microphone permission is pending", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("RTCPeerConnection", FakePeerConnection);
    vi.stubGlobal("Audio", class { play = async () => undefined; pause = () => undefined; });
    const transport = new RealtimeWebRtcTransport();
    try {
      await transport.createOffer({ getAudioTracks: () => [] } as unknown as MediaStream);
      await vi.advanceTimersByTimeAsync(30_000);
      await expect(transport.applyAnswer("answer")).resolves.toBeUndefined();
    } finally {
      transport.close();
      vi.useRealTimers();
    }
  });

  it("uses audio-only subscription SDP and releases microphone audio only after the peer connects", async () => {
    installVoiceBrowserFakes();
    vi.stubGlobal("Audio", class { play = async () => undefined; pause = () => undefined; });
    const client = new RealtimeAudioClient({}, {
      browserAudio: true, requiresWebRtcOffer: true, webRtcStartEventRequired: false,
    });
    const connecting = client.connect();
    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];
    const peer = FakePeerConnection.instances[0];
    const gate = FakeAudioNode.instances.find(node => node.name === "pcm-startup")!;
    expect(peer.createDataChannel).not.toHaveBeenCalled();
    expect(peer.addTrack.mock.calls[0][0]).toBe("buffered-track");
    peer.setRemoteDescription = vi.fn(async () => undefined);
    socket.open();
    socket.receive({ type: "audio_ready", sound_effects: true, requires_webrtc_answer: true, webrtc_answer_sdp: "answer" });
    await Promise.resolve();
    expect(gate.port.postMessage.mock.calls.some(([message]) => message.type === "start")).toBe(false);
    expect(FakeAudioContext.voices).toHaveLength(0);
    peer.connectionState = "connected";
    peer.events.dispatchEvent(new Event("connectionstatechange"));
    await connecting;
    expect(gate.port.postMessage.mock.calls.filter(([message]) => message.type === "start")).toHaveLength(1);
    expect(FakeAudioContext.voices).toHaveLength(2);
    socket.receive({ type: "audio_closed" });
    await client.disconnect();
  });

  it.each(["failed", "closed"] as const)("rejects a subscription peer that becomes %s", async (state) => {
    vi.stubGlobal("RTCPeerConnection", FakePeerConnection);
    const transport = new RealtimeWebRtcTransport();
    await transport.createOffer(undefined, false);
    const peer = FakePeerConnection.instances[0];
    peer.setRemoteDescription = vi.fn(async () => undefined);
    const answering = transport.applyAnswer("answer");
    const rejected = expect(answering).rejects.toThrow("media connection failed");
    peer.connectionState = state;
    peer.events.dispatchEvent(new Event("connectionstatechange"));
    await rejected;
    transport.close();
  });

  it("bounds audio-only startup and removes its connection listener on timeout", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("RTCPeerConnection", FakePeerConnection);
    const transport = new RealtimeWebRtcTransport();
    await transport.createOffer(undefined, false, 1000);
    const peer = FakePeerConnection.instances[0];
    peer.setRemoteDescription = vi.fn(async () => undefined);
    const answering = transport.applyAnswer("answer");
    const rejected = expect(answering).rejects.toThrow("GPT-Live did not start");
    await vi.advanceTimersByTimeAsync(1000);
    await rejected;
    expect(peer.removeEventListener).toHaveBeenCalledWith("connectionstatechange", expect.any(Function));
    transport.close();
    vi.useRealTimers();
  });

  it("cancels audio-only startup immediately when the user closes the transport", async () => {
    vi.stubGlobal("RTCPeerConnection", FakePeerConnection);
    const transport = new RealtimeWebRtcTransport();
    await transport.createOffer(undefined, false);
    FakePeerConnection.instances[0].setRemoteDescription = vi.fn(async () => undefined);
    const answering = transport.applyAnswer("answer");
    const rejected = expect(answering).rejects.toThrow(/closed/);
    await Promise.resolve();
    transport.close();
    await rejected;
  });

  it("negotiates an early answer but releases no input until the control connection is ready", async () => {
    installVoiceBrowserFakes();
    vi.stubGlobal("Audio", class { play = async () => undefined; pause = () => undefined; });
    const client = new RealtimeAudioClient({}, { browserAudio: true, requiresWebRtcOffer: true });
    let connected = false;
    const connecting = client.connect().then(() => { connected = true; });
    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];
    const peer = FakePeerConnection.instances[0];
    const gate = FakeAudioNode.instances.find(node => node.name === "pcm-startup")!;
    socket.open();
    socket.receive({ type: "input_prefix", sample_rate: 48000, audio: "AQACAA==" });
    socket.receive({ type: "audio_transport", webrtc_answer_sdp: "early-answer" });
    await vi.waitFor(() => expect(peer.remoteDescriptions).toHaveLength(1));
    expect(connected).toBe(false);
    expect(gate.port.postMessage.mock.calls.some(([m]) => m.type === "start")).toBe(false);
    socket.receive({ type: "audio_ready", requires_webrtc_answer: true, webrtc_answer_sdp: "early-answer" });
    await connecting;
    expect(peer.remoteDescriptions).toHaveLength(1);
    expect(gate.port.postMessage.mock.calls.filter(([m]) => m.type === "start")).toHaveLength(1);
    socket.receive({ type: "audio_closed" });
    await client.disconnect();
  });

  it("gathers ICE for a silent browser track while microphone permission is pending", async () => {
    const { track } = installVoiceBrowserFakes();
    vi.stubGlobal("Audio", class { play = async () => undefined; pause = () => undefined; });
    let release!: (stream: MediaStream) => void;
    vi.mocked(navigator.mediaDevices.getUserMedia).mockImplementation(() => new Promise(resolve => { release = resolve; }));
    const client = new RealtimeAudioClient({}, { browserAudio: true, requiresWebRtcOffer: true });
    const connecting = client.connect();
    await vi.waitFor(() => expect(FakePeerConnection.instances[0]?.localDescription).toBeTruthy());
    expect(FakePeerConnection.instances[0].addTrack.mock.calls[0][0]).toBe("buffered-track");
    expect(FakeAudioNode.instances.some(node => node.name === "pcm-startup")).toBe(false);
    expect(FakeWebSocket.instances).toHaveLength(0);
    release({ getTracks: () => [track], getAudioTracks: () => [track] } as unknown as MediaStream);
    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];
    socket.open();
    socket.receive({ type: "audio_ready", requires_webrtc_answer: true, webrtc_answer_sdp: "answer" });
    await connecting;
    socket.receive({ type: "audio_closed" });
    await client.disconnect();
  });

  it.each([true, false])("discards all retained input when muted during startup (RTP=%s)", async (webrtc) => {
    installVoiceBrowserFakes();
    vi.stubGlobal("Audio", class { play = async () => undefined; pause = () => undefined; });
    const client = new RealtimeAudioClient({}, { browserAudio: true, requiresWebRtcOffer: webrtc });
    const connecting = client.connect();
    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];
    socket.open();
    const capture = FakeAudioNode.instances.find(node => node.name === "pcm-capture")!;
    capture.port.onmessage!({ data: new Int16Array([3, 4]).buffer } as MessageEvent);
    socket.receive({ type: "input_prefix", sample_rate: 48000, audio: "AQACAA==" });
    socket.receive({ type: "input_mute", muted: true });
    socket.receive({ type: "audio_ready", input_muted: true, requires_webrtc_answer: webrtc,
      ...(webrtc ? { webrtc_answer_sdp: "answer" } : {}) });
    await connecting;
    const gate = FakeAudioNode.instances.find(node => node.name === "pcm-startup");
    expect(gate?.port.postMessage.mock.calls.some(([m]) => m.type === "start") ?? false).toBe(false);
    expect(socket.sent.filter(v => v instanceof ArrayBuffer)).toHaveLength(0);
    socket.receive({ type: "input_mute", muted: false });
    if (gate) expect(gate.port.postMessage).toHaveBeenLastCalledWith({ type: "start" });
    socket.receive({ type: "audio_closed" });
    await client.disconnect();
  });

  it("cannot release audio after cancellation during the media handshake", async () => {
    const { track } = installVoiceBrowserFakes();
    vi.stubGlobal("Audio", class { play = async () => undefined; pause = () => undefined; });
    const client = new RealtimeAudioClient({}, { browserAudio: true, requiresWebRtcOffer: true });
    const result = client.connect().catch(error => error);
    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];
    const peer = FakePeerConnection.instances[0];
    const gate = FakeAudioNode.instances.find(node => node.name === "pcm-startup")!;
    peer.setRemoteDescription = vi.fn(async () => undefined);
    socket.open();
    socket.receive({ type: "audio_transport", webrtc_answer_sdp: "answer" });
    socket.receive({ type: "audio_ready", requires_webrtc_answer: true, webrtc_answer_sdp: "answer" });
    socket.receive({ type: "audio_closed" });
    await client.disconnect();
    expect(await result).toBeInstanceOf(Error);
    peer.channel.dispatchEvent(new MessageEvent("message", { data: '{"type":"session.started"}' }));
    expect(gate.port.postMessage.mock.calls.some(([m]) => m.type === "start")).toBe(false);
    expect(track.stop).toHaveBeenCalled();
    expect(peer.close).toHaveBeenCalled();
  });

  it("uses buffered RTP and forwards input levels before the media handshake completes", async () => {
    installVoiceBrowserFakes();
    vi.stubGlobal("Audio", class { play = async () => undefined; pause = () => undefined; });
    const client = new RealtimeAudioClient({}, { browserAudio: true, requiresWebRtcOffer: true });
    const connecting = client.connect();
    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];
    const peer = FakePeerConnection.instances[0];
    const gate = FakeAudioNode.instances.find(node => node.name === "pcm-startup")!;
    const capture = FakeAudioNode.instances.find(node => node.name === "pcm-capture")!;
    expect(peer.addTrack.mock.calls[0][0]).toBe("buffered-track");
    expect(gate.port.postMessage).not.toHaveBeenCalled();
    socket.open();
    expect(JSON.parse(String(socket.sent[0])).capture_started_at_ms).toBeGreaterThan(0);
    socket.receive({ type: "input_prefix", sample_rate: 48000, audio: "AQACAA==" });
    expect(gate.port.postMessage.mock.calls[0][0].type).toBe("prefix");
    let release!: () => void;
    peer.setRemoteDescription = vi.fn(async () => { await new Promise<void>(resolve => { release = resolve; }); });
    socket.receive({ type: "audio_ready", requires_webrtc_answer: true, webrtc_answer_sdp: "answer" });
    capture.port.onmessage!({ data: { type: "level", rms: 0.2 } } as MessageEvent);
    expect(socket.sent.map(v => JSON.parse(String(v))).at(-1)).toMatchObject({ type: "media_levels", input_active: true });
    expect(gate.port.postMessage.mock.calls.some(([m]) => m.type === "start")).toBe(false);
    peer.channel.dispatchEvent(new MessageEvent("message", { data: '{"type":"session.started"}' }));
    release();
    await connecting;
    expect(gate.port.postMessage.mock.calls.filter(([m]) => m.type === "start")).toHaveLength(1);
    socket.receive({ type: "reconnecting" });
    expect(gate.port.postMessage).toHaveBeenLastCalledWith({ type: "suspend" });
    socket.receive({ type: "audio_closed" });
    await client.disconnect();
  });

  it.each([true, false])("silences the microphone track while Jarvis's mute is on (WebRTC=%s)", async (webrtc) => {
    const { track } = installVoiceBrowserFakes() as { track: { stop: () => void; enabled?: boolean } };
    vi.stubGlobal("Audio", class { play = async () => undefined; pause = () => undefined; });
    const client = new RealtimeAudioClient({}, { browserAudio: true, requiresWebRtcOffer: webrtc });
    const connecting = client.connect();
    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];
    socket.open();
    socket.receive({
      type: "audio_ready",
      requires_webrtc_answer: webrtc,
      input_muted: true,
      ...(webrtc ? { webrtc_answer_sdp: "answer" } : {}),
    });
    expect(track.enabled).toBe(false);
    await connecting;
    socket.receive({ type: "input_mute", muted: false });
    expect(track.enabled).toBe(true);
    socket.receive({ type: "input_mute", muted: true });
    expect(track.enabled).toBe(false);
    socket.receive({ type: "input_mute", muted: false });
    socket.receive({ type: "audio_stopping" });
    expect(track.enabled).toBe(false);
    // An unmute after the call began closing never reopens the microphone.
    socket.receive({ type: "input_mute", muted: false });
    expect(track.enabled).toBe(false);
    socket.receive({ type: "audio_closed" });
    await client.disconnect();
  });

  it("precedes browser PCM with native wake audio on the local/Gemini path", async () => {
    installVoiceBrowserFakes();
    const client = new RealtimeAudioClient({}, { browserAudio: true });
    const connecting = client.connect();
    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];
    socket.open();
    const capture = FakeAudioNode.instances.find(node => node.name === "pcm-capture")!;
    capture.port.onmessage!({ data: new Int16Array([3, 4]).buffer } as MessageEvent);
    socket.receive({ type: "input_prefix", sample_rate: 48000, audio: "AQACAA==" });
    socket.receive({ type: "audio_ready", requires_webrtc_answer: false });
    await connecting;
    expect(socket.sent.filter(v => v instanceof ArrayBuffer).map(v => [...new Int16Array(v as ArrayBuffer)])).toEqual([[1, 2], [3, 4]]);
    socket.receive({ type: "audio_closed" });
    await client.disconnect();
  });

  it.each([true, false])("forwards measured microphone/output levels and releases them on close (WebRTC=%s)", async (webrtc) => {
    installVoiceBrowserFakes();
    vi.stubGlobal("Audio", class { play = async () => undefined; pause = () => undefined; });
    const onPlaybackState = vi.fn();
    const onStatus = vi.fn();
    const onAudio = vi.fn();
    const client = new RealtimeAudioClient({ onPlaybackState, onStatus, onAudio }, { browserAudio: true, requiresWebRtcOffer: webrtc });
    const connecting = client.connect();
    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];
    socket.open();
    socket.receive({ type: "audio_ready", requires_webrtc_answer: webrtc, ...(webrtc ? { webrtc_answer_sdp: "answer" } : {}) });
    await connecting;
    if (webrtc) {
      FakePeerConnection.instances[0].ontrack?.({ streams: [{}] } as unknown as RTCTrackEvent);
    } else {
      socket.receiveBinary(new Int16Array(4800).buffer);
      expect(onAudio).not.toHaveBeenCalled();
    }
    const capture = FakeAudioNode.instances.find(node => node.name === "pcm-capture")!;
    const output = FakeAudioNode.instances.find(node => node.name === (webrtc ? "pcm-level" : "pcm-playback"))!;
    const emit = (node: FakeAudioNode, rms: number) => node.port.onmessage?.({ data: { type: "level", rms } } as MessageEvent);
    const frames = () => socket.sent.map(value => JSON.parse(String(value))).filter(value => value.type === "media_levels");
    const tick = performance.now() + 100;
    const clock = vi.spyOn(performance, "now").mockReturnValue(tick);
    try {
      emit(capture, 0.12);
      expect(frames().at(-1)).toMatchObject({ input_active: true, playback_active: false });
      expect(frames().at(-1).input_level).toBeGreaterThan(0);
      emit(output, 0.1);
      expect(onPlaybackState).toHaveBeenLastCalledWith(true);
      expect(frames().at(-1).output_level).toBeGreaterThan(0);
      expect(frames().at(-1).playback_active).toBe(true);
      const count = frames().length;
      for (let i = 0; i < 20; i++) emit(capture, 0.12);
      expect(frames()).toHaveLength(count);
      clock.mockReturnValue(tick + 101);
      emit(capture, 0.12);
      expect(frames()).toHaveLength(count + 1);
      for (let i = 0; i < 20; i++) emit(output, 0);
      expect(onPlaybackState).toHaveBeenLastCalledWith(true);
      emit(output, 0);
      expect(onPlaybackState).toHaveBeenLastCalledWith(false);
      expect(frames().at(-1).playback_active).toBe(false);
      // The backend decides whether silence means thinking or listening.
      expect(onStatus.mock.calls.some(([status]) => status === "listening")).toBe(false);
      const beforeStall = frames().length;
      socket.bufferedAmount = 2048;
      clock.mockReturnValue(tick + 201);
      emit(output, 0.1);
      expect(frames()).toHaveLength(beforeStall);
      socket.bufferedAmount = 0;
      clock.mockReturnValue(tick + 202);
      emit(capture, 0.12);
      expect(frames()).toHaveLength(beforeStall + 1);
      expect(frames().at(-1).playback_active).toBe(true);
      const staleMeter = output.port.onmessage!;
      const closing = client.disconnect();
      socket.receive({ type: "audio_closed" });
      await closing;
      const sent = socket.sent.length;
      if (webrtc) staleMeter({ data: { type: "level", rms: 0.3 } } as MessageEvent);
      expect(socket.sent).toHaveLength(sent);
      expect(onPlaybackState).toHaveBeenLastCalledWith(false);
    } finally {
      clock.mockRestore();
    }
  });

  beforeEach(() => {
    resetConnectBudgetForTests();
    FakeAudioContext.voices = [];
    vi.stubGlobal("window", {
      location: { protocol: "https:", host: "app.example", hostname: "app.example" },
      __JARVIS_TOKEN: "tok",
      isSecureContext: true,
      setTimeout: globalThis.setTimeout,
      clearTimeout: globalThis.clearTimeout,
    });
    FakePeerConnection.instances = [];
    FakePeerConnection.offerSdp = "offer-sdp";
    FakeWebSocket.instances = [];
    FakeAudioNode.instances = [];
    wsFakes.mintWsTicket.mockClear();
  });

  afterEach(() => { resetConnectBudgetForTests(); vi.unstubAllGlobals(); });

  it("drops muted PCM, rejects stale state and preserves mute through readiness", async () => {
    const { track } = installVoiceBrowserFakes();
    const client = new RealtimeAudioClient();
    const connecting = client.connect();
    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];
    socket.open();
    socket.receive({ type: "output_state", muted: true, volume: 0.4, revision: 2 });
    socket.receive({ type: "audio_ready", output_muted: true, output_volume: 0.4, output_revision: 2 });
    await connecting;
    const playback = FakeAudioNode.instances.find(node => node.name === "pcm-playback")!;
    playback.port.postMessage.mockClear();
    socket.receiveBinary(new Int16Array([1000, 1000]).buffer);
    socket.receive({ type: "output_state", muted: false, volume: 0.4, revision: 1 });
    socket.receiveBinary(new Int16Array([2000, 2000]).buffer);
    expect(playback.port.postMessage).not.toHaveBeenCalled();
    socket.receive({ type: "output_state", muted: false, volume: 0.4, revision: 3 });
    socket.receiveBinary(new Int16Array([3000, 3000]).buffer);
    expect(playback.port.postMessage.mock.calls.filter(([m]) => m.type === "pcm")).toHaveLength(1);
    expect(track.stop).not.toHaveBeenCalled();
    expect(socket.sent.filter(x => typeof x === "string" && x.includes("audio_stop"))).toEqual([]);
    socket.receive({ type: "audio_stopping" });
    socket.receive({ type: "output_state", muted: false, volume: 0.4, revision: 4 });
    socket.receiveBinary(new Int16Array([4000, 4000]).buffer);
    expect(playback.port.postMessage.mock.calls.filter(([m]) => m.type === "pcm")).toHaveLength(1);
    await client.disconnect();
  });

  it("mutes live WebRTC output and newly created media without pausing its timeline", async () => {
    vi.stubGlobal("RTCPeerConnection", FakePeerConnection);
    const players: Array<{ muted: boolean; volume: number; pause: ReturnType<typeof vi.fn> }> = [];
    vi.stubGlobal("Audio", class {
      muted = false; volume = 1;
      play = vi.fn(async () => undefined);
      pause = vi.fn();
      constructor() { players.push(this); }
    });
    const transport = new RealtimeWebRtcTransport();
    const stream = { getAudioTracks: () => [] } as unknown as MediaStream;
    await transport.createOffer(stream, false);
    expect(players[0].muted).toBe(true);
    transport.setOutputState(false, 0.4);
    expect(players[0]).toMatchObject({ muted: false, volume: 0.4 });
    transport.setOutputState(true, 0.7);
    expect(players[0].pause).not.toHaveBeenCalled();
    await transport.createOffer(stream, false);
    expect(players[1]).toMatchObject({ muted: true, volume: 0.7 });
    transport.setOutputState(false, 0.7);
    expect(players[1].muted).toBe(false);
    transport.close();
  });

  it("does not replay microphone frames captured during a reconnect", async () => {
    installVoiceBrowserFakes();
    let readyCount = 0;
    const client = new RealtimeAudioClient({ onStatus: status => {
      if (status === "audio_ready") readyCount += 1;
    } });
    const connecting = client.connect();
    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];
    socket.open();
    socket.receive({ type: "audio_ready", output_sample_rate: 24000 });
    await connecting;
    const capture = FakeAudioNode.instances.find(node => node.port.onmessage)!;
    const frame = (value: number) => capture.port.onmessage!({ data: new Uint8Array([value]).buffer } as MessageEvent);
    frame(1);
    socket.receive({ type: "reconnecting" });
    frame(2);
    socket.receive({ type: "audio_ready", output_sample_rate: 24000 });
    await vi.waitFor(() => expect(readyCount).toBe(2));
    frame(3);
    expect(socket.sent.filter(x => x instanceof ArrayBuffer).map(x => new Uint8Array(x as ArrayBuffer)[0])).toEqual([1, 3]);
    await client.disconnect();
  });

  it("preserves the negotiated peer when only the subscription sideband reconnects", async () => {
    installVoiceBrowserFakes();
    let readyCount = 0;
    const client = new RealtimeAudioClient({ onStatus: status => {
      if (status === "audio_ready") readyCount += 1;
    } }, { requiresWebRtcOffer: true, webRtcStartEventRequired: false });
    const connecting = client.connect();
    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];
    socket.open();
    socket.receive({ type: "audio_ready", requires_webrtc_answer: true, webrtc_answer_sdp: "answer" });
    await connecting;
    const peer = FakePeerConnection.instances[0];
    socket.receive({ type: "reconnecting" });
    socket.receive({ type: "audio_ready", reconnected: true, reuse_webrtc: true });
    await vi.waitFor(() => expect(readyCount).toBe(2));
    expect(FakePeerConnection.instances).toHaveLength(1);
    expect(peer.createOffer).toHaveBeenCalledOnce();
    expect(peer.setRemoteDescription).toHaveBeenCalledOnce();
    expect(peer.close).not.toHaveBeenCalled();
    expect(socket.close).not.toHaveBeenCalled();
    socket.receive({ type: "audio_closed" });
    await client.disconnect();
  });

  it("correlates a replacement WebRTC offer with the server request", async () => {
    installVoiceBrowserFakes();
    const client = new RealtimeAudioClient();
    const transport = { createOffer: vi.fn(async () => "v=0\r\nreplacement"),
      applyAnswer: vi.fn(async () => undefined), close: vi.fn(), muteOutput: vi.fn(), setOutputState: vi.fn() };
    Object.assign(client, { webRtcTransport: transport });
    const connecting = client.connect();
    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];
    socket.open();
    socket.receive({ type: "audio_ready", output_sample_rate: 24000 });
    await connecting;
    socket.receive({ type: "reconnecting" });
    socket.receive({ type: "reconnect_offer", request_id: "resume-1" });
    await vi.waitFor(() => expect(socket.sent.some(x => typeof x === "string" && x.includes("resume-1"))).toBe(true));
    const response = socket.sent.filter(x => typeof x === "string").map(x => JSON.parse(x as string)).find(x => x.type === "reconnect_offer");
    expect(response).toEqual({ type: "reconnect_offer", request_id: "resume-1", sdp: "v=0\r\nreplacement" });
    await client.disconnect();
  });

  it("keeps the media peer when only the Live sideband reconnects", async () => {
    installVoiceBrowserFakes();
    const transport = { createOffer: vi.fn(async () => "offer"),
      applyAnswer: vi.fn(async () => undefined), close: vi.fn(), muteOutput: vi.fn(), setOutputState: vi.fn() };
    let readyCount = 0;
    const client = new RealtimeAudioClient({ onStatus: status => {
      if (status === "audio_ready") readyCount += 1;
    } });
    Object.assign(client, { webRtcTransport: transport });
    const connecting = client.connect();
    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];
    socket.open();
    socket.receive({ type: "audio_ready", requires_webrtc_answer: true,
      webrtc_answer_sdp: "answer", output_sample_rate: 24000 });
    await connecting;
    transport.close.mockClear();
    socket.receive({ type: "reconnecting" });
    socket.receive({ type: "audio_ready", reuse_webrtc: true, output_sample_rate: 24000 });
    await vi.waitFor(() => expect(readyCount).toBe(2));
    expect(transport.close).not.toHaveBeenCalled();
    expect(transport.applyAnswer).toHaveBeenCalledTimes(1);
    await client.disconnect();
  });

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
    const offerSdp = "v=0\r\no=- 123 1 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\n";
    FakePeerConnection.offerSdp = offerSdp;
    const client = new RealtimeAudioClient({}, { requiresWebRtcOffer: true });
    const connecting = client.connect();

    await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const socket = FakeWebSocket.instances[0];
    socket.open();
    const start = JSON.parse(String(socket.sent[0])) as Record<string, unknown>;
    expect(start).toMatchObject({
      type: "audio_start",
      sample_rate: 48_000,
      webrtc_offer_sdp: offerSdp,
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
    releaseCapture?.({ getTracks: () => [track], getAudioTracks: () => [track] } as unknown as MediaStream);
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

  it("dispatches local startup before a 350 ms native peer stall without bypassing microphone permission", async () => {
    const { track } = installVoiceBrowserFakes();
    vi.stubGlobal("Audio", class { play = async () => undefined; pause = () => undefined; });
    let elapsed = 0, microphoneRequestedAt = -1;
    const anchor = performance.now();
    const clock = vi.spyOn(performance, "now").mockImplementation(() => anchor + elapsed);
    let releaseCapture!: (stream: MediaStream) => void;
    vi.mocked(navigator.mediaDevices.getUserMedia).mockImplementation(() => {
      microphoneRequestedAt = elapsed;
      return new Promise<MediaStream>(resolve => { releaseCapture = resolve; });
    });
    vi.stubGlobal("RTCPeerConnection", class extends FakePeerConnection {
      constructor() {
        expect(FakeAudioContext.instances.at(-1)!.resume).toHaveBeenCalledOnce();
        expect(wsFakes.mintWsTicket).toHaveBeenCalledOnce();
        elapsed += 350;
        super();
      }
    });
    const client = new RealtimeAudioClient({}, { browserAudio: true, requiresWebRtcOffer: true });
    const connecting = client.connect();
    try {
      expect(microphoneRequestedAt).toBe(0);
      expect(FakeWebSocket.instances).toHaveLength(0);
      releaseCapture({ getTracks: () => [track], getAudioTracks: () => [track] } as unknown as MediaStream);
      await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
      const socket = FakeWebSocket.instances[0];
      FakeAudioNode.instances.find(node => node.name === "pcm-capture")!
        .port.onmessage?.({ data: new ArrayBuffer(2) } as MessageEvent);
      socket.open();
      const batches = socket.sent.map(frame => JSON.parse(String(frame)))
        .filter(frame => frame.type === "startup_timing").map(frame => frame.marks_ms);
      expect(batches.length).toBeGreaterThan(1);
      expect(batches.every(batch => Object.keys(batch).length <= 8)).toBe(true);
      const marks = Object.assign({}, ...batches);
      expect(marks.local_requests_dispatched).toBe(0);
      expect(marks.peer_created).toBe(350);
      expect(marks.first_capture_frame).toBe(350);
      socket.receive({ type: "audio_ready", webrtc_answer_sdp: "answer" });
      await connecting;
      socket.close();
    } finally {
      await client.disconnect();
      clock.mockRestore();
    }
  });

  it("releases a failed start's late microphone grant while a replacement start owns its own capture", async () => {
    const { track } = installVoiceBrowserFakes();
    vi.stubGlobal("Audio", class { play = async () => undefined; pause = () => undefined; });
    const replacementTrack = { stop: vi.fn() };
    const grants: Array<(stream: MediaStream) => void> = [];
    vi.mocked(navigator.mediaDevices.getUserMedia).mockImplementation(
      () => new Promise<MediaStream>(resolve => { grants.push(resolve); }),
    );
    let failDestination = true;
    vi.stubGlobal("AudioContext", class extends FakeAudioContext {
      constructor() {
        super();
        if (failDestination) {
          failDestination = false;
          this.createMediaStreamDestination.mockImplementation(() => { throw new Error("Audio destination unavailable"); });
        }
      }
    });
    const client = new RealtimeAudioClient({}, { browserAudio: true, requiresWebRtcOffer: true });
    await expect(client.connect()).rejects.toThrow("Audio destination unavailable");
    expect(FakeAudioContext.instances.at(-1)!.close).toHaveBeenCalledOnce();
    const replacement = client.connect();
    try {
      expect(grants).toHaveLength(2);
      expect(FakeWebSocket.instances).toHaveLength(0);
      grants[0]({ getTracks: () => [track], getAudioTracks: () => [track] } as unknown as MediaStream);
      await vi.waitFor(() => expect(track.stop).toHaveBeenCalledOnce());
      expect(replacementTrack.stop).not.toHaveBeenCalled();
      grants[1]({ getTracks: () => [replacementTrack], getAudioTracks: () => [replacementTrack] } as unknown as MediaStream);
      await vi.waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
      const socket = FakeWebSocket.instances[0];
      socket.open();
      socket.receive({ type: "audio_ready", webrtc_answer_sdp: "answer" });
      await replacement;
      expect(replacementTrack.stop).not.toHaveBeenCalled();
      socket.close();
    } finally { await client.disconnect(); }
    expect(replacementTrack.stop).toHaveBeenCalledOnce();
  });

  it("settles a cancelled connection-budget wait and reconnects the same client", async () => {
    installVoiceBrowserFakes();
    vi.useFakeTimers();
    Object.assign(window, { setTimeout: globalThis.setTimeout, clearTimeout: globalThis.clearTimeout });
    resetConnectBudgetForTests();
    const client = new RealtimeAudioClient();
    try {
      for (let i = 0; i < BURST; i++) requestConnect(() => undefined);
      await vi.advanceTimersByTimeAsync(0);
      const first = client.connect().catch((error: unknown) => error);
      await vi.advanceTimersByTimeAsync(0);
      expect(FakeAudioNode.instances.some(node => node.name === "pcm-capture")).toBe(true);
      expect(FakeWebSocket.instances).toHaveLength(0);
      await client.disconnect();
      await expect(first).resolves.toMatchObject({ message: "Voice start cancelled" });
      const replacement = client.connect();
      await vi.advanceTimersByTimeAsync(200);
      expect(FakeWebSocket.instances).toHaveLength(1);
      const socket = FakeWebSocket.instances[0];
      socket.open();
      socket.receive({ type: "audio_ready", output_sample_rate: 24000 });
      await replacement;
      socket.close();
    } finally {
      await client.disconnect();
      resetConnectBudgetForTests();
      vi.useRealTimers();
    }
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
