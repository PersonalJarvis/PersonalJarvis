/**
 * Component tests for FeedbackView.
 *
 * The section files bugs AND feature requests, both as real GitHub issues on
 * the repository's own issue forms. What the tests below protect is the wiring
 * that is invisible when it breaks: an issue opened without `?template=` still
 * opens — it just lands blank, unlabelled and unstructured, and nothing in the
 * UI looks wrong. Same for a prefilled field id that no longer exists: GitHub
 * drops it silently and that part of the report is simply gone.
 *
 * Also covered: the URL budget (a long report must not produce a dead link),
 * the account note (said before the user hits a login wall), and the board,
 * which reads open issues without any login at all.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { FeedbackView } from "@/views/feedback/FeedbackView";
import * as openExternal from "@/lib/openExternal";

const ISSUES_URL = "https://github.com/PersonalJarvis/PersonalJarvis/issues";

const STATUS_NOT_CONFIGURED = {
  configured: false,
  github_url: ISSUES_URL,
  templates: {
    bug: "bug_report.yml",
    idea: "feature_request.yml",
    question: null,
  },
  context: {
    app_version: "1.0.8",
    os: "TestOS-1.0",
    python: "3.11.0",
    os_choice: "Windows",
  },
};

const STATUS_CONFIGURED = { ...STATUS_NOT_CONFIGURED, configured: true };

const EMPTY_BOARD = { available: false, ideas: [], bugs: [], detail: "unreachable" };

const FULL_BOARD = {
  available: true,
  ideas: [{ number: 12, title: "Offline mode", url: `${ISSUES_URL}/12`, upvotes: 7, comments: 2 }],
  bugs: [{ number: 34, title: "Crash on startup", url: `${ISSUES_URL}/34`, upvotes: 3, comments: 1 }],
  detail: "",
};

/** Stub fetch for the three feedback endpoints. */
function stubFeedbackApi(status: object, opts: { board?: object; postResult?: object } = {}) {
  const fetchMock = vi.fn(async (url: unknown, init?: RequestInit) => {
    const href = String(url);
    if (href.includes("/api/feedback/status")) {
      return { ok: true, json: async () => status } as Response;
    }
    if (href.includes("/api/feedback/board")) {
      return { ok: true, json: async () => opts.board ?? EMPTY_BOARD } as Response;
    }
    if (href.includes("/api/feedback") && init?.method === "POST") {
      return {
        ok: true,
        json: async () =>
          opts.postResult ?? { ok: true, status: "sent", detail: "", github_url: null },
      } as Response;
    }
    return { ok: false, status: 404, statusText: "Not Found", json: async () => ({}) } as Response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function fillBugForm(what = "It broke when I clicked the button.", steps = "1. Click it") {
  fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Something broke" } });
  fireEvent.change(screen.getByLabelText("What happened?"), { target: { value: what } });
  fireEvent.change(screen.getByLabelText("Steps to reproduce"), { target: { value: steps } });
}

/** Decode a query value the way a server would (`+` is a space in a query). */
function param(url: string, key: string): string | null {
  return new URL(url).searchParams.get(key);
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("FeedbackView", () => {
  /** Wait for the mount-time probe so state settles inside act(). */
  async function probeSettled() {
    await screen.findByRole("button", { name: "Report the bug on GitHub" });
  }

  it("offers both report kinds plus a question route", async () => {
    stubFeedbackApi(STATUS_NOT_CONFIGURED);
    render(<FeedbackView />);

    expect(screen.getByRole("radiogroup", { name: "What is it?" })).toBeTruthy();
    expect(screen.getByRole("radio", { name: "Bug" })).toBeTruthy();
    expect(screen.getByRole("radio", { name: "Feature" })).toBeTruthy();
    expect(screen.getByRole("radio", { name: "Question" })).toBeTruthy();
    await probeSettled();
  });

  it("opens a bug on the bug issue form with every field prefilled", async () => {
    stubFeedbackApi(STATUS_NOT_CONFIGURED);
    const openSpy = vi.spyOn(openExternal, "openExternalUrl").mockResolvedValue(undefined);

    render(<FeedbackView />);
    const submit = await screen.findByRole("button", { name: "Report the bug on GitHub" });
    fillBugForm();
    fireEvent.click(submit);

    await waitFor(() => expect(openSpy).toHaveBeenCalledTimes(1));
    const url = String(openSpy.mock.calls[0][0]);
    expect(url.startsWith(`${ISSUES_URL}/new?`)).toBe(true);
    // Without the template the issue lands blank and unlabelled.
    expect(param(url, "template")).toBe("bug_report.yml");
    // Passing `title` overrides the form's own default prefix, so it is set here.
    expect(param(url, "title")).toBe("[Bug]: Something broke");
    expect(param(url, "what-happened")).toBe("It broke when I clicked the button.");
    expect(param(url, "steps")).toBe("1. Click it");
    // Both taken from the running install so nobody has to look them up.
    expect(param(url, "os")).toBe("Windows");
    expect(param(url, "python")).toBe("3.11.0");
  });

  it("opens a feature request on the feature issue form", async () => {
    stubFeedbackApi(STATUS_NOT_CONFIGURED);
    const openSpy = vi.spyOn(openExternal, "openExternalUrl").mockResolvedValue(undefined);

    render(<FeedbackView />);
    await probeSettled();
    fireEvent.click(screen.getByRole("radio", { name: "Feature" }));

    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Offline mode" } });
    fireEvent.change(screen.getByLabelText("What problem does this solve?"), {
      target: { value: "I travel without internet." },
    });
    fireEvent.change(screen.getByLabelText("How should it work?"), {
      target: { value: "Queue everything locally." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Suggest the feature on GitHub" }));

    await waitFor(() => expect(openSpy).toHaveBeenCalledTimes(1));
    const url = String(openSpy.mock.calls[0][0]);
    expect(param(url, "template")).toBe("feature_request.yml");
    expect(param(url, "title")).toBe("[Feature]: Offline mode");
    expect(param(url, "problem")).toBe("I travel without internet.");
    expect(param(url, "solution")).toBe("Queue everything locally.");
    // The bug form's fields must not leak into a feature request.
    expect(param(url, "what-happened")).toBeNull();
    expect(param(url, "os")).toBeNull();
  });

  it("keeps a long report inside the URL budget and saves the full text", async () => {
    stubFeedbackApi(STATUS_NOT_CONFIGURED);
    const openSpy = vi.spyOn(openExternal, "openExternalUrl").mockResolvedValue(undefined);
    const writeText = vi.fn(async (_text: string) => undefined);
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });

    render(<FeedbackView />);
    const submit = await screen.findByRole("button", { name: "Report the bug on GitHub" });
    // Percent-encoding roughly triples a newline-heavy body, so this alone
    // would push the URL far past what GitHub accepts.
    const huge = "line of detail\n".repeat(400);
    fillBugForm(huge, huge);
    fireEvent.click(submit);

    await waitFor(() => expect(openSpy).toHaveBeenCalledTimes(1));
    expect(String(openSpy.mock.calls[0][0]).length).toBeLessThanOrEqual(6000);
    // Nothing the user wrote may be lost: the untrimmed report goes to the
    // clipboard, and they are told so.
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    expect(String(writeText.mock.calls[0][0])).toContain(huge.trim());
    expect(screen.getByRole("status").textContent).toContain("clipboard");
  });

  it("does not open the tracker for a question", async () => {
    stubFeedbackApi(STATUS_NOT_CONFIGURED);
    const openSpy = vi.spyOn(openExternal, "openExternalUrl").mockResolvedValue(undefined);

    render(<FeedbackView />);
    await probeSettled();
    fireEvent.click(screen.getByRole("radio", { name: "Question" }));

    // A question has no issue form — the form is replaced, not just relabelled.
    expect(screen.queryByLabelText("Title")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Open Discord" }));
    expect(openSpy).toHaveBeenCalledWith("https://discord.gg/x7USduHxbc");
  });

  it("states the account requirement before the user hits the login wall", async () => {
    stubFeedbackApi(STATUS_NOT_CONFIGURED);
    render(<FeedbackView />);
    await probeSettled();

    expect(screen.getByText(/free GitHub account/)).toBeTruthy();
  });

  it("shows what others already asked for, without any login", async () => {
    stubFeedbackApi(STATUS_NOT_CONFIGURED, { board: FULL_BOARD });
    const openSpy = vi.spyOn(openExternal, "openExternalUrl").mockResolvedValue(undefined);

    render(<FeedbackView />);

    await screen.findByText("What others are asking for");
    expect(screen.getByText("Known bugs")).toBeTruthy();
    expect(screen.getByText("Offline mode")).toBeTruthy();

    fireEvent.click(screen.getByText("Offline mode"));
    expect(openSpy).toHaveBeenCalledWith(`${ISSUES_URL}/12`);
  });

  it("hides the board entirely when nothing could be loaded", async () => {
    stubFeedbackApi(STATUS_NOT_CONFIGURED, { board: EMPTY_BOARD });
    render(<FeedbackView />);
    await probeSettled();

    // An empty list would read as "nobody ever asked for anything".
    expect(screen.queryByText("What others are asking for")).toBeNull();
    expect(screen.queryByText("See all on GitHub")).toBeNull();
  });

  it("adds the account-free direct path only on an operator install", async () => {
    const fetchMock = stubFeedbackApi(STATUS_CONFIGURED);
    render(<FeedbackView />);

    // The screenshot picker rides with the direct channel — a GitHub issue URL
    // cannot carry an image.
    await screen.findByText("Click to attach a screenshot");
    fillBugForm();
    fireEvent.click(screen.getByRole("button", { name: "Send without a GitHub account" }));

    await screen.findByText("Thanks — that arrived!");
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    const body = JSON.parse(String(post?.[1]?.body)) as Record<string, unknown>;
    expect(body).toMatchObject({ type: "bug", title: "Something broke" });
    // Both fields reach the direct channel, which has no separate steps field.
    expect(String(body.description)).toContain("It broke when I clicked the button.");
    expect(String(body.description)).toContain("1. Click it");
  });

  it("offers no direct path and no screenshot picker on a plain install", async () => {
    stubFeedbackApi(STATUS_NOT_CONFIGURED);
    render(<FeedbackView />);
    await probeSettled();

    expect(screen.queryByRole("button", { name: "Send without a GitHub account" })).toBeNull();
    expect(screen.queryByText("Click to attach a screenshot")).toBeNull();
  });

  it("keeps submit disabled until the title and the first field are filled", async () => {
    stubFeedbackApi(STATUS_NOT_CONFIGURED);
    render(<FeedbackView />);

    const submit = await screen.findByRole("button", { name: "Report the bug on GitHub" });
    expect((submit as HTMLButtonElement).disabled).toBe(true);

    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Something broke" } });
    // Title alone is not a report.
    expect((submit as HTMLButtonElement).disabled).toBe(true);

    fireEvent.change(screen.getByLabelText("What happened?"), { target: { value: "It broke." } });
    await waitFor(() => expect((submit as HTMLButtonElement).disabled).toBe(false));
  });
});
