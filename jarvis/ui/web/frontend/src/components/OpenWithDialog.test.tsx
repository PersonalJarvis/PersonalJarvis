import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { OpenWithDialog } from "./OpenWithDialog";

afterEach(cleanup);

const OPENERS = [
  { id: "default", label: "System default app" },
  { id: "browser", label: "Browser" },
  { id: "code", label: "VS Code" },
];

describe("OpenWithDialog", () => {
  it("lists each opener (editor labels pass through, structural ones localise)", () => {
    render(
      <OpenWithDialog openers={OPENERS} onPick={() => {}} onClose={() => {}} />,
    );
    // The editor proper name comes straight from the backend.
    expect(screen.getByText("VS Code")).toBeDefined();
    // "default"/"browser" resolve through i18n (English default locale).
    expect(screen.getByText("System default app")).toBeDefined();
    expect(screen.getByText("Browser")).toBeDefined();
  });

  it("picks an opener with remember=false by default", () => {
    const onPick = vi.fn();
    render(
      <OpenWithDialog openers={OPENERS} onPick={onPick} onClose={() => {}} />,
    );
    fireEvent.click(screen.getByText("VS Code"));
    expect(onPick).toHaveBeenCalledWith("code", false);
  });

  it("passes remember=true once the checkbox is ticked", () => {
    const onPick = vi.fn();
    render(
      <OpenWithDialog openers={OPENERS} onPick={onPick} onClose={() => {}} />,
    );
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByText("System default app"));
    expect(onPick).toHaveBeenCalledWith("default", true);
  });

  it("shows an empty hint when no apps were detected", () => {
    render(
      <OpenWithDialog openers={[]} onPick={() => {}} onClose={() => {}} />,
    );
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(
      screen.getByText("No apps detected to open this file."),
    ).toBeDefined();
  });

  it("shows a detecting state (not the empty hint) while still loading", () => {
    render(
      <OpenWithDialog openers={[]} loading onPick={() => {}} onClose={() => {}} />,
    );
    // The misleading "no apps" message must NOT flash while detection runs.
    expect(
      screen.queryByText("No apps detected to open this file."),
    ).toBeNull();
    expect(screen.getByText("Detecting apps…")).toBeDefined();
  });
});
