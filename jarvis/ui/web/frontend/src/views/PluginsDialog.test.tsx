import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { PluginsDialog } from "./PluginsDialog";

const catalog = {
  version: 1, schema_version: "test", total: 3, connected: 1,
  category_order: ["Developer", "Calendar & Mail"],
  plugins: [
    { id: "github", display_name: "GitHub", description: "Repositories and pull requests", category: "Developer", logo_slug: "github", featured: true, status: "not_connected", auth: { mode: "pat_paste", instruction_md: "Create a token", token_creation_url: "https://github.com/settings/tokens" } },
    { id: "gmail", display_name: "Gmail", description: "Search and read your mail", category: "Calendar & Mail", logo_slug: "gmail", status: "connected", auth: { mode: "pat_paste" } },
    { id: "outlook", display_name: "Outlook", description: "Microsoft mail and calendar", category: "Calendar & Mail", logo_slug: "outlook", status: "not_connected", auth: { mode: "pat_paste", instruction_md: "Connect your account", token_creation_url: "https://example.com" } },
  ],
};

function setup() {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify(catalog))));
  const onClose = vi.fn();
  const query = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={query}><PluginsDialog onClose={onClose} /></QueryClientProvider>);
  return { onClose };
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it("shows recommendations, larger icon tiles and descriptions with an always visible search", async () => {
  setup();
  const gmail = await screen.findByRole("button", { name: "Gmail" });
  expect(gmail.querySelector(".h-12.w-12")).not.toBeNull();
  expect(screen.getByText("Search and read your mail")).toBeDefined();
  expect(screen.getByRole("tab", { name: "Recommended" }).getAttribute("aria-selected")).toBe("true");
  expect(screen.queryByRole("button", { name: "Outlook" })).toBeNull();
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "Microsoft" } });
  await screen.findByRole("button", { name: "Outlook" });
  expect(screen.queryByRole("button", { name: "Gmail" })).toBeNull();
});

it("groups all plugins in product category order and keeps installed filtering functional", async () => {
  setup();
  await screen.findByRole("button", { name: "Gmail" });
  fireEvent.click(screen.getByRole("tab", { name: "All 3" }));
  expect(screen.getAllByRole("heading", { level: 3 }).map((heading) => heading.textContent)).toEqual(["Calendar & Mail", "Developer"]);
  fireEvent.click(screen.getByRole("tab", { name: "Installed 1" }));
  expect(screen.getByRole("button", { name: "Gmail" })).toBeDefined();
  expect(screen.queryByRole("button", { name: "GitHub" })).toBeNull();
});

it("closes credential setup with Escape without closing the plugin window", async () => {
  const { onClose } = setup();
  const github = await screen.findByRole("button", { name: "GitHub" });
  const row = github.closest("li")!;
  fireEvent.click(within(row).getByRole("button", { name: "Connect" }));
  await screen.findByRole("dialog", { name: "Connect GitHub" });
  fireEvent.keyDown(document, { key: "Escape" });
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Connect GitHub" })).toBeNull());
  expect(onClose).not.toHaveBeenCalled();
  expect(screen.getByRole("dialog", { name: "Plugins" })).toBeDefined();
});
