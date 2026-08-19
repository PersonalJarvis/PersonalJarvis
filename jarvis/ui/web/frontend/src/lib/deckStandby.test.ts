import { describe, expect, test } from "vitest";
import {
  arcPath,
  gatesFor,
  polar,
  resolvePhase,
  reticleSizeFor,
  revealDelayMs,
  revealWipeFor,
  ringSizeFor,
  ringTicks,
  CARD_POWER_ON,
  HANDOFF,
  type BoardSlot,
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

  test("every card wipes in away from the orb", () => {
    expect(revealWipeFor("centre-top")).toBe("down");
    expect(revealWipeFor("centre-bottom")).toBe("down");
    expect(revealWipeFor("left-top")).toBe("left");
    expect(revealWipeFor("left-bottom")).toBe("left");
    expect(revealWipeFor("right-top")).toBe("right");
    expect(revealWipeFor("right-bottom")).toBe("right");
  });
});

describe("the hand-off is one launch on one clock", () => {
  const SLOTS: BoardSlot[] = [
    "centre-top",
    "centre-bottom",
    "left-top",
    "right-top",
    "left-bottom",
    "right-bottom",
  ];

  test("the burst leads, the orb travels through it, the cards follow the wave", () => {
    // The first card's frame starts drawing inside the flare's light, and the
    // flare and the ring are both out before that card has filled in.
    const firstCard = revealDelayMs("centre-top") / 1000;
    const firstCardIn = firstCard + CARD_POWER_ON.wipeLeadS + CARD_POWER_ON.wipeS;
    expect(firstCard).toBeLessThan(HANDOFF.flareS);
    expect(HANDOFF.flareS).toBeLessThanOrEqual(firstCardIn);
    expect(HANDOFF.ringS).toBeLessThanOrEqual(firstCardIn);
    // The orb leaves a beat after the flare starts and is on the board before
    // the standby layer has faded — no moment with two orbs or none.
    expect(HANDOFF.travelDelayS).toBeGreaterThan(0);
    expect(HANDOFF.travelDelayS + HANDOFF.travelS).toBeLessThanOrEqual(
      HANDOFF.stageFadeDelayS + HANDOFF.stageFadeS,
    );
    // The landing ring fires as the orb arrives, the headline after it.
    expect(HANDOFF.landDelayS).toBeGreaterThanOrEqual(HANDOFF.travelDelayS + HANDOFF.travelS - 0.1);
    expect(HANDOFF.headlineDelayS).toBeGreaterThan(HANDOFF.landDelayS);
    // The wave is still out when the cards start — they appear as it passes.
    expect(HANDOFF.waveS).toBeGreaterThan(firstCard);
  });

  test("the final scan runs once every card has locked", () => {
    const lastLock = Math.max(
      ...SLOTS.map(
        (slot) =>
          revealDelayMs(slot) / 1000 + CARD_POWER_ON.wipeLeadS + CARD_POWER_ON.wipeS + CARD_POWER_ON.lockS,
      ),
    );
    expect(HANDOFF.boardSweepDelayS).toBeGreaterThanOrEqual(lastLock - 0.4);
    // The whole launch is over in under two seconds: one of these plays on
    // every first turn of a session, and it has to thrill, not delay.
    expect(HANDOFF.boardSweepDelayS + HANDOFF.boardSweepS).toBeLessThan(2);
  });

  test("a card's frame draws ahead of its content, and its ghost outlives the lock", () => {
    expect(CARD_POWER_ON.wipeLeadS).toBeGreaterThan(0);
    expect(CARD_POWER_ON.frameS).toBeGreaterThan(CARD_POWER_ON.wipeLeadS);
    expect(CARD_POWER_ON.ghostFadeDelayS).toBeGreaterThan(CARD_POWER_ON.wipeLeadS + CARD_POWER_ON.wipeS);
  });
});
