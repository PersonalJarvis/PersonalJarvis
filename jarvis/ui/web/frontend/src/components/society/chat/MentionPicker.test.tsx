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
  it("groups plugins and MCP servers and picks on click", () => {
    const items = filterMentions(
      buildMentionCatalog(
        [],
        [
          cap({ id: "plugin:gmail", label: "gmail", one_liner: "Read and send mail." }),
          cap({ id: "mcp:github/create_issue" }),
          cap({ id: "mcp:github/list_issues" }),
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
