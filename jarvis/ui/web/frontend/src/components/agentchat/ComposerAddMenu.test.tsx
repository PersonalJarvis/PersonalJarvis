import { useRef, useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ComposerAddMenu } from "./ComposerAddMenu";
import { ToolChoiceChips } from "./ToolChoiceChips";
import { readToolChoices, type ToolChoice } from "./toolChoices";
import { sendAgentChatMessage } from "@/lib/agentChatApi";
import gmailLogo from "@/assets/brands/gmail.svg?url";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));

const gmail: ToolChoice = {
  id: "plugin:gmail",
  label: "Gmail",
  brand: "gmail",
  category: "plugins",
  group: "Gmail",
  description: "Read your inbox",
  available: true,
  tool_names: ["gmail"],
  skill: "",
};
const disconnected = {
  ...gmail,
  id: "plugin:offline",
  label: "Offline",
  available: false,
};

function setup(items = [gmail, disconnected]) {
  const connect = vi.fn(),
    attach = vi.fn(),
    folder = vi.fn();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({ items, mode: "browse", total: items.length }))),
  );
  function Harness() {
    const anchor = useRef<HTMLDivElement>(null);
    const [selected, setSelected] = useState<ToolChoice[]>([]);
    return (
      <div ref={anchor}>
        <ToolChoiceChips
          items={selected}
          onRemove={(id) => setSelected(selected.filter((r) => r.id !== id))}
        />
        <ComposerAddMenu
          anchorRef={anchor}
          provider="openai"
          model="test"
          cwd=""
          stance="ask"
          selected={selected}
          onChange={setSelected}
          onAttach={attach}
          onFolder={folder}
          onConnect={connect}
          disabled={false}
        />
      </div>
    );
  }
  render(<Harness />);
  fireEvent.click(screen.getByTestId("composer-add"));
  return { connect, attach, folder };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Add menu", () => {
  it("shows the original Gmail mark and toggles removable selections", async () => {
    setup();
    const row = await screen.findByRole("button", {
      name: /Gmail Read your inbox/,
    });
    expect(row.querySelector("img")?.getAttribute("src")).toBe(gmailLogo);
    fireEvent.click(row);
    expect(row.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByTestId("tool-choice-chips").textContent).toContain("Gmail");
    fireEvent.click(screen.getByRole("button", { name: "chat_tools.remove Gmail" }));
    expect(row.getAttribute("aria-pressed")).toBe("false");
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(screen.getByTestId("composer-add"));
  });

  it("opens setup for unavailable plugins once they are searched for", async () => {
    const { connect } = setup();
    await screen.findByRole("button", { name: /Gmail Read your inbox/ });
    expect(screen.queryByRole("button", { name: /Offline Read your inbox/ })).toBeNull();
    fireEvent.change(screen.getByLabelText("chat_tools.search"), {
      target: { value: "Offline" },
    });
    fireEvent.click(
      await screen.findByRole("button", { name: /Offline Read your inbox/ }, { timeout: 2000 }),
    );
    expect(connect).toHaveBeenCalledOnce();
    expect(screen.queryByTestId("tool-choice-chips")).toBeNull();
  });

  it("lists the connected plugin, not its commands or bundled skill", async () => {
    setup([
      gmail,
      { ...gmail, id: "tool:gmail/send", label: "send", description: "Send a message" },
      {
        ...gmail,
        id: "skill:plugin-gmail",
        label: "plugin-gmail",
        category: "skills",
        group: "skills",
        skill: "plugin-gmail",
        description: "Use Gmail",
      },
    ]);
    expect(await screen.findByRole("button", { name: /Gmail Read your inbox/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /send Send a message/ })).toBeNull();
    expect(screen.queryByText("plugin-gmail")).toBeNull();
  });

  it("forwards category and natural-language query to the server", async () => {
    setup();
    await screen.findByRole("button", { name: /Gmail Read your inbox/ });
    fireEvent.click(screen.getByLabelText("chat_tools.filter"));
    const memoryOption = await screen.findByRole("option", { name: "chat_tools.memory" });
    fireEvent.pointerDown(memoryOption);
    fireEvent.click(memoryOption);
    fireEvent.change(screen.getByLabelText("chat_tools.search"), {
      target: { value: "what did I decide" },
    });
    await waitFor(
      () => {
        const calls = vi.mocked(fetch).mock.calls;
        const url = String(calls[calls.length - 1][0]);
        expect(url).toContain("category=memory");
        expect(url).toContain("q=what+did+I+decide");
      },
      { timeout: 1500 },
    );
  });

  it("keeps file and folder actions functional", async () => {
    const { attach } = setup();
    fireEvent.click(screen.getByRole("button", { name: "chat_tools.attach" }));
    expect(attach).toHaveBeenCalledOnce();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("reports errors with retry instead of an empty result", async () => {
    setup();
    vi.mocked(fetch).mockRejectedValue(new Error("offline"));
    expect(await screen.findByRole("alert")).toBeDefined();
    expect(screen.getByRole("button", { name: "chat_tools.retry" })).toBeDefined();
  });

  it("sends only choice IDs and supports old receipts", async () => {
    const fetcher = vi.fn<typeof fetch>(async () => new Response('{"turn_id":"turn"}'));
    vi.stubGlobal("fetch", fetcher);
    await sendAgentChatMessage("session", "Read it", [], [gmail.id]);
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual(
      expect.objectContaining({
        text: "Read it",
        attachments: [],
        tool_choices: ["plugin:gmail"],
      }),
    );
    expect(readToolChoices(undefined)).toEqual([]);
    expect(readToolChoices([gmail, { id: "invalid" }])).toEqual([gmail]);
  });
});
