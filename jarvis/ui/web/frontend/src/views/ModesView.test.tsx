/**
 * Component tests for ModesView — the shelf of characters.
 *
 * Pinned here is what a user noticed was missing: a card can be OPENED to read
 * everything the mode does (the full character text, not the one-liner), a mode
 * can be switched with one explicit click, and while the Agentic IDE holds the
 * persona the screen tells the truth — the click was stored (chosen) even though
 * something else is in force (active) — instead of looking broken.
 *
 * Driven through a stateful fetch fake, so a switch and the re-read that
 * follows agree with each other. No jest-dom in this repo — assertions use
 * toBeTruthy()/toBeNull().
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

// ViewHeader lives in ChatsView, which drags in the whole chat surface; the
// view only needs its shape.
vi.mock("@/views/ChatsView", () => ({
  ViewHeader: ({ title }: { title: string }) => <header>{title}</header>,
}));

// The voice interview button needs a call controller; none of these tests
// start a call, so a quiet stub keeps the realtime stack out of the render.
vi.mock("@/components/agentic/useVoiceCall", () => ({
  useVoiceCall: () => ({
    active: false,
    busy: false,
    connecting: false,
    toggleCall: vi.fn(async () => undefined),
    voiceState: "idle",
  }),
}));

import { ModesView } from "@/views/ModesView";
import { setUiLanguage } from "@/i18n";
import { useEventStore } from "@/store/events";
import type { AssistantMode } from "@/lib/modesApi";

const MODES: AssistantMode[] = [
  {
    slug: "assistant",
    name: "Assistant",
    emoji: "🎩",
    description: "Precise and quiet.",
    character: "",
    built_in: true,
    edited: false,
    voice: "",
    verbosity: "normal",
    proactivity: "normal",
  },
  {
    slug: "friend",
    name: "Friend",
    emoji: "🫂",
    description: "Warm and casual.",
    character: "You are talking to a friend, not serving a client.\n\nDrop the deference.",
    built_in: true,
    edited: false,
    voice: "",
    verbosity: "normal",
    proactivity: "normal",
  },
  {
    slug: "coding",
    name: "Coding",
    emoji: "💻",
    description: "Terminal-aware.",
    character: "The user is working in the Agentic IDE.",
    built_in: true,
    edited: false,
    voice: "",
    verbosity: "normal",
    proactivity: "normal",
  },
  {
    slug: "night-owl",
    name: "Night Owl",
    emoji: "🦉",
    description: "Quiet and short.",
    character: "Speak quietly. It is late.",
    built_in: false,
    edited: false,
    voice: "nova",
    verbosity: "brief",
    proactivity: "reactive",
  },
];

interface Call {
  url: string;
  method: string;
  body: unknown;
}

/**
 * A stand-in for /api/modes that keeps the one bit of state that matters: the
 * user's stored choice, and — optionally — a section override that outranks it,
 * exactly the way the backend's `active_slug()` composes the two.
 */
function stubServer(opts: { chosen?: string; override?: string } = {}) {
  const state = { chosen: opts.chosen ?? "assistant", override: opts.override ?? "" };
  const calls: Call[] = [];
  const payload = () => ({
    modes: MODES,
    active: state.override || state.chosen,
    chosen: state.chosen,
    section_override: state.override,
    verbosities: ["brief", "normal", "rich"],
    proactivities: ["reactive", "normal", "forward"],
  });
  const json = (body: unknown, status = 200) => ({
    ok: status >= 200 && status < 300,
    status,
    statusText: "OK",
    json: async () => body,
  });

  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      const body = init?.body ? JSON.parse(init.body as string) : null;
      calls.push({ url, method, body });
      if (url === "/api/modes" && method === "GET") return json(payload());
      if (url === "/api/modes/active" && method === "PUT") {
        state.chosen = (body as { slug: string }).slug;
        return json({ ok: true, ...payload(), chosen: state.chosen, restart_required: false });
      }
      if (url === "/api/modes" && method === "POST") return json({ ok: true, ...payload() });
      return json({ detail: `unexpected ${method} ${url}` }, 404);
    }),
  );
  return { calls, state };
}

beforeEach(() => {
  setUiLanguage("en");
  useEventStore.setState({ toasts: [] });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function renderView() {
  render(<ModesView />);
  await waitFor(() => expect(screen.getByTestId("mode-card-friend")).toBeTruthy());
}

describe("ModesView — reading a mode", () => {
  it("opens with the mode in force and shows its full character text", async () => {
    stubServer({ chosen: "friend" });
    await renderView();

    const detail = screen.getByTestId("mode-detail");
    expect(within(detail).getByText("Friend")).toBeTruthy();
    // The full text, verbatim — not the one-line description.
    expect(screen.getByTestId("mode-detail-character").textContent).toContain(
      "You are talking to a friend, not serving a client.",
    );
    expect(within(detail).getByText("In use now")).toBeTruthy();
  });

  it("clicking a card opens it without switching to it", async () => {
    const server = stubServer({ chosen: "assistant" });
    await renderView();

    fireEvent.click(screen.getByTestId("mode-card-night-owl"));

    const detail = screen.getByTestId("mode-detail");
    expect(within(detail).getByText("Night Owl")).toBeTruthy();
    expect(screen.getByTestId("mode-detail-character").textContent).toContain(
      "Speak quietly. It is late.",
    );
    // The knobs and the voice are spelled out, not just the description.
    expect(within(detail).getByText("Short answers")).toBeTruthy();
    expect(within(detail).getByText("Answers only what was asked")).toBeTruthy();
    expect(within(detail).getByText("nova")).toBeTruthy();
    expect(within(detail).getByText("Yours")).toBeTruthy();
    // Reading is not choosing: nothing was written.
    expect(server.calls.filter((c) => c.method === "PUT")).toHaveLength(0);
    expect(server.state.chosen).toBe("assistant");
  });

  it("states that the default mode adds nothing rather than showing a blank box", async () => {
    stubServer({ chosen: "assistant" });
    await renderView();
    expect(screen.getByTestId("mode-detail-character").textContent).toContain(
      "Adds nothing on top of the base persona",
    );
  });
});

describe("ModesView — switching", () => {
  it("the card's Use switches with one click and the shelf follows", async () => {
    const server = stubServer({ chosen: "assistant" });
    await renderView();

    fireEvent.click(screen.getByTestId("mode-use-friend"));

    await waitFor(() => expect(server.state.chosen).toBe("friend"));
    const put = server.calls.find((c) => c.method === "PUT");
    expect(put?.url).toBe("/api/modes/active");
    expect(put?.body).toEqual({ slug: "friend" });
    await waitFor(() =>
      expect(screen.getByTestId("mode-card-friend").getAttribute("data-active")).toBe("true"),
    );
    expect(screen.getByTestId("mode-card-assistant").getAttribute("data-active")).toBeNull();
    // The card in use offers no second "Use".
    expect(screen.queryByTestId("mode-use-friend")).toBeNull();
    expect(screen.getByTestId("mode-use-assistant")).toBeTruthy();
  });

  it("the open panel's Use switches too", async () => {
    const server = stubServer({ chosen: "assistant" });
    await renderView();

    fireEvent.click(screen.getByTestId("mode-card-night-owl"));
    fireEvent.click(screen.getByTestId("mode-detail-use"));

    await waitFor(() => expect(server.state.chosen).toBe("night-owl"));
    await waitFor(() =>
      expect(within(screen.getByTestId("mode-detail")).getByText("In use now")).toBeTruthy(),
    );
  });
});

describe("ModesView — while the Agentic IDE holds the persona", () => {
  it("says which mode is in force, and that the click was stored", async () => {
    const server = stubServer({ chosen: "assistant", override: "coding" });
    await renderView();

    const banner = screen.getByTestId("mode-override-banner");
    expect(banner.textContent).toContain("Coding is in force");
    expect(banner.textContent).toContain("Your own choice, Assistant");
    expect(screen.getByTestId("mode-card-coding").getAttribute("data-active")).toBe("true");

    fireEvent.click(screen.getByTestId("mode-use-friend"));
    await waitFor(() => expect(server.state.chosen).toBe("friend"));

    // Coding stays in force — but the shelf now shows Friend as the stored
    // choice, and a toast says so, instead of the click vanishing.
    await waitFor(() =>
      expect(within(screen.getByTestId("mode-card-friend")).getByText("Your choice")).toBeTruthy(),
    );
    expect(screen.getByTestId("mode-card-coding").getAttribute("data-active")).toBe("true");
    expect(screen.getByTestId("mode-override-banner").textContent).toContain(
      "Your own choice, Friend",
    );
    const toasts = useEventStore.getState().toasts;
    expect(toasts.some((t) => t.message.includes("Friend is saved as your choice"))).toBe(true);
  });
});

describe("ModesView — editing", () => {
  it("Edit loads the mode into the form and saves it back under the same id", async () => {
    const server = stubServer({ chosen: "assistant" });
    await renderView();

    fireEvent.click(screen.getByTestId("mode-card-night-owl"));
    fireEvent.click(screen.getByTestId("mode-detail-edit"));

    const form = screen.getByTestId("mode-form");
    expect(within(form).getByText("Editing Night Owl")).toBeTruthy();
    const character = within(form).getByLabelText("How it behaves") as HTMLTextAreaElement;
    expect(character.value).toBe("Speak quietly. It is late.");

    fireEvent.change(character, { target: { value: "Speak quietly. It is very late." } });
    fireEvent.click(screen.getByTestId("mode-save-button"));

    await waitFor(() => expect(server.calls.some((c) => c.method === "POST")).toBe(true));
    const post = server.calls.find((c) => c.method === "POST")?.body as Record<string, unknown>;
    expect(post.slug).toBe("night-owl");
    expect(post.name).toBe("Night Owl");
    expect(post.character).toBe("Speak quietly. It is very late.");
    expect(post.voice).toBe("nova");
  });
});
