import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
vi.mock("@/i18n", () => ({ useT: () => (k: string) => k }));
const saveWakeWord = vi.fn().mockResolvedValue({ ok: true });
vi.mock("@/hooks/useWakeWord", () => ({ useWakeWord: () => ({ saveWakeWord }) }));
import { WakeWordStep } from "./WakeWordStep";
afterEach(() => { cleanup(); saveWakeWord.mockClear(); });

const onb = {
  state: { legal_references: [{ label: "EUIPO", url: "https://euipo.europa.eu/eSearch/" }] },
  acknowledgeWakeWord: vi.fn().mockResolvedValue(undefined),
} as never;

it("shows the derived-name preview only after a valid word is typed", () => {
  render(<WakeWordStep onb={onb} goNext={vi.fn()} goBack={vi.fn()} skip={vi.fn()} isFirst={false} isLast={false} />);
  // No preview before typing.
  expect(screen.queryByText("onboarding.wake_word.derived_name")).toBeNull();
  // A valid word (>= 2 chars) surfaces the hint line.
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Nova" } });
  expect(screen.queryByText("onboarding.wake_word.derived_name")).not.toBeNull();
});

it("requires word + acknowledgment, then saves 'Hey <word>' and advances", async () => {
  const goNext = vi.fn();
  render(<WakeWordStep onb={onb} goNext={goNext} goBack={vi.fn()} skip={vi.fn()} isFirst={false} isLast={false} />);

  // The trademark references are tucked behind a "How to check" toggle now —
  // reveal them before asserting the register link is present.
  fireEvent.click(screen.getByRole("button", { name: "onboarding.wake_word.learn_more" }));
  expect(screen.getByRole("link", { name: "EUIPO" })).toBeDefined();

  const cta = screen.getByRole("button", { name: "onboarding.wake_word.cta" });
  expect((cta as HTMLButtonElement).disabled).toBe(true);

  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Nova" } });
  expect((cta as HTMLButtonElement).disabled).toBe(true); // checkbox still unticked
  fireEvent.click(screen.getByRole("checkbox"));
  expect((cta as HTMLButtonElement).disabled).toBe(false);

  fireEvent.click(cta);
  await waitFor(() => expect(saveWakeWord).toHaveBeenCalled());
  expect(saveWakeWord.mock.calls[0][0].phrase).toBe("Hey Nova");
  expect((onb as never as { acknowledgeWakeWord: ReturnType<typeof vi.fn> }).acknowledgeWakeWord).toHaveBeenCalled();
  expect(goNext).toHaveBeenCalled();
});
