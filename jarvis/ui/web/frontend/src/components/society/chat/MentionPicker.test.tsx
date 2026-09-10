import { createRef } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { Capability } from "@/components/society/data";

import { MentionPicker } from "./MentionPicker";
import { buildMentionCatalog, filterMentions } from "./mentionItems";

afterEach(cleanup);

function cap(over: Partial<Capability> & Pick<Capability, "id">): Capability {
  const kind = (over.id.split(":")[0] || "plugin") as Capability["kind"];
  return {
    kind,
    label: over.id.replace(/^[^:]+:/, ""),
    one_liner: over.one_liner ?? "Does the thing.",
    risk_tier: "monitor",
    connected: true,
    tool_name: over.id.replace(/^[^:]+:/, ""),
    ...over,
  };
}

describe("MentionPicker", () => {
  it("uses the existing coding logos and disables unavailable CLI rows", () => {
    const items = buildMentionCatalog([], [], [
      { name: "codex", display_name: "Codex", installed: true, version: null, install_command: null },
      { name: "claude", display_name: "Claude Code", installed: true, version: null, install_command: null },
      { name: "custom-cli", display_name: "Custom CLI", installed: true, custom: true,
        logo_url: "/uploads/custom.svg", version: null, install_command: null },
      { name: "missing", display_name: "Missing CLI", installed: false, version: null, install_command: null },
    ]);
    const anchor = createRef<HTMLDivElement>();
    const picked: string[] = [];
    render(<><div ref={anchor} /><MentionPicker anchorRef={anchor} open items={items} loading={false}
      activeIndex={0} onHover={() => {}} onPick={(item) => picked.push(item.value)} /></>);
    expect(screen.getByText("Coding Agents")).toBeTruthy();
    expect(screen.getByTestId("agent-mark-codex").getAttribute("data-logo")).toBe("/provider-logos/openai.svg");
    expect(screen.getByTestId("agent-mark-claude").getAttribute("data-logo")).toBe("/provider-logos/claude.svg");
    expect(screen.getByTestId("agent-mark-custom-cli").getAttribute("data-logo")).toBe("/uploads/custom.svg");
    const missing = screen.getByText("Missing CLI").closest('[role="option"]')!;
    expect(missing.getAttribute("aria-disabled")).toBe("true");
    fireEvent.click(missing);
    expect(picked).toEqual([]);
    fireEvent.click(screen.getByText("Codex"));
    expect(picked).toEqual(["codex"]);
  });
  it("groups plugins and MCP servers and picks on click", () => {
    const items = filterMentions(
      buildMentionCatalog(
        [],
        [
          cap({ id: "plugin:gmail", label: "gmail", one_liner: "Read and send mail." }),
          cap({ id: "mcp:sentry/create_issue" }),
          cap({ id: "mcp:sentry/list_issues" }),
          cap({ id: "core:search-web", label: "search-web" }),
        ],
      ),
      "",
    );
    const picked: string[] = [];
    const anchor = createRef<HTMLDivElement>();
    render(
      <>
        <div ref={anchor} />
        <MentionPicker
          anchorRef={anchor}
          open
          items={items}
          loading={false}
          activeIndex={0}
          onHover={() => undefined}
          onPick={(item) => picked.push(item.value)}
        />
      </>,
    );
    expect(screen.getByTestId("mention-picker")).toBeTruthy();
    expect(screen.getByText("Plugins")).toBeTruthy();
    expect(screen.getByText("MCP servers")).toBeTruthy();
    expect(screen.getByText("Jarvis tools")).toBeTruthy();
    const gmail = screen.getAllByTestId("mention-picker-item").find((el) =>
      (el.textContent ?? "").includes("@gmail"),
    );
    expect(gmail).toBeTruthy();
    fireEvent.click(gmail!);
    expect(picked).toEqual(["gmail"]);
  });
});
