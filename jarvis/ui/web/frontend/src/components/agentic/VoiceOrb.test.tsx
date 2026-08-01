import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { documentState } = vi.hoisted(() => ({
  documentState: { visible: true },
}));

vi.mock("@/hooks/useDocumentVisible", () => ({
  useDocumentVisible: () => documentState.visible,
}));

import { VoiceOrb } from "./VoiceOrb";

function fakeCanvasContext() {
  const gradient = { addColorStop: vi.fn() } as unknown as CanvasGradient;
  return {
    arc: vi.fn(),
    beginPath: vi.fn(),
    clearRect: vi.fn(),
    clip: vi.fn(),
    createLinearGradient: vi.fn(() => gradient),
    createRadialGradient: vi.fn(() => gradient),
    fill: vi.fn(),
    fillRect: vi.fn(),
    restore: vi.fn(),
    save: vi.fn(),
    scale: vi.fn(),
    stroke: vi.fn(),
    translate: vi.fn(),
    fillStyle: "",
    lineWidth: 1,
    strokeStyle: "",
  } as unknown as CanvasRenderingContext2D;
}

function setReducedMotion(matches: boolean): void {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn(() => ({
      matches,
      media: "(prefers-reduced-motion: reduce)",
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

beforeEach(() => {
  documentState.visible = true;
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("voice orb renderer", () => {
  it("repaints a still orb when the voice state changes", () => {
    setReducedMotion(true);
    const context = fakeCanvasContext();
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(context);

    const { rerender } = render(<VoiceOrb state="idle" />);
    expect(context.createLinearGradient).toHaveBeenCalledTimes(1);

    rerender(<VoiceOrb state="listening" />);
    expect(context.createLinearGradient).toHaveBeenCalledTimes(2);
  });

  it("cancels animation when hidden and again when unmounted", () => {
    setReducedMotion(false);
    const context = fakeCanvasContext();
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(context);
    const requestFrame = vi
      .spyOn(window, "requestAnimationFrame")
      .mockReturnValueOnce(11)
      .mockReturnValueOnce(12);
    const cancelFrame = vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {});

    const { rerender, unmount } = render(<VoiceOrb state="idle" />);
    expect(requestFrame).toHaveBeenCalledTimes(1);

    documentState.visible = false;
    rerender(<VoiceOrb state="idle" />);
    expect(cancelFrame).toHaveBeenCalledWith(11);

    documentState.visible = true;
    rerender(<VoiceOrb state="idle" />);
    expect(requestFrame).toHaveBeenCalledTimes(2);
    unmount();
    expect(cancelFrame).toHaveBeenCalledWith(12);
  });
});
