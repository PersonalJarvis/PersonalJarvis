import { afterEach, describe, expect, it, vi } from "vitest";

import {
  deleteModel,
  getModelOptions,
  hfPullName,
  localModelsKeys,
  searchCatalog,
  setRole,
} from "./useLocalModels";

function stubFetch(body: unknown, ok = true, status = 200) {
  const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) => ({
    ok,
    status,
    json: async () => body,
  }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useLocalModels fetch helpers", () => {
  it("keeps the slashes of a Hugging Face model name in the path", async () => {
    const fetchMock = stubFetch({
      model: "hf.co/u/r:Q4_K_M",
      options: {},
      configured: false,
    });
    await getModelOptions("ollama", "hf.co/u/r:Q4_K_M");
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toBe(
      "/api/providers/ollama/local-models/models/hf.co/u/r%3AQ4_K_M/options",
    );
  });

  it("sends the role body as JSON to PUT roles/{role}", async () => {
    const fetchMock = stubFetch({
      ok: true,
      role: "chat",
      model: "x",
      config_key: "",
      message: "",
    });
    await setRole("ollama", "chat", "qwen3.5:4b");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe("/api/providers/ollama/local-models/roles/chat");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(String(init.body))).toEqual({ model: "qwen3.5:4b" });
  });

  it("forwards sort and capability only when set", async () => {
    const fetchMock = stubFetch({
      query: "",
      sort: "popular",
      capability: null,
      models: [],
      error: null,
    });
    await searchCatalog("ollama");
    await searchCatalog("ollama", {
      q: "qwen",
      sort: "newest",
      capability: "tools",
    });
    expect(String(fetchMock.mock.calls[0][0])).toBe(
      "/api/providers/ollama/local-models/catalog",
    );
    expect(String(fetchMock.mock.calls[1][0])).toBe(
      "/api/providers/ollama/local-models/catalog?q=qwen&sort=newest&capability=tools",
    );
  });

  it("surfaces the backend sentence of a refused delete", async () => {
    stubFetch({ detail: "qwen3.5:4b is still the pick for chat." }, false, 409);
    await expect(deleteModel("ollama", "qwen3.5:4b")).rejects.toThrow(
      "still the pick",
    );
  });

  it("builds the hf.co pull name the backend uses", () => {
    expect(hfPullName("unsloth", "Qwen3.8-27B-GGUF", "Q4_K_M")).toBe(
      "hf.co/unsloth/Qwen3.8-27B-GGUF:Q4_K_M",
    );
    expect(hfPullName("unsloth", "Qwen3.8-27B-GGUF")).toBe(
      "hf.co/unsloth/Qwen3.8-27B-GGUF",
    );
  });

  it("scopes every query key under local-models and the provider", () => {
    expect(localModelsKeys.roles("ollama")).toEqual([
      "local-models",
      "ollama",
      "roles",
    ]);
    expect(localModelsKeys.catalog("ollama", { q: "a" })).toEqual([
      "local-models",
      "ollama",
      "catalog",
      "a",
      "popular",
      "",
      50,
    ]);
  });
});
