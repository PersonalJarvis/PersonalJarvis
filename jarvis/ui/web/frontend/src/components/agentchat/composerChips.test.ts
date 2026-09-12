import { describe, expect, it } from "vitest";

import { choiceTag, choiceToken, splitMessageChips } from "./composerChips";
import type { ToolChoice } from "./toolChoices";

const gmail: ToolChoice = {
  id: "plugin:gmail",
  label: "Gmail",
  brand: "gmail",
  category: "plugins",
  group: "Gmail",
  description: "Mail",
  available: true,
  tool_names: ["gmail"],
  skill: "",
};

describe("composer chips", () => {
  it("tags a plugin as @gmail", () => {
    expect(choiceTag(gmail)).toBe("gmail");
    expect(choiceToken(gmail)).toBe("@gmail");
  });

  it("keeps chips in the sentence, not in a row of their own", () => {
    const parts = splitMessageChips("I am @gmail now", [gmail]);
    expect(parts).toEqual([
      { type: "text", text: "I am " },
      { type: "chip", row: gmail },
      { type: "text", text: " now" },
    ]);
  });

  it("still shows a chip after old messages that stored it separately", () => {
    const parts = splitMessageChips("Find that email", [gmail]);
    expect(parts[0]).toEqual({ type: "text", text: "Find that email" });
    expect(parts[1]).toEqual({ type: "chip", row: gmail });
  });

  it("renders each selected connector once when an old receipt duplicates it", () => {
    const duplicate = { ...gmail };
    const parts = splitMessageChips("Use @gmail", [gmail, duplicate]);
    expect(parts.filter((part) => part.type === "chip")).toEqual([
      { type: "chip", row: duplicate },
    ]);
  });
});
