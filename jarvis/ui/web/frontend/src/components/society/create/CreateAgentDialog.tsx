/**
 * "New agent": a name, the runtime it runs on (Jarvis, Hermes or OpenClaw —
 * chosen here once and fixed for the agent's life) and the small companion
 * bot that follows it. Everything is optional except the runtime's needs:
 * Hermes and OpenClaw run on an API key or a local model, so they ask which.
 * Mounted once (App.tsx); every plus opens it through `useCreateAgentDialog`.
 */
import { useEffect, useMemo, useState, Suspense, lazy } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { BrandedSelect } from "@/components/ui/select";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { useT } from "@/i18n";
import type { AgentRuntime } from "@/lib/societyApi";
import { AgentNameTaken, useCreateSocietyAgent } from "../data";
import { defaultCompanion, type CompanionAppearance } from "../companion/appearance";
import { RuntimeChoice, runtimeReady, useAgentRuntimes } from "../card/RuntimePicker";
import { useCreateAgentDialog } from "./createAgentStore";

const CompanionEditor = lazy(() =>
  import("../companion/CompanionEditor").then((m) => ({ default: m.CompanionEditor })),
);

/** Provider ids the runtimes route (`jarvis/agent_runtimes/model_map.py`). */
const PROVIDER_NAMES: Record<string, string> = {
  "claude-api": "Anthropic Claude",
  openai: "OpenAI",
  gemini: "Google Gemini",
  grok: "xAI Grok",
  openrouter: "OpenRouter",
  nvidia: "NVIDIA NIM",
  ollama: "Ollama",
  "local-openai": "Local server",
};

function randomCompanion(): CompanionAppearance {
  return defaultCompanion(`${Date.now()}:${Math.random()}`);
}

export function CreateAgentDialogHost() {
  const open = useCreateAgentDialog((s) => s.open);
  // A fresh form every time it opens.
  return open ? <CreateAgentDialog /> : null;
}

function CreateAgentDialog() {
  const t = useT();
  const { finish, cancel } = useCreateAgentDialog.getState();
  const createAgent = useCreateSocietyAgent();
  const runtimes = useAgentRuntimes();
  const [name, setName] = useState("");
  const [runtime, setRuntime] = useState<AgentRuntime>("jarvis");
  const [provider, setProvider] = useState("");
  const [companion, setCompanion] = useState<CompanionAppearance>(randomCompanion);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const providers = useMemo(
    () => (Array.isArray(runtimes.data?.supported_providers) ? runtimes.data.supported_providers : []),
    [runtimes.data],
  );
  const external = runtime !== "jarvis";
  useEffect(() => {
    // The first usable provider until the person picks one.
    if (external && !providers.includes(provider)) setProvider(providers[0] ?? "");
  }, [external, providers, provider]);

  const ready = runtimeReady(runtime, runtimes.data);
  const needsProvider = external && !provider;
  const canCreate = !saving && ready && !needsProvider;

  async function submit() {
    if (!canCreate) return;
    setSaving(true);
    setError(null);
    try {
      const agent = await createAgent({
        name,
        runtime,
        provider: external ? provider : undefined,
        companion,
      });
      finish(agent);
    } catch (exc) {
      setError(
        exc instanceof AgentNameTaken
          ? t("society.create_agent.name_taken")
          : exc instanceof Error ? exc.message : String(exc),
      );
      setSaving(false);
    }
  }

  return (
    <Dialog.Root open onOpenChange={(next) => { if (!next && !saving) cancel(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-scrim/60 backdrop-blur-sm" />
        <Dialog.Content
          data-testid="create-agent-dialog"
          className="fixed left-1/2 top-1/2 z-50 flex max-h-[90dvh] w-[min(560px,calc(100vw-24px))] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-xl border border-border bg-popover text-foreground shadow-float"
        >
          <header className="flex items-center gap-3 border-b border-border p-4">
            <div className="flex-1">
              <Dialog.Title className="font-semibold">{t("society.create_agent.title")}</Dialog.Title>
              <Dialog.Description className="text-sm text-muted-foreground">
                {t("society.create_agent.subtitle")}
              </Dialog.Description>
            </div>
            <button type="button" aria-label={t("society.card.close")} onClick={() => { if (!saving) cancel(); }} className="rounded p-2 hover:bg-secondary">
              <X size={18} />
            </button>
          </header>
          <form
            className="flex min-h-0 flex-col"
            onSubmit={(event) => { event.preventDefault(); void submit(); }}
          >
            <div className="flex min-h-0 flex-col gap-5 overflow-y-auto p-4">
              <label className="flex flex-col gap-1.5 text-sm">
                <span className="font-medium">{t("society.create_agent.name_label")}</span>
                <Input
                  autoFocus
                  value={name}
                  maxLength={40}
                  onChange={(event) => setName(event.target.value)}
                  placeholder={t("society.create_agent.name_placeholder")}
                  disabled={saving}
                  data-testid="create-agent-name"
                />
              </label>

              <div className="flex flex-col gap-1.5 text-sm">
                <span className="font-medium">{t("society.create_agent.type_label")}</span>
                <RuntimeChoice value={runtime} onChange={setRuntime} disabled={saving} />
                <p className="text-xs text-muted-foreground">{t("society.create_agent.type_fixed")}</p>
              </div>

              {external ? (
                <div className="flex flex-col gap-1.5 text-sm">
                  <span className="font-medium">{t("society.create_agent.provider_label")}</span>
                  {providers.length ? (
                    <BrandedSelect
                      value={provider}
                      onValueChange={setProvider}
                      ariaLabel={t("society.create_agent.provider_label")}
                      disabled={saving}
                      testId="create-agent-provider"
                      options={providers.map((id) => ({
                        value: id,
                        label: PROVIDER_NAMES[id] ?? id,
                        icon: <ProviderLogo providerId={id} label={PROVIDER_NAMES[id] ?? id} size="sm" />,
                      }))}
                    />
                  ) : (
                    <p className="text-xs text-muted-foreground">{t("society.create_agent.no_provider")}</p>
                  )}
                  <p className="text-xs text-muted-foreground">{t("society.create_agent.provider_hint")}</p>
                </div>
              ) : null}

              <div className="flex flex-col gap-1.5 text-sm">
                <span className="font-medium">{t("society.create_agent.companion_label")}</span>
                <div className="-mx-4">
                  <Suspense fallback={null}>
                    <CompanionEditor value={companion} onChange={setCompanion} disabled={saving} />
                  </Suspense>
                </div>
              </div>
            </div>
            <footer className="flex shrink-0 flex-wrap items-center justify-end gap-2 border-t border-border p-4">
              {error ? <p role="alert" className="mr-auto text-sm text-destructive">{error}</p> : null}
              <Button type="button" variant="ghost" onClick={() => cancel()} disabled={saving}>
                {t("society.create_agent.cancel")}
              </Button>
              <Button type="submit" disabled={!canCreate} data-testid="create-agent-submit">
                {t(saving ? "society.create_agent.creating" : "society.create_agent.create")}
              </Button>
            </footer>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
