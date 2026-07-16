import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DocsSearchModal, renderSearchSnippet } from "./DocsSearchModal";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("renderSearchSnippet", () => {
  it("renders only search highlights and keeps all other markup inert", () => {
    const { container } = render(
      <div>
        {renderSearchSnippet(
          '<img src=x onerror="alert(1)"><mark>voice</mark><script>bad()</script>',
        )}
      </div>,
    );

    expect(container.querySelector("mark")?.textContent).toBe("voice");
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container.textContent).toContain("<img");
    expect(container.textContent).toContain("<script>");
  });

  it("distinguishes a failed search from an empty result", async () => {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ detail: "Search unavailable" }), {
          status: 503,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });

    render(
      <QueryClientProvider client={client}>
        <DocsSearchModal open onOpenChange={() => {}} onSelect={() => {}} />
      </QueryClientProvider>,
    );
    fireEvent.change(screen.getByRole("combobox"), {
      target: { value: "voice" },
    });

    await waitFor(() => {
      expect(screen.getByRole("alert").textContent).toContain(
        "Search could not be loaded.",
      );
    });
    expect(screen.queryByText('No results for "voice"')).toBeNull();
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
  });
});
