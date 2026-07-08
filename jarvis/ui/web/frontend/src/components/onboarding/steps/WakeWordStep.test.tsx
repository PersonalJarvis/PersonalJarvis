import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
vi.mock("@/i18n", () => ({ useT: () => (k: string) => k }));
const saveWakeWord = vi.fn().mockResolvedValue({ ok: true, degraded: false });
const setWakeActivation = vi.fn().mockResolvedValue({ ok: true, enabled: true, restart_required: true });
vi.mock("@/hooks/useWakeWord", () => ({
  useWakeWord: () => ({ saveWakeWord, setWakeActivation }),
  useLocalSpeechInstall: () => ({
    status: { state: "idle", message: "", available: true },
    install: vi.fn(),
  }),
}));
import { WakeWordStep } from "./WakeWordStep";
afterEach(() => {
  cleanup();
  saveWakeWord.mockClear();
  saveWakeWord.mockResolvedValue({ ok: true, degraded: false });
  setWakeActivation.mockClear();
});

const onb = {
  state: { legal_references: [{ label: "EUIPO", url: "https://euipo.europa.eu/eSearch/" }] },
  acknowledgeWakeWord: vi.fn().mockResolvedValue(undefined),
} as never;

function renderStep(goNext = vi.fn()) {
  render(<WakeWordStep onb={onb} goNext={goNext} goBack={vi.fn()} skip={vi.fn()} isFirst={false} isLast={false} />);
  return { goNext };
}

function selectWakeMode() {
  fireEvent.click(screen.getByRole("button", { name: /mode_wake_title/ }));
}

function selectShortcutMode() {
  fireEvent.click(screen.getByRole("button", { name: /mode_shortcut_title/ }));
}

it("shows the mode choice first, with wake-word and keyboard-shortcut options", () => {
  renderStep();
  expect(screen.getByRole("button", { name: /mode_wake_title/ })).toBeDefined();
  expect(screen.getByRole("button", { name: /mode_shortcut_title/ })).toBeDefined();
  // Neither the wake-word input nor the shortcut CTA are visible yet.
  expect(screen.queryByRole("textbox")).toBeNull();
});

it("keyboard-shortcut path: turns the wake word off and advances, no phrase required", async () => {
  const { goNext } = renderStep();
  selectShortcutMode();
  fireEvent.click(screen.getByRole("button", { name: "onboarding.wake_word.shortcut_cta" }));
  await waitFor(() => expect(setWakeActivation).toHaveBeenCalledWith(false));
  expect(goNext).toHaveBeenCalled();
  // The wake-only save path was never touched.
  expect(saveWakeWord).not.toHaveBeenCalled();
});

it("back-to-choice returns from a chosen mode to the mode picker", () => {
  renderStep();
  selectWakeMode();
  expect(screen.getByRole("textbox")).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "onboarding.wake_word.back_to_choice" }));
  expect(screen.getByRole("button", { name: /mode_wake_title/ })).toBeDefined();
  expect(screen.queryByRole("textbox")).toBeNull();
});

it("wake-word path: shows the derived-name preview only after a valid word is typed", () => {
  renderStep();
  selectWakeMode();
  expect(screen.queryByText("onboarding.wake_word.derived_name")).toBeNull();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Nova" } });
  expect(screen.queryByText("onboarding.wake_word.derived_name")).not.toBeNull();
});

it("wake-word path: requires word + ack, saves 'Hey <word>', activates the wake word, and advances", async () => {
  const { goNext } = renderStep();
  selectWakeMode();

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
  await waitFor(() => expect(setWakeActivation).toHaveBeenCalledWith(true));
  expect(goNext).toHaveBeenCalled();
});

it("wake-word path: a degraded save does NOT advance and offers the local-speech install", async () => {
  saveWakeWord.mockResolvedValue({ ok: true, degraded: true });
  const { goNext } = renderStep();
  selectWakeMode();

  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Nova" } });
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(screen.getByRole("button", { name: "onboarding.wake_word.cta" }));

  await waitFor(() =>
    expect(screen.getByText("settings_view.wake_word.needs_whisper_hint")).toBeDefined(),
  );
  expect(goNext).not.toHaveBeenCalled();
  expect(setWakeActivation).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("button", { name: "onboarding.wake_word.continue_anyway" }));
  await waitFor(() => expect(setWakeActivation).toHaveBeenCalledWith(true));
  expect(goNext).toHaveBeenCalled();
});

it("wake-word path: renders a mic-check control that reports a good level", async () => {
  const fetchSpy = vi.fn(() =>
    Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ max_dbfs: -15.0, no_device: false, too_quiet: false }),
    }),
  );
  vi.stubGlobal("fetch", fetchSpy);
  renderStep();
  selectWakeMode();

  expect(screen.getByText("onboarding.wake_word.mic_check.title")).toBeDefined();
  const testButton = screen.getByRole("button", { name: "onboarding.wake_word.mic_check.test_button" });
  expect(screen.getByRole("button", { name: "onboarding.wake_word.mic_check.say_once_button" })).toBeDefined();

  fireEvent.click(testButton);
  await waitFor(() =>
    expect(fetchSpy).toHaveBeenCalledWith("/api/settings/wake-word/mic-level"),
  );
  await waitFor(() => expect(screen.getByText("onboarding.wake_word.mic_check.good")).toBeDefined());
});

it("wake-word path: mic-check shows the amber too-quiet warning without blocking the save CTA", async () => {
  const fetchSpy = vi.fn(() =>
    Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ max_dbfs: -55.0, no_device: false, too_quiet: true }),
    }),
  );
  vi.stubGlobal("fetch", fetchSpy);
  renderStep();
  selectWakeMode();

  fireEvent.click(screen.getByRole("button", { name: "onboarding.wake_word.mic_check.test_button" }));
  const warning = await screen.findByText("onboarding.wake_word.mic_check.too_quiet");
  expect(warning).toBeDefined();

  // Acknowledgment must never be blocked by a failed/quiet mic check — the
  // save CTA is only gated on word length + the ack checkbox.
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Nova" } });
  fireEvent.click(screen.getByRole("checkbox"));
  const cta = screen.getByRole("button", { name: "onboarding.wake_word.cta" });
  expect((cta as HTMLButtonElement).disabled).toBe(false);
});

it("wake-word path: mic-check reports a neutral no-device state on a headless host", async () => {
  const fetchSpy = vi.fn(() =>
    Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ max_dbfs: -120.0, no_device: true, too_quiet: false }),
    }),
  );
  vi.stubGlobal("fetch", fetchSpy);
  renderStep();
  selectWakeMode();

  fireEvent.click(screen.getByRole("button", { name: "onboarding.wake_word.mic_check.say_once_button" }));
  await waitFor(() =>
    expect(screen.getByText("onboarding.wake_word.mic_check.no_device")).toBeDefined(),
  );
});
