import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

import { InstallStandard, type InstallStandardWire } from "@/components/InstallStandard";

const copied: string[] = [];
vi.mock("@/lib/clipboard", () => ({
  robustCopy: async (text: string) => {
    copied.push(text);
    return true;
  },
}));

afterEach(() => {
  cleanup();
  copied.length = 0;
});

// Deliberately NOT derived from the name: the point of this component is that
// it renders whatever the backend computed. A test that rebuilt the strings
// here would pass even if the component started building its own.
const INSTALL: InstallStandardWire = {
  cli: "jarvis marketplace install three-point-check",
  runner: "uvx --from personal-jarvis jarvis marketplace install three-point-check",
  prompt: 'Install the "three-point-check" skill from the community marketplace.',
};

describe("InstallStandard", () => {
  it("opens on the CLI command and shows it verbatim", () => {
    render(<InstallStandard install={INSTALL} />);
    expect(screen.getByText(INSTALL.cli)).toBeTruthy();
  });

  it("switches to the runner and the prompt without rewriting them", () => {
    render(<InstallStandard install={INSTALL} />);
    fireEvent.click(screen.getByText("uvx"));
    expect(screen.getByText(INSTALL.runner)).toBeTruthy();
    fireEvent.click(screen.getByText("Prompt"));
    expect(screen.getByText(INSTALL.prompt)).toBeTruthy();
  });

  it("copies the tab that is actually showing", async () => {
    render(<InstallStandard install={INSTALL} />);
    fireEvent.click(screen.getByText("uvx"));
    // The copy handler is async and flips the button to "Copied" afterwards —
    // act() so that state update happens inside the test, not after it.
    await act(async () => {
      fireEvent.click(screen.getByLabelText(/Copy the uvx install command/i));
    });
    expect(copied).toEqual([INSTALL.runner]);
  });

  it("takes a heading and a note for the Publish tab's 'Share it' use", () => {
    render(
      <InstallStandard install={INSTALL} heading="Share it" note="Anyone can run this now." />,
    );
    expect(screen.getByText("Share it")).toBeTruthy();
    expect(screen.getByText("Anyone can run this now.")).toBeTruthy();
  });
});
