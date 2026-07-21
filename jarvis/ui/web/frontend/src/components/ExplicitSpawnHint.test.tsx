import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { ExplicitSpawnHint } from "@/components/ExplicitSpawnHint";
import { useEventStore } from "@/store/events";

afterEach(() => {
  cleanup();
  useEventStore.setState({ assistantName: "Assistant" });
});

describe("ExplicitSpawnHint", () => {
  it("tells the user agents start only on an explicit ask, with the dynamic brand", () => {
    // Pin an ARBITRARY brand — never the host's live wake-word config (§4).
    useEventStore.setState({ assistantName: "Nova" });
    render(<ExplicitSpawnHint />);
    expect(screen.getByText(/Nova-Agents/)).toBeTruthy();
    expect(screen.getByText(/explicitly|ausdrücklich|explícitamente/i)).toBeTruthy(); // i18n-allow: asserts the localized hint content under test
  });

  it("falls back to the neutral brand when no wake word is set", () => {
    useEventStore.setState({ assistantName: "Assistant" });
    render(<ExplicitSpawnHint />);
    expect(screen.getByText(/Assistant-Agents/)).toBeTruthy();
  });
});
