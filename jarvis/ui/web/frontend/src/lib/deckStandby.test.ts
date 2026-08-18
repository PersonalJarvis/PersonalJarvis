import { describe, expect, test } from "vitest";
import {
  arcPath,
  gatesFor,
  polar,
  resolvePhase,
  reticleSizeFor,
  revealDelayMs,
  ringSizeFor,
  ringTicks,
  type GateInput,
  type PhaseInput,
} from "@/lib/deckStandby";

/**
 * The deck's opening act (2026-08-18): boot while the app comes up, standby
 * until somebody speaks, the board from the first turn on — forward only.
 */

const READY: PhaseInput = {
  connected: true,
  voiceReady: true,
  boardOpen: false,
  turnIndex: 0,
  messageCount: 0,
  voiceEngaged: false,
};

describe("resolvePhase", () => {
  test("boots while the link or the voice stack is still coming up", () => {
    expect(resolvePhase({ ...READY, connected: false, voiceReady: false })).toBe("boot");
    expect(resolvePhase({ ...READY, connected: true, voiceReady: false })).toBe("boot");
    expect(resolvePhase({ ...READY, connected: false, voiceReady: true })).toBe("boot");
  });

  test("stands by once both are up and nobody has spoken", () => {
    expect(resolvePhase(READY)).toBe("standby");
  });

  test("the first turn opens the board", () => {
    expect(resolvePhase({ ...READY, turnIndex: 1 })).toBe("board");
  });

  test("a conversation that already exists is the board, however it started", () => {
    expect(resolvePhase({ ...READY, messageCount: 2 })).toBe("board");
  });

  test("reaching for the voice — a call being set up or live — is the board", () => {
    expect(resolvePhase({ ...READY, voiceEngaged: true })).toBe("board");
  });

  test("opening the board by hand wins, even mid-boot", () => {
    expect(resolvePhase({ ...READY, connected: false, voiceReady: false, boardOpen: true })).toBe("board");
  });

  test("a person mid-conversation is never sent back to the start screen", () => {
    // The link dropped for a moment: the board holds.
    expect(resolvePhase({ ...READY, connected: false, turnIndex: 3 })).toBe("board");
  });
});

const GATES_START: GateInput = {
  connected: false,
  voiceReady: false,
  brainProvider: "",
  wakeEnabled: null,
  settled: false,
};

function state(gates: ReturnType<typeof gatesFor>): string[] {
  return gates.map((g) => `${g.id}:${g.state}`);
}

describe("gatesFor", () => {
  test("everything is pending before the link is up", () => {
    expect(state(gatesFor(GATES_START))).toEqual([
      "link:pending",
      "voice:pending",
      "brain:pending",
      "wake:pending",
    ]);
  });

  test("the gates light in boot order — link, voice, then the facts", () => {
    expect(state(gatesFor({ ...GATES_START, connected: true }))).toEqual([
      "link:ok",
      "voice:pending",
      "brain:pending",
      "wake:pending",
    ]);
    expect(
      state(gatesFor({ ...GATES_START, connected: true, voiceReady: true, brainProvider: "openrouter", wakeEnabled: true })),
    ).toEqual(["link:ok", "voice:ok", "brain:ok", "wake:ok"]);
  });

  test("a brain that is known before the voice is up is ok already", () => {
    expect(state(gatesFor({ ...GATES_START, connected: true, brainProvider: "groq" }))[2]).toBe("brain:ok");
  });

  test("the wake fact waits for the boot: a switched-off wake word is 'off', not pending", () => {
    expect(state(gatesFor({ ...GATES_START, connected: true, wakeEnabled: false }))[3]).toBe("wake:pending");
    expect(state(gatesFor({ ...GATES_START, connected: true, voiceReady: true, wakeEnabled: false }))[3]).toBe(
      "wake:off",
    );
  });

  test("a fact that never arrives is called absent once the dust has settled", () => {
    const up = { ...GATES_START, connected: true, voiceReady: true };
    expect(state(gatesFor(up)).slice(2)).toEqual(["brain:pending", "wake:pending"]);
    expect(state(gatesFor({ ...up, settled: true })).slice(2)).toEqual(["brain:off", "wake:off"]);
  });

  test("settling never marks anything absent while the boot is still on", () => {
    expect(state(gatesFor({ ...GATES_START, connected: true, settled: true })).slice(2)).toEqual([
      "brain:pending",
      "wake:pending",
    ]);
  });
});

describe("ring geometry", () => {
  test("polar puts 0° at twelve o'clock and turns clockwise", () => {
    const [x0, y0] = polar(100, 100, 50, 0);
    expect(x0).toBeCloseTo(100);
    expect(y0).toBeCloseTo(50);
    const [x90, y90] = polar(100, 100, 50, 90);
    expect(x90).toBeCloseTo(150);
    expect(y90).toBeCloseTo(100);
  });

  test("arcPath takes the large-arc flag past 180°", () => {
    expect(arcPath(100, 100, 50, 0, 90)).toBe("M 100 50 A 50 50 0 0 1 150 100");
    expect(arcPath(100, 100, 50, 0, 270)).toMatch(/A 50 50 0 1 1 /);
  });

  test("ticks are weighted every 15° and every 90°", () => {
    const ticks = ringTicks(3);
    expect(ticks).toHaveLength(120);
    expect(ticks.find((t) => t.deg === 0)?.weight).toBe(2);
    expect(ticks.find((t) => t.deg === 15)?.weight).toBe(1);
    expect(ticks.find((t) => t.deg === 3)?.weight).toBe(0);
    expect(ticks.filter((t) => t.weight === 2)).toHaveLength(4);
  });
});

describe("sizing", () => {
  test("the ring fills the shorter side of the stage, within bounds", () => {
    expect(ringSizeFor(0, 0)).toBe(760);
    expect(ringSizeFor(1200, 700)).toBe(660);
    expect(ringSizeFor(400, 900)).toBe(360);
    expect(ringSizeFor(200, 200)).toBe(260);
    expect(ringSizeFor(2000, 2000)).toBe(760);
  });

  test("the reticle is a little over half the ring, within its own bounds", () => {
    expect(reticleSizeFor(660)).toBe(396);
    expect(reticleSizeFor(260)).toBe(200);
    expect(reticleSizeFor(760)).toBe(420);
  });

  test("the board reveals from the centre outward", () => {
    expect(revealDelayMs("centre-top")).toBeLessThan(revealDelayMs("centre-bottom"));
    expect(revealDelayMs("centre-bottom")).toBeLessThan(revealDelayMs("left-top"));
    expect(revealDelayMs("left-top")).toBe(revealDelayMs("right-top"));
    expect(revealDelayMs("left-bottom")).toBeGreaterThan(revealDelayMs("right-top"));
  });
});
