import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Combobox } from "@/components/ui/combobox";

const GROUPS = [
  {
    id: "main",
    options: [
      { value: "alpha", label: "Alpha" },
      { value: "blocked", label: "Blocked", disabled: true },
      { value: "charlie", label: "Charlie" },
    ],
  },
];

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Combobox non-search listbox accessibility", () => {
  it("exposes the active option through a stable aria-activedescendant", async () => {
    render(
      <Combobox
        value="alpha"
        groups={GROUPS}
        onChange={() => {}}
        ariaLabel="Example choice"
      />,
    );

    fireEvent.click(screen.getByRole("combobox", { name: "Example choice" }));
    const listbox = await screen.findByRole("listbox", {
      name: "Example choice",
    });
    const alpha = screen.getByRole("option", { name: "Alpha" });

    await waitFor(() => expect(document.activeElement).toBe(listbox));
    expect(alpha.id).not.toBe("");
    expect(listbox.getAttribute("aria-activedescendant")).toBe(alpha.id);

    fireEvent.keyDown(listbox, { key: "ArrowDown" });
    const charlie = screen.getByRole("option", { name: "Charlie" });
    expect(listbox.getAttribute("aria-activedescendant")).toBe(charlie.id);

    fireEvent.keyDown(listbox, { key: "Home" });
    expect(listbox.getAttribute("aria-activedescendant")).toBe(alpha.id);

    fireEvent.keyDown(listbox, { key: "End" });
    expect(listbox.getAttribute("aria-activedescendant")).toBe(charlie.id);
  });

  it("commits the keyboard-active enabled option", async () => {
    const onChange = vi.fn();
    render(
      <Combobox
        value="alpha"
        groups={GROUPS}
        onChange={onChange}
        ariaLabel="Example choice"
      />,
    );

    fireEvent.click(screen.getByRole("combobox", { name: "Example choice" }));
    const listbox = await screen.findByRole("listbox", {
      name: "Example choice",
    });
    fireEvent.keyDown(listbox, { key: "ArrowDown" });
    fireEvent.keyDown(listbox, { key: "Enter" });

    expect(onChange).toHaveBeenCalledWith("charlie");
  });

  it("tracks duplicate values as distinct active occurrences", async () => {
    const onChange = vi.fn();
    render(
      <Combobox
        value="baseline"
        groups={[
          {
            id: "common",
            options: [
              { value: "english", label: "English (Common)" },
              { value: "baseline", label: "Baseline" },
            ],
          },
          {
            id: "all",
            options: [
              { value: "english", label: "English (All)" },
              { value: "spanish", label: "Spanish" },
            ],
          },
        ]}
        onChange={onChange}
        ariaLabel="Language"
      />,
    );

    fireEvent.click(screen.getByRole("combobox", { name: "Language" }));
    const listbox = await screen.findByRole("listbox", { name: "Language" });
    const [commonEnglish, allEnglish] = screen.getAllByRole("option", {
      name: /English/,
    });

    expect(commonEnglish.id).not.toBe(allEnglish.id);
    fireEvent.keyDown(listbox, { key: "ArrowDown" });
    fireEvent.keyDown(listbox, { key: "ArrowDown" });
    expect(listbox.getAttribute("aria-activedescendant")).toBe(allEnglish.id);
    expect(document.querySelectorAll('[data-active="true"]')).toHaveLength(1);

    fireEvent.keyDown(listbox, { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith("english");
  });
});
