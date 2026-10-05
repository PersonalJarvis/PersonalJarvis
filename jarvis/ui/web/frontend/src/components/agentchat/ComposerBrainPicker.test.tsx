import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ComposerBrainPicker } from "@/components/agentchat/ComposerBrainPicker";
import { matchPreviews } from "@/components/agentchat/useChatAttachments";
import type { ChatAttachment } from "@/lib/agentChatApi";

const GROUPS = [
  {
    id: "claude",
    label: "Claude",
    options: [
      { value: "claude\u0001", label: "Default model", triggerLabel: "Claude" },
      { value: "claude\u0001opus", label: "Opus" },
    ],
  },
  {
    id: "grok",
    label: "Grok",
    options: [{ value: "grok\u0001grok-5", label: "Grok 5" }],
  },
];

const SECTIONS = [
  { id: "claude", label: "Claude", icon: <span>C</span> },
  { id: "grok", label: "Grok", icon: <span>G</span> },
];

function renderPicker(onChange = vi.fn()) {
  render(
    <ComposerBrainPicker
      value={"claude\u0001opus"}
      groups={GROUPS}
      sections={SECTIONS}
      currentSection="claude"
      onChange={onChange}
      ariaLabel="Model"
      fallbackLabel="Model"
      searchPlaceholder="Search models…"
    />,
  );
  return onChange;
}

function optionValues(panel: HTMLElement): (string | null)[] {
  return within(panel).getAllByRole("option").map((el) => el.getAttribute("data-value"));
}

describe("ComposerBrainPicker", () => {
  beforeEach(() => {
    window.localStorage.clear();
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      writable: true,
      value: vi.fn(),
    });
  });
  afterEach(() => window.localStorage.clear());

  it("opens on the answering provider and browses another from the rail", async () => {
    const onChange = renderPicker();
    expect(screen.getByTestId("composer-model").textContent).toContain("Opus");
    fireEvent.click(screen.getByTestId("composer-model"));
    const panel = await screen.findByTestId("composer-model-panel");
    expect(optionValues(panel)).toEqual(["claude\u0001", "claude\u0001opus"]);

    fireEvent.click(within(panel).getByTestId("composer-model-rail-grok"));
    expect(optionValues(panel)).toEqual(["grok\u0001grok-5"]);
    fireEvent.click(within(panel).getByText("Grok 5"));
    expect(onChange).toHaveBeenCalledWith("grok\u0001grok-5");
  });

  it("searches every provider whatever the rail shows", async () => {
    renderPicker();
    fireEvent.click(screen.getByTestId("composer-model"));
    const panel = await screen.findByTestId("composer-model-panel");
    fireEvent.change(within(panel).getByTestId("composer-model-search"), { target: { value: "grok" } });
    expect(optionValues(panel)).toEqual(["grok\u0001grok-5"]);
  });

  it("keeps starred models under the star and remembers them", async () => {
    const onChange = renderPicker();
    fireEvent.click(screen.getByTestId("composer-model"));
    let panel = await screen.findByTestId("composer-model-panel");
    fireEvent.click(within(panel).getByTestId("composer-model-rail-favorites"));
    expect(within(panel).queryAllByRole("option")).toHaveLength(0);
    expect(panel.textContent).toContain("Star a model to keep it here");

    fireEvent.click(within(panel).getByTestId("composer-model-rail-claude"));
    const stars = within(panel).getAllByTestId("composer-model-star");
    fireEvent.click(stars[1]);
    // Starring is not picking.
    expect(onChange).not.toHaveBeenCalled();
    expect(JSON.parse(window.localStorage.getItem("jarvis.chat.favoriteModels") ?? "[]")).toEqual([
      "claude\u0001opus",
    ]);

    fireEvent.click(within(panel).getByTestId("composer-model-rail-favorites"));
    panel = screen.getByTestId("composer-model-panel");
    expect(optionValues(panel)).toEqual(["claude\u0001opus"]);
  });
});

describe("matchPreviews", () => {
  const image = (name: string): ChatAttachment => ({
    name,
    reference: `@${name}`,
    kind: "image",
    detail: "",
    described_by: "none",
    note: "",
  });

  beforeEach(() => {
    let n = 0;
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: () => `blob:${++n}` }));
  });
  afterEach(() => vi.unstubAllGlobals());

  it("pairs by name, and a lone image on both sides by being alone", () => {
    const shot = new File(["x"], "shot.png", { type: "image/png" });
    expect(matchPreviews([image("shot.png")], [shot])).toEqual({ "shot.png": "blob:1" });
    expect(matchPreviews([image("chat-20261005.png")], [shot])).toEqual({ "chat-20261005.png": "blob:2" });
  });

  it("gives no picture when the pairing is ambiguous or there are no bytes", () => {
    const a = new File(["a"], "a.png", { type: "image/png" });
    const b = new File(["b"], "b.png", { type: "image/png" });
    expect(matchPreviews([image("x.png"), image("y.png")], [a, b])).toEqual({});
    expect(matchPreviews([image("x.png")], [])).toEqual({});
  });
});
