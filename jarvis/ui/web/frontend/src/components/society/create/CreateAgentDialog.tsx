/**
 * "New agent": a name, the runtime it runs on (Jarvis, Hermes or OpenClaw —
 * chosen here once and fixed for the agent's life), the model it starts on
 * (provider, how it pays — subscription, API key or local — and the model
 * itself; all changeable later in its chat) and the small companion bot that
 * follows it. Only what is connected on this machine is offered
 * (`seatChoice.ts`). Mounted once (App.tsx); every plus opens it through
 * `useCreateAgentDialog`.
 */
import { useEffect, useMemo, useState, Suspense, lazy } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { BrandedSelect } from "@/components/ui/select";
import { AgentMark } from "@/components/agentic/AgentMark";
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { useT } from "@/i18n";
import type { AgentRuntime } from "@/lib/societyApi";
import { AgentNameTaken, RuntimeProviderUnsupported, useCreateSocietyAgent } from "../data";
import { defaultCompanion, type CompanionAppearance } from "../companion/appearance";
import { RuntimeChoice, useAgentRuntimes } from "../card/RuntimePicker";
import { SandboxChoice } from "../card/AgentSandboxSection";
import { modelSeats, runtimeSeats } from "../chat/modelChoices";
import { useModelMenuData } from "../chat/useModelMenuData";
import { accountChoice, accountHint } from "./brainPicker";
import { useCreateAgentDialog } from "./createAgentStore";
import { AccessChoice } from "./AccessChoice";
import { accessModels, defaultModel, pickableOption, providerChoices, type AccessBlocked, type AccessOption } from "./seatChoice";

const CompanionEditor = lazy(() =>
  import("../companion/CompanionEditor").then((m) => ({ default: m.CompanionEditor })),
);

/** A model list this long gets a search field. */
const SEARCH_FROM = 12;

function list(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

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
  const menu = useModelMenuData();
  const [name, setName] = useState("");
  const [runtime, setRuntime] = useState<AgentRuntime>("jarvis");
  const [executionEnvironment, setExecutionEnvironment] = useState<"local" | "sandbox">("local");
  const [providerId, setProviderId] = useState("");
  const [kind, setKind] = useState("");
  const [model, setModel] = useState("");
  const [account, setAccount] = useState("");
  const [companion, setCompanion] = useState<CompanionAppearance>(randomCompanion);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const external = runtime !== "jarvis";
  const defaultModelLabel = t("agent_chat.model_default");
  const data = runtimes.data;
  const supportedKey = list(data?.supported_providers).join(",");
  const gatewayKey = list(data?.subscription_providers).join(",");
  const loginKey = list(data?.login_providers).join(",");
  const accessKey = JSON.stringify(data?.access && typeof data.access === "object" ? data.access : {});
  // Refusals only concern Hermes / OpenClaw (Claude's login billed as Extra Usage).
  const blockedKey = JSON.stringify(external && data?.access_blocked && typeof data.access_blocked === "object" ? data.access_blocked : {});
  const choices = useMemo(() => {
    const all = modelSeats(menu.options, Array.isArray(menu.providers) ? menu.providers : [], menu.live, defaultModelLabel);
    const split = (key: string) => key.split(",").filter(Boolean);
    const seats = external ? runtimeSeats(all, split(supportedKey), split(gatewayKey), split(loginKey)) : all;
    return providerChoices(seats, JSON.parse(accessKey) as Record<string, string[]>, external, JSON.parse(blockedKey) as AccessBlocked);
  }, [menu.options, menu.providers, menu.live, defaultModelLabel, external, supportedKey, gatewayKey, loginKey, accessKey, blockedKey]);

  // The person's picks stay while they are offered; a pick that disappears (a
  // key removed, another runtime chosen) falls back to the first one left.
  const chosen = choices.find((choice) => choice.id === providerId)
    ?? choices.find((choice) => pickableOption(choice)) ?? choices[0] ?? null;
  // A refused access is listed with its reason but never picked.
  const option: AccessOption | null = pickableOption(chosen, kind);
  const models = accessModels(option);
  const accounts = accountChoice(option?.seat ?? null);
  const modelValue = models.some((entry) => entry.id === model) ? model : defaultModel(option);
  const accountValue = accounts.some((entry) => entry.id === account) ? account : "";
  const seatKey = `${chosen?.id ?? ""}|${option?.kind ?? ""}`;
  useEffect(() => {
    // Another provider or access: its own default model, not the last one's id.
    setModel("");
  }, [seatKey]);

  // A runtime still being set up does not hold creation back: the agent's
  // first turn waits for the setup (agent_runtimes.manager). A Jarvis agent
  // with nothing connected yet still starts: it takes the last chat seat.
  const needsProvider = external && !option;
  const canCreate = !saving && !needsProvider;

  async function submit() {
    if (!canCreate) return;
    setSaving(true);
    setError(null);
    try {
      const agent = await createAgent({
        name,
        runtime,
        executionEnvironment: external ? "local" : executionEnvironment,
        provider: option?.seat.provider.id,
        model: option ? modelValue : undefined,
        accountId: option ? accountValue || option.accountId : undefined,
        companion,
      });
      finish(agent);
    } catch (exc) {
      setError(
        exc instanceof AgentNameTaken
          ? t("society.create_agent.name_taken")
          : exc instanceof RuntimeProviderUnsupported
            ? t("society.runtime.provider_unsupported")
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

              <div className="flex flex-col gap-1.5 text-sm">
                <span className="font-medium">{t("society.create_agent.provider_label")}</span>
                {chosen ? (
                  <BrandedSelect
                    value={chosen.id}
                    onValueChange={(next) => { setProviderId(next); setKind(""); setAccount(""); }}
                    ariaLabel={t("society.create_agent.provider_label")}
                    disabled={saving}
                    testId="create-agent-provider"
                    options={choices.map((choice) => ({
                      value: choice.id,
                      label: choice.label,
                      // A coding CLI wears its own mark: a letter in a box is not a logo.
                      icon: choice.mark
                        ? <AgentMark agent={choice.mark} label={choice.label} logoUrl={choice.logoUrl} variant="plain" size="sm" />
                        : <ProviderLogo providerId={choice.logo} label={choice.label} size="sm" />,
                    }))}
                  />
                ) : (
                  <p className="text-xs text-muted-foreground">
                    {menu.loading
                      ? t("society.create.catalog_loading")
                      : t(external ? "society.create_agent.no_provider" : "society.create_agent.no_provider_jarvis")}
                  </p>
                )}
              </div>

              {chosen ? (
                <AccessChoice
                  options={chosen.options}
                  value={option?.kind ?? ""}
                  hint={option ? t(option.extraUsage ? "society.create_agent.access_extra_usage" : `society.create_agent.access_hint_${option.kind}`) : ""}
                  disabled={saving}
                  onChange={(next) => { setKind(next); setAccount(""); }}
                />
              ) : null}

              {!external ? <SandboxChoice value={executionEnvironment} onChange={setExecutionEnvironment} disabled={saving} /> : null}
              {chosen && option && accounts.length ? (
                <div className="flex flex-col gap-1.5 text-sm">
                  <span className="font-medium">{t("society.create_agent.account_label")}</span>
                  <BrandedSelect
                    value={accountValue}
                    onValueChange={setAccount}
                    ariaLabel={t("society.create_agent.account_label")}
                    disabled={saving}
                    testId="create-agent-account"
                    options={[
                      { value: "", label: t("society.create_agent.active_account") },
                      ...accounts.map((entry) => ({ value: entry.id, label: entry.label, hint: accountHint(entry) })),
                    ]}
                  />
                </div>
              ) : null}

              {chosen && option ? (
                <div className="flex flex-col gap-1.5 text-sm">
                  <span className="font-medium">{t("society.create_agent.model_label")}</span>
                  <BrandedSelect
                    value={modelValue}
                    onValueChange={setModel}
                    ariaLabel={t("society.create_agent.model_label")}
                    disabled={saving}
                    testId="create-agent-model"
                    searchPlaceholder={models.length >= SEARCH_FROM ? t("society.create_agent.model_search") : undefined}
                    options={(models.length ? models : [{ id: "", label: defaultModelLabel }]).map((entry) => ({
                      value: entry.id,
                      label: entry.label || entry.id || defaultModelLabel,
                    }))}
                  />
                  <p className="text-xs text-muted-foreground">{t("society.create_agent.model_hint")}</p>
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
