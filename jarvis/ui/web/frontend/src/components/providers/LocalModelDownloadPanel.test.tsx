/**
 * Component tests for the local-model download panel.
 *
 * The feature exists because a keyless install used to dead-end at "run:
 * ollama pull <model>" — a terminal instruction in an app with no terminal.
 * What these tests pin is that the panel never invents any part of that story:
 *
 *  - it appears only where the SERVER can actually be told to fetch a model,
 *    decided by a capability flag rather than a provider name (AP-21);
 *  - installed state and the memory verdict come from the server, verbatim;
 *  - a "tight" fit is shown as a caution, never as a disabled button — a GPU
 *    runs models the RAM rule calls tight;
 *  - a started download reports real progress instead of freezing on a spinner.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { AuthWidget } from "@/components/providers/ProviderTierSection";
import type { ProviderDescriptor } from "@/hooks/useProviders";

vi.mock("@/i18n", () => ({
  useT: () => (key: string) =>
    ({
      "apikeys_model_pull.title": "Models on this machine",
      "apikeys_model_pull.memory": "{0} GB RAM",
      "apikeys_model_pull.size": "~{0} GB",
      "apikeys_model_pull.installed": "Installed",
      "apikeys_model_pull.download": "Download",
      "apikeys_model_pull.custom_placeholder": "Search the library, or type an exact name…",
      "apikeys_model_pull.library_title": "Every model in the Ollama library",
      "apikeys_model_pull.library_searching": "Searching the library…",
      "apikeys_model_pull.library_no_results": "No models found for that search.",
      "apikeys_model_pull.library_loading_versions": "Loading versions…",
      "apikeys_model_pull.library_hosted": "Hosted",
      "apikeys_model_pull.library_context": "{0} context",
      "apikeys_model_pull.library_size_unknown": "size unknown",
      "apikeys_model_pull.hardware_gpu": "{0} GB graphics memory",
      "apikeys_model_pull.best_for_machine": "Best for your machine",
      "apikeys_model_pull.role_chat": "Chat and voice",
      "apikeys_model_pull.role_vision": "Sees your screen",
      "apikeys_model_pull.role_coder": "Coding worker",
      "apikeys_model_pull.role_embedding": "Search embeddings",
    })[key] ?? key,
}));

const PULL_CARD: ProviderDescriptor = {
  id: "ollama",
  label: "Ollama (local)",
  tier: "brain",
  auth_mode: "none",
  secret_keys: [],
  secrets_set: {},
  dashboard_url: null,
  login_cli: null,
  install_hint: null,
  credential_path_hint: null,
  configured: true,
  active: false,
  cli_installed: null,
  credential_help: "Runs models fully local.",
  signup_url: null,
  billing: "local",
  alt_credential: null,
  local_runtime: null,
  supports_model_pull: true,
} as ProviderDescriptor;

const CATALOG = {
  server: "http://localhost:11434",
  server_reachable: true,
  message: "",
  memory_gb: 32,
  installed: ["qwen3.5:latest"],
  models: [
    {
      id: "qwen3.5",
      label: "Qwen 3.5",
      size_gb: 5.2,
      purpose: "The balanced default.",
      tools: true,
      vision: false,
      installed: true,
      fit: "comfortable",
      fit_note: "Runs comfortably in 32 GB of memory.",
    },
    {
      id: "qwen3-vl",
      label: "Qwen 3 VL",
      size_gb: 6,
      purpose: "Sees images.",
      tools: true,
      vision: true,
      installed: false,
      fit: "tight",
      fit_note: "Needs about 8 GB with 8 GB installed.",
    },
  ],
};

function installFetchMock(handler: (url: string, init?: RequestInit) => unknown) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const body = handler(String(input), init);
    return {
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => body,
      text: async () => JSON.stringify(body),
    } as Response;
  });
  (globalThis as unknown as { fetch: typeof fetch }).fetch =
    fetchMock as unknown as typeof fetch;
  return fetchMock;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("local model download panel", () => {
  it("renders nothing on a card whose server has no download API", async () => {
    const fetchMock = installFetchMock(() => CATALOG);
    const cloudCard = { ...PULL_CARD, id: "openai", supports_model_pull: false };

    render(<AuthWidget descriptor={cloudCard} onChanged={() => {}} />);

    expect(screen.queryByText("Models on this machine")).toBeNull();
    // And it must not even ASK: a cloud card firing this request would get a
    // 400 on every render of the provider list.
    expect(
      fetchMock.mock.calls.filter((c) => String(c[0]).includes("pullable-models")),
    ).toHaveLength(0);
  });

  it("shows what the server reports: sizes, memory, and what is already there", async () => {
    installFetchMock(() => CATALOG);

    render(<AuthWidget descriptor={PULL_CARD} onChanged={() => {}} />);

    await waitFor(() => expect(screen.getByText("Qwen 3 VL")).toBeTruthy());
    expect(screen.getByText("32 GB RAM")).toBeTruthy();
    expect(screen.getByText("Installed")).toBeTruthy();
    expect(screen.getByText("Sees images.")).toBeTruthy();
  });

  it("shows a tight fit as a caution, never as a blocked download", async () => {
    installFetchMock(() => CATALOG);

    render(<AuthWidget descriptor={PULL_CARD} onChanged={() => {}} />);

    await waitFor(() =>
      expect(screen.getByText("Needs about 8 GB with 8 GB installed.")).toBeTruthy(),
    );
    const buttons = screen.getAllByRole("button", { name: /download/i });
    expect(buttons.some((b) => !(b as HTMLButtonElement).disabled)).toBe(true);
  });

  it("starts a download and then reports the server's progress", async () => {
    const calls: string[] = [];
    installFetchMock((url) => {
      calls.push(url);
      if (url.includes("/pull/status")) {
        return {
          state: "running",
          model: "qwen3-vl",
          message: "pulling manifest",
          percent: 42,
        };
      }
      if (url.endsWith("/pull")) {
        return { state: "running", model: "qwen3-vl", message: "Starting…" };
      }
      return CATALOG;
    });

    render(<AuthWidget descriptor={PULL_CARD} onChanged={() => {}} />);
    await waitFor(() => expect(screen.getByText("Qwen 3 VL")).toBeTruthy());

    const download = screen
      .getAllByRole("button", { name: /download/i })
      .find((b) => !(b as HTMLButtonElement).disabled)!;
    fireEvent.click(download);

    await waitFor(() => expect(screen.getByText(/qwen3-vl: Starting/)).toBeTruthy());
    expect(calls.some((u) => u.endsWith("/api/providers/ollama/pull"))).toBe(true);
  });

  it("keeps the shortlist visible when the server is down, with its reason", async () => {
    installFetchMock(() => ({
      ...CATALOG,
      server_reachable: false,
      message: "Ollama did not answer at http://localhost:11434.",
    }));

    render(<AuthWidget descriptor={PULL_CARD} onChanged={() => {}} />);

    await waitFor(() =>
      expect(screen.getByText(/Ollama did not answer/)).toBeTruthy(),
    );
    expect(screen.getByText("Qwen 3 VL")).toBeTruthy();
    // Nothing can be pulled from a server that is not there.
    for (const button of screen.getAllByRole("button", { name: /download/i })) {
      expect((button as HTMLButtonElement).disabled).toBe(true);
    }
  });
});

/**
 * Hardware-aware ranking.
 *
 * The shortlist used to be the same four names on every machine. These pin the
 * three things that replaced it: the panel groups by the job a model does, it
 * marks the server's pick for THIS machine, and it says which memory figure the
 * verdicts were judged against — because "18 GB is tight" is bewildering on a
 * box with 64 GB of RAM until you know the number being compared is the card's.
 */
const RANKED_CATALOG = {
  server: "http://localhost:11434",
  server_reachable: true,
  message: "",
  memory_gb: 64,
  accelerator_gb: 24,
  accelerator_source: "nvidia-smi",
  roles: ["chat", "vision", "embedding"],
  installed: [],
  models: [
    {
      id: "gpt-oss:120b",
      label: "GPT-OSS 120B",
      size_gb: 65.4,
      purpose: "Frontier-class answers.",
      role: "chat",
      tools: true,
      vision: false,
      installed: false,
      fit: "tight",
      fit_note: "Bigger than the 24 GB of graphics memory.",
      recommended: false,
    },
    {
      id: "gpt-oss:20b",
      label: "GPT-OSS 20B",
      size_gb: 13.8,
      purpose: "Noticeably stronger reasoning.",
      role: "chat",
      tools: true,
      vision: false,
      installed: false,
      fit: "comfortable",
      fit_note: "Fits in the 24 GB of graphics memory on this machine.",
      recommended: true,
    },
    {
      id: "qwen3-vl",
      label: "Qwen 3 VL 8B",
      size_gb: 6.1,
      purpose: "Screen Context and Computer-Use.",
      role: "vision",
      tools: true,
      vision: true,
      installed: false,
      fit: "comfortable",
      fit_note: "Fits in the 24 GB of graphics memory on this machine.",
      recommended: true,
    },
    {
      id: "bge-m3",
      label: "BGE-M3",
      size_gb: 1.2,
      purpose: "Multilingual embeddings.",
      role: "embedding",
      tools: false,
      vision: false,
      installed: false,
      fit: "comfortable",
      fit_note: "Fits in the 24 GB of graphics memory on this machine.",
      recommended: false,
    },
  ],
};

describe("local model download panel — hardware-aware ranking", () => {
  it("marks the server's pick for this machine, one per role", async () => {
    installFetchMock(() => RANKED_CATALOG);

    render(<AuthWidget descriptor={PULL_CARD} onChanged={() => {}} />);

    await waitFor(() => expect(screen.getByText("GPT-OSS 20B")).toBeTruthy());
    const recommended = document.querySelectorAll('[data-recommended="true"]');
    expect(recommended).toHaveLength(2);
    expect(
      screen.getByTestId("model-pull-row-gpt-oss:20b").getAttribute("data-recommended"),
    ).toBe("true");
    // The biggest model is NOT the pick — it does not fit this card.
    expect(
      screen
        .getByTestId("model-pull-row-gpt-oss:120b")
        .getAttribute("data-recommended"),
    ).toBe("false");
  });

  it("names the graphics memory the verdicts were judged against", async () => {
    installFetchMock(() => RANKED_CATALOG);

    render(<AuthWidget descriptor={PULL_CARD} onChanged={() => {}} />);

    await waitFor(() => expect(screen.getByTestId("model-pull-hardware")).toBeTruthy());
    expect(screen.getByTestId("model-pull-hardware").textContent).toContain("24");
  });

  it("falls back to the RAM figure when no accelerator could be read", async () => {
    installFetchMock(() => ({
      ...RANKED_CATALOG,
      accelerator_gb: 0,
      accelerator_source: "none",
    }));

    render(<AuthWidget descriptor={PULL_CARD} onChanged={() => {}} />);

    await waitFor(() => expect(screen.getByTestId("model-pull-hardware")).toBeTruthy());
    expect(screen.getByTestId("model-pull-hardware").textContent).toContain("64");
  });

  it("keeps working on a payload with no roles at all", async () => {
    // An older backend: one unlabelled group, exactly as the panel looked
    // before roles existed. No empty headings, no crash.
    installFetchMock(() => CATALOG);

    render(<AuthWidget descriptor={PULL_CARD} onChanged={() => {}} />);

    await waitFor(() => expect(screen.getByText("Qwen 3 VL")).toBeTruthy());
    expect(document.querySelectorAll('[data-recommended="true"]')).toHaveLength(0);
  });
});

/**
 * Choosing freely from the PUBLIC library.
 *
 * The shortlist is for someone with no opinion. Someone with one used to get a
 * blind text box: leave the app, look the name up, come back and hope — and the
 * common mistake (pulling a bare name when you wanted a specific size) failed
 * silently by downloading something else. These pin the replacement: the field
 * searches the real catalog, a result opens into its actual published versions
 * with sizes, and none of it is allowed to become load-bearing — an offline
 * catalog must still leave a working exact-name download behind.
 */
const LIBRARY_SEARCH = {
  query: "qwen",
  error: null,
  models: [
    {
      name: "qwen3.5",
      description: "A family of open-source multimodal models.",
      capabilities: ["vision", "tools"],
      cloud: true,
      sizes: ["2b", "9b", "122b"],
      pulls: "17.2M",
      updated: "2 months ago",
      installed: false,
    },
  ],
};

const LIBRARY_TAGS = {
  model: "qwen3.5",
  error: null,
  tags: [
    {
      tag: "9b",
      id: "qwen3.5:9b",
      size_gb: 6.6,
      context: "256K",
      inputs: "Text, Image",
      updated: "5 months ago",
      cloud: false,
      installed: false,
      fit: "comfortable",
      fit_note: "Runs comfortably in 32 GB of memory.",
    },
    {
      tag: "122b",
      id: "qwen3.5:122b",
      size_gb: 65.4,
      context: "256K",
      inputs: "Text, Image",
      updated: "5 months ago",
      cloud: false,
      installed: false,
      fit: "tight",
      fit_note: "Needs about 67 GB with 32 GB installed.",
    },
    {
      tag: "cloud",
      id: "qwen3.5:cloud",
      size_gb: null,
      context: "256K",
      inputs: "Text, Image",
      updated: "5 months ago",
      cloud: true,
      installed: false,
      fit: "unknown",
      fit_note: "",
    },
  ],
};

function libraryFetchMock(overrides: Record<string, unknown> = {}) {
  const calls: string[] = [];
  installFetchMock((url) => {
    calls.push(url);
    for (const [fragment, body] of Object.entries(overrides)) {
      if (url.includes(fragment)) return body;
    }
    if (url.includes("/library/search")) return LIBRARY_SEARCH;
    if (url.includes("/tags")) return LIBRARY_TAGS;
    if (url.endsWith("/pull")) return { state: "running", model: "x", message: "Starting…" };
    return CATALOG;
  });
  return calls;
}

describe("local model download panel — browsing the public library", () => {
  it("says on the card that the whole library is searchable here", async () => {
    // The first version of this shipped as a bare input with a changed
    // placeholder, sitting exactly where the old blind text field had been.
    // It worked, and it was invisible — reported as "there is no model
    // search". A feature indistinguishable from what it replaced has not
    // been delivered, so the heading is part of the feature, not decoration.
    libraryFetchMock();

    render(<AuthWidget descriptor={PULL_CARD} onChanged={() => {}} />);

    await waitFor(() =>
      expect(screen.getByText("Every model in the Ollama library")).toBeTruthy(),
    );
  });

  it("does not touch the public catalog until the user asks", async () => {
    // The panel renders inside a provider LIST; searching on mount would hit
    // the catalog once per card on every render of the settings view.
    const calls = libraryFetchMock();

    render(<AuthWidget descriptor={PULL_CARD} onChanged={() => {}} />);

    await waitFor(() => expect(screen.getByText("Qwen 3 VL")).toBeTruthy());
    expect(calls.some((u) => u.includes("/library/search"))).toBe(false);
  });

  it("searches the catalog and shows what it found", async () => {
    libraryFetchMock();

    render(<AuthWidget descriptor={PULL_CARD} onChanged={() => {}} />);
    await waitFor(() => expect(screen.getByText("Qwen 3 VL")).toBeTruthy());

    fireEvent.change(screen.getByPlaceholderText(/Search the library/), {
      target: { value: "qwen" },
    });

    await waitFor(
      () => expect(screen.getByTestId("library-row-qwen3.5")).toBeTruthy(),
      { timeout: 3000 },
    );
    expect(
      screen.getByText("A family of open-source multimodal models."),
    ).toBeTruthy();
  });

  it("opens a model into its real published versions, with sizes", async () => {
    libraryFetchMock();

    render(<AuthWidget descriptor={PULL_CARD} onChanged={() => {}} />);
    await waitFor(() => expect(screen.getByText("Qwen 3 VL")).toBeTruthy());
    fireEvent.change(screen.getByPlaceholderText(/Search the library/), {
      target: { value: "qwen" },
    });
    await waitFor(
      () => expect(screen.getByTestId("library-row-qwen3.5")).toBeTruthy(),
      { timeout: 3000 },
    );

    fireEvent.click(screen.getByText("qwen3.5"));

    await waitFor(() =>
      expect(screen.getByTestId("library-tag-qwen3.5:9b")).toBeTruthy(),
    );
    // The whole point of the tag list: sizes differ by an order of magnitude.
    expect(screen.getByTestId("library-tag-qwen3.5:122b").textContent).toContain("65.4");
    expect(screen.getByTestId("library-tag-qwen3.5:9b").textContent).toContain("6.6");
  });

  it("downloads the exact tag the user picked, not the bare name", async () => {
    const calls = libraryFetchMock();

    render(<AuthWidget descriptor={PULL_CARD} onChanged={() => {}} />);
    await waitFor(() => expect(screen.getByText("Qwen 3 VL")).toBeTruthy());
    fireEvent.change(screen.getByPlaceholderText(/Search the library/), {
      target: { value: "qwen" },
    });
    await waitFor(
      () => expect(screen.getByTestId("library-row-qwen3.5")).toBeTruthy(),
      { timeout: 3000 },
    );
    fireEvent.click(screen.getByText("qwen3.5"));
    await waitFor(() =>
      expect(screen.getByTestId("library-tag-qwen3.5:9b")).toBeTruthy(),
    );

    const row = screen.getByTestId("library-tag-qwen3.5:9b");
    fireEvent.click(row.querySelector("button")!);

    await waitFor(() =>
      expect(calls.some((u) => u.endsWith("/api/providers/ollama/pull"))).toBe(true),
    );
  });

  it("offers no download for a hosted-only tag", async () => {
    libraryFetchMock();

    render(<AuthWidget descriptor={PULL_CARD} onChanged={() => {}} />);
    await waitFor(() => expect(screen.getByText("Qwen 3 VL")).toBeTruthy());
    fireEvent.change(screen.getByPlaceholderText(/Search the library/), {
      target: { value: "qwen" },
    });
    await waitFor(
      () => expect(screen.getByTestId("library-row-qwen3.5")).toBeTruthy(),
      { timeout: 3000 },
    );
    fireEvent.click(screen.getByText("qwen3.5"));
    await waitFor(() =>
      expect(screen.getByTestId("library-tag-qwen3.5:cloud")).toBeTruthy(),
    );

    const hosted = screen.getByTestId("library-tag-qwen3.5:cloud");
    expect(hosted.textContent).toContain("Hosted");
    expect(hosted.querySelector("button")).toBeNull();
  });

  it("keeps the exact-name download when the catalog is unreachable", async () => {
    // Scraping a public website is a liability; it must never become the only
    // way to install a model.
    const calls = libraryFetchMock({
      "/library/search": {
        query: "mistral",
        models: [],
        error: "ollama.com did not answer, so the library cannot be browsed.",
      },
    });

    render(<AuthWidget descriptor={PULL_CARD} onChanged={() => {}} />);
    await waitFor(() => expect(screen.getByText("Qwen 3 VL")).toBeTruthy());

    const field = screen.getByPlaceholderText(/Search the library/);
    fireEvent.change(field, { target: { value: "mistral" } });

    await waitFor(() => expect(screen.getByText(/did not answer/)).toBeTruthy(), {
      timeout: 3000,
    });

    const downloadButtons = screen.getAllByRole("button", { name: /download/i });
    fireEvent.click(downloadButtons[downloadButtons.length - 1]);
    await waitFor(() =>
      expect(calls.some((u) => u.endsWith("/api/providers/ollama/pull"))).toBe(true),
    );
  });
});
