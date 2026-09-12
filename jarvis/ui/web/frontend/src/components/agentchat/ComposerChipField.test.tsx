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

  it("inserts the chip at the typing caret, not the start of the field", () => {
    const handle = createRef<ComposerChipFieldHandle>();
    render(
      <ComposerChipField
        ref={handle}
        placeholder="Message"
        onSubmit={() => {}}
        onDraftChange={() => {}}
      />,
    );
    const field = screen.getByTestId("composer-chip-field");
    handle.current?.setText("Hello, this is Ruben");
    field.focus();
    const range = document.createRange();
    range.selectNodeContents(field);
    range.collapse(false);
    const sel = document.getSelection();
    sel?.removeAllRanges();
    sel?.addRange(range);
    document.dispatchEvent(new Event("selectionchange"));

    handle.current?.insertChip(gmail);

    const draft = handle.current!.getDraft();
    expect(field.firstChild?.textContent).toContain("Hello, this is Ruben");
    expect(field.querySelector('[data-brand="gmail"]')).not.toBeNull();
    expect(draft.text.startsWith("Hello, this is Ruben")).toBe(true);
    expect(draft.text).toContain("@gmail");
    expect(draft.caret).toBeGreaterThan(draft.text.indexOf("@gmail"));
  });

  it("keeps an already selected connector to one chip", () => {
    const handle = createRef<ComposerChipFieldHandle>();
    render(<ComposerChipField ref={handle} placeholder="Message" onSubmit={() => {}} onDraftChange={() => {}} />);
    handle.current?.insertChip(gmail);
    handle.current?.insertChip(gmail);
    expect(handle.current?.getDraft().choices.map((row) => row.id)).toEqual(["plugin:gmail"]);
  });
});
