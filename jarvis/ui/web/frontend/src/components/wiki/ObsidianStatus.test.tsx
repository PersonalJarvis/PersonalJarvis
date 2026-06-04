/**
 * Tests for the ObsidianStatus pill.
 *
 * Behaviour anchors:
 *   1. Loading state until the first fetch resolves.
 *   2. Five visual classifications (ok, install, register, unclear-error, unclear-note).
 *   3. Click forwards `onOpenSetup(status)` only for non-OK / non-loading.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ObsidianStatus } from "@/components/wiki/ObsidianStatus";
import type { ObsidianStatus as ObsidianStatusType } from "@/types/setup";

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

function neverResolves(): Promise<Response> {
  return new Promise<Response>(() => {
    /* intentionally never resolves */
  });
}

const OK_STATUS: ObsidianStatusType = {
  installed: true,
  version: "1.7.4",
  config_exists: true,
  vault_registered: true,
  vault_path: "C:/vault",
  recommended_action: "ok",
};

const INSTALL_STATUS: ObsidianStatusType = {
  installed: false,
  version: null,
  config_exists: false,
  vault_registered: false,
  vault_path: "C:/vault",
  recommended_action: "install_obsidian",
};

const REGISTER_STATUS: ObsidianStatusType = {
  installed: true,
  version: "1.7.4",
  config_exists: true,
  vault_registered: false,
  vault_path: "C:/vault",
  recommended_action: "register_vault",
};

const NOTE_STATUS: ObsidianStatusType = {
  installed: true,
  version: "1.7.4",
  config_exists: true,
  vault_registered: true,
  vault_path: "C:/vault",
  recommended_action: "ok",
  note: "Obsidian-Config-Datei nicht lesbar.",
};

describe("ObsidianStatus", () => {
  it("renders loading initially before fetch resolves", () => {
    const fetchImpl = vi.fn(neverResolves) as unknown as typeof fetch;
    render(
      <ObsidianStatus onOpenSetup={() => {}} fetchImpl={fetchImpl} />,
    );
    const pill = screen.getByTestId("obsidian-status-pill");
    expect(pill.getAttribute("data-visual")).toBe("loading");
    expect(pill.textContent).toContain("…");
    expect(screen.getByTestId("obsidian-status-spinner")).toBeDefined();
  });

  it("renders 'verbunden' when status is ok", async () => {
    const fetchImpl = vi.fn(
      async () => jsonResponse(OK_STATUS),
    ) as unknown as typeof fetch;
    render(
      <ObsidianStatus onOpenSetup={() => {}} fetchImpl={fetchImpl} />,
    );
    await waitFor(() => {
      const pill = screen.getByTestId("obsidian-status-pill");
      expect(pill.getAttribute("data-visual")).toBe("ok");
    });
    const pill = screen.getByTestId("obsidian-status-pill");
    expect(pill.textContent).toContain("verbunden");
    // Tooltip surfaces vault_path + version.
    expect(pill.getAttribute("title")).toContain("C:/vault");
    expect(pill.getAttribute("title")).toContain("1.7.4");
    // OK pill is non-interactive.
    expect((pill as HTMLButtonElement).disabled).toBe(true);
    // Green colour class present (#5bd4a4).
    expect(pill.className).toContain("#5bd4a4");
  });

  it("renders 'nicht installiert' when installed=false", async () => {
    const fetchImpl = vi.fn(
      async () => jsonResponse(INSTALL_STATUS),
    ) as unknown as typeof fetch;
    render(
      <ObsidianStatus onOpenSetup={() => {}} fetchImpl={fetchImpl} />,
    );
    await waitFor(() => {
      const pill = screen.getByTestId("obsidian-status-pill");
      expect(pill.getAttribute("data-visual")).toBe("install");
    });
    const pill = screen.getByTestId("obsidian-status-pill");
    expect(pill.textContent).toContain("nicht installiert");
    expect(pill.className).toContain("#facc15");
    expect((pill as HTMLButtonElement).disabled).toBe(false);
  });

  it("renders 'nicht registriert' when vault_registered=false", async () => {
    const fetchImpl = vi.fn(
      async () => jsonResponse(REGISTER_STATUS),
    ) as unknown as typeof fetch;
    render(
      <ObsidianStatus onOpenSetup={() => {}} fetchImpl={fetchImpl} />,
    );
    await waitFor(() => {
      const pill = screen.getByTestId("obsidian-status-pill");
      expect(pill.getAttribute("data-visual")).toBe("register");
    });
    const pill = screen.getByTestId("obsidian-status-pill");
    expect(pill.textContent).toContain("nicht registriert");
    expect(pill.className).toContain("#ffb84d");
  });

  it("renders 'Status unklar' on fetch failure", async () => {
    const fetchImpl = vi.fn(async () => {
      throw new Error("network down");
    }) as unknown as typeof fetch;
    render(
      <ObsidianStatus onOpenSetup={() => {}} fetchImpl={fetchImpl} />,
    );
    await waitFor(() => {
      const pill = screen.getByTestId("obsidian-status-pill");
      expect(pill.getAttribute("data-visual")).toBe("unclear");
    });
    const pill = screen.getByTestId("obsidian-status-pill");
    expect(pill.textContent).toContain("Status unklar");
    expect(pill.className).toContain("#8d94a8");
  });

  it("renders 'Status unklar' when note is set", async () => {
    const fetchImpl = vi.fn(
      async () => jsonResponse(NOTE_STATUS),
    ) as unknown as typeof fetch;
    render(
      <ObsidianStatus onOpenSetup={() => {}} fetchImpl={fetchImpl} />,
    );
    await waitFor(() => {
      const pill = screen.getByTestId("obsidian-status-pill");
      expect(pill.getAttribute("data-visual")).toBe("unclear");
    });
    const pill = screen.getByTestId("obsidian-status-pill");
    expect(pill.textContent).toContain("Status unklar");
    expect(pill.getAttribute("title")).toContain("nicht lesbar");
  });

  it("clicking a non-ok pill calls onOpenSetup with the status", async () => {
    const fetchImpl = vi.fn(
      async () => jsonResponse(REGISTER_STATUS),
    ) as unknown as typeof fetch;
    const onOpenSetup = vi.fn();
    render(
      <ObsidianStatus onOpenSetup={onOpenSetup} fetchImpl={fetchImpl} />,
    );
    await waitFor(() => {
      expect(
        screen.getByTestId("obsidian-status-pill").getAttribute("data-visual"),
      ).toBe("register");
    });
    fireEvent.click(screen.getByTestId("obsidian-status-pill"));
    expect(onOpenSetup).toHaveBeenCalledTimes(1);
    expect(onOpenSetup).toHaveBeenCalledWith(REGISTER_STATUS);
  });

  it("clicking the ok pill does NOT call onOpenSetup", async () => {
    const fetchImpl = vi.fn(
      async () => jsonResponse(OK_STATUS),
    ) as unknown as typeof fetch;
    const onOpenSetup = vi.fn();
    render(
      <ObsidianStatus onOpenSetup={onOpenSetup} fetchImpl={fetchImpl} />,
    );
    await waitFor(() => {
      expect(
        screen.getByTestId("obsidian-status-pill").getAttribute("data-visual"),
      ).toBe("ok");
    });
    // The button is disabled, but fire the click anyway — disabled buttons
    // should not dispatch click handlers in jsdom.
    fireEvent.click(screen.getByTestId("obsidian-status-pill"));
    expect(onOpenSetup).not.toHaveBeenCalled();
  });
});
