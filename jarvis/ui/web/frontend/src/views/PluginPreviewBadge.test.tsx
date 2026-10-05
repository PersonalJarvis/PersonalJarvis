import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { type Plugin, PreviewBadge } from "@/views/PluginsView";

afterEach(cleanup);

function plugin(overrides: Partial<Plugin> = {}): Plugin {
  return {
    id: "github",
    name: "GitHub",
    description: "Repos and pull requests",
    category: "Developer",
    logoSlug: "github",
    authMode: "oauth_device_flow",
    authConfig: { mode: "oauth_device_flow" },
    status: "available",
    longevity: "self_renewing",
    oauthClientConfigured: false,
    fromMarketplace: false,
    selfUploaded: false,
    acceptance: "preview",
    ...overrides,
  } as Plugin;
}

describe("PreviewBadge", () => {
  it("marks a built-in plugin that has not passed the end-to-end journey", () => {
    render(<PreviewBadge plugin={plugin()} />);
    const badge = screen.getByText("Preview");
    expect(badge.getAttribute("title")).toMatch(/end to end/);
  });

  it("stays hidden once the journey is verified", () => {
    const { container } = render(<PreviewBadge plugin={plugin({ acceptance: "verified" })} />);
    expect(container.textContent).toBe("");
  });

  it("leaves marketplace and self-uploaded plugins to their own badges", () => {
    const market = render(<PreviewBadge plugin={plugin({ fromMarketplace: true })} />);
    expect(market.container.textContent).toBe("");
    const own = render(<PreviewBadge plugin={plugin({ selfUploaded: true })} />);
    expect(own.container.textContent).toBe("");
  });
});
