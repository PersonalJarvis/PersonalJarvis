/**
 * The assistant-name rebrand: locale strings refer to the assistant via a
 * `{name}` token, and useT()/interpolateName substitute the configured name
 * (store `assistantName`). This locks (a) the pure substitution and (b) that a
 * live store rename flows through t() so the whole UI follows the new name.
 */
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { interpolateName, useT } from "@/i18n";
import { useEventStore } from "@/store/events";

describe("interpolateName (pure)", () => {
  it("substitutes every {name} occurrence", () => {
    expect(interpolateName("{name} knows {name}", "Ruben")).toBe("Ruben knows Ruben");
  });

  it("is a no-op for strings without the token", () => {
    expect(interpolateName("no token here", "Ruben")).toBe("no token here");
  });

  it("leaves the token intact for an empty name (never blanks copy)", () => {
    expect(interpolateName("{name} here", "")).toBe("{name} here");
  });
});

// "topbar.restart_hint" is "Restart {name}" in the English locale — a stable,
// simple {name} carrier to prove the hook substitutes the live store name.
function RestartHintProbe() {
  const t = useT();
  return <span data-testid="hint">{t("topbar.restart_hint")}</span>;
}

describe("useT name substitution", () => {
  beforeEach(() => {
    useEventStore.setState({ assistantName: "Jarvis" });
  });
  afterEach(() => cleanup());

  it("renders the configured assistant name inside a {name} locale string", () => {
    useEventStore.setState({ assistantName: "Ruben" });
    render(<RestartHintProbe />);
    const text = screen.getByTestId("hint").textContent ?? "";
    expect(text).toBe("Restart Ruben");
    expect(text).not.toContain("{name}");
    expect(text).not.toContain("Jarvis");
  });

  it("renders the default 'Jarvis' when the store is unseeded (test backward-compat)", () => {
    render(<RestartHintProbe />);
    expect(screen.getByTestId("hint").textContent).toBe("Restart Jarvis");
  });

  it("follows a live rename through t()", () => {
    render(<RestartHintProbe />);
    expect(screen.getByTestId("hint").textContent).toBe("Restart Jarvis");

    act(() => {
      useEventStore.setState({ assistantName: "Athena" });
    });

    expect(screen.getByTestId("hint").textContent).toBe("Restart Athena");
  });
});
