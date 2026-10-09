import { describe, expect, it } from "vitest";

import { hrefWithSection } from "./sectionUrl";

const BASE = "http://localhost:8765/";

describe("hrefWithSection", () => {
  it("names the section on an address that carries none", () => {
    expect(hrefWithSection(BASE, "agentic-ide")).toBe("/?view=agentic-ide");
  });

  it("replaces the section a previous navigation left behind", () => {
    expect(hrefWithSection(`${BASE}?view=chats`, "settings")).toBe(
      "/?view=settings",
    );
  });

  it("writes nothing when the address already names the section", () => {
    expect(hrefWithSection(`${BASE}?view=docs`, "docs")).toBeNull();
  });

  it("carries the rest of the address over untouched", () => {
    // `?solo=1` decides whether the window has chrome, `?doc=` which guide the
    // Docs view reopens, and the hash which heading it scrolls to — dropping
    // any of them would trade one lost place for another.
    expect(
      hrefWithSection(`${BASE}?solo=1&doc=agent-contract#naming`, "docs"),
    ).toBe("/?solo=1&doc=agent-contract&window=docs&view=docs#naming");
  });

  it("keeps the window owner through tab changes and reloads", () => {
    const next = hrefWithSection(`${BASE}?view=profile&solo=1&window=settings`, "apikeys");
    expect(next).toBe("/?view=apikeys&solo=1&window=settings");
    expect(hrefWithSection(new URL(next!, BASE).href, "shortcuts")).toBe("/?view=shortcuts&solo=1&window=settings");
  });

  it("upgrades an older solo URL before changing its tab", () => {
    expect(hrefWithSection(`${BASE}?view=profile&solo=1`, "apikeys")).toBe("/?view=apikeys&solo=1&window=profile");
  });
});
