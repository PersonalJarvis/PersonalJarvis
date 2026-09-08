import seedCatalog from "../../../../../../marketplace/seed_catalog.json";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { toolIdentity } from "./toolIdentity";
import { ToolChoiceChips, ToolChoiceIcon } from "./ToolChoiceChips";
import type { ToolChoice } from "./toolChoices";
import { EMPTY_TIMELINE, reduceEvents } from "./reduce";
import { AgentTimeline } from "./AgentTimeline";

const row = (over: Partial<ToolChoice> = {}): ToolChoice => ({
  id: "plugin:gmail",
  label: "Gmail",
  category: "plugins",
  group: "Gmail",
  brand: "gmail",
  description: "Read your inbox",
  available: true,
  tool_names: ["gmail"],
  skill: "",
  ...over,
});
afterEach(cleanup);

describe("tool visual identities", () => {
  it.each([
    ["mcp:github-mcp", "mcp", "github"],
    ["tool:mcp__github__search", "mcp", "github"],
    ["skill:google-workspace:gws-gmail-read", "skills", "gmail"],
    ["skill:google-drive:sheets", "skills", "google_drive"],
    ["tool:cli_gh", "cli", "github"],
    ["tool:cli_codex", "cli", "openai"],
    ["cli:kubectl", "cli", "kubernetes"],
    ["cli:gcloud", "cli", "google-cloud"],
    ["cli:az", "cli", "azure"],
    ["skill:figma:design", "skills", "figma"],
    ["mcp:postgres-server", "mcp", "postgresql"],
  ])("resolves %s to the original %s identity", (id, category, brand) => {
    const identity = toolIdentity(
      row({ id, category: category as ToolChoice["category"], brand: "", group: "", label: "" }),
    );
    expect(identity.key).toBe(brand);
    expect(identity.logo).toBeTruthy();
  });

  it("has a local mark for every shipped marketplace connector", () => {
    for (const plugin of seedCatalog.plugins) {
      const identity = toolIdentity(
        row({
          id: `plugin:${plugin.id}`,
          brand: plugin.id,
          label: plugin.display_name,
          group: plugin.display_name,
        }),
      );
      expect(identity.logo, plugin.id).toBeTruthy();
      expect(identity.key, plugin.id).toBe(plugin.id);
      expect(identity.logo).not.toMatch(/^https?:/);
    }
  });

  it.each(["my-github-helper", "drive-jarvis-cli", "daily-brief"])(
    "does not invent a brand for %s",
    (id) => {
      const identity = toolIdentity(
        row({
          id: `skill:${id}`,
          label: id,
          brand: "",
          group: "skills",
          description: "Work with Gmail and GitHub",
        }),
      );
      expect(identity.logo).toBeUndefined();
      expect(identity.Glyph).toBeTruthy();
    },
  );

  it("uses distinct glyphs for unbranded documents and memory", () => {
    const pdf = toolIdentity(
      row({ id: "skill:pdf", label: "PDF", brand: "", group: "skills", category: "skills" }),
    );
    const memory = toolIdentity(
      row({
        id: "tool:wiki-recall",
        label: "Recall",
        brand: "",
        group: "memory",
        category: "memory",
      }),
    );
    expect(pdf.Glyph).not.toBe(memory.Glyph);
  });

  it("preserves brand identity in a reloaded user message below the text", () => {
    const saved = JSON.parse(JSON.stringify(row()));
    const { items } = reduceEvents(EMPTY_TIMELINE, [
      {
        seq: 1,
        ts_ms: 1000,
        kind: "user_message",
        payload: { text: "Find that email", tool_choices: [saved] },
      },
    ]);
    render(
      <AgentTimeline
        items={items}
        assistantName="Jarvis"
        providerLabel={(s) => s}
        onDecide={() => {}}
      />,
    );
    const chips = screen.getByTestId("tool-choice-chips");
    expect(chips.dataset.editable).toBe("false");
    expect(chips.querySelector('[data-brand="gmail"]')).not.toBeNull();
    expect(chips.previousElementSibling?.textContent).toBe("Find that email");
    expect(chips.querySelector("button")).toBeNull();
  });

  it("uses the same ink for composer and sent tags, with removal only in the composer", () => {
    const items = [row(), row({ id: "plugin:github", brand: "github", label: "GitHub" })];
    const removed: string[] = [];
    const { container } = render(
      <>
        <ToolChoiceChips items={items} onRemove={(id) => removed.push(id)} />
        <ToolChoiceChips items={items} />
      </>,
    );
    const gmail = container.querySelectorAll<HTMLElement>('[data-brand="gmail"]');
    expect(gmail[0].style.cssText).toBe(gmail[1].style.cssText);
    expect(gmail[0].style.getPropertyValue("--tool-ink-light")).toBe("#b3261e");
    const github = container.querySelectorAll<HTMLElement>('[data-brand="github"]');
    expect(github[0].querySelector(".tool-choice-mask")).not.toBeNull();
    fireEvent.click(gmail[0].querySelector("button")!);
    expect(removed).toEqual(["plugin:gmail"]);
  });

  it("falls back to a meaningful symbol when a colour logo fails", () => {
    const { container } = render(<ToolChoiceIcon row={row()} />);
    fireEvent.error(container.querySelector("img")!);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("svg")).not.toBeNull();
  });
});
