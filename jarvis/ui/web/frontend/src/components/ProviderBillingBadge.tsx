import { CreditCard, Laptop, Sparkles } from "lucide-react";
import type { Billing } from "@/hooks/useProviders";
import { cn } from "@/lib/utils";

/**
 * A small badge that tells the user HOW a provider is billed — the
 * API-key-vs-subscription distinction they asked for. Driven by the backend's
 * `billing` field (derived from auth_mode), never by a provider name.
 */
const META: Record<Billing, { label: string; icon: typeof CreditCard; className: string }> = {
  api: {
    label: "API · billed per token",
    icon: CreditCard,
    className: "border-sky-500/30 bg-sky-500/10 text-sky-600 dark:text-sky-400",
  },
  subscription: {
    label: "Subscription login",
    icon: Sparkles,
    className: "border-violet-500/30 bg-violet-500/10 text-violet-600 dark:text-violet-400",
  },
  subscription_or_api: {
    label: "Subscription or API key",
    icon: Sparkles,
    className: "border-violet-500/30 bg-violet-500/10 text-violet-600 dark:text-violet-400",
  },
  local: {
    label: "Local · no key needed",
    icon: Laptop,
    className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  },
};

export function ProviderBillingBadge({ billing, className }: { billing: Billing; className?: string }) {
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
      <Icon className="h-3 w-3" />
      {meta.label}
    </span>
  );
}
