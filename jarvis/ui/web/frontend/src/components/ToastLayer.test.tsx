import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useI18nStore } from "@/i18n";
import { useEventStore } from "@/store/events";
import { ToastLayer } from "./ToastLayer";

beforeEach(() => {
  useI18nStore.getState().setUi("en", { push: false });
  useEventStore.setState({ toasts: [] });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("ToastLayer", () => {
  it("shows a plain toast without a button", () => {
    render(<ToastLayer />);
    act(() => useEventStore.getState().pushToast("info", "Saved"));

    expect(screen.getByText("Saved")).toBeTruthy();
    expect(screen.queryByTestId("toast-action")).toBeNull();
  });

  it("shows the one button of a toast, runs it, and closes the toast", () => {
    const onAction = vi.fn();
    render(<ToastLayer />);
    act(() =>
      useEventStore.getState().pushToast("warning", "Personal Jarvis needs microphone access to hear you.", {
        action: { label: "Open System Settings", onAction },
      }),
    );

    const button = screen.getByRole("button", { name: "Open System Settings" });
    fireEvent.click(button);

    expect(onAction).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("Personal Jarvis needs microphone access to hear you.")).toBeNull();
  });

  it("keeps the toast up after a button that asked to stay (a retry)", () => {
    const onAction = vi.fn();
    render(<ToastLayer />);
    act(() =>
      useEventStore.getState().pushToast("warning", "Needs a restart", {
        action: { label: "Quit and reopen", onAction, keepOpen: true },
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Quit and reopen" }));

    expect(onAction).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Needs a restart")).toBeTruthy();
  });

  it("closes with the X like every other toast", () => {
    render(<ToastLayer />);
    act(() =>
      useEventStore.getState().pushToast("info", "Needs access", {
        action: { label: "Open System Settings", onAction: vi.fn() },
      }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));

    expect(screen.queryByText("Needs access")).toBeNull();
  });

  it("the button sits in the same toast box as the message (one box, not a second layer)", () => {
    render(<ToastLayer />);
    act(() =>
      useEventStore.getState().pushToast("info", "Needs access", {
        action: { label: "Open System Settings", onAction: vi.fn() },
      }),
    );

    const box = screen.getByRole("status");
    expect(box.contains(screen.getByTestId("toast-action"))).toBe(true);
    expect(screen.getAllByRole("status")).toHaveLength(1);
  });
});

describe("ToastLayer placement", () => {
  it("renders into <body>, so it can sit above the setup spotlight (also a body-level portal)", () => {
    const { container } = render(<ToastLayer />);
    act(() => useEventStore.getState().pushToast("info", "Saved"));

    expect(container.querySelector("[role=status]")).toBeNull();
    expect(document.body.querySelector(":scope > div.fixed.z-\\[115\\] [role=status]")).not.toBeNull();
  });
});

describe("pushToast with an action and a lifetime", () => {
  it("keeps a toast with a button up for its own ttl, not the 3.5 s default", () => {
    vi.useFakeTimers();
    act(() =>
      useEventStore.getState().pushToast("info", "Needs access", {
        action: { label: "Go", onAction: vi.fn() },
        ttlMs: 20_000,
      }),
    );

    act(() => void vi.advanceTimersByTime(19_000));
    expect(useEventStore.getState().toasts).toHaveLength(1);
    act(() => void vi.advanceTimersByTime(1_500));
    expect(useEventStore.getState().toasts).toEqual([]);
  });

  it("a plain toast still goes away after the default lifetime", () => {
    vi.useFakeTimers();
    act(() => useEventStore.getState().pushToast("info", "Saved"));

    act(() => void vi.advanceTimersByTime(3_600));

    expect(useEventStore.getState().toasts).toEqual([]);
  });

  it("collapses a repeat of the same sentence and button into one toast", () => {
    const action = { label: "Go", onAction: vi.fn() };
    act(() => {
      useEventStore.getState().pushToast("info", "Needs access", { action });
      useEventStore.getState().pushToast("info", "Needs access", { action });
    });

    expect(useEventStore.getState().toasts).toHaveLength(1);
    expect(useEventStore.getState().toasts[0].count).toBe(2);
  });

  it("keeps the same sentence with another button as another toast", () => {
    act(() => {
      useEventStore.getState().pushToast("info", "Needs access", { action: { label: "Go", onAction: vi.fn() } });
      useEventStore.getState().pushToast("info", "Needs access", { action: { label: "Stay", onAction: vi.fn() } });
    });

    expect(useEventStore.getState().toasts).toHaveLength(2);
  });
});
