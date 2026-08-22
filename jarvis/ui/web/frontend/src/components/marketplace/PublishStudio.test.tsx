/**
 * The Publish Studio: the doors, the form, the check, the submit.
 *
 * Pinned here: the entry screen offers every way a package can arrive, a
 * blank skill starts with a template rather than an empty box, the Check
 * button runs the backend's own validator and reports its field errors where
 * the field is, and Publish stays disabled until somebody is signed in.
 *
 * No jest-dom in this repo — assertions use toBeTruthy()/toBeNull().
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/openExternal", () => ({ openExternalUrl: vi.fn() }));
vi.mock("@/lib/clipboard", () => ({ robustCopy: vi.fn(async () => true) }));

import { PublishStudio } from "@/components/marketplace/PublishStudio";
import type { PublishIdentityWire } from "@/components/marketplace/PublishIdentity";
import { setUiLanguage } from "@/i18n";

const SIGNED_IN: PublishIdentityWire = {
  enabled: true,
  wallpapers_enabled: true,
  signed_in: true,
  login: "octocat",
  avatar_url: null,
};
const SIGNED_OUT: PublishIdentityWire = { enabled: true, wallpapers_enabled: true, signed_in: false };

function stubServer(identity: PublishIdentityWire, opts?: { validateErrors?: unknown[] }) {
  const calls: { url: string; body?: unknown }[] = [];
  const json = (body: unknown, ok = true, status = 200) => ({ ok, status, json: async () => body });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: unknown, init?: { method?: string; body?: string }) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      calls.push({ url, body: init?.body ? JSON.parse(init.body) : undefined });
      if (url === "/api/marketplace/publish/identity" && method === "GET") return json(identity);
      if (url === "/api/marketplace/publish/validate") {
        return json({ ok: !(opts?.validateErrors?.length), errors: opts?.validateErrors ?? [] });
      }
      if (url === "/api/marketplace/publish/submit") {
        const body = JSON.parse(init?.body ?? "{}") as { name: string; version: string };
        return json(
          {
            ok: true,
            name: body.name,
            version: body.version,
            pr_url: "https://github.com/PersonalJarvis/marketplace/pull/1",
            install: {
              cli: `jarvis marketplace install ${body.name}`,
              runner: `uvx --from personal-jarvis jarvis marketplace install ${body.name}`,
              prompt: `Install the "${body.name}" skill from the community marketplace.`,
            },
          },
          true,
          201,
        );
      }
      if (url === "/api/wallpapers/uploads") {
        return json({
          items: [
            { id: "u0000000000000001", title: "Harbour At Dawn", theme: "light", createdAt: 1, source: "own" },
            {
              id: "u0000000000000002",
              title: "Borrowed",
              theme: "dark",
              createdAt: 2,
              source: "marketplace",
              publisher: "someone",
            },
          ],
        });
      }
      if (url.startsWith("/api/marketplace/publish/status")) return json({ live: true });
      return json({});
    }),
  );
  return calls;
}

function renderStudio(onClose = () => undefined, initialStage?: "source" | "wallpapers") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PublishStudio onClose={onClose} initialStage={initialStage} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  setUiLanguage("en");
});

describe("PublishStudio", () => {
  it("opens on the doors, with the wallpaper lane among them", async () => {
    stubServer(SIGNED_IN);
    renderStudio();
    expect(await screen.findByTestId("studio-door-folder")).toBeTruthy();
    expect(screen.getByTestId("studio-door-github")).toBeTruthy();
    expect(screen.getByTestId("studio-door-wallpaper")).toBeTruthy();
    // The rail lights the "what" station once somebody is signed in.
    await screen.findByText(/Signed in as @octocat/);
  });

  it("closes on Escape", async () => {
    stubServer(SIGNED_IN);
    const onClose = vi.fn();
    renderStudio(onClose);
    await screen.findByTestId("studio-door-folder");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("starts a blank skill with a template, checks it, and publishes it", async () => {
    const calls = stubServer(SIGNED_IN);
    renderStudio();
    await screen.findByTestId("studio-door-folder");

    fireEvent.click(screen.getByRole("button", { name: /^Skill$/ }));
    const form = await screen.findByTestId("studio-form");
    expect(form).toBeTruthy();
    // The SKILL.md box is pre-filled — nobody starts at an empty document.
    expect((screen.getByDisplayValue(/name: my-skill/) as HTMLTextAreaElement).value).toContain(
      "---",
    );
    // The live card preview follows the name.
    fireEvent.change(screen.getByTestId("studio-name"), { target: { value: "three-point-check" } });
    expect(screen.getByTestId("studio-card-preview").textContent).toContain("three-point-check");

    fireEvent.click(screen.getByRole("button", { name: "Check" }));
    expect(await screen.findByTestId("studio-check-ok")).toBeTruthy();
    const validate = calls.find((c) => c.url === "/api/marketplace/publish/validate");
    expect(validate?.body).toMatchObject({ kind: "skill", name: "three-point-check", version: "1.0.0" });

    fireEvent.click(screen.getByTestId("studio-publish"));
    const done = await screen.findByTestId("studio-published");
    expect(done.textContent).toContain("three-point-check");
    expect(done.textContent).toContain("View the pull request");
  });

  it("puts a field error where the field is", async () => {
    stubServer(SIGNED_IN, {
      validateErrors: [{ field: "version", error: "version must be plain SemVer, e.g. 1.0.0" }],
    });
    renderStudio();
    await screen.findByTestId("studio-door-folder");
    fireEvent.click(screen.getByRole("button", { name: /^Skill$/ }));
    await screen.findByTestId("studio-form");
    fireEvent.click(screen.getByRole("button", { name: "Check" }));
    expect(await screen.findByText(/version must be plain SemVer/)).toBeTruthy();
    expect(screen.queryByTestId("studio-check-ok")).toBeNull();
  });

  it("keeps Publish disabled until somebody is signed in", async () => {
    stubServer(SIGNED_OUT);
    renderStudio();
    await screen.findByTestId("studio-door-folder");
    fireEvent.click(screen.getByRole("button", { name: /^Skill$/ }));
    await screen.findByTestId("studio-form");
    expect((screen.getByTestId("studio-publish") as HTMLButtonElement).disabled).toBe(true);
  });

  it("lists only the owner's own pictures in the wallpaper lane", async () => {
    stubServer(SIGNED_IN);
    renderStudio(() => undefined, "wallpapers");
    const grid = await screen.findByTestId("studio-own-wallpapers");
    expect(grid.textContent).toContain("Harbour At Dawn");
    // An installed community picture is somebody else's work — not offered.
    expect(grid.textContent).not.toContain("Borrowed");

    fireEvent.click(screen.getByRole("button", { name: /Harbour At Dawn/ }));
    expect(await screen.findByTestId("publish-wallpaper-dialog")).toBeTruthy();
  });

  it("says so when publishing is disabled in this deployment", async () => {
    stubServer({ enabled: false, wallpapers_enabled: false, signed_in: false });
    renderStudio();
    expect(await screen.findByText(/Publishing is disabled/)).toBeTruthy();
  });

  it("speaks German when the UI does", async () => {
    stubServer(SIGNED_IN);
    setUiLanguage("de");
    renderStudio();
    expect(await screen.findByText("Paketordner ablegen")).toBeTruthy(); // i18n-allow
    expect(screen.getByText("Im Marketplace veröffentlichen")).toBeTruthy(); // i18n-allow
  });
});
