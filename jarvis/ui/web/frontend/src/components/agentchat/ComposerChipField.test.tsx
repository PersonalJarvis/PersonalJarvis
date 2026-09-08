import { createRef } from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { ComposerChipField, type ComposerChipFieldHandle } from "./ComposerChipField";
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

describe("ComposerChipField", () => {
  it("puts a plugin chip in the sentence at the caret", () => {
    const handle = createRef<ComposerChipFieldHandle>();
    render(
      <ComposerChipField
        ref={handle}
        placeholder="Message"
        onSubmit={() => {}}
        onDraftChange={() => {}}
      />,
    );
    handle.current?.hydrate("I am  now", []);
    handle.current?.hydrate("I am @gmail now", [gmail]);
    const field = screen.getByTestId("composer-chip-field");
    expect(field.querySelector('[data-brand="gmail"]')?.textContent).toContain("Gmail");
    expect(handle.current?.getDraft().text).toContain("@gmail");
    expect(handle.current?.getDraft().choices.map((row) => row.id)).toEqual(["plugin:gmail"]);
  });
});
