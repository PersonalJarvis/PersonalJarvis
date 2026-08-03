import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  clearVoiceInputLevel,
  setBrowserVoiceInputOwnership,
  setVoiceInputLevel,
} from "@/lib/voiceInputLevel";

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
  setBrowserVoiceInputOwnership(false);
  clearVoiceInputLevel();
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
    nextFrame?.(49);
    expect(display.drawImage).toHaveBeenCalledTimes(1);
    nextFrame?.(50);
    expect(display.drawImage).toHaveBeenCalledTimes(2);
    nextFrame?.(99);
    expect(display.drawImage).toHaveBeenCalledTimes(2);
    nextFrame?.(100);
    expect(display.drawImage).toHaveBeenCalledTimes(3);
    nextFrame?.(600_150);
    expect(display.drawImage).toHaveBeenCalledTimes(4);

    const meanDelta = snapshots[2].reduce(
      (total, value, index) => total + Math.abs(value - snapshots[3][index]),
      0,
    ) / snapshots[2].length;
    expect(meanDelta).toBeLessThan(2);
  });

  it("moves the internal weather faster while thinking than while idle", () => {
    setReducedMotion(false);
    vi.spyOn(performance, "now").mockReturnValue(0);
    const snapshots: number[][] = [];
    const { texture } = installCanvasContexts();
    vi.mocked(texture.putImageData).mockImplementation((image) => {
      snapshots.push(Array.from(image.data));
    });
    let nextFrame: FrameRequestCallback | undefined;
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      nextFrame = callback;
      return 31;
    });

    const { rerender } = render(<VoiceOrb state="idle" />);
    for (let now = 50; now <= 500; now += 50) nextFrame?.(now);

    const idleStart = snapshots[0];
    const idleEnd = snapshots[snapshots.length - 1];
    const idleDelta = idleStart.reduce(
      (total, value, index) => total + Math.abs(value - idleEnd[index]),
      0,
    ) / idleStart.length;

    rerender(<VoiceOrb state="thinking" />);
    for (let now = 550; now <= 1_050; now += 50) nextFrame?.(now);
    const thinkingEnd = snapshots[snapshots.length - 1];
    const thinkingDelta = idleEnd.reduce(
      (total, value, index) => total + Math.abs(value - thinkingEnd[index]),
      0,
    ) / idleEnd.length;

    expect(thinkingDelta).toBeGreaterThan(idleDelta * 2);
  });

  it("adds and releases a soft speaking impulse without snapping on exit", () => {
    setReducedMotion(false);
    vi.spyOn(performance, "now").mockReturnValue(0);
    const { display } = installCanvasContexts();
    let nextFrame: FrameRequestCallback | undefined;
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      nextFrame = callback;
      return 41;
    });

    const { rerender } = render(<VoiceOrb state="idle" />);
    const idleRadius = vi.mocked(display.arc).mock.calls[0][2];
    rerender(<VoiceOrb state="speaking" />);
    nextFrame?.(60);
    let calls = vi.mocked(display.arc).mock.calls;
    const onsetRadius = calls[calls.length - 1][2];

    for (let now = 110; now <= 310; now += 50) nextFrame?.(now);
    calls = vi.mocked(display.arc).mock.calls;
    const settledRadius = calls[calls.length - 1][2];

    rerender(<VoiceOrb state="thinking" />);
    nextFrame?.(360);
    calls = vi.mocked(display.arc).mock.calls;
    const exitRadius = calls[calls.length - 1][2];

    expect(onsetRadius).toBeGreaterThan(idleRadius * 1.01);
    expect(settledRadius).toBeLessThan(onsetRadius * 0.995);
    expect(Math.abs(exitRadius - settledRadius) / settledRadius).toBeLessThan(0.015);
  });

  it("expands from the measured microphone level while listening", () => {
    setReducedMotion(false);
    vi.spyOn(performance, "now").mockReturnValue(0);
    const { display } = installCanvasContexts();
    let nextFrame: FrameRequestCallback | undefined;
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => {
      nextFrame = callback;
      return 51;
    });

    render(<VoiceOrb state="listening" />);
    const quietRadius = vi.mocked(display.arc).mock.calls[0][2];

    setVoiceInputLevel(1);
    nextFrame?.(60);
    let calls = vi.mocked(display.arc).mock.calls;
    const voicedRadius = calls[calls.length - 1][2];

    clearVoiceInputLevel();
    for (let now = 110; now <= 410; now += 50) nextFrame?.(now);
    calls = vi.mocked(display.arc).mock.calls;
    const releasedRadius = calls[calls.length - 1][2];

    expect(voicedRadius).toBeGreaterThan(quietRadius * 1.015);
    expect(releasedRadius).toBeLessThan(voicedRadius * 0.99);
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
