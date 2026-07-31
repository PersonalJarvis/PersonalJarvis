import { CreditCard, Laptop, Sparkles } from "lucide-react";
import type { Billing } from "@/hooks/useProviders";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

/**
 * A small badge that tells the user HOW a provider is billed — the
 * API-key-vs-subscription distinction they asked for. Driven by the backend's
 * `billing` field (derived from auth_mode), never by a provider name.
 */
const META: Record<Billing, { labelKey: string; icon: typeof CreditCard; className: string }> = {
  api: {
    labelKey: "provider_billing.api",
    icon: CreditCard,
    className: "border-sky-500/30 bg-sky-500/10 text-sky-600 dark:text-sky-400",
  },
  subscription: {
    labelKey: "provider_billing.subscription",
    icon: Sparkles,
    className: "border-violet-500/30 bg-violet-500/10 text-violet-600 dark:text-violet-400",
  },
  subscription_or_api: {
    labelKey: "provider_billing.subscription_or_api",
    icon: Sparkles,
    className: "border-violet-500/30 bg-violet-500/10 text-violet-600 dark:text-violet-400",
  },
  local: {
    labelKey: "provider_billing.local",
    icon: Laptop,
    className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  },
};

export function ProviderBillingBadge({ billing, className }: { billing: Billing; className?: string }) {
  const t = useT();
  const meta = META[billing];
  if (!meta) return null;
  const Icon = meta.icon;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium",
        meta.className,
        className,
      )}
    >
      <Icon aria-hidden="true" className="h-3 w-3" />
      {t(meta.labelKey)}
    </span>
  );
}
