import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { BrowserPointer } from "./BrowserPointer";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test("cursor uses frame coordinates with letterboxing and preserves a single click pulse", () => {
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(640);
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(480);
  const pointer = { x: 100, y: 200, width: 1280, height: 800, click_id: 1, click_x: 100, click_y: 200 };
  const { rerender } = render(<BrowserPointer pointer={pointer} />);
  expect(screen.getByTestId("browser-agent-pointer").style.left).toBe("50px");
  expect(screen.getByTestId("browser-agent-pointer").style.top).toBe("140px");
  const click = screen.getByTestId("browser-click-mark");
  rerender(<BrowserPointer pointer={{ ...pointer, x: 200 }} />);
  expect(screen.getByTestId("browser-click-mark")).toBe(click);
  rerender(<BrowserPointer />);
  expect(screen.queryByTestId("browser-agent-pointer")).toBeNull();
});
