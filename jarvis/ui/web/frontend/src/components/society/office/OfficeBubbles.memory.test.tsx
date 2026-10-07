/**
 * The lead pet's thought bubble shows its memory receipts end to end: a
 * `MemoryFileWrite` payload in the store becomes "Updating memory · MEMORY.md",
 * then "Saved to MEMORY.md" — and nothing claims "saved" without a receipt.
 */
import type { ReactNode } from "react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act, createRoot, extend, type ReconcilerRoot } from "@react-three/fiber";
import { cleanup, render, screen } from "@testing-library/react";
import * as THREE from "three";
import { loadLocaleChunk, setUiLanguage } from "@/i18n";
import { useMemoryWrites } from "@/store/memoryWrites";
import type { SocietyAgent } from "../data";
import { AgentBubble } from "./OfficeBubbles";
import { useOfficeTalk } from "./officeTalk";

/** What the in-world `<Html>` would put on screen, captured per render. */
const shown: { current: ReactNode } = { current: null };
vi.mock("@react-three/drei", async (original) => ({
  ...await original<typeof import("@react-three/drei")>(),
  Html: ({ children }: { children: ReactNode }) => { shown.current = children; return null; },
}));

extend(THREE);
let root: ReconcilerRoot<HTMLCanvasElement> | null = null;

function fakeRenderer(canvas: HTMLCanvasElement) {
  const noop = () => undefined;
  return {
    domElement: canvas, render: noop, setSize: noop, setPixelRatio: noop, setAnimationLoop: noop, dispose: noop,
    getPixelRatio: () => 1, shadowMap: { enabled: false, type: 0, needsUpdate: false },
    xr: { enabled: false, isPresenting: false, addEventListener: noop, removeEventListener: noop, setAnimationLoop: noop },
    outputColorSpace: "srgb", toneMapping: 0, toneMappingExposure: 1,
    info: { render: {}, memory: {} },
  };
}

const lead = { agentId: "jarvis", name: "George", tier: "lead", state: "idle", chatSessionId: null } as SocietyAgent;
const scout = { agentId: "scout", name: "Scout", tier: "specialist", state: "idle", chatSessionId: null } as SocietyAgent;

async function bubbleOf(agent: SocietyAgent, lines: { kind: "agent" | "tool"; text: string }[] = []) {
  shown.current = null;
  const canvas = document.createElement("canvas");
  root = root ?? createRoot(canvas);
  root.configure({
    gl: fakeRenderer(canvas) as unknown as NonNullable<Parameters<ReconcilerRoot<HTMLCanvasElement>["configure"]>[0]>["gl"],
    frameloop: "never", size: { width: 100, height: 100, top: 0, left: 0 },
  });
  await act(async () => {
    root?.render(<AgentBubble agent={agent} lines={lines as never} selected={false} height={1} onSelect={() => undefined} />);
  });
  cleanup();
  if (shown.current) render(<>{shown.current}</>);
  return shown.current;
}

function receipt(phase: string, file = "MEMORY.md", id = "u1", agent = "jarvis") {
  act(() => useMemoryWrites.getState().receive({ update_id: id, agent_id: agent, file, phase, operation: "add" }));
}

beforeAll(async () => {
  setUiLanguage("en");
  await loadLocaleChunk("society");
});

beforeEach(() => {
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
  useMemoryWrites.getState().reset();
  useOfficeTalk.setState({ agentId: null });
});

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  cleanup();
  useMemoryWrites.getState().reset();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("the lead's memory receipt", () => {
  it("names the file while writing and claims saved only after the receipt", async () => {
    receipt("pending");
    await bubbleOf(lead);
    const row = screen.getByRole("status");
    expect(row.dataset.phase).toBe("pending");
    expect(row.textContent).toMatch(/MEMORY\.md/);
    expect(row.textContent).toBe("Updating memory · MEMORY.md");

    receipt("saved");
    await bubbleOf(lead);
    expect(screen.getByRole("status").dataset.phase).toBe("saved");
    expect(screen.getByRole("status").textContent).toBe("Saved to MEMORY.md");
  });

  it("shows a failed write as failed", async () => {
    receipt("pending", "SOUL.md");
    receipt("failed", "SOUL.md");
    await bubbleOf(lead);
    expect(screen.getByRole("status").dataset.phase).toBe("failed");
    expect(screen.getByRole("status").textContent).toMatch(/SOUL\.md/);
  });

  it("keeps the agent's own thought and adds the memory line under it", async () => {
    receipt("pending", "USER.md");
    await bubbleOf({ ...lead, state: "working" }, [{ kind: "agent", text: "Checking the calendar" }]);
    expect(screen.getByText(/Checking the calendar/)).toBeTruthy();
    expect(screen.getByRole("status").textContent).toMatch(/USER\.md/);
  });

  it("leaves the bubble empty again when the write changed nothing", async () => {
    receipt("pending");
    receipt("unchanged");
    expect(await bubbleOf(lead)).toBeNull();
  });

  it("never draws a memory line over another agent", async () => {
    receipt("pending", "MEMORY.md", "u2", "scout");
    expect(await bubbleOf(scout)).toBeNull();
    receipt("pending", "MEMORY.md", "u3", "jarvis");
    expect(await bubbleOf(scout)).toBeNull();
  });
});
