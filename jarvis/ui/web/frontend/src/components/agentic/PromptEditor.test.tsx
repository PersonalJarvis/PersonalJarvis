import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PromptEditor } from "./PromptEditor";

describe("PromptEditor", () => {
  it("keeps ordinary typing local and sends the draft on Enter", () => {
    const onSend = vi.fn(async () => undefined);
    let parentRenders = 0;
    function Parent() {
      parentRenders += 1;
      return (
        <PromptEditor
          target="Nova"
          sending={false}
          seed={{ value: "", revision: 0 }}
          onSend={onSend}
        />
      );
    }
    render(<Parent />);
    const editor = screen.getByLabelText("Instruction for Nova");

    fireEvent.change(editor, { target: { value: "Run the tests" } });
    expect(parentRenders).toBe(1);
    fireEvent.keyDown(editor, { key: "Enter" });

    expect(onSend).toHaveBeenCalledWith("Run the tests");
  });

  it("replaces the draft only when the reset revision changes", () => {
    const onSend = vi.fn(async () => undefined);
    const view = render(
      <PromptEditor
        target="Nova"
        sending={false}
        seed={{ value: "old", revision: 0 }}
        onSend={onSend}
      />,
    );
    fireEvent.change(screen.getByLabelText("Instruction for Nova"), {
      target: { value: "local edit" },
    });

    view.rerender(
      <PromptEditor
        target="Nova"
        sending={false}
        seed={{ value: "restored", revision: 1 }}
        onSend={onSend}
      />,
    );

    expect((screen.getByLabelText("Instruction for Nova") as HTMLTextAreaElement).value).toBe(
      "restored",
    );
  });

  it("does not send an unfinished IME composition", () => {
    const onSend = vi.fn(async () => undefined);
    render(
      <PromptEditor
        target="Nova"
        sending={false}
        seed={{ value: "draft", revision: 0 }}
        onSend={onSend}
      />,
    );

    fireEvent.keyDown(screen.getByLabelText("Instruction for Nova"), {
      key: "Enter",
      isComposing: true,
    });

    expect(onSend).not.toHaveBeenCalled();
  });
});
