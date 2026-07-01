/**
 * Tests for the ObsidianSetupDialog three-step walkthrough.
 *
 * Behaviour anchors:
 *   1. Step 1 is the entry point when Obsidian is not installed.
 *   2. ``onStatusRefresh`` advances or stays put depending on the
 *      refreshed install state.
 *   3. ``POST /api/setup/obsidian/register`` advances on 200, surfaces a
 *      hint on 409, surfaces an error + help link on 500.
 *   4. Step 3 fires ``window.location.href = "obsidian://…"`` and
 *      ``onComplete`` runs before ``onClose`` on confirmation.
 *   5. Escape closes the dialog.
 *   6. A fully-ok seed jumps directly to step 3 (manual re-test entry).
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

import { ObsidianSetupDialog } from "@/components/wiki/ObsidianSetupDialog";
import type { ObsidianStatus } from "@/types/setup";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const STATUS_NOT_INSTALLED: ObsidianStatus = {
  installed: false,
  version: null,
  config_exists: false,
  vault_registered: false,
  vault_path: "C:/Users/Administrator/wiki/obsidian-vault",
  recommended_action: "install_obsidian",
};

const STATUS_NOT_REGISTERED: ObsidianStatus = {
  installed: true,
  version: "1.7.4",
  config_exists: true,
  vault_registered: false,
  vault_path: "C:/Users/Administrator/wiki/obsidian-vault",
  recommended_action: "register_vault",
};

const STATUS_OK: ObsidianStatus = {
  installed: true,
  version: "1.7.4",
  config_exists: true,
  vault_registered: true,
  vault_path: "C:/Users/Administrator/wiki/obsidian-vault",
  recommended_action: "ok",
};

describe("ObsidianSetupDialog", () => {
  it("renders step 1 active when installed=false", () => {
    render(
      <ObsidianSetupDialog
        open
        onClose={() => {}}
        initialStatus={STATUS_NOT_INSTALLED}
      />,
    );
    expect(screen.getByTestId("obsidian-setup-step-1")).toBeDefined();
    expect(screen.queryByTestId("obsidian-setup-step-2")).toBeNull();
    expect(screen.queryByTestId("obsidian-setup-step-3")).toBeNull();
    const marker1 = screen.getByTestId("obsidian-setup-step-marker-1");
    expect(marker1.getAttribute("data-state")).toBe("active");
    expect(screen.getByTestId("obsidian-setup-download-link")).toBeDefined();
  });

  it("clicking 'I installed it' calls onStatusRefresh and advances on installed=true", async () => {
    const refresh = vi.fn(async () => STATUS_NOT_REGISTERED);
    render(
      <ObsidianSetupDialog
        open
        onClose={() => {}}
        initialStatus={STATUS_NOT_INSTALLED}
        onStatusRefresh={refresh}
      />,
    );
    fireEvent.click(screen.getByTestId("obsidian-setup-installed-continue"));
    await waitFor(() => {
      expect(refresh).toHaveBeenCalledTimes(1);
    });
    await waitFor(() => {
      expect(screen.getByTestId("obsidian-setup-step-2")).toBeDefined();
    });
    expect(screen.queryByTestId("obsidian-setup-step-1")).toBeNull();
    const marker2 = screen.getByTestId("obsidian-setup-step-marker-2");
    expect(marker2.getAttribute("data-state")).toBe("active");
  });

  it("clicking 'I installed it' shows retry hint on installed=false", async () => {
    const refresh = vi.fn(async () => STATUS_NOT_INSTALLED);
    render(
      <ObsidianSetupDialog
        open
        onClose={() => {}}
        initialStatus={STATUS_NOT_INSTALLED}
        onStatusRefresh={refresh}
      />,
    );
    fireEvent.click(screen.getByTestId("obsidian-setup-installed-continue"));
    await waitFor(() => {
      expect(screen.getByTestId("obsidian-setup-install-hint")).toBeDefined();
    });
    // Still on step 1.
    expect(screen.getByTestId("obsidian-setup-step-1")).toBeDefined();
    expect(screen.queryByTestId("obsidian-setup-step-2")).toBeNull();
  });

  it("clicking 'Register now' with 200 added advances to step 3", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse({ status: "added", vault_uuid: "abc", backup_path: null }),
    ) as unknown as typeof fetch;
    render(
      <ObsidianSetupDialog
        open
        onClose={() => {}}
        initialStatus={STATUS_NOT_REGISTERED}
        fetchImpl={fetchImpl}
      />,
    );
    expect(screen.getByTestId("obsidian-setup-step-2")).toBeDefined();
    fireEvent.click(screen.getByTestId("obsidian-setup-register"));
    await waitFor(() => {
      expect(fetchImpl).toHaveBeenCalledWith(
        "/api/setup/obsidian/register",
        expect.objectContaining({ method: "POST" }),
      );
    });
    await waitFor(() => {
      expect(screen.getByTestId("obsidian-setup-step-3")).toBeDefined();
    });
    expect(screen.queryByTestId("obsidian-setup-step-2")).toBeNull();
  });

  it("clicking 'Register now' with 409 shows config_missing hint", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse(
        {
          detail: {
            status: "config_missing",
            error: "obsidian.json fehlt",
          },
        },
        409,
      ),
    ) as unknown as typeof fetch;
    render(
      <ObsidianSetupDialog
        open
        onClose={() => {}}
        initialStatus={STATUS_NOT_REGISTERED}
        fetchImpl={fetchImpl}
      />,
    );
    fireEvent.click(screen.getByTestId("obsidian-setup-register"));
    await waitFor(() => {
      expect(screen.getByTestId("obsidian-setup-register-hint")).toBeDefined();
    });
    // Still on step 2.
    expect(screen.getByTestId("obsidian-setup-step-2")).toBeDefined();
    expect(screen.queryByTestId("obsidian-setup-step-3")).toBeNull();
  });

  it("clicking 'Register now' with 500 shows error + help link", async () => {
    const fetchImpl = vi.fn(async () =>
      jsonResponse(
        {
          detail: {
            status: "rolled_back",
            error: "disk full",
          },
        },
        500,
      ),
    ) as unknown as typeof fetch;
    render(
      <ObsidianSetupDialog
        open
        onClose={() => {}}
        initialStatus={STATUS_NOT_REGISTERED}
        fetchImpl={fetchImpl}
      />,
    );
    fireEvent.click(screen.getByTestId("obsidian-setup-register"));
    await waitFor(() => {
      expect(screen.getByTestId("obsidian-setup-register-error")).toBeDefined();
    });
    const errBox = screen.getByTestId("obsidian-setup-register-error");
    expect(errBox.textContent).toContain("disk full");
    expect(screen.getByTestId("obsidian-setup-help-link")).toBeDefined();
  });

  it("clicking 'Open in Obsidian' sets window.location.href to obsidian://...", () => {
    // jsdom's `window.location` is normally read-only-ish; replace the
    // ``href`` setter with a tracker for the duration of the test.
    let lastHref: string | null = null;
    const originalLocation = window.location;
    Object.defineProperty(window, "location", {
      configurable: true,
      value: {
        ...originalLocation,
        get href() {
          return lastHref ?? "";
        },
        set href(value: string) {
          lastHref = value;
        },
      },
    });

    try {
      render(
        <ObsidianSetupDialog
          open
          onClose={() => {}}
          initialStatus={STATUS_OK}
        />,
      );
      fireEvent.click(screen.getByTestId("obsidian-setup-launch"));
      expect(lastHref).not.toBeNull();
      expect(lastHref!.startsWith("obsidian://open?vault=")).toBe(true);
      // The vault path ends in "obsidian-vault" — that final segment must
      // appear (URL-encoded) in the href.
      expect(lastHref!).toContain(encodeURIComponent("obsidian-vault"));
    } finally {
      Object.defineProperty(window, "location", {
        configurable: true,
        value: originalLocation,
      });
    }
  });

  it("clicking 'It worked — Done' calls onComplete then onClose", () => {
    const calls: string[] = [];
    const onComplete = vi.fn(() => calls.push("complete"));
    const onClose = vi.fn(() => calls.push("close"));
    render(
      <ObsidianSetupDialog
        open
        onClose={onClose}
        onComplete={onComplete}
        initialStatus={STATUS_OK}
      />,
    );
    fireEvent.click(screen.getByTestId("obsidian-setup-finish"));
    expect(onComplete).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(calls).toEqual(["complete", "close"]);
  });

  it("escape key closes the dialog via onClose", () => {
    const onClose = vi.fn();
    render(
      <ObsidianSetupDialog
        open
        onClose={onClose}
        initialStatus={STATUS_NOT_INSTALLED}
      />,
    );
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("initialStatus with installed=true and vault_registered=true skips to step 3", () => {
    render(
      <ObsidianSetupDialog
        open
        onClose={() => {}}
        initialStatus={STATUS_OK}
      />,
    );
    expect(screen.getByTestId("obsidian-setup-step-3")).toBeDefined();
    expect(screen.queryByTestId("obsidian-setup-step-1")).toBeNull();
    expect(screen.queryByTestId("obsidian-setup-step-2")).toBeNull();
    const marker3 = screen.getByTestId("obsidian-setup-step-marker-3");
    expect(marker3.getAttribute("data-state")).toBe("active");
  });
});
