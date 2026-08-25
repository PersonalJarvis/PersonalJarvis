import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { mockState, mockProviders } = vi.hoisted(() => ({
  mockState: {
    activeSection: "local-models" as string,
    setActiveSection: vi.fn(),
  },
  mockProviders: {
    providers: [] as Array<Record<string, unknown>>,
    loading: false,
    error: null as string | null,
    refetch: vi.fn(async () => undefined),
  },
}));

vi.mock("@/store/events", () => ({
  useEventStore: (selector: (s: typeof mockState) => unknown) =>
    selector(mockState),
}));

vi.mock("@/i18n", () => ({
  // Identity translator: assertions match the keys themselves.
  useT: () => (key: string) => key,
  useLocaleChunk: () => true,
}));

vi.mock("@/hooks/useProviders", () => ({
  useProviders: () => mockProviders,
}));

// Every panel has its own tests; here only the mount and the wiring matter.
vi.mock("@/views/local-models/OverviewPanel", () => ({
  OverviewPanel: ({
    providerId,
    onTune,
    onOpenApiKeys,
    onBrowse,
    onOpenAssistant,
    onReportProblem,
    assistantSlot,
  }: {
    providerId: string;
    onTune?: (model: string) => void;
    onOpenApiKeys?: () => void;
    onBrowse?: () => void;
    onOpenAssistant?: () => void;
    onReportProblem?: () => void;
    assistantSlot?: React.ReactNode;
  }) => (
    <div data-testid="overview-panel" data-provider={providerId}>
      <button type="button" onClick={() => onTune?.("qwen3.5:4b")}>
        tune
      </button>
      <button type="button" onClick={() => onOpenApiKeys?.()}>
        keys
      </button>
      <button type="button" onClick={() => onBrowse?.()}>
        browse
      </button>
      <button type="button" onClick={() => onOpenAssistant?.()}>
        assistant
      </button>
      <button type="button" onClick={() => onReportProblem?.()}>
        broken
      </button>
      {assistantSlot}
    </div>
  ),
}));
vi.mock("@/views/local-models/InventoryPanel", () => ({
  InventoryPanel: () => <div data-testid="inventory-panel" />,
}));
vi.mock("@/views/local-models/CataloguePanel", () => ({
  CataloguePanel: () => <div data-testid="catalogue-panel" />,
}));
vi.mock("@/views/local-models/HuggingFacePanel", () => ({
  HuggingFacePanel: () => <div data-testid="huggingface-panel" />,
}));
vi.mock("@/views/local-models/ServerPanel", () => ({
  ServerPanel: ({ initialLogOpen }: { initialLogOpen?: boolean }) => (
    <div
      data-testid="server-panel"
      data-log-open={initialLogOpen ? "yes" : "no"}
    />
  ),
}));
vi.mock("@/views/local-models/TuneSheet", () => ({
  TuneSheet: ({
    model,
    onClose,
  }: {
    model: { name: string };
    onClose?: () => void;
  }) => (
    <div data-testid="tune-sheet" data-model={model.name}>
      <button type="button" onClick={onClose}>
        close
      </button>
    </div>
  ),
}));
vi.mock("@/hooks/useLocalModels", () => ({
  useInventory: () => ({
    isLoading: false,
    data: { models: [{ name: "qwen3.5:4b" }] },
  }),
}));

import {
  LOCAL_MODELS_MODE_KEY,
  LocalModelsView,
} from "@/views/LocalModelsView";
import { LOCAL_MODELS_SEED_KEY } from "@/lib/localModelsSeed";

const OLLAMA = {
  id: "ollama",
  label: "Ollama",
  supports_model_pull: true,
  supports_base_url: true,
};

beforeEach(() => {
  window.localStorage.removeItem(LOCAL_MODELS_MODE_KEY);
  window.localStorage.removeItem(LOCAL_MODELS_SEED_KEY);
  mockState.setActiveSection = vi.fn();
  mockProviders.providers = [OLLAMA];
  mockProviders.loading = false;
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("LocalModelsView", () => {
  it("renders the header, the Simple rail and the overview by default", () => {
    render(<LocalModelsView />);

    expect(screen.getByText("local_models.title")).toBeDefined();
    expect(
      screen.getByRole("tab", { name: "local_models.tab_overview" }),
    ).toBeDefined();
    expect(
      screen.getByRole("tab", { name: "local_models.tab_catalogue" }),
    ).toBeDefined();
    expect(
      screen.getByRole("tab", { name: "local_models.tab_server" }),
    ).toBeDefined();
    // Simple hides the Advanced-only tabs.
    expect(
      screen.queryByRole("tab", { name: "local_models.tab_models" }),
    ).toBeNull();
    expect(
      screen.queryByRole("tab", { name: "local_models.tab_huggingface" }),
    ).toBeNull();
    expect(screen.getByTestId("local-models-overview")).toBeDefined();
  });

  it("switches tabs and mounts the Catalogue and Server panels", () => {
    render(<LocalModelsView />);
    expect(
      screen.getByTestId("overview-panel").getAttribute("data-provider"),
    ).toBe("ollama");

    fireEvent.click(
      screen.getByRole("tab", { name: "local_models.tab_catalogue" }),
    );
    expect(screen.getByTestId("catalogue-panel")).toBeDefined();
    expect(screen.queryByTestId("local-models-overview")).toBeNull();

    fireEvent.click(
      screen.getByRole("tab", { name: "local_models.tab_server" }),
    );
    expect(screen.getByTestId("server-panel")).toBeDefined();
  });

  it("opens the Tune sheet for the model a role row names, and closes it again", () => {
    render(<LocalModelsView />);
    expect(screen.queryByTestId("local-models-tune-drawer")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "tune" }));
    expect(screen.getByTestId("tune-sheet").getAttribute("data-model")).toBe(
      "qwen3.5:4b",
    );

    fireEvent.click(screen.getByRole("button", { name: "close" }));
    expect(screen.queryByTestId("local-models-tune-drawer")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "keys" }));
    expect(mockState.setActiveSection).toHaveBeenCalledWith("apikeys");
  });

  it("reveals Models in Advanced and remembers the choice", () => {
    render(<LocalModelsView />);

    fireEvent.click(
      screen.getByRole("tab", { name: "local_models.mode_advanced" }),
    );
    expect(
      screen.getByRole("tab", { name: "local_models.tab_models" }),
    ).toBeDefined();
    expect(
      screen.getByRole("tab", { name: "local_models.tab_huggingface" }),
    ).toBeDefined();
    expect(window.localStorage.getItem(LOCAL_MODELS_MODE_KEY)).toBe("advanced");

    fireEvent.click(
      screen.getByRole("tab", { name: "local_models.tab_models" }),
    );
    expect(screen.getByTestId("inventory-panel")).toBeDefined();

    fireEvent.click(
      screen.getByRole("tab", { name: "local_models.tab_huggingface" }),
    );
    expect(screen.getByTestId("huggingface-panel")).toBeDefined();

    // Back to Simple: the rail drops Models and the view falls back to Overview.
    fireEvent.click(
      screen.getByRole("tab", { name: "local_models.mode_simple" }),
    );
    expect(
      screen.queryByRole("tab", { name: "local_models.tab_models" }),
    ).toBeNull();
    expect(screen.getByTestId("local-models-overview")).toBeDefined();
  });

  it("goes back to API Keys and says so when no server can pull models", () => {
    mockProviders.providers = [{ id: "openai", label: "OpenAI" }];
    render(<LocalModelsView />);

    expect(screen.getByText("local_models.no_provider")).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "local_models.back" }));
    expect(mockState.setActiveSection).toHaveBeenCalledWith("apikeys");
  });

  it("mounts the overview from the seed before the provider list resolves", () => {
    window.localStorage.setItem(LOCAL_MODELS_SEED_KEY, "ollama");
    mockProviders.providers = [];
    mockProviders.loading = true;
    render(<LocalModelsView />);

    expect(screen.getByTestId("local-models-overview")).toBeDefined();
    expect(
      screen.getByTestId("overview-panel").getAttribute("data-provider"),
    ).toBe("ollama");
    expect(screen.queryByText("local_models.loading")).toBeNull();
  });

  it("writes the seed once providers resolve, and clears it when no card can pull", () => {
    render(<LocalModelsView />);
    expect(window.localStorage.getItem(LOCAL_MODELS_SEED_KEY)).toBe("ollama");
    cleanup();

    mockProviders.providers = [{ id: "openai", label: "OpenAI" }];
    render(<LocalModelsView />);
    expect(window.localStorage.getItem(LOCAL_MODELS_SEED_KEY)).toBeNull();
    expect(screen.getByText("local_models.no_provider")).toBeDefined();
  });

  it("wires the overview actions: browse, assistant placeholder, server log", () => {
    render(<LocalModelsView />);

    fireEvent.click(screen.getByRole("button", { name: "assistant" }));
    expect(screen.getByTestId("local-models-assistant")).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "assistant" }));
    expect(screen.queryByTestId("local-models-assistant")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "browse" }));
    expect(screen.getByTestId("catalogue-panel")).toBeDefined();

    fireEvent.click(
      screen.getByRole("tab", { name: "local_models.tab_overview" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "broken" }));
    expect(
      screen.getByTestId("server-panel").getAttribute("data-log-open"),
    ).toBe("yes");
  });
});
