import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { BrandedSelect } from "@/components/ui/select";

describe("BrandedSelect", () => {
  it("renders the app-owned panel and selects with the keyboard", async () => {
    const onValueChange = vi.fn();
    render(
      <BrandedSelect
        value="fast"
        onValueChange={onValueChange}
        ariaLabel="Model"
        testId="model-select"
        options={[
          { value: "fast", label: "Fast" },
          { value: "offline", label: "Unavailable", disabled: true },
          { value: "frontier", label: "Frontier" },
        ]}
      />,
    );

    fireEvent.click(screen.getByTestId("model-select"));
    const panel = await screen.findByTestId("model-select-panel");
    expect(panel.className).toContain("bg-popover");

    const unavailable = screen.getByRole("option", { name: "Unavailable" });
    expect(unavailable.getAttribute("aria-disabled")).toBe("true");
    fireEvent.click(unavailable);
    expect(onValueChange).not.toHaveBeenCalled();

    fireEvent.keyDown(panel, { key: "ArrowDown" });
    fireEvent.keyDown(panel, { key: "Enter" });
    expect(onValueChange).toHaveBeenCalledWith("frontier");
  });

  it("keeps the selected value on the trigger for forms and tests", () => {
    render(
      <BrandedSelect
        value="voice-b"
        onValueChange={() => {}}
        ariaLabel="Voice"
        testId="voice-select"
        options={[
          { value: "voice-a", label: "Voice A" },
          { value: "voice-b", label: "Voice B" },
        ]}
      />,
    );

    const trigger = screen.getByTestId("voice-select");
    expect(trigger.getAttribute("data-value")).toBe("voice-b");
    expect(trigger.textContent).toContain("Voice B");
  });
});
