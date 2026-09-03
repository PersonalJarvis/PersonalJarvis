import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { QUEUE_MAX, ROOM_IDLE_MS, TALK_TIMEOUT_MS, pairKey, roomKey } from "./conversation";
import {
  clearSpeechPoses,
  lineLifeMs,
  orderedTalks,
  setSpeechPose,
  speechPose,
  useConversationStore,
  type IncomingMessage,
  type IncomingRoom,
} from "./conversationStore";

const KNOWN = new Set(["scout", "archivist", "runner"]);

let nextSeq = 1;

function message(over: Partial<IncomingMessage> = {}): IncomingMessage {
  const seq = over.seq ?? nextSeq++;
  const base: IncomingMessage = {
    eventId: `evt-${seq}`,
    seq,
    msgType: "SAY",
    fromAgent: "scout",
    toAgent: "archivist",
    roomId: "",
    roomRound: 0,
    text: "Found the manifest.",
    textChars: 19,
    truncated: false,
  };
  return { ...base, ...over, seq };
}

function room(over: Partial<IncomingRoom> = {}): IncomingRoom {
  return {
    roomId: "r1",
    phase: "open",
    members: ["scout", "archivist"],
    topic: "Plan the week",
    reason: "",
    maxRounds: 3,
    ...over,
  };
}

function send(m: IncomingMessage): void {
  useConversationStore.getState().noteMessage(m, KNOWN);
}

beforeEach(() => {
  vi.useFakeTimers();
  nextSeq = 1;
  useConversationStore.setState({ talks: {}, room: null, lastSeq: 0 });
  clearSpeechPoses();
});

afterEach(() => {
  useConversationStore.getState().reset();
  vi.useRealTimers();
});

describe("one conversation per pair", () => {
  it("opens a conversation on the first line", () => {
    send(message());
    const talks = useConversationStore.getState().talks;
    const key = pairKey("scout", "archivist");
    expect(Object.keys(talks)).toEqual([key]);
    expect(talks[key].speakerId).toBe("scout");
    expect(talks[key].partnerId).toBe("archivist");
    expect(talks[key].line?.text).toBe("Found the manifest.");
    expect(talks[key].kind).toBeNull(); // the scene decides from the live distance
  });

  it("lands a reply on the SAME conversation instead of opening a second", () => {
    send(message());
    send(message({ fromAgent: "archivist", toAgent: "scout", msgType: "ANSWER" }));

    const talks = useConversationStore.getState().talks;
    expect(Object.keys(talks)).toHaveLength(1);
    const talk = talks[pairKey("scout", "archivist")];
    expect(talk.queued).toHaveLength(1);
    expect(talk.queued[0].speakerId).toBe("archivist");
    expect(talk.queued[0].kind).toBe("answer");
  });

  it("swaps the speaker when the queue is promoted", () => {
    send(message());
    send(message({ fromAgent: "archivist", toAgent: "scout", msgType: "ANSWER" }));
    const key = pairKey("scout", "archivist");

    useConversationStore.getState().advance(key);
    const talk = useConversationStore.getState().talks[key];
    expect(talk.speakerId).toBe("archivist");
    expect(talk.queued).toHaveLength(0);
  });

  it("empties the bubble when nothing is queued, and keeps the conversation", () => {
    send(message());
    const key = pairKey("scout", "archivist");
    useConversationStore.getState().advance(key);
    expect(useConversationStore.getState().talks[key].line).toBeNull();
  });
});

describe("a burst", () => {
  it("queues up to the cap and digests the rest", () => {
    for (let i = 0; i < QUEUE_MAX + 4; i++) send(message({ text: `line ${i}` }));

    const talk = useConversationStore.getState().talks[pairKey("scout", "archivist")];
    expect(talk.queued).toHaveLength(QUEUE_MAX);
    expect(talk.skipped).toBe(3); // one on screen + QUEUE_MAX queued + 3 collapsed
  });

  it("drops a frame it has already drawn", () => {
    send(message({ seq: 7 }));
    send(message({ seq: 7, text: "same row again" }));
    const talk = useConversationStore.getState().talks[pairKey("scout", "archivist")];
    expect(talk.queued).toHaveLength(0);
    expect(talk.line?.text).toBe("Found the manifest.");
  });

  it("drops a frame older than the highest it has seen", () => {
    send(message({ seq: 20 }));
    send(message({ seq: 3, text: "a straggler" }));
    const talk = useConversationStore.getState().talks[pairKey("scout", "archivist")];
    expect(talk.queued).toHaveLength(0);
  });
});

describe("who is on the island", () => {
  it("shows a one-sided conversation when the partner has no figure", () => {
    send(message({ toAgent: "user" }));
    const talk = useConversationStore.getState().talks[pairKey("scout", "user")];
    expect(talk).toBeDefined();
    expect(talk.partnerId).toBe("");
    expect(talk.speakerId).toBe("scout");
  });

  it("keeps the receiver's side when the SENDER is off-island", () => {
    send(message({ fromAgent: "scheduler", toAgent: "scout" }));
    const talk = useConversationStore.getState().talks[pairKey("scheduler", "scout")];
    expect(talk).toBeDefined();
    expect(talk.partnerId).toBe("scout");
  });

  it("draws nothing at all when neither side has a figure", () => {
    send(message({ fromAgent: "scheduler", toAgent: "user" }));
    expect(useConversationStore.getState().talks).toEqual({});
    // …but the sequence still advances, so the frame is not replayed.
    expect(useConversationStore.getState().lastSeq).toBeGreaterThan(0);
  });
});

describe("the watchdogs", () => {
  it("closes a conversation nothing has advanced", () => {
    send(message());
    const key = pairKey("scout", "archivist");
    expect(useConversationStore.getState().talks[key]).toBeDefined();

    vi.advanceTimersByTime(TALK_TIMEOUT_MS + 10);
    expect(useConversationStore.getState().talks[key]).toBeUndefined();
  });

  it("frees the figures it was driving when it closes", () => {
    send(message());
    setSpeechPose("scout", { heading: 1, talking: true });
    setSpeechPose("archivist", { heading: 2, talking: false });

    vi.advanceTimersByTime(TALK_TIMEOUT_MS + 10);
    expect(speechPose("scout")).toBeNull();
    expect(speechPose("archivist")).toBeNull();
  });

  it("closes a room nobody has spoken in", () => {
    useConversationStore.getState().noteRoom(room());
    expect(useConversationStore.getState().room?.roomId).toBe("r1");

    vi.advanceTimersByTime(ROOM_IDLE_MS + 10);
    expect(useConversationStore.getState().room).toBeNull();
  });
});

describe("a room", () => {
  it("opens, counts its rounds and settles with a reason", () => {
    const store = useConversationStore.getState();
    store.noteRoom(room());
    expect(useConversationStore.getState().room?.topic).toBe("Plan the week");
    expect(useConversationStore.getState().room?.maxRounds).toBe(3);

    send(message({ toAgent: "", roomId: "r1", roomRound: 2, text: "I will take the mail." }));
    expect(useConversationStore.getState().room?.round).toBe(2);
    expect(useConversationStore.getState().talks[roomKey("r1")]).toBeDefined();

    store.noteRoom(room({ phase: "settle", reason: "round_cap" }));
    expect(useConversationStore.getState().room?.settled).toBe(true);
    expect(useConversationStore.getState().room?.reason).toBe("round_cap");
  });

  it("ignores a settle for a room it is not showing", () => {
    const store = useConversationStore.getState();
    store.noteRoom(room());
    store.noteRoom(room({ roomId: "other", phase: "settle", reason: "silence" }));
    expect(useConversationStore.getState().room?.settled).toBe(false);
  });
});

describe("the helpers the scene reads", () => {
  it("orders conversations oldest first, so the oldest wins a heading", () => {
    send(message());
    vi.advanceTimersByTime(50);
    send(message({ fromAgent: "runner", toAgent: "scout" }));

    const ordered = orderedTalks(useConversationStore.getState().talks);
    expect(ordered).toHaveLength(2);
    expect(ordered[0].startedMs).toBeLessThanOrEqual(ordered[1].startedMs);
  });

  it("gives a line a life proportional to its length", () => {
    send(message({ text: "hi", textChars: 2 }));
    const short = lineLifeMs(useConversationStore.getState().talks[pairKey("scout", "archivist")]);
    useConversationStore.getState().reset();
    send(message({ text: "x".repeat(180), textChars: 180 }));
    const long = lineLifeMs(useConversationStore.getState().talks[pairKey("scout", "archivist")]);
    expect(long).toBeGreaterThan(short);
  });

  it("reports no life for a conversation whose bubble has gone", () => {
    send(message());
    const key = pairKey("scout", "archivist");
    useConversationStore.getState().advance(key);
    expect(lineLifeMs(useConversationStore.getState().talks[key])).toBe(0);
  });
});

describe("reset", () => {
  it("forgets every conversation, pose and timer", () => {
    send(message());
    useConversationStore.getState().noteRoom(room());
    setSpeechPose("scout", { heading: 0, talking: true });

    useConversationStore.getState().reset();
    expect(useConversationStore.getState().talks).toEqual({});
    expect(useConversationStore.getState().room).toBeNull();
    expect(speechPose("scout")).toBeNull();

    // A watchdog that survived reset would fire into an empty store.
    expect(() => vi.advanceTimersByTime(ROOM_IDLE_MS * 2)).not.toThrow();
    expect(useConversationStore.getState().room).toBeNull();
  });
});
