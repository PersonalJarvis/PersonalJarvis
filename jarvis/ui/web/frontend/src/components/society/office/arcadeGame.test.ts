import { describe, expect, it } from "vitest";
import { ARCADE_SHIP_Y, ARCADE_W, invaderPos, newArcade, stepArcade, swarmInterval, type ArcadeInput } from "./arcadeGame";

const idle: ArcadeInput = { left: false, right: false, fire: false };
const run = (s: ReturnType<typeof newArcade>, input: ArcadeInput, seconds: number) => {
  for (let t = 0; t < seconds; t += 1 / 120) stepArcade(s, input, 1 / 120, () => 0.5);
};

describe("arcade game", () => {
  it("does nothing until started", () => {
    const s = newArcade();
    run(s, { ...idle, right: true }, 1);
    expect(s.shipX).toBe(ARCADE_W / 2);
  });

  it("moves the ship and keeps it on screen", () => {
    const s = newArcade();
    s.phase = "playing";
    run(s, { ...idle, left: true }, 5);
    expect(s.shipX).toBe(10);
  });

  it("scores when a shot hits an invader", () => {
    const s = newArcade();
    s.phase = "playing";
    s.bombTimer = 99;
    const target = s.invaders.find((inv) => inv.row === 4 && inv.col === 0)!;
    const { x, y } = invaderPos(s, target);
    s.shot = { x: x + 5, y: y + 4 };
    stepArcade(s, idle, 0.001);
    expect(target.alive).toBe(false);
    expect(s.score).toBe(10);
    expect(s.shot).toBeNull();
  });

  it("loses a life to a bomb and ends the game on the last one", () => {
    const s = newArcade();
    s.phase = "playing";
    s.bombTimer = 99;
    s.bombs = [{ x: s.shipX, y: ARCADE_SHIP_Y }];
    stepArcade(s, idle, 0.001);
    expect(s.lives).toBe(2);
    expect(s.hitTimer).toBeGreaterThan(0);
    s.lives = 1;
    s.hitTimer = 0;
    s.bombs = [{ x: s.shipX, y: ARCADE_SHIP_Y }];
    stepArcade(s, idle, 0.001);
    expect(s.phase).toBe("over");
  });

  it("marches faster as invaders fall, and starts the next wave when all are gone", () => {
    const s = newArcade();
    s.phase = "playing";
    const full = swarmInterval(s);
    s.invaders.forEach((inv, i) => { inv.alive = i === 0; });
    expect(swarmInterval(s)).toBeLessThan(full);
    s.invaders[0].alive = false;
    s.bombTimer = 99;
    stepArcade(s, idle, 0.001);
    expect(s.wave).toBe(2);
    expect(s.invaders.every((inv) => inv.alive)).toBe(true);
  });

  it("ends the game when the swarm reaches the ship", () => {
    const s = newArcade();
    s.phase = "playing";
    s.swarmY = ARCADE_SHIP_Y;
    stepArcade(s, idle, 0.001);
    expect(s.phase).toBe("over");
  });
});
