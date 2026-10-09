/**
 * The runtime an agent runs on (Jarvis, Hermes or OpenClaw), as a mark: inline
 * beside a word (`RuntimeMark`) or as a small round badge pinned to the corner
 * of an agent's face (`RuntimeBadge`), so a roster row says which loop runs it.
 */
import { ProviderLogo } from "@/components/providers/ProviderLogo";
import { useT } from "@/i18n";
import type { AgentRuntime } from "@/lib/societyApi";
import { cn } from "@/lib/utils";

export function RuntimeMark({ runtime, label }: { runtime: AgentRuntime; label: string }) {
  // The built-in runtime carries the Jarvis app logo, not the user's pet:
  // here it names a product beside Hermes and OpenClaw, not the companion.
  return runtime === "jarvis"
    ? <img src="/jarvis-gigi-256.png?v=hood" alt="" aria-hidden="true" width={16} height={16} className="size-4 shrink-0 rounded-[4px]" data-testid="runtime-mark-jarvis" />
    : <ProviderLogo providerId={runtime} label={label} size="sm" />;
}

/** The runtime's mark in a round chip; the parent positions it over the avatar. */
export function RuntimeBadge({ runtime, className }: { runtime: AgentRuntime | undefined; className?: string }) {
  const t = useT();
  const resolved = runtime ?? "jarvis";
  const label = t("society.runtime.runs_on").replace("{0}", t(`society.runtime.${resolved}`));
  return (
    <span
      role="img"
      aria-label={label}
      title={label}
      data-testid={`runtime-badge-${resolved}`}
      className={cn(
        "pointer-events-auto inline-flex h-[18px] w-[18px] items-center justify-center overflow-hidden rounded-full bg-secondary ring-2 ring-sidebar [&_img]:size-3.5",
        className,
      )}
    >
      <RuntimeMark runtime={resolved} label={t(`society.runtime.${resolved}`)} />
    </span>
  );
}
