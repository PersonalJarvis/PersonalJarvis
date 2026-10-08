import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BotStage, useDoneFlourish } from "./BotStage";
import { DONE_MS, MIN_SCENE_MS, SCENES, THINK_ROTATE_MS, sceneFor } from "./scenes";
import type { Presence } from "../messengerPresence";

afterEach(cleanup);

describe("sceneFor", () => {
  it("offers many thinking scenes and one for every presence", () => {
    expect(SCENES.thinking.length).toBeGreaterThanOrEqual(10);
    for (const presence of Object.keys(SCENES) as Presence[]) expect(SCENES[presence].length).toBeGreaterThan(0);
  });

  it("is stable per turn and varies across turns", () => {
    expect(sceneFor("thinking", "turn-a")).toBe(sceneFor("thinking", "turn-a"));
    const seen = new Set(Array.from({ length: 40 }, (_, i) => sceneFor("thinking", `turn-${i}`)));
    expect(seen.size).toBeGreaterThanOrEqual(6);
  });

  it("never repeats a scene from one rotation step to the next", () => {
    for (let step = 0; step < 20; step++) {
      expect(sceneFor("thinking", "t", step)).not.toBe(sceneFor("thinking", "t", step + 1));
    }
  });
});

describe("BotStage", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  const face = <span data-testid="face" />;
  const scene = () => screen.getByTestId("messenger-presence").dataset.presence;
  const sceneName = () => document.querySelector(".bot-stage")?.getAttribute("data-scene");

  it("holds a scene long enough that a quick call does not flicker", () => {
    const { rerender } = render(<BotStage presence="thinking" seed="t" avatar={face} color="#7ab6ef" />);
    rerender(<BotStage presence="searching" seed="t" avatar={face} color="#7ab6ef" />);
    rerender(<BotStage presence="thinking" seed="t" avatar={face} color="#7ab6ef" />);
    act(() => { vi.advanceTimersByTime(MIN_SCENE_MS + 10); });
    expect(SCENES.thinking).toContain(sceneName());
    rerender(<BotStage presence="writing" seed="t" avatar={face} color="#7ab6ef" />);
    expect(SCENES.thinking).toContain(sceneName());
    act(() => { vi.advanceTimersByTime(MIN_SCENE_MS + 10); });
    expect(SCENES.writing).toContain(sceneName());
    expect(scene()).toBe("writing");
  });

  it("rotates a long think through different scenes", () => {
    render(<BotStage presence="thinking" seed="t" avatar={face} color="#7ab6ef" />);
    const first = sceneName();
    act(() => { vi.advanceTimersByTime(THINK_ROTATE_MS + 10); });
    expect(sceneName()).not.toBe(first);
    expect(SCENES.thinking).toContain(sceneName());
  });

  it("plays the agent's own face", () => {
    render(<BotStage presence="typing" seed="t" avatar={face} color="#7ab6ef" />);
    expect(screen.getByTestId("face")).toBeTruthy();
  });
});

describe("useDoneFlourish", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  function Probe({ running, done }: { running: boolean; done: boolean }) {
    return <span data-testid="flourish">{String(useDoneFlourish(running, done))}</span>;
  }

  it("cheers once when a live turn finishes, then clears", () => {
    const { rerender } = render(<Probe running done={false} />);
    rerender(<Probe running={false} done />);
    expect(screen.getByTestId("flourish").textContent).toBe("true");
    act(() => { vi.advanceTimersByTime(DONE_MS + 10); });
    expect(screen.getByTestId("flourish").textContent).toBe("false");
  });

  it("stays quiet for a turn that was already finished when it loaded", () => {
    render(<Probe running={false} done />);
    expect(screen.getByTestId("flourish").textContent).toBe("false");
  });
});

describe("expressive faces", () => {
  it("give each eye an open, happy and shut shape the stage can swap", async () => {
    const { AgentSymbol } = await import("@/components/society/AgentSymbol");
    const { container } = render(<AgentSymbol shape="circle" color="#79c7c4" size={28} expressive />);
    for (const side of ["l", "r"]) {
      const eye = container.querySelector(`.ae-${side}`);
      expect(eye?.querySelector(".ae-open")).not.toBeNull();
      expect(eye?.querySelector(".ae-happy")).not.toBeNull();
      expect(eye?.querySelector(".ae-shut")).not.toBeNull();
    }
  });

  it("leave an ordinary face exactly as it was", async () => {
    const { AgentSymbol } = await import("@/components/society/AgentSymbol");
    const { container } = render(<AgentSymbol shape="circle" color="#79c7c4" size={28} />);
    expect(container.querySelector(".ae-eye")).toBeNull();
    expect(container.querySelectorAll(".agent-symbol-lids ellipse")).toHaveLength(2);
  });
});
