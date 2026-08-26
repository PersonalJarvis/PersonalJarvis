/**
 * The "Set up everything" flow against a faked fetch: the plan (installed
 * first, kept picks, skipped slots, one download per missing model), the
 * server step (a stopped local server is started before the plan is read),
 * and the writes (one PUT per role, one tune per model, a refused slot does
 * not stop the rest).
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import type { OverviewResponse, RoleRow } from "@/hooks/useLocalModels";

import { planRoles, runLocalSetup, type SetupStep } from "./localSetup";

const BASE = "/api/providers/ollama/local-models";

function role(overrides: Partial<RoleRow> & { id: RoleRow["id"] }): RoleRow {
  return {
    label_key: `local_models.role_${overrides.id}`,
    config_key: "",
    current: "",
    installed: false,
    required: [],
    recommended_capabilities: [],
    qualifying: [],
    recommended: "",
    recommended_reason: "",
    writable: true,
    advanced: false,
    note: "",
    ...overrides,
  };
}

function overview(
  roles: RoleRow[],
  models: string[],
  server: Partial<OverviewResponse["server"]> = {},
): OverviewResponse {
  return {
    server: {
      installed: true,
      binary: "ollama",
      running: true,
      version: "0.32.15",
      detail: "",
      base_url: "http://127.0.0.1:11434",
      host_kind: "local",
      models_dir: "",
      running_models: [],
      disk_bytes: 0,
      loaded_vram_bytes: 0,
      error: null,
      ...server,
    },
    roles: { provider: "ollama", server: "", roles, error: null },
    inventory: {
      provider: "ollama",
      server: "",
      models: models.map((name) => ({
        name,
        size_bytes: 1,
        digest: "",
        modified_at: "",
        family: "",
        parameter_size: "",
        quantization_level: "",
        context_length: null,
        capabilities: ["completion"],
        license: "",
        probed: true,
        used_by: [],
        loaded: false,
        size_vram_bytes: 0,
        expires_at: "",
        running_context_length: null,
      })),
      running: [],
      disk_bytes: 0,
      loaded_vram_bytes: 0,
      error: null,
    },
    recommended: {
      server: "",
      server_reachable: true,
      message: "",
      memory_gb: 32,
      accelerator_gb: 16,
      accelerator_source: "nvidia-smi",
      roles: ["chat", "vision", "coder", "embedding"],
      curated_reviewed_on: "2026-08-24",
      models: [],
      installed: [],
    },
    source: "live",
    fetched_at: 1_700_000_000,
  };
}

const ROLES: RoleRow[] = [
  role({
    id: "chat",
    current: "gemma4:12b-it-qat",
    installed: true,
    recommended: "gemma4:12b-it-qat",
  }),
  role({ id: "voice", current: "qwen3.5:4b", installed: true, recommended: "ornith:9b" }),
  role({ id: "tools_screen", recommended: "qwen3.5:4b" }),
  role({ id: "deep", recommended: "ornith:9b" }),
  role({
    id: "embedding",
    note: "The wiki embeds with openai, not with Ollama.",
  }),
  role({ id: "ack", writable: false, advanced: true, recommended: "qwen3.5:4b" }),
];

describe("planRoles", () => {
  it("keeps a role on its pick, assigns the rest, skips what is served elsewhere, downloads once", () => {
    const plan = planRoles(overview(ROLES, ["gemma4:12b-it-qat", "qwen3.5:4b"]));
    expect(plan.kept).toEqual([{ role: "chat", model: "gemma4:12b-it-qat" }]);
    expect(plan.assign).toEqual([
      { role: "voice", model: "ornith:9b" },
      { role: "tools_screen", model: "qwen3.5:4b" },
      { role: "deep", model: "ornith:9b" },
    ]);
    expect(plan.skipped).toEqual([
      { role: "embedding", note: "The wiki embeds with openai, not with Ollama." },
    ]);
    // ornith:9b is wanted twice and missing once; qwen3.5:4b is on disk.
    expect(plan.pulls).toEqual(["ornith:9b"]);
  });

  it("treats ':latest' as the same download and a missing pick as a skip", () => {
    const plan = planRoles(
      overview(
        [
          role({ id: "chat", current: "qwen3.5", installed: true, recommended: "qwen3.5:latest" }),
          role({ id: "deep" }),
        ],
        ["qwen3.5:latest"],
      ),
    );
    expect(plan.kept).toEqual([{ role: "chat", model: "qwen3.5:latest" }]);
    expect(plan.assign).toEqual([]);
    expect(plan.skipped).toEqual([{ role: "deep", note: "" }]);
  });
});

interface Script {
  overviews: OverviewResponse[];
  refuse?: string;
}

function installFetch(script: Script) {
  let reads = 0;
  const calls: string[] = [];
  const ok = (body: unknown) =>
    ({ ok: true, status: 200, json: async () => body }) as Response;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push(`${method} ${url}`);
    if (url === `${BASE}/overview?fresh=1`) {
      const body = script.overviews[Math.min(reads, script.overviews.length - 1)];
      reads += 1;
      return ok(body);
    }
    if (url === "/api/providers/ollama/ollama-runtime/start" && method === "POST")
      return ok({ ok: true, detail: "started", status: { running: true } });
    if (url.startsWith(`${BASE}/roles/`) && method === "PUT") {
      const roleId = url.split("/").pop();
      if (roleId === script.refuse)
        return {
          ok: false,
          status: 422,
          json: async () => ({ detail: "Install the managed voice server first." }),
        } as Response;
      return ok({ ok: true, role: roleId, model: "", config_key: "", message: "" });
    }
    if (url === "/api/providers/ollama/pull" && method === "POST")
      return ok({ state: "running", model: "", message: "" });
    if (url.startsWith("/api/providers/ollama/pull/status"))
      return ok({ state: "done", model: "", message: "", percent: 100 });
    if (url === `${BASE}/verify` && method === "POST")
      return ok({
        ok: true,
        status: "ok",
        reason: "",
        steps: [
          { id: "server", ok: true, model: "", detail: "Ollama 0.32.15", ms: 12 },
          { id: "chat", ok: true, model: "gemma4:12b-it-qat", detail: "Answered.", ms: 1800 },
          { id: "embedding", ok: null, model: "", detail: "No embedding role is configured.", ms: 0 },
        ],
      });
    if (url === `${BASE}/runtime/autostart` && method === "PUT")
      return ok({ enabled: true, in_use: true, reason: "local models serve the chat role" });
    throw new Error(`unexpected fetch: ${method} ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("runLocalSetup", () => {
  it("downloads what is missing, writes each role once, tunes each model once", async () => {
    const { calls } = installFetch({
      overviews: [overview(ROLES, ["gemma4:12b-it-qat", "qwen3.5:4b"])],
    });
    const steps: SetupStep[] = [];
    const tuned: string[] = [];
    const summary = await runLocalSetup({
      providerId: "ollama",
      report: (s) => steps.push(s),
      alive: () => true,
      tune: async (model) => {
        tuned.push(model);
        return `${model} tuned`;
      },
      pollMs: 0,
    });
    expect(summary.serverStarted).toBe(false);
    expect(summary.pulled).toEqual(["ornith:9b"]);
    expect(summary.assigned.map((a) => a.role)).toEqual(["voice", "tools_screen", "deep"]);
    expect(summary.kept).toEqual([{ role: "chat", model: "gemma4:12b-it-qat" }]);
    expect(summary.skipped).toHaveLength(1);
    // ornith:9b serves two roles and is tuned once.
    expect(tuned).toEqual(["ornith:9b", "qwen3.5:4b"]);
    expect(summary.readbacks).toEqual({
      "ornith:9b": "ornith:9b tuned",
      "qwen3.5:4b": "qwen3.5:4b tuned",
    });
    expect(calls.filter((c) => c.startsWith(`PUT ${BASE}/roles/`))).toEqual([
      `PUT ${BASE}/roles/voice`,
      `PUT ${BASE}/roles/tools_screen`,
      `PUT ${BASE}/roles/deep`,
    ]);
    expect(calls).not.toContain("POST /api/providers/ollama/ollama-runtime/start");
    expect(steps.at(-1)?.phase).toBe("done");
    // One download, three writes, two tunes (ornith:9b serves two roles),
    // then the proof and the boot switch.
    expect(steps.map((s) => s.phase)).toEqual([
      "planning",
      "pulling",
      "assigning",
      "tuning",
      "assigning",
      "tuning",
      "assigning",
      "verifying",
      "saving",
      "done",
    ]);
    expect(calls).toContain(`POST ${BASE}/verify`);
    expect(calls).toContain(`PUT ${BASE}/runtime/autostart`);
    expect(summary.verify?.ok).toBe(true);
    expect(summary.verify?.steps.map((s) => s.id)).toEqual(["server", "chat", "embedding"]);
    expect(summary.autostart).toBe(true);
  });

  it("proves the setup even when nothing changed, and never saves autostart for a remote server", async () => {
    const remote = overview(
      [role({ id: "chat", current: "qwen3.5:4b", installed: true, recommended: "qwen3.5:4b" })],
      ["qwen3.5:4b"],
      { host_kind: "remote", base_url: "http://box.lan:11434" },
    );
    const { calls } = installFetch({ overviews: [remote] });
    const summary = await runLocalSetup({
      providerId: "ollama",
      report: () => undefined,
      alive: () => true,
      tune: async () => "",
      pollMs: 0,
    });
    expect(summary.kept).toEqual([{ role: "chat", model: "qwen3.5:4b" }]);
    expect(summary.verify?.ok).toBe(true);
    expect(summary.autostart).toBeUndefined();
    expect(calls).toContain(`POST ${BASE}/verify`);
    expect(calls).not.toContain(`PUT ${BASE}/runtime/autostart`);
  });

  it("starts a stopped local server first and reads the plan from the server that answers", async () => {
    const stopped = overview([], [], { running: false, installed: true });
    const running = overview(
      [role({ id: "chat", recommended: "qwen3.5:4b" })],
      ["qwen3.5:4b"],
    );
    const { calls } = installFetch({ overviews: [stopped, running] });
    const summary = await runLocalSetup({
      providerId: "ollama",
      report: () => undefined,
      alive: () => true,
      tune: async () => "",
      pollMs: 0,
    });
    expect(summary.serverStarted).toBe(true);
    expect(calls.indexOf("POST /api/providers/ollama/ollama-runtime/start")).toBeLessThan(
      calls.lastIndexOf(`GET ${BASE}/overview?fresh=1`),
    );
    expect(summary.assigned).toEqual([{ role: "chat", model: "qwen3.5:4b" }]);
  });

  it("stops with the backend's sentence when a remote server does not answer", async () => {
    installFetch({
      overviews: [
        overview([], [], {
          running: false,
          host_kind: "remote",
          error: "The server at http://box:11434 did not answer.",
        }),
      ],
    });
    await expect(
      runLocalSetup({
        providerId: "ollama",
        report: () => undefined,
        alive: () => true,
        tune: async () => "",
        pollMs: 0,
      }),
    ).rejects.toThrow("did not answer");
  });

  it("a refused slot is skipped with its reason and the other roles still land", async () => {
    installFetch({
      overviews: [overview(ROLES, ["gemma4:12b-it-qat", "qwen3.5:4b", "ornith:9b"])],
      refuse: "voice",
    });
    const summary = await runLocalSetup({
      providerId: "ollama",
      report: () => undefined,
      alive: () => true,
      tune: async () => "",
      pollMs: 0,
    });
    expect(summary.assigned.map((a) => a.role)).toEqual(["tools_screen", "deep"]);
    expect(summary.skipped).toContainEqual({
      role: "voice",
      note: "Install the managed voice server first.",
    });
  });
});
