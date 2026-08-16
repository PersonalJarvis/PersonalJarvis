import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { setUiLanguage } from "@/i18n";
import { SkillUploadDialog } from "@/views/SkillUploadDialog";

/**
 * What the preview promises, the install has to deliver.
 *
 * The dialog's whole job is to answer "what happens if I say yes" before the
 * yes. Two ways it could lie, and both are pinned here: offering an install
 * the server has already said is blocked, and staying quiet about a skill that
 * will land as a draft rather than going live.
 */

const READY_REPORT = {
  ready: true,
  problems: [],
  lint_findings: [],
  files: ["SKILL.md", "references/guide.md"],
  ignored: [".DS_Store"],
  stripped_root: "my-skill",
  total_bytes: 2048,
  skill: {
    name: "my-skill",
    description: "A skill of my own.",
    category: "productivity",
    version: "1.0.0",
    tags: [],
    state: "validated",
    resource_count: 1,
  },
  limits: { max_file_bytes: 10485760, max_total_bytes: 52428800, max_file_count: 2000 },
};

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return { ok, status, json: async () => body } as Response;
}

let fetchMock: ReturnType<typeof vi.fn>;

function stubFetch(responses: Record<string, unknown>) {
  fetchMock = vi.fn(async (url: string) => {
    const key = Object.keys(responses).find((candidate) => url.includes(candidate));
    if (!key) return jsonResponse({ detail: "unexpected call" }, false, 404);
    return jsonResponse(responses[key]);
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
}

function withClient(node: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return <QueryClientProvider client={client}>{node}</QueryClientProvider>;
}

/** Picks a folder through the hidden input, the way the browser would. */
function pickFolder(files: File[]) {
  const input = screen.getByTestId("skill-folder-input") as HTMLInputElement;
  Object.defineProperty(input, "files", { value: files, configurable: true });
  fireEvent.change(input);
}

function skillFile(relativePath: string): File {
  const file = new File(["---\nname: my-skill\n---\n"], relativePath.split("/").pop()!);
  Object.defineProperty(file, "webkitRelativePath", { value: relativePath });
  return file;
}

beforeEach(() => {
  // Pinned rather than inherited: a changed default language must not
  // silently turn every assertion below into a lookup miss.
  setUiLanguage("en");
  stubFetch({ "/upload/inspect": READY_REPORT });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("SkillUploadDialog", () => {
  it("offers both ways in before asking for anything", () => {
    render(withClient(<SkillUploadDialog open onClose={() => {}} />));

    expect(screen.getByText("Upload files")).toBeTruthy();
    expect(screen.getByText("Fetch from a link")).toBeTruthy();
    // Nothing to confirm yet — the footer only appears once a road is chosen.
    expect(screen.queryByText("Install")).toBeNull();
  });

  it("shows what the upload holds, not what was dropped", async () => {
    render(withClient(<SkillUploadDialog open onClose={() => {}} />));
    fireEvent.click(screen.getByText("Upload files"));

    pickFolder([skillFile("my-skill/SKILL.md"), skillFile("my-skill/references/guide.md")]);

    await waitFor(() => expect(screen.getByText("my-skill")).toBeTruthy());
    // The wrapper folder is gone from the listing — these are the paths that
    // will actually be installed.
    expect(screen.getByText("SKILL.md")).toBeTruthy();
    expect(screen.getByText("references/guide.md")).toBeTruthy();
    // The file count shrank by one; the reason is on screen.
    expect(screen.getByText(/Left out/)).toBeTruthy();
  });

  it("sends the relative paths beside the files", async () => {
    render(withClient(<SkillUploadDialog open onClose={() => {}} />));
    fireEvent.click(screen.getByText("Upload files"));

    pickFolder([skillFile("my-skill/SKILL.md")]);

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = fetchMock.mock.calls[0][1].body as FormData;
    expect(JSON.parse(body.get("paths") as string)).toEqual(["my-skill/SKILL.md"]);
  });

  it("refuses to offer an install the server has blocked", async () => {
    stubFetch({
      "/upload/inspect": {
        ...READY_REPORT,
        ready: false,
        problems: ["Skill 'my-skill' already exists."],
      },
    });
    render(withClient(<SkillUploadDialog open onClose={() => {}} />));
    fireEvent.click(screen.getByText("Upload files"));

    pickFolder([skillFile("my-skill/SKILL.md")]);

    await waitFor(() =>
      expect(screen.getAllByText("Skill 'my-skill' already exists.").length).toBeGreaterThan(0),
    );
    expect(screen.getByText("Install").closest("button")?.disabled).toBe(true);
  });

  it("says up front that an unsafe skill will land as a draft", async () => {
    stubFetch({
      "/upload/inspect": {
        ...READY_REPORT,
        lint_findings: ["os.system call in a code block"],
        skill: { ...READY_REPORT.skill, state: "draft" },
      },
    });
    render(withClient(<SkillUploadDialog open onClose={() => {}} />));
    fireEvent.click(screen.getByText("Upload files"));

    pickFolder([skillFile("my-skill/SKILL.md")]);

    await waitFor(() =>
      expect(screen.getByText(/The safety check found something/)).toBeTruthy(),
    );
    expect(screen.getByText(/Will land as a draft/)).toBeTruthy();
    // A finding is not a blocker: it changes where the skill lands, and the
    // owner may still say yes to that.
    expect(screen.getByText("Install").closest("button")?.disabled).toBe(false);
  });

  it("installs and reports the name back", async () => {
    stubFetch({
      "/upload/inspect": READY_REPORT,
      "/api/skills/upload": { name: "my-skill" },
    });
    const onInstalled = vi.fn();
    const onClose = vi.fn();
    render(withClient(<SkillUploadDialog open onClose={onClose} onInstalled={onInstalled} />));
    fireEvent.click(screen.getByText("Upload files"));

    pickFolder([skillFile("my-skill/SKILL.md")]);
    await waitFor(() => expect(screen.getByText("my-skill")).toBeTruthy());

    fireEvent.click(screen.getByText("Install"));

    await waitFor(() => expect(onInstalled).toHaveBeenCalledWith("my-skill"));
    expect(onClose).toHaveBeenCalled();
  });

  it("puts the long-dead link import back within reach", async () => {
    stubFetch({ "/api/skills/import": { name: "linked-skill" } });
    const onInstalled = vi.fn();
    render(withClient(<SkillUploadDialog open onClose={() => {}} onInstalled={onInstalled} />));

    fireEvent.click(screen.getByText("Fetch from a link"));
    fireEvent.change(screen.getByLabelText("Address of the SKILL.md"), {
      target: { value: "https://example.test/SKILL.md" },
    });
    fireEvent.click(screen.getByText("Install"));

    await waitFor(() => expect(onInstalled).toHaveBeenCalledWith("linked-skill"));
  });
});
