import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { OfficeFallback } from "./OfficeFallback";

afterEach(cleanup);

it("offers the equivalent data surface when graphics are unavailable", () => {
  const open = vi.fn();
  render(
    <OfficeFallback
      message="Graphics unavailable"
      actionLabel="Open ledger"
      onAction={open}
    />,
  );

  expect(screen.getByRole("status").textContent).toContain("Graphics unavailable");
  fireEvent.click(screen.getByRole("button", { name: "Open ledger" }));
  expect(open).toHaveBeenCalledOnce();
});
