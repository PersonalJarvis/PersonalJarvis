import * as Dialog from "@radix-ui/react-dialog";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Combobox, isComboboxPanelEvent } from "@/components/ui/combobox";

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

describe("Combobox panel placement", () => {
  it("opens downwards under the trigger when there is room", async () => {
    vi.spyOn(window, "innerHeight", "get").mockReturnValue(900);
    vi.spyOn(HTMLButtonElement.prototype, "getBoundingClientRect").mockReturnValue({
      top: 100, bottom: 128, left: 40, right: 240, width: 200, height: 28, x: 40, y: 100, toJSON: () => ({}),
    } as DOMRect);
    render(<Combobox value="alpha" groups={GROUPS} onChange={() => {}} ariaLabel="Down" testId="down" />);
    fireEvent.click(screen.getByTestId("down"));
    const panel = await screen.findByTestId("down-panel");
    expect(panel.style.top).toBe("134px");
    expect(panel.style.bottom).toBe("");
  });

  it("hangs its bottom edge over a trigger near the viewport floor instead of assuming a height", async () => {
    // A composer pill 60px above the floor: below is cramped, above is roomy.
    vi.spyOn(window, "innerHeight", "get").mockReturnValue(900);
    vi.spyOn(HTMLButtonElement.prototype, "getBoundingClientRect").mockReturnValue({
      top: 812, bottom: 840, left: 40, right: 240, width: 200, height: 28, x: 40, y: 812, toJSON: () => ({}),
    } as DOMRect);
    render(<Combobox value="alpha" groups={GROUPS} onChange={() => {}} ariaLabel="Up" testId="up" />);
    fireEvent.click(screen.getByTestId("up"));
    const panel = await screen.findByTestId("up-panel");
    // bottom = innerHeight - trigger.top + gap: the list grows upwards from
    // the pill, however short it is — no top computed from MAX_PANEL_HEIGHT.
    expect(panel.style.bottom).toBe("94px");
    expect(panel.style.top).toBe("");
    expect(parseInt(panel.style.maxHeight, 10)).toBeGreaterThanOrEqual(160);
  });
});

describe("Combobox inside a modal dialog", () => {
  it("marks the panel so a dialog can treat it as inside, and keeps pointer events", async () => {
    render(<Combobox value="alpha" groups={GROUPS} onChange={() => {}} ariaLabel="Pick" testId="pick" />);
    fireEvent.click(screen.getByTestId("pick"));
    const panel = await screen.findByTestId("pick-panel");
    expect(panel.getAttribute("data-combobox-panel")).toBe("");
    expect(panel.className).toContain("pointer-events-auto");
  });

  it("mounts the list inside the nearest dialog so a modal can click it", async () => {
    render(
      <div role="dialog" data-testid="host-dialog">
        <Combobox value="alpha" groups={GROUPS} onChange={() => {}} ariaLabel="Pick" testId="inside" />
      </div>,
    );
    fireEvent.click(screen.getByTestId("inside"));
    const panel = await screen.findByTestId("inside-panel");
    expect(screen.getByTestId("host-dialog").contains(panel)).toBe(true);
    expect(panel.parentElement).not.toBe(document.body);
  });

  it("lets a pick land without dismissing the wrapping Radix dialog", async () => {
    const onChange = vi.fn();
    function App() {
      const [open, setOpen] = useState(true);
      const [value, setValue] = useState("alpha");
      return (
        <Dialog.Root open={open} onOpenChange={setOpen}>
          <Dialog.Portal>
            <Dialog.Overlay />
            <Dialog.Content
              aria-describedby={undefined}
              onPointerDownOutside={(event) => {
                if (isComboboxPanelEvent(event)) event.preventDefault();
              }}
              onFocusOutside={(event) => {
                if (isComboboxPanelEvent(event)) event.preventDefault();
              }}
              onInteractOutside={(event) => {
                if (isComboboxPanelEvent(event)) event.preventDefault();
              }}
            >
              <Dialog.Title>Create</Dialog.Title>
              <Combobox
                value={value}
                groups={GROUPS}
                onChange={(next) => {
                  setValue(next);
                  onChange(next);
                }}
                ariaLabel="Pick"
                testId="modal-pick"
              />
            </Dialog.Content>
          </Dialog.Portal>
        </Dialog.Root>
      );
    }
    render(<App />);
    fireEvent.click(screen.getByTestId("modal-pick"));
    const option = await screen.findByRole("option", { name: "Charlie" });
    fireEvent.pointerDown(option);
    fireEvent.click(option);
    expect(onChange).toHaveBeenCalledWith("charlie");
    expect(screen.getByText("Create")).toBeTruthy();
    expect(screen.getByTestId("modal-pick").getAttribute("data-value")).toBe("charlie");
  });
});

describe("isComboboxPanelEvent", () => {
  it("reads the original click target, not the node the custom event is dispatched on", () => {
    const panel = document.createElement("div");
    panel.setAttribute("data-combobox-panel", "");
    const option = document.createElement("div");
    panel.appendChild(option);
    const original = new Event("pointerdown");
    Object.defineProperty(original, "target", { value: option });
    expect(
      isComboboxPanelEvent({
        target: document.createElement("div"),
        detail: { originalEvent: original },
      }),
    ).toBe(true);
    expect(isComboboxPanelEvent({ target: document.createElement("div") })).toBe(false);
  });
});
