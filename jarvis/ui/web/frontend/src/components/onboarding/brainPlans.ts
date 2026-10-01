/**
 * Which provider cards the brain beat shows, and when a choice is complete.
 *
 * Pure functions over the backend catalog (`GET /api/providers`) and the
 * starter plans (`GET /api/setup/starter-plans`), so the beat never decides
 * anything by a provider NAME: a catalog with one provider renders one card,
 * and any single key that can be the brain works (AP-21).
 */
import type { ProviderDescriptor } from "@/hooks/useProviders";
import type { StarterPlan } from "@/hooks/useStarterPlans";

/** The one slot a card asks for; providers with several keys expose the first. */
export function primarySlot(p: ProviderDescriptor): string | null {
  return p.secret_keys[0] ?? null;
}

/** A key is stored for this card's own slot. */
export function slotConfigured(p: ProviderDescriptor): boolean {
  const slot = primarySlot(p);
  if (!slot) return p.configured;
  return Boolean(p.secrets_set[slot]);
}

/** A key reaches this card — its own, or the family key that covers it. */
export function slotEffective(p: ProviderDescriptor): boolean {
  const slot = primarySlot(p);
  if (!slot) return p.configured;
  return Boolean(p.secrets_effective?.[slot] ?? p.secrets_set[slot]);
}

/**
 * Brain providers a first-run user can start with: anything that takes a
 * pasted key and can be the primary brain. Recommended first, then whatever
 * already has a key, then catalog order.
 */
export function startableProviders(providers: ProviderDescriptor[]): ProviderDescriptor[] {
  return providers
    .filter((p) => p.tier === "brain" && p.auth_mode === "api_key" && p.brain_switchable !== false)
    .filter((p) => (p.secret_keys?.length ?? 0) > 0)
    .sort((a, b) => {
      const ra = a.recommended ? 0 : 1;
      const rb = b.recommended ? 0 : 1;
      if (ra !== rb) return ra - rb;
      const ca = slotConfigured(a) ? 0 : 1;
      const cb = slotConfigured(b) ? 0 : 1;
      return ca - cb;
    });
}

/** Every key the plan needs is saved (dedicated or covered by the family). */
export function planKeysComplete(plan: StarterPlan, startable: ProviderDescriptor[]): boolean {
  return (
    plan.key_slots.length > 0 &&
    plan.key_slots.every((s) =>
      startable.some((p) => primarySlot(p) === s.slot && slotEffective(p)),
    )
  );
}
