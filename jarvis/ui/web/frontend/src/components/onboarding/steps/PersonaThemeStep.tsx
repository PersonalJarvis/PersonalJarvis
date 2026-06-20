import { useState } from "react";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import type { StepProps } from "../OnboardingFlow";

export function PersonaThemeStep({ goNext, skip }: StepProps) {
  const t = useT();
  const [name, setName] = useState("");

  function saveName() {
    if (!name.trim()) return;
    // Omit `persist` — it defaults to true server-side. NEVER send persist:false here.
    void fetch("/api/settings/assistant-name", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.trim() }),
    }).catch(() => undefined);
  }

  return (
    <div className="flex flex-col gap-4">
      <h2 className="font-display text-lg font-semibold">{t("onboarding.persona.title")}</h2>
      <label className="text-xs font-medium text-muted-foreground">
        {t("onboarding.persona.name_label")}
        <input
          aria-label={t("onboarding.persona.name_label")}
          type="text"
          value={name}
          maxLength={40}
          onChange={(e) => setName(e.target.value)}
          onBlur={saveName}
          placeholder={t("onboarding.persona.name_placeholder")}
          className="mt-1 w-full rounded-md border border-muted-foreground/25 bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/60 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/40"
        />
      </label>
      <Button className="w-full" onClick={goNext}>{t("onboarding.nav.next")}</Button>
      <button className="text-xs text-muted-foreground underline" onClick={skip}>
        {t("onboarding.persona.skip")}
      </button>
    </div>
  );
}
