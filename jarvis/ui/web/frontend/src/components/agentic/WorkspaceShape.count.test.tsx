import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { CountStepper, CountTrack } from "./WorkspaceShape";

afterEach(cleanup);

/**
 * The backend's runaway guard, which is also the track's upper end.
 *
 * Written out rather than imported because the point of these tests is that the
 * control adapts to whatever the backend reports — a shared constant would let
 * both sides drift together and the tests would still pass.
 */
const MAX = 100;

/** No jest-dom in this repo — assertions read the elements directly. */
function fieldValue(): string {
  return (screen.getByTestId("terminal-count-value") as HTMLInputElement).value;
}

/** The count lives in the view in the real app, so it does here too. */
function Stepper({ start = 4 }: { start?: number }) {
  const [count, setCount] = useState(start);
  return <CountStepper count={count} max={MAX} onChange={setCount} />;
}

function Track({ start = 4 }: { start?: number }) {
  const [count, setCount] = useState(start);
  return <CountTrack count={count} max={MAX} onChange={setCount} />;
}

describe("CountStepper", () => {
  it("takes a number typed digit by digit", () => {
    // The maintainer's report was "you cannot even type a number yourself"
    // (2026-08-11). Typing is what this asserts, one keystroke at a time,
    // because a controlled field can accept a wholesale `change` and still
    // fight the user on the second digit.
    render(<Stepper />);
    const field = screen.getByTestId("terminal-count-value");
    fireEvent.focus(field);
    fireEvent.change(field, { target: { value: "2" } });
    expect(fieldValue()).toBe("2");
    fireEvent.change(field, { target: { value: "25" } });
    expect(fieldValue()).toBe("25");
  });

  it("holds a typed number at the workspace maximum", () => {
    render(<Stepper />);
    fireEvent.change(screen.getByTestId("terminal-count-value"), {
      target: { value: "999" },
    });
    expect(fieldValue()).toBe(String(MAX));
  });

  it("ignores what is not a digit instead of losing the count", () => {
    render(<Stepper />);
    fireEvent.change(screen.getByTestId("terminal-count-value"), {
      target: { value: "12 panes" },
    });
    expect(fieldValue()).toBe("12");
  });

  it("steps with the arrow keys, which a text field has no native answer for", () => {
    render(<Stepper start={4} />);
    const field = screen.getByTestId("terminal-count-value");
    fireEvent.keyDown(field, { key: "ArrowUp" });
    expect(fieldValue()).toBe("5");
    fireEvent.keyDown(field, { key: "ArrowDown" });
    fireEvent.keyDown(field, { key: "ArrowDown" });
    expect(fieldValue()).toBe("3");
  });

  it("returns to the real count when an edit is abandoned", () => {
    render(<Stepper start={4} />);
    const field = screen.getByTestId("terminal-count-value");
    fireEvent.change(field, { target: { value: "" } });
    expect(fieldValue()).toBe("");
    fireEvent.blur(field);
    expect(fieldValue()).toBe("4");
  });

  it("puts the whole middle segment on the field, not just the digits", () => {
    // The click target used to be the 48 px the digits occupied; everything
    // around them was a plain div and swallowed the click silently.
    const { container } = render(<Stepper />);
    const field = container.querySelector("#workspace-terminal-count");
    const segment = field?.closest("label");
    expect(segment).toBeTruthy();
    expect(segment?.getAttribute("for")).toBe("workspace-terminal-count");
  });
});

describe("CountTrack", () => {
  it("labels the track's own ends, not a maximum it used to have", () => {
    render(<Track />);
    expect(screen.getByTestId("terminal-count-range").getAttribute("max")).toBe(
      String(MAX),
    );
    expect(
      screen.getByRole("button", { name: "Use 100 terminals" }),
    ).toBeTruthy();
    expect(
      screen.queryByRole("button", { name: "Use 12 terminals" }),
    ).toBeNull();
  });

  it("places every label at the fraction of the track it stands for", () => {
    render(<Track />);
    const at = (value: number) =>
      (
        screen.getByRole("button", {
          name: `Use ${value} terminals`,
        }) as HTMLElement
      ).style.left;
    expect(at(1)).toBe("0%");
    expect(at(50)).toBe(`${((50 - 1) / (MAX - 1)) * 100}%`);
    expect(at(100)).toBe("100%");
  });
});
