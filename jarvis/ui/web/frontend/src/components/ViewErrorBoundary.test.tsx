/**
 * A rebuilt bundle is not a crash, and the card has to say so.
 *
 * The live report (2026-08-23): opening the Wiki section after the frontend
 * had been rebuilt under the open window produced a red crash card reading
 * "Cannot read properties of undefined (reading 'WikiGraph')". Nothing was
 * wrong with the wiki — the window was asking for a chunk that no longer
 * existed. These tests pin the two halves of telling those apart: what the
 * user is shown, and what the button does.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ViewErrorBoundary } from "./ViewErrorBoundary";

const reloadWhenServable = vi.fn();

vi.mock("@/lib/safeReload", () => ({
  reloadWhenServable: (...args: unknown[]) => reloadWhenServable(...args),
  browserSafeReloadDeps: () => ({}),
}));

function Boom({ message }: { message: string }): JSX.Element {
  throw new Error(message);
}

const STALE_CHUNK =
  "Failed to fetch dynamically imported module: " +
  "http://127.0.0.1:47821/assets/WikiGraph-ZXJsF_pi.js";

function renderCrash(message: string) {
  const onRecover = vi.fn();
  // React logs every caught error; the noise is not what is under test.
  const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
  const consoleInfo = vi.spyOn(console, "info").mockImplementation(() => {});
  render(
    <ViewErrorBoundary viewName="memory" resetKey="memory" onRecover={onRecover}>
      <Boom message={message} />
    </ViewErrorBoundary>,
  );
  return { onRecover, consoleError, consoleInfo };
}

afterEach(() => {
  vi.restoreAllMocks();
  reloadWhenServable.mockClear();
});

describe("ViewErrorBoundary", () => {
  it("offers a reload — not 'back to chats' — when the chunk is stale", () => {
    const { onRecover } = renderCrash(STALE_CHUNK);

    const buttons = screen.getAllByRole("button");
    expect(buttons).toHaveLength(1);
    fireEvent.click(buttons[0]);

    expect(reloadWhenServable).toHaveBeenCalledTimes(1);
    expect(onRecover).not.toHaveBeenCalled();
  });

  it("never shows the raw engine message for a stale chunk", () => {
    // "Failed to fetch dynamically imported module: …WikiGraph-ZXJsF_pi.js"
    // tells the user nothing they can act on and reads as a broken section.
    renderCrash(STALE_CHUNK);

    expect(screen.queryByText(/WikiGraph/)).toBeNull();
    expect(screen.queryByText(/dynamically imported/i)).toBeNull();
  });

  it("does not log a stale chunk as a crash", () => {
    // A rebuild is routine. Reporting it at error level is what made a normal
    // `npm run build` look like a fault in whichever section was open.
    const { consoleError } = renderCrash(STALE_CHUNK);

    const ours = consoleError.mock.calls.filter(
      (call) => call[0] === "Jarvis view crashed",
    );
    expect(ours).toHaveLength(0);
  });

  it("still reports a genuine component bug, with its message and recovery", () => {
    const { onRecover, consoleError } = renderCrash(
      "Cannot read properties of undefined (reading 'nodes')",
    );

    expect(screen.getByText(/reading 'nodes'/)).toBeTruthy();

    fireEvent.click(screen.getAllByRole("button")[0]);
    expect(onRecover).toHaveBeenCalledTimes(1);
    expect(reloadWhenServable).not.toHaveBeenCalled();

    // React re-renders a failing tree once more to build a better stack, so
    // the boundary legitimately catches twice. What matters is that it logged.
    const ours = consoleError.mock.calls.filter(
      (call) => call[0] === "Jarvis view crashed",
    );
    expect(ours.length).toBeGreaterThan(0);
  });
});
