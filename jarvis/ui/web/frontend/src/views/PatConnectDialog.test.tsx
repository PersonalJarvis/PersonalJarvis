import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PatConnectDialog } from "@/views/PluginsView";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function makePlugin(id: string): any {
  return {
    id,
    name: id,
    description: "d",
    category: "Communication",
    logoSlug: id,
    authMode: "pat_paste",
    authConfig: {
      mode: "pat_paste",
      token_creation_url: "https://example.test",
      token_prefix: "",
      instruction_md: "md",
    },
    status: "not_connected",
    featured: false,
    liveCallable: false,
  };
}

afterEach(cleanup);

describe("PatConnectDialog owner lock", () => {
  it("shows a numeric owner-id field for a channel plugin and forwards it", () => {
    const onSubmit = vi.fn();
    render(
      <PatConnectDialog
        plugin={makePlugin("discord")}
        onClose={() => {}}
        onSubmit={onSubmit}
        isPending={false}
        errorMessage={null}
      />,
    );

    const idField = screen.getByLabelText(/user id/i);
    fireEvent.change(screen.getByPlaceholderText(/^token$/i), {
      target: { value: "bot-token" },
    });
    fireEvent.change(idField, { target: { value: "4242" } });
    fireEvent.click(screen.getByRole("button", { name: /^Connect/i }));

    expect(onSubmit).toHaveBeenCalledWith("bot-token", 4242);
  });

  it("omits the owner-id field for non-channel plugins and submits null", () => {
    const onSubmit = vi.fn();
    render(
      <PatConnectDialog
        plugin={makePlugin("github")}
        onClose={() => {}}
        onSubmit={onSubmit}
        isPending={false}
        errorMessage={null}
      />,
    );

    expect(screen.queryByLabelText(/user id/i)).toBeNull();
    fireEvent.change(screen.getByPlaceholderText(/^token$/i), {
      target: { value: "ghp_x" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Connect/i }));

    expect(onSubmit).toHaveBeenCalledWith("ghp_x", null);
  });

  it("forwards null when the owner-id field is left blank", () => {
    const onSubmit = vi.fn();
    render(
      <PatConnectDialog
        plugin={makePlugin("telegram")}
        onClose={() => {}}
        onSubmit={onSubmit}
        isPending={false}
        errorMessage={null}
      />,
    );

    fireEvent.change(screen.getByPlaceholderText(/^token$/i), {
      target: { value: "123:ABC" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^Connect/i }));

    expect(onSubmit).toHaveBeenCalledWith("123:ABC", null);
  });
});
