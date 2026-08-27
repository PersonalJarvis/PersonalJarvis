import { describe, expect, it } from "vitest";

import {
  activeToken,
  applyPick,
  filterItems,
  groupRuns,
  isStaticTrigger,
  type TypeaheadItem,
} from "@/components/agentchat/typeahead";

const ALL = ["/", "@", "$"] as const;

function item(value: string, kind = "skill", group = "project", hint = ""): TypeaheadItem {
  return { value, label: value, hint, kind, group };
}

describe("activeToken", () => {
  it("opens on a trigger at the caret's token", () => {
    expect(activeToken("/", 1, ALL)).toEqual({ trigger: "/", query: "", start: 0, end: 1 });
    expect(activeToken("/com", 4, ALL)).toEqual({ trigger: "/", query: "com", start: 0, end: 4 });
    expect(activeToken("look at @src/ap", 15, ALL)).toEqual({
      trigger: "@",
      query: "src/ap",
      start: 8,
      end: 15,
    });
    expect(activeToken("use $grill", 10, ALL)).toEqual({ trigger: "$", query: "grill", start: 4, end: 10 });
  });

  it("opens the slash list only at the very start of the message", () => {
    expect(activeToken("see /etc/hosts", 14, ALL)).toBeNull();
    expect(activeToken("  /commit", 9, ALL)).toEqual({ trigger: "/", query: "commit", start: 2, end: 9 });
    expect(activeToken("https://x", 9, ALL)).toBeNull();
  });

  it("never opens inside an address or after the token ended", () => {
    expect(activeToken("mail a@b.c", 10, ALL)).toBeNull();
    expect(activeToken("@src/app.py next", 16, ALL)).toBeNull();
    expect(activeToken("@src ", 5, ALL)).toBeNull();
  });

  it("only opens the triggers the seat honours", () => {
    expect(activeToken("/x", 2, ["@"])).toBeNull();
    expect(activeToken("$x", 2, ["@", "$"])).not.toBeNull();
    expect(activeToken("/x", 2, [])).toBeNull();
  });

  it("follows the caret, not the end of the text", () => {
    const text = "@src tail";
    expect(activeToken(text, 4, ALL)?.query).toBe("src");
    expect(activeToken(text, 9, ALL)).toBeNull();
  });
});

describe("applyPick", () => {
  it("writes the pick over the token and adds a space after a name", () => {
    const token = activeToken("fix /com please", 8, ["/"]);
    expect(token).toBeNull(); // not at the start — but a start token works:
    const start = activeToken("/com please", 4, ["/"])!;
    expect(applyPick("/com please", start, item("commit"))).toEqual({
      text: "/commit  please",
      caret: 8,
    });
  });

  it("keeps the caret inside a folder pick so the list narrows into it", () => {
    const token = activeToken("read @sr", 8, ["@"])!;
    expect(applyPick("read @sr", token, item("src/", "folder", "files"))).toEqual({
      text: "read @src/",
      caret: 10,
    });
  });
});

describe("filterItems", () => {
  it("ranks a prefix over a substring over a hint hit and keeps arrival order within a rank", () => {
    const items = [
      item("release", "skill", "project", "Tag and commit"),
      item("autocommit"),
      item("commit"),
      item("deploy"),
    ];
    expect(filterItems(items, "commit").map((i) => i.value)).toEqual(["commit", "autocommit", "release"]);
    expect(filterItems(items, "").map((i) => i.value)).toEqual(["release", "autocommit", "commit", "deploy"]);
  });
});

describe("groupRuns / isStaticTrigger", () => {
  it("cuts the arrival order into runs of one group", () => {
    const runs = groupRuns([item("a"), item("b"), item("c", "skill", "account"), item("d")]);
    expect(runs.map((r) => [r.group, r.items.length])).toEqual([
      ["project", 2],
      ["account", 1],
      ["project", 1],
    ]);
  });

  it("reads / and $ once and asks per keystroke for @", () => {
    expect(isStaticTrigger("/")).toBe(true);
    expect(isStaticTrigger("$")).toBe(true);
    expect(isStaticTrigger("@")).toBe(false);
  });
});
