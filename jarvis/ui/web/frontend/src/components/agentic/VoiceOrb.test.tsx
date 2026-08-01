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
  return {
    arc: vi.fn(),
    beginPath: vi.fn(),
    clearRect: vi.fn(),
    clip: vi.fn(),
    createImageData: vi.fn((width: number, height: number) => ({
      data: new Uint8ClampedArray(width * height * 4),
      width,
      height,
    })),
    drawImage: vi.fn(),
    putImageData: vi.fn(),
    restore: vi.fn(),
    save: vi.fn(),
    imageSmoothingEnabled: false,
    imageSmoothingQuality: "low",
  } as unknown as CanvasRenderingContext2D;
}

function installCanvasContexts() {
  const display = fakeCanvasContext();
  const texture = fakeCanvasContext();
  let call = 0;
  vi.spyOn(HTMLCanvasElement.prototype, "getContext")
    .mockImplementation(() => (call++ % 2 === 0 ? display : texture));
  return { display, texture };
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
    const { display, texture } = installCanvasContexts();

    const { rerender } = render(<VoiceOrb state="idle" />);
    expect(texture.putImageData).toHaveBeenCalledTimes(1);
    expect(display.drawImage).toHaveBeenCalledTimes(1);
    expect(display.clip).toHaveBeenCalledTimes(1);

    rerender(<VoiceOrb state="listening" />);
    expect(texture.putImageData).toHaveBeenCalledTimes(2);
    expect(display.drawImage).toHaveBeenCalledTimes(2);
  });

  it("keeps reduced-motion weather fixed when returning to a state", () => {
    setReducedMotion(true);
    const snapshots: number[][] = [];
    const { texture } = installCanvasContexts();
    vi.mocked(texture.putImageData).mockImplementation((image) => {
      snapshots.push(Array.from(image.data));
    });

    const { rerender } = render(<VoiceOrb state="idle" />);
    rerender(<VoiceOrb state="listening" />);
    rerender(<VoiceOrb state="idle" />);

    expect(snapshots).toHaveLength(3);
    expect(snapshots[2]).toEqual(snapshots[0]);
  });

  it("caps painting and advances weather independently of absolute uptime", () => {
    setReducedMotion(false);
    vi.spyOn(performance, "now").mockReturnValue(0);
    const snapshots: number[][] = [];
    const { display, texture } = installCanvasContexts();
    vi.mocked(texture.putImageData).mockImplementation((image) => {
      snapshots.push(Array.from(image.data));
    });
    let nextFrame: FrameRequestCallback | undefined;
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      nextFrame = callback;
      return 21;
    });

    render(<VoiceOrb state="idle" />);
    expect(display.drawImage).toHaveBeenCalledTimes(1);
    nextFrame?.(20);
    expect(display.drawImage).toHaveBeenCalledTimes(1);
    nextFrame?.(600_050);
    expect(display.drawImage).toHaveBeenCalledTimes(2);

    const meanDelta = snapshots[0].reduce(
      (total, value, index) => total + Math.abs(value - snapshots[1][index]),
      0,
    ) / snapshots[0].length;
    expect(meanDelta).toBeLessThan(2);
  });

  it("cancels animation when hidden and again when unmounted", () => {
    setReducedMotion(false);
    installCanvasContexts();
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
