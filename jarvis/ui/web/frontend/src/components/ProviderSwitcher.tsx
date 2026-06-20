import { useState } from "react";
import { Button } from "@/components/ui/button";
import { switchBrainProvider, useProviders } from "@/hooks/useProviders";
import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";

/**
 * Kompakter Brain-Provider-Switcher für die Debug-View. Zieht die Liste
 * dynamisch aus /api/providers — keine hartcodierten Namen mehr.
 */
export function ProviderSwitcher() {
  const t = useT();
  const { providers, refetch } = useProviders();
  const brainProviders = providers.filter((p) => p.tier === "brain");
  const active = brainProviders.find((p) => p.active) ?? brainProviders[0];
  const [target, setTarget] = useState(active?.id ?? "");
  const [pending, setPending] = useState(false);
  const pushToast = useEventStore((s) => s.pushToast);

  if (!brainProviders.length) {
    return (
      <p className="text-xs text-muted-foreground">
        {t("provider_switcher.loading_hint")}
      </p>
    );
  }

  const choice = target || active?.id || brainProviders[0].id;

  async function handleApply() {
    if (!choice) return;
    setPending(true);
    try {
      await switchBrainProvider(choice);
      pushToast("success", `Brain → ${choice}`);
      window.dispatchEvent(new CustomEvent("jarvis:brain-switched"));
      refetch();
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="space-y-3">
      <label className="text-xs uppercase tracking-wide text-muted-foreground">
        Active Brain
      </label>
      <select
        value={choice}
        onChange={(e) => setTarget(e.target.value)}
        className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
      >
        {brainProviders.map((p) => (
          <option key={p.id} value={p.id} disabled={!p.configured}>
            {p.label}
            {!p.configured && ` — ${t("provider_switcher.no_credential")}`}
          </option>
        ))}
      </select>
      <Button onClick={handleApply} disabled={pending} className="w-full">
        {pending ? "Switching…" : "Apply"}
      </Button>
      {active && (
        <p className="text-xs text-muted-foreground">
          {t("provider_switcher.active")}: <code className="font-mono">{active.id}</code>
        </p>
      )}
    </div>
  );
}
