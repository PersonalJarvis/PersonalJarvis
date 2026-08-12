/**
 * Component tests for FeedbackView.
 *
 * The Feedback section offers two honest paths:
 *   1. Discord (primary): a button straight to the #report-a-bug forum plus a
 *      permanent invite, both via {@link openExternalUrl} — NOT a bare anchor,
 *      because the desktop WebView2 shell drops target="_blank".
 *   2. An in-app form (secondary) that probes GET /api/feedback/status first:
 *      - webhook configured  → POST /api/feedback (direct dispatch).
 *      - not configured      → the submit button opens a PREFILLED GitHub
 *        issue with the typed report + system context instead.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { FeedbackView } from "@/views/feedback/FeedbackView";
import * as openExternal from "@/lib/openExternal";

const STATUS_NOT_CONFIGURED = {
  configured: false,
  github_url: "https://github.com/PersonalJarvis/PersonalJarvis/issues",
  context: { app_version: "1.0.8", os: "TestOS-1.0", python: "3.11.0" },
};

const STATUS_CONFIGURED = { ...STATUS_NOT_CONFIGURED, configured: true };

/** Stub fetch: GET /status returns `status`, POST /api/feedback returns `postResult`. */
function stubFeedbackApi(status: object, postResult?: object) {
  const fetchMock = vi.fn(async (url: unknown, init?: RequestInit) => {
    const href = String(url);
    if (href.includes("/api/feedback/status")) {
      return { ok: true, json: async () => status } as Response;
    }
    if (href.includes("/api/feedback") && init?.method === "POST") {
      return {
        ok: true,
        json: async () =>
          postResult ?? { ok: true, status: "sent", detail: "", github_url: null },
      } as Response;
    }
    return { ok: false, status: 404, statusText: "Not Found", json: async () => ({}) } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function fillForm() {
  fireEvent.change(screen.getByLabelText("Title"), {
    target: { value: "Something broke" },
  });
  fireEvent.change(screen.getByLabelText("Description"), {
    target: { value: "It broke when I clicked the button." },
  });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("FeedbackView", () => {
  /** Wait for the mount-time status probe so state settles inside act(). */
  async function probeSettled() {
    await screen.findByRole("button", { name: "Open a prefilled GitHub issue" });
  }

  it("renders the Discord call-to-action with a forward button", async () => {
    stubFeedbackApi(STATUS_NOT_CONFIGURED);
    render(<FeedbackView />);

    expect(
      screen.getByRole("button", { name: "Open #report-a-bug" }),
    ).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Join Discord first" }),
    ).toBeTruthy();
    await probeSettled();
  });

  it("opens the bug forum directly via the external-open bridge", async () => {
    stubFeedbackApi(STATUS_NOT_CONFIGURED);
    const openSpy = vi
      .spyOn(openExternal, "openExternalUrl")
      .mockResolvedValue(undefined);

    render(<FeedbackView />);
    fireEvent.click(
      screen.getByRole("button", { name: "Open #report-a-bug" }),
    );

    expect(openSpy).toHaveBeenCalledTimes(1);
    expect(openSpy).toHaveBeenCalledWith(
      "https://discord.com/channels/1511102439066177656/1521522036709789736",
    );
    await probeSettled();
  });

  it("keeps a permanent server invite for people who have not joined yet", async () => {
    stubFeedbackApi(STATUS_NOT_CONFIGURED);
    const openSpy = vi
      .spyOn(openExternal, "openExternalUrl")
      .mockResolvedValue(undefined);

    render(<FeedbackView />);
    fireEvent.click(
      screen.getByRole("button", { name: "Join Discord first" }),
    );

    expect(openSpy).toHaveBeenCalledTimes(1);
    expect(openSpy).toHaveBeenCalledWith("https://discord.gg/x7USduHxbc");
    await probeSettled();
  });

  it("renders the in-app form below the Discord card", async () => {
    stubFeedbackApi(STATUS_NOT_CONFIGURED);
    render(<FeedbackView />);

    expect(screen.getByLabelText("Title")).toBeTruthy();
    expect(screen.getByLabelText("Description")).toBeTruthy();
    expect(screen.getByRole("radiogroup", { name: "Type" })).toBeTruthy();
    expect(screen.getByRole("radio", { name: "Bug" })).toBeTruthy();
    expect(screen.getByRole("radio", { name: "Idea" })).toBeTruthy();
    expect(screen.getByRole("radio", { name: "Question" })).toBeTruthy();
    await probeSettled();
  });

  it("opens a prefilled GitHub issue when no feedback channel is configured", async () => {
    stubFeedbackApi(STATUS_NOT_CONFIGURED);
    const openSpy = vi
      .spyOn(openExternal, "openExternalUrl")
      .mockResolvedValue(undefined);

    render(<FeedbackView />);

    // The probe resolves → the submit button flips to GitHub mode.
    const submit = await screen.findByRole("button", {
      name: "Open a prefilled GitHub issue",
    });
    fillForm();
    fireEvent.click(submit);

    await waitFor(() => expect(openSpy).toHaveBeenCalledTimes(1));
    const url = String(openSpy.mock.calls[0][0]);
    expect(url).toContain(
      "https://github.com/PersonalJarvis/PersonalJarvis/issues/new?",
    );
    expect(url).toContain("Something+broke");
    // System context from the status probe rides along in the issue body.
    expect(decodeURIComponent(url.replace(/\+/g, " "))).toContain(
      "App version: 1.0.8",
    );
  });

  it("hides the screenshot picker in GitHub mode (a URL cannot carry an image)", async () => {
    stubFeedbackApi(STATUS_NOT_CONFIGURED);
    render(<FeedbackView />);

    await screen.findByRole("button", { name: "Open a prefilled GitHub issue" });
    expect(screen.queryByText("Click to attach a screenshot")).toBeNull();
  });

  it("dispatches through POST /api/feedback when the channel is configured", async () => {
    const fetchMock = stubFeedbackApi(STATUS_CONFIGURED);
    render(<FeedbackView />);

    // Configured mode keeps the plain submit label and shows the picker.
    await screen.findByText("Click to attach a screenshot");
    fillForm();
    fireEvent.click(screen.getByRole("button", { name: "Submit" }));

    await screen.findByText("Thanks — sent to our Discord!");
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(post).toBeTruthy();
    const body = JSON.parse(String(post?.[1]?.body)) as Record<string, unknown>;
    expect(body).toMatchObject({
      type: "bug",
      title: "Something broke",
      description: "It broke when I clicked the button.",
    });
  });

  it("keeps submit disabled until title and description are filled", async () => {
    stubFeedbackApi(STATUS_CONFIGURED);
    render(<FeedbackView />);

    const submit = screen.getByRole("button", { name: "Submit" });
    expect((submit as HTMLButtonElement).disabled).toBe(true);
    fillForm();
    await waitFor(() =>
      expect((submit as HTMLButtonElement).disabled).toBe(false),
    );
  });
});
