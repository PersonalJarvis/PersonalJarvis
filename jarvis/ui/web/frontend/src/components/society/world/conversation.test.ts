import { describe, expect, it } from "vitest";

import { MSG_TYPES } from "@/lib/societyApi";
import { headingFor } from "./walkerKinematics";
import {
  ARC_BEADS,
  BUBBLE_MAX_S,
  BUBBLE_MIN_S,
  CALL_ORDER,
  MIN_FACE_M,
  NEAR_EXIT_M,
  NEAR_RADIUS_M,
  ROOM_ORDER,
  TALK_ORDER,
  WORLD_MSG_TYPES,
  arcApexFor,
  arcPoint,
  beadAlive,
  beadScale,
  bubbleSeconds,
  classify,
  clamp01,
  driftedApart,
  ease,
  facingBetween,
  hangupSweep,
  isOpenPhase,
  kindOf,
  nextCallPhase,
  nextRoomPhase,
  nextTalkPhase,
  pairKey,
  pingFade,
  pingScale,
  roomKey,
  tangentFacing,
} from "./conversation";

describe("who is talking to whom", () => {
  it("keys a pair the same way round", () => {
    expect(pairKey("scout", "archivist")).toBe(pairKey("archivist", "scout"));
    expect(pairKey("a", "b")).toBe("a|b");
  });

  it("keeps a room's key apart from any pair", () => {
    expect(roomKey("r1")).toBe("room:r1");
    expect(roomKey("r1")).not.toBe(pairKey("room", "r1"));
  });

  it("names the bubble by the board type", () => {
    expect(kindOf("QUERY")).toBe("query");
    expect(kindOf("ANSWER")).toBe("answer");
    expect(kindOf("PROPOSE")).toBe("propose");
    expect(kindOf("SAY")).toBe("say");
    expect(kindOf("nonsense")).toBe("say");
  });
});

describe("near or far", () => {
  it("talks inside the radius and calls outside it", () => {
    expect(classify(0)).toBe("talk");
    expect(classify(NEAR_RADIUS_M - 0.1)).toBe("talk");
    expect(classify(NEAR_RADIUS_M)).toBe("call");
    expect(classify(120)).toBe("call");
  });

  it("holds a running talk through the hysteresis band", () => {
    expect(NEAR_EXIT_M).toBeGreaterThan(NEAR_RADIUS_M);
    expect(driftedApart(NEAR_RADIUS_M + 1)).toBe(false);
    expect(driftedApart(NEAR_EXIT_M + 0.1)).toBe(true);
  });

  it("puts the two closest distinct hubs safely on the far side", () => {
    // The Signal Office and the Desktop hall are mirrored halves of one block
    // template, 28 m apart — the tightest pair the island can produce.
    expect(classify(28)).toBe("call");
    // …and two figures on one stand tile are always well inside.
    expect(classify(0.6)).toBe("talk");
  });
});

describe("how long a line stays up", () => {
  it("is monotonic and clamped", () => {
    expect(bubbleSeconds(0)).toBe(BUBBLE_MIN_S);
    expect(bubbleSeconds(10_000)).toBe(BUBBLE_MAX_S);
    expect(bubbleSeconds(120)).toBeGreaterThan(bubbleSeconds(20));
    expect(bubbleSeconds(-5)).toBe(BUBBLE_MIN_S);
  });

  it("keeps a full 180-character preview readable", () => {
    const s = bubbleSeconds(180);
    expect(s).toBeGreaterThanOrEqual(BUBBLE_MIN_S);
    expect(s).toBeLessThanOrEqual(BUBBLE_MAX_S);
  });
});

describe("the phase clocks", () => {
  it("walks each order and stops at done", () => {
    expect(TALK_ORDER[TALK_ORDER.length - 1]).toBe("done");
    expect(nextTalkPhase("turn")).toBe("speak");
    expect(nextTalkPhase("hold")).toBe("done");
    expect(nextTalkPhase("done")).toBe("done");

    expect(nextCallPhase("dial")).toBe("arc");
    expect(nextCallPhase("hangup")).toBe("done");
    expect(nextCallPhase("done")).toBe("done");
    expect(CALL_ORDER).toContain("arc");

    expect(nextRoomPhase("opening")).toBe("live");
    expect(nextRoomPhase("closing")).toBe("done");
    expect(ROOM_ORDER[0]).toBe("opening");
  });

  it("marks exactly the phases that end on an event, not a clock", () => {
    expect(isOpenPhase("speak")).toBe(true);
    expect(isOpenPhase("live")).toBe(true);
    expect(isOpenPhase("turn")).toBe(false);
    expect(isOpenPhase("hangup")).toBe(false);
  });
});

describe("facing", () => {
  it("agrees with the walker's own +Z convention", () => {
    // This is the assertion that stops a figure talking to the back of a head:
    // the pipeline faces +Z, so the heading is atan2(dx, dz).
    for (const [dx, dz] of [
      [1, 0],
      [0, 1],
      [-1, 0],
      [0, -1],
      [3, -4],
    ] as const) {
      expect(facingBetween(0, 0, dx, dz)).toBeCloseTo(headingFor(dx, dz), 10);
    }
  });

  it("looks straight at the other one", () => {
    expect(facingBetween(0, 0, 0, 5)).toBeCloseTo(0, 10);
    expect(facingBetween(0, 0, 5, 0)).toBeCloseTo(Math.PI / 2, 10);
  });

  it("turns two figures on one tile across each other, not into each other", () => {
    const front = 0.4;
    const a = tangentFacing(front, 1);
    const b = tangentFacing(front, -1);
    expect(Math.abs(a - b)).toBeCloseTo(Math.PI, 10);
    expect(MIN_FACE_M).toBeGreaterThan(0.6); // wider than the lateral offset
  });
});

describe("the signal arc", () => {
  const a = { x: -10, y: 2.7, z: 4 };
  const b = { x: 30, y: 3.9, z: -12 };

  it("starts at one head and ends at the other", () => {
    expect(arcPoint(a, b, 0, 12)).toEqual(a);
    const end = arcPoint(a, b, 1, 12);
    expect(end.x).toBeCloseTo(b.x, 10);
    expect(end.y).toBeCloseTo(b.y, 10);
    expect(end.z).toBeCloseTo(b.z, 10);
  });

  it("actually reaches its apex in the middle", () => {
    const apex = 12;
    const mid = arcPoint(a, b, 0.5, apex);
    expect(mid.y).toBeCloseTo((a.y + b.y) / 2 + apex, 10);
  });

  it("never dips below the lower head", () => {
    for (let t = 0; t <= 1.0001; t += 0.05) {
      expect(arcPoint(a, b, t, 9).y).toBeGreaterThanOrEqual(Math.min(a.y, b.y) - 1e-9);
    }
  });

  it("clamps its rise so it clears the island without a terrain query", () => {
    expect(arcApexFor(0)).toBe(5);
    expect(arcApexFor(28)).toBeCloseTo(7.48, 2);
    expect(arcApexFor(400)).toBe(22);
    // The summit sits at 12.6 m and the town at 1.8 m.
    expect(arcApexFor(120)).toBeGreaterThan(12.6 - 1.8);
  });
});

describe("the beads", () => {
  it("is born only at the sending end", () => {
    expect(beadScale(0, ARC_BEADS, 0)).toBeGreaterThan(0);
    expect(beadScale(ARC_BEADS - 1, ARC_BEADS, 0)).toBe(0);
  });

  it("lights every bead once the signal has landed", () => {
    for (let i = 0; i < ARC_BEADS; i++) {
      expect(beadScale(i, ARC_BEADS, 1)).toBeGreaterThan(0);
    }
  });

  it("stays inside 0..1", () => {
    for (let t = 0; t <= 1.0001; t += 0.1) {
      for (let i = 0; i < ARC_BEADS; i++) {
        const s = beadScale(i, ARC_BEADS, t);
        expect(s).toBeGreaterThanOrEqual(0);
        expect(s).toBeLessThanOrEqual(1);
      }
    }
  });

  it("runs off from the sending end on hang-up", () => {
    expect(hangupSweep(0)).toBe(0);
    expect(hangupSweep(99)).toBe(1);
    expect(beadAlive(0, ARC_BEADS, 0.5)).toBe(false);
    expect(beadAlive(ARC_BEADS - 1, ARC_BEADS, 0.5)).toBe(true);
    expect(beadAlive(ARC_BEADS - 1, ARC_BEADS, 1)).toBe(false);
  });
});

describe("the small curves", () => {
  it("clamps and eases", () => {
    expect(clamp01(-3)).toBe(0);
    expect(clamp01(3)).toBe(1);
    expect(ease(0)).toBe(0);
    expect(ease(1)).toBe(1);
    expect(ease(0.5)).toBeCloseTo(0.5, 10);
  });

  it("expands a ping and fades it as it goes", () => {
    expect(pingScale(0)).toBe(0);
    expect(pingScale(99)).toBe(1);
    expect(pingFade(0)).toBe(1);
    expect(pingFade(99)).toBe(0);
  });
});

describe("the drift guard", () => {
  it("only lists board types the backend can actually produce", () => {
    for (const type of WORLD_MSG_TYPES) {
      expect(MSG_TYPES).toContain(type);
    }
  });

  it("covers every talking type and both room brackets", () => {
    expect(new Set(WORLD_MSG_TYPES)).toEqual(
      new Set(["SAY", "QUERY", "ANSWER", "PROPOSE", "ROOM_OPEN", "ROOM_SETTLE"]),
    );
  });
});
