import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
vi.mock("@/i18n", () => ({ useT: () => (k: string) => k }));
const localSpeech = vi.hoisted(() => ({
  onInstalled: undefined as (() => void) | undefined,
  startInstall: vi.fn(),
}));
const saveWakeWord = vi.fn().mockResolvedValue({ ok: true, degraded: false });
const setWakeActivation = vi.fn().mockResolvedValue({
  ok: true,
  enabled: true,
  applied_live: true,
  restart_required: false,
});
vi.mock("@/hooks/useWakeWord", () => ({
  useWakeWord: () => ({ saveWakeWord, setWakeActivation }),
  useLocalSpeechInstall: (onInstalled?: () => void) => {
    localSpeech.onInstalled = onInstalled;
    return {
      status: { state: "idle", message: "", available: true },
      install: localSpeech.startInstall,
    };
  },
}));
import { WakeWordStep } from "./WakeWordStep";
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  saveWakeWord.mockClear();
  saveWakeWord.mockResolvedValue({ ok: true, degraded: false });
  setWakeActivation.mockClear();
  localSpeech.startInstall.mockClear();
  localSpeech.onInstalled = undefined;
});

const onb = {
  state: { legal_references: [{ label: "EUIPO", url: "https://euipo.europa.eu/eSearch/" }] },
  acknowledgeWakeWord: vi.fn().mockResolvedValue(undefined),
} as never;

function renderStep(goNext = vi.fn(), setSummary = vi.fn()) {
  render(
    <WakeWordStep
      onb={onb}
      goNext={goNext}
      goBack={vi.fn()}
      skip={vi.fn()}
      isFirst={false}
      isLast={false}
      setSummary={setSummary}
      summaries={{}}
    />,
  );
  return { goNext, setSummary };
}

const primary = () => screen.getByTestId("onboarding-primary") as HTMLButtonElement;

it("offers both activation paths on one screen, wake word preselected with its input open", () => {
  renderStep();
  expect(screen.getByTestId("wake-mode-wake").getAttribute("aria-checked")).toBe("true");
  expect(screen.getByTestId("wake-mode-shortcut").getAttribute("aria-checked")).toBe("false");
  expect(screen.getByRole("textbox")).toBeDefined();
});

it("keyboard-shortcut path: turns the wake word off and advances, no phrase required", async () => {
  const { goNext, setSummary } = renderStep();
  fireEvent.click(screen.getByTestId("wake-mode-shortcut"));
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(setSummary).toHaveBeenLastCalledWith("onboarding.wake_word.summary_shortcut");
  fireEvent.click(primary());
  await waitFor(() => expect(setWakeActivation).toHaveBeenCalledWith(false));
  expect(goNext).toHaveBeenCalled();
  expect(saveWakeWord).not.toHaveBeenCalled();
});

it("wake-word path: shows the derived-name preview and reports 'Hey <word>' as the summary", () => {
  const { setSummary } = renderStep();
  expect(screen.queryByText("onboarding.wake_word.derived_name")).toBeNull();
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Nova" } });
  expect(screen.queryByText("onboarding.wake_word.derived_name")).not.toBeNull();
  expect(setSummary).toHaveBeenLastCalledWith("Hey Nova");
});

it("wake-word path: requires word + ack, saves 'Hey <word>', activates, and advances", async () => {
  const { goNext } = renderStep();

  fireEvent.click(screen.getByRole("button", { name: "onboarding.wake_word.learn_more" }));
  expect(screen.getByRole("link", { name: "EUIPO" })).toBeDefined();

  expect(primary().disabled).toBe(true);
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Nova" } });
  expect(primary().disabled).toBe(true); // checkbox still unticked
  fireEvent.click(screen.getByTestId("wake-ack"));
  expect(primary().disabled).toBe(false);

  fireEvent.click(primary());
  await waitFor(() => expect(saveWakeWord).toHaveBeenCalled());
  expect(saveWakeWord.mock.calls[0][0].phrase).toBe("Hey Nova");
  expect(
    (onb as never as { acknowledgeWakeWord: ReturnType<typeof vi.fn> }).acknowledgeWakeWord,
  ).toHaveBeenCalled();
  await waitFor(() => expect(setWakeActivation).toHaveBeenCalledWith(true));
  expect(goNext).toHaveBeenCalled();
});

it("wake-word path: a degraded save does NOT advance and offers the local-speech install", async () => {
  saveWakeWord.mockResolvedValue({ ok: true, degraded: true });
  const { goNext } = renderStep();

  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Nova" } });
  fireEvent.click(screen.getByTestId("wake-ack"));
  fireEvent.click(primary());

  await waitFor(() =>
    expect(screen.getByText("settings_view.wake_word.needs_whisper_hint")).toBeDefined(),
  );
  expect(goNext).not.toHaveBeenCalled();
  expect(setWakeActivation).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("button", { name: "onboarding.wake_word.continue_anyway" }));
  await waitFor(() => expect(setWakeActivation).toHaveBeenCalledWith(true));
  expect(goNext).toHaveBeenCalled();
});

it("wake-word path: completed local install clears the stale degraded warning", async () => {
  saveWakeWord
    .mockResolvedValueOnce({ ok: true, degraded: true })
    .mockResolvedValue({ ok: true, degraded: false });
  const { goNext } = renderStep();

  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Nova" } });
  fireEvent.click(screen.getByTestId("wake-ack"));
  fireEvent.click(primary());

  await screen.findByText("settings_view.wake_word.needs_whisper_hint");
  fireEvent.click(
    screen.getByRole("button", { name: "settings_view.wake_word.enable_local_button" }),
  );
  expect(localSpeech.startInstall).toHaveBeenCalledOnce();

  act(() => localSpeech.onInstalled?.());

  await waitFor(() =>
    expect(screen.queryByText("settings_view.wake_word.needs_whisper_hint")).toBeNull(),
  );
  expect(
    screen.queryByRole("button", { name: "onboarding.wake_word.continue_anyway" }),
  ).toBeNull();

  // The normal CTA re-validates the already-persisted phrase and activates it.
  fireEvent.click(primary());
  await waitFor(() => expect(saveWakeWord).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(setWakeActivation).toHaveBeenCalledWith(true));
  expect(goNext).toHaveBeenCalled();
});

it("renders ONE mic-check control that reports a good level", async () => {
  const fetchSpy = vi.fn(() =>
    Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ max_dbfs: -15.0, no_device: false, too_quiet: false }),
    }),
  );
  vi.stubGlobal("fetch", fetchSpy);
  renderStep();

  expect(screen.getAllByTestId("wake-mic-test")).toHaveLength(1);
  fireEvent.click(screen.getByTestId("wake-mic-test"));
  await waitFor(() =>
    expect(fetchSpy).toHaveBeenCalledWith("/api/settings/wake-word/mic-level"),
  );
  await waitFor(() => expect(screen.getByText("onboarding.wake_word.mic_check.good")).toBeDefined());
});

it("mic-check shows the too-quiet warning without blocking the save CTA", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ max_dbfs: -55.0, no_device: false, too_quiet: true }),
      }),
    ),
  );
  renderStep();
  fireEvent.click(screen.getByTestId("wake-mic-test"));
  await waitFor(() =>
    expect(screen.getByText("onboarding.wake_word.mic_check.too_quiet")).toBeDefined(),
  );
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Nova" } });
  fireEvent.click(screen.getByTestId("wake-ack"));
  expect(primary().disabled).toBe(false);
});

it("mic-check surfaces permission_required and a probe failure honestly", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: () =>
          Promise.resolve({
            max_dbfs: -90,
            no_device: false,
            too_quiet: true,
            permission_required: true,
          }),
      }),
    ),
  );
  renderStep();
  fireEvent.click(screen.getByTestId("wake-mic-test"));
  await waitFor(() =>
    expect(
      screen.getByText("onboarding.wake_word.mic_check.permission_required"),
    ).toBeDefined(),
  );
  cleanup();
  vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("down"))));
  renderStep();
  fireEvent.click(screen.getByTestId("wake-mic-test"));
  await waitFor(() =>
    expect(screen.getByText("onboarding.wake_word.mic_check.error")).toBeDefined(),
  );
});
