import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

vi.mock("@/i18n", () => ({ useT: () => (k: string) => k }));
vi.mock("@/components/MascotGigi", () => ({ MascotGigi: () => <div data-testid="gigi" /> }));

// Mock the overlay-style hook so the step has no real network dependency. The
// step must persist the pick through this hook's saveStyle and NEVER restart.
const saveStyle = vi.fn().mockResolvedValue({
  ok: true,
  style: "jarvis_bar",
  persisted: true,
  applied_live: false, // even when a restart would be "required", onboarding must not restart
  restart_required: true,
});
vi.mock("@/hooks/useOverlayStyle", () => ({
  useOverlayStyle: () => ({
    config: { style: "jarvis_bar", options: ["jarvis_bar", "mascot", "none"] },
    loading: false,
    error: null,
    refetch: vi.fn(),
    saveStyle,
  }),
}));

import { SystemStyleStep } from "./SystemStyleStep";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderStep() {
  const goNext = vi.fn();
  const skip = vi.fn();
  render(
    <SystemStyleStep
      onb={{} as never}
      goNext={goNext}
      goBack={vi.fn()}
      skip={skip}
      isFirst={false}
      isLast={false}
    />,
  );
  return { goNext, skip };
}

it("renders all three overlay-style options", () => {
  renderStep();
  expect(
    screen.getByRole("button", { name: "onboarding.system_style.options.jarvis_bar" }),
  ).toBeDefined();
  expect(
    screen.getByRole("button", { name: "onboarding.system_style.options.mascot" }),
  ).toBeDefined();
  expect(
    screen.getByRole("button", { name: "onboarding.system_style.options.none" }),
  ).toBeDefined();
});

it("pre-selects the Jarvis Bar and labels it Recommended", () => {
  renderStep();
  // Recommended badge is shown.
  expect(screen.getByText("onboarding.system_style.recommended")).toBeDefined();
  // The bar card is the pressed/selected one.
  const bar = screen.getByRole("button", {
    name: "onboarding.system_style.options.jarvis_bar",
  });
  expect(bar.getAttribute("aria-pressed")).toBe("true");
});

it("persists the chosen style via saveStyle on click", async () => {
  renderStep();
  fireEvent.click(
    screen.getByRole("button", { name: "onboarding.system_style.options.mascot" }),
  );
  await waitFor(() => expect(saveStyle).toHaveBeenCalledWith("mascot"));
});

it("does not auto-restart on a pick (restart is a separate, explicit action)", async () => {
  const fetchSpy = vi.fn((..._args: unknown[]) =>
    Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) }),
  );
  vi.stubGlobal("fetch", fetchSpy);
  renderStep();
  fireEvent.click(
    screen.getByRole("button", { name: "onboarding.system_style.options.mascot" }),
  );
  await waitFor(() => expect(saveStyle).toHaveBeenCalled());
  // Picking alone must never hit the restart endpoint.
  const restartCalls = fetchSpy.mock.calls.filter(
    (args) => typeof args[0] === "string" && (args[0] as string).includes("restart-app"),
  );
  expect(restartCalls).toHaveLength(0);
});

it("offers a one-click restart when the pick needs one, and posts restart-app", async () => {
  const fetchSpy = vi.fn((..._args: unknown[]) =>
    Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) }),
  );
  vi.stubGlobal("fetch", fetchSpy);
  renderStep();
  // The mocked saveStyle returns applied_live:false → a restart is needed.
  fireEvent.click(
    screen.getByRole("button", { name: "onboarding.system_style.options.mascot" }),
  );
  const restartBtn = await screen.findByText("onboarding.system_style.restart_now");
  fireEvent.click(restartBtn);
  await waitFor(() =>
    expect(
      fetchSpy.mock.calls.some(
        (args) =>
          typeof args[0] === "string" && args[0].includes("/api/settings/restart-app"),
      ),
    ).toBe(true),
  );
});

it("reverts the optimistic selection when the save fails", async () => {
  saveStyle.mockRejectedValueOnce(new Error("network down"));
  renderStep();
  fireEvent.click(
    screen.getByRole("button", { name: "onboarding.system_style.options.mascot" }),
  );
  await waitFor(() => expect(saveStyle).toHaveBeenCalledWith("mascot"));
  // The pick was not persisted, so the highlighted card snaps back to the
  // last-known persisted value (jarvis_bar) and an error is shown.
  await waitFor(() =>
    expect(
      screen
        .getByRole("button", { name: "onboarding.system_style.options.jarvis_bar" })
        .getAttribute("aria-pressed"),
    ).toBe("true"),
  );
  expect(screen.getByText("onboarding.system_style.save_error")).toBeDefined();
});

it("advances on Next and on Skip", () => {
  const props = renderStep();
  fireEvent.click(screen.getByText("onboarding.nav.next"));
  expect(props.goNext).toHaveBeenCalled();
  fireEvent.click(screen.getByText("onboarding.system_style.skip"));
  expect(props.skip).toHaveBeenCalled();
});
