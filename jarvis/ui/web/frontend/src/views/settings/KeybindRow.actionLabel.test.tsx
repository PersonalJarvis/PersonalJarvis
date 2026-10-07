import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { KeybindRow, validationText } from "./KeybindRow";

const labels: Record<string, string> = {
  "settings_view.keybinds.validation.collision": "{combo} is already used by {action}",
  "settings_view.keybinds.hangup_label": "End call",
};
vi.mock("@/i18n", () => ({ useT: () => (key: string) => labels[key] ?? key }));

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ available: false })));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("client-side shortcut labels", () => {
  it("uses the caller label for collisions and occupied keys on the keyboard", () => {
    const onSave = vi.fn();
    const keybinds = { call: "ctrl+space", quick_switch: "ctrl+space" };
    render(<KeybindRow action="call" label="Start call" loading={false} onSave={onSave}
      actionLabel={(action) => action === "quick_switch" ? "Quick switch" : undefined}
      config={{ keybinds, defaults: {}, suggestions: [], restart_required: false }} />);
    expect(screen.getByTestId("keybind-validation-call").textContent).toContain("already used by Quick switch");
    fireEvent.click(screen.getByTestId("combo-field-call"));
    expect(screen.getByLabelText("Space").getAttribute("title")).toContain("Quick switch");
    expect(onSave).not.toHaveBeenCalled();
  });

  it("keeps translated backend labels and unknown action IDs when the callback has no label", () => {
    const t = (key: string) => labels[key] ?? key;
    const actionLabel = () => undefined;
    expect(validationText({ status: "error", reason: "collision", cautions: [], conflict: { action: "hangup", combo: "f8" } }, t, actionLabel))
      .toBe("F8 is already used by End call");
    expect(validationText({ status: "error", reason: "collision", cautions: [], conflict: { action: "future_action", combo: "f9" } }, t, actionLabel))
      .toBe("F9 is already used by future_action");
  });
});
