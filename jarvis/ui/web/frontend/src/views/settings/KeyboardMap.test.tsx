import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { KeyboardMap } from "./KeyboardMap";

function renderMap(over: Partial<Parameters<typeof KeyboardMap>[0]> = {}) {
  const onToggleToken = vi.fn();
  render(
    <KeyboardMap
      pressedCodes={over.pressedCodes ?? new Set()}
      selectedTokens={over.selectedTokens ?? new Set()}
      boundTokens={over.boundTokens ?? {}}
      platform={over.platform ?? "pc"}
      onToggleToken={over.onToggleToken ?? onToggleToken}
    />,
  );
  return { onToggleToken };
}

describe("KeyboardMap", () => {
  it("renders the function row, letters, arrows and the nav cluster", () => {
    renderMap();
    expect(screen.getByTestId("key-F5")).toBeTruthy();
    expect(screen.getByTestId("key-KeyA")).toBeTruthy();
    expect(screen.getByTestId("key-ArrowUp")).toBeTruthy();
    expect(screen.getByTestId("key-PageUp")).toBeTruthy();
  });

  it("highlights a physically pressed key live", () => {
    renderMap({ pressedCodes: new Set(["F5"]) });
    // The pressed style is the only one that uses the inverted foreground.
    expect(screen.getByTestId("key-F5").className).toContain(
      "text-primary-foreground",
    );
    expect(screen.getByTestId("key-F6").className).not.toContain(
      "text-primary-foreground",
    );
  });

  it("marks the selected combo tokens with aria-pressed", () => {
    renderMap({ selectedTokens: new Set(["f5", "ctrl"]) });
    expect(screen.getByTestId("key-F5").getAttribute("aria-pressed")).toBe("true");
    // Both Ctrl keys reflect the ctrl token.
    expect(
      screen.getByTestId("key-ControlLeft").getAttribute("aria-pressed"),
    ).toBe("true");
    expect(screen.getByTestId("key-F6").getAttribute("aria-pressed")).toBe("false");
  });

  it("flags keys already bound to another action", () => {
    renderMap({ boundTokens: { f1: "Hangup" } });
    expect(screen.getByTestId("key-F1").getAttribute("title")).toContain("Hangup");
    // A free key carries no such marker.
    expect(screen.getByTestId("key-F5").getAttribute("title")).not.toContain(
      "Hangup",
    );
  });

  it("toggles a bindable key's token on click", () => {
    const { onToggleToken } = renderMap();
    fireEvent.click(screen.getByTestId("key-KeyA"));
    expect(onToggleToken).toHaveBeenCalledWith("a");
    fireEvent.click(screen.getByTestId("key-F5"));
    expect(onToggleToken).toHaveBeenCalledWith("f5");
    fireEvent.click(screen.getByTestId("key-ControlLeft"));
    expect(onToggleToken).toHaveBeenCalledWith("ctrl");
    fireEvent.click(screen.getByTestId("key-ArrowUp"));
    expect(onToggleToken).toHaveBeenCalledWith("up");
  });

  it("does not bind punctuation / CapsLock (dead keys are disabled)", () => {
    const { onToggleToken } = renderMap();
    const dead = screen.getByTestId("key-Backquote") as HTMLButtonElement;
    expect(dead.disabled).toBe(true);
    fireEvent.click(dead);
    expect(onToggleToken).not.toHaveBeenCalled();
  });

  it("shows Mac modifier glyphs on a mac keyboard", () => {
    renderMap({ platform: "mac" });
    expect(screen.getByTestId("key-MetaLeft").textContent).toContain("⌘");
  });
});
