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
import { useEventStore } from "@/store/events";
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
  vault_path: "C:/Users/TestUser/wiki/obsidian-vault",
  recommended_action: "install_obsidian",
};

const STATUS_NOT_REGISTERED: ObsidianStatus = {
  installed: true,
  version: "1.7.4",
  config_exists: true,
  vault_registered: false,
  vault_path: "C:/Users/TestUser/wiki/obsidian-vault",
  recommended_action: "register_vault",
};

const STATUS_OK: ObsidianStatus = {
  installed: true,
  version: "1.7.4",
  config_exists: true,
  vault_registered: true,
  vault_path: "C:/Users/TestUser/wiki/obsidian-vault",
  recommended_action: "ok",
};

const VAULTS = [
  { path: "C:/Users/TestUser/Notes", name: "Notes" },
  { path: "C:/Users/TestUser/Work", name: "Work" },
];

/**
 * Fetch stub that branches by URL: the vault-choice picker's ``GET
 * .../vaults`` and the register flow's ``POST .../register`` need
 * different bodies within the same test.
 */
function makeVaultAwareFetch(
  vaults: typeof VAULTS,
  registerBody: unknown,
): typeof fetch {
  return vi.fn(async (input: RequestInfo | URL) => {
    const href = typeof input === "string" ? input : input.toString();
    if (href.includes("/obsidian/vaults")) {
      return jsonResponse({ ok: true, config_exists: true, vaults });
    }
    if (href.includes("/obsidian/register")) {
      return jsonResponse(registerBody);
    }
    return jsonResponse({}, 404);
  }) as unknown as typeof fetch;
}

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

  it("opens the matching in-app guide when registration fails", async () => {
    const onClose = vi.fn();
    useEventStore.setState({ activeSection: "memory" });
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
        onClose={onClose}
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
    fireEvent.click(screen.getByTestId("obsidian-setup-help-link"));
    expect(useEventStore.getState().activeSection).toBe("docs");
    expect(new URL(window.location.href).searchParams.get("doc")).toBe(
      "connect-obsidian",
    );
    expect(onClose).toHaveBeenCalledTimes(1);
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
      expect(lastHref!).toBe(
        `obsidian://open?path=${encodeURIComponent(STATUS_OK.vault_path)}`,
      );
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

  describe("vault choice (spec A6)", () => {
    it("interpolates the assistant name into the vault-choice strings (no literal {name})", () => {
      // The vault-choice strings refer to the assistant by the `{name}`
      // token (i18n/index.ts invariant), resolved through the same
      // interpolation `useT()` applies to every sibling string. In tests the
      // token resolves to the neutral default name ("Assistant"), so the
      // rendered card must show that — never a raw "{name}" placeholder.
      render(
        <ObsidianSetupDialog
          open
          onClose={() => {}}
          initialStatus={STATUS_NOT_REGISTERED}
        />,
      );
      const separateBtn = screen.getByTestId("obsidian-setup-mode-separate");
      expect(separateBtn.textContent).toContain("Assistant");
      expect(separateBtn.textContent).not.toContain("{name}");
    });

    it("renders both vault-choice options, 'existing' enabled once the vault list loads", async () => {
      const fetchImpl = makeVaultAwareFetch(VAULTS, {
        status: "added",
        active_vault_root: "C:/Users/TestUser/wiki/obsidian-vault",
        restart_required: false,
      });
      render(
        <ObsidianSetupDialog
          open
          onClose={() => {}}
          initialStatus={STATUS_NOT_REGISTERED}
          fetchImpl={fetchImpl}
        />,
      );
      // "Separate" is the default and starts selected.
      const separate = screen.getByTestId("obsidian-setup-mode-separate");
      expect(separate.getAttribute("aria-checked")).toBe("true");

      // "Existing" starts disabled (no vault list yet) and enables once the
      // fetch resolves.
      await waitFor(() => {
        expect(
          screen.getByTestId("obsidian-setup-mode-existing").hasAttribute("disabled"),
        ).toBe(false);
      });
      expect(
        screen.getByTestId("obsidian-setup-mode-existing").getAttribute("aria-checked"),
      ).toBe("false");
    });

    it("choosing 'existing' + a vault registers with mode=existing and shows the target path", async () => {
      const fetchImpl = makeVaultAwareFetch(VAULTS, {
        status: "added",
        active_vault_root: "C:/Users/TestUser/Work/Jarvis",
        restart_required: true,
      });
      render(
        <ObsidianSetupDialog
          open
          onClose={() => {}}
          initialStatus={STATUS_NOT_REGISTERED}
          fetchImpl={fetchImpl}
        />,
      );
      await waitFor(() => {
        expect(
          screen.getByTestId("obsidian-setup-mode-existing").hasAttribute("disabled"),
        ).toBe(false);
      });
      fireEvent.click(screen.getByTestId("obsidian-setup-mode-existing"));

      const select = screen.getByTestId(
        "obsidian-setup-vault-select",
      ) as HTMLSelectElement;
      fireEvent.change(select, { target: { value: VAULTS[1].path } });

      fireEvent.click(screen.getByTestId("obsidian-setup-register"));

      await waitFor(() => {
        expect(fetchImpl).toHaveBeenCalledWith(
          "/api/setup/obsidian/register",
          expect.objectContaining({
            method: "POST",
            body: JSON.stringify({
              mode: "existing",
              existing_vault_path: VAULTS[1].path,
            }),
          }),
        );
      });

      await waitFor(() => {
        expect(screen.getByTestId("obsidian-setup-step-3")).toBeDefined();
      });
      const target = screen.getByTestId("obsidian-setup-active-vault-root");
      expect(target.textContent).toContain("C:/Users/TestUser/Work/Jarvis");
      expect(screen.getByTestId("obsidian-setup-restart-hint")).toBeDefined();
    });

    it("after choosing 'existing', 'Open in Obsidian' targets the picked vault, not the default one", async () => {
      // Regression: the live-test button must target the vault the user
      // actually picked (VAULTS[1] = "Work"), never the default
      // separate-vault path from `initialStatus`.
      const fetchImpl = makeVaultAwareFetch(VAULTS, {
        status: "added",
        active_vault_root: "C:/Users/TestUser/Work/Jarvis",
        restart_required: true,
      });
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
            initialStatus={STATUS_NOT_REGISTERED}
            fetchImpl={fetchImpl}
          />,
        );
        await waitFor(() => {
          expect(
            screen.getByTestId("obsidian-setup-mode-existing").hasAttribute("disabled"),
          ).toBe(false);
        });
        fireEvent.click(screen.getByTestId("obsidian-setup-mode-existing"));
        fireEvent.change(
          screen.getByTestId("obsidian-setup-vault-select") as HTMLSelectElement,
          { target: { value: VAULTS[1].path } },
        );
        fireEvent.click(screen.getByTestId("obsidian-setup-register"));

        await waitFor(() => {
          expect(screen.getByTestId("obsidian-setup-step-3")).toBeDefined();
        });
        fireEvent.click(screen.getByTestId("obsidian-setup-launch"));
        expect(lastHref).not.toBeNull();
        expect(lastHref!).toBe(
          `obsidian://open?path=${encodeURIComponent("C:/Users/TestUser/Work/Jarvis")}`,
        );
      } finally {
        Object.defineProperty(window, "location", {
          configurable: true,
          value: originalLocation,
        });
      }
    });

    it("clicking 'Restart now' POSTs to the shared restart-app endpoint", async () => {
      const fetchImpl = makeVaultAwareFetch(VAULTS, {
        status: "added",
        active_vault_root: "C:/Users/TestUser/Notes/Jarvis",
        restart_required: true,
      });
      render(
        <ObsidianSetupDialog
          open
          onClose={() => {}}
          initialStatus={STATUS_NOT_REGISTERED}
          fetchImpl={fetchImpl}
        />,
      );
      await waitFor(() => {
        expect(
          screen.getByTestId("obsidian-setup-mode-existing").hasAttribute("disabled"),
        ).toBe(false);
      });
      fireEvent.click(screen.getByTestId("obsidian-setup-mode-existing"));
      fireEvent.click(screen.getByTestId("obsidian-setup-register"));

      const restartButton = await screen.findByTestId("obsidian-setup-restart-now");
      fireEvent.click(restartButton);

      await waitFor(() => {
        expect(fetchImpl).toHaveBeenCalledWith(
          "/api/settings/restart-app",
          expect.objectContaining({ method: "POST" }),
        );
      });
    });

    it("choosing 'existing' with an unknown path shows an inline error and stays on step 2", async () => {
      const fetchImpl = makeVaultAwareFetch(VAULTS, {
        status: "config_missing",
        error: "existing vault path not found",
        active_vault_root: "C:/Users/TestUser/wiki/obsidian-vault",
        restart_required: false,
      });
      render(
        <ObsidianSetupDialog
          open
          onClose={() => {}}
          initialStatus={STATUS_NOT_REGISTERED}
          fetchImpl={fetchImpl}
        />,
      );
      await waitFor(() => {
        expect(
          screen.getByTestId("obsidian-setup-mode-existing").hasAttribute("disabled"),
        ).toBe(false);
      });
      fireEvent.click(screen.getByTestId("obsidian-setup-mode-existing"));
      fireEvent.click(screen.getByTestId("obsidian-setup-register"));

      await waitFor(() => {
        expect(
          screen.getByTestId("obsidian-setup-register-error").textContent,
        ).toContain("existing vault path not found");
      });
      expect(screen.getByTestId("obsidian-setup-step-2")).toBeDefined();
    });
  });
});
