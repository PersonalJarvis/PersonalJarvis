/**
 * Retire this agent — the one control on the card that cannot be taken back.
 *
 * It lives in two places and must behave identically in both: the profile
 * face's action bar (beside Pause, where someone managing an agent looks for
 * it) and the chat face's options rail. Hence one component rather than two
 * copies of the confirmation logic.
 *
 * Three things it does deliberately:
 *
 *  - it ARMS rather than asking. The first press turns the button into a red
 *    "confirm", the second does it. A modal for this would be one more dialog
 *    on top of a card that is already a dialog;
 *  - it DISARMS itself after a few seconds. A button left armed on a card
 *    someone walks away from is a delete waiting to happen;
 *  - the lead is not retirable and the button says so instead of failing at
 *    the API: `roster.archive` refuses it (the society has exactly one lead,
 *    MASTERPLAN §2.5), so there is nothing to try.
 *
 * Pressing it archives the row and then hands the island the execution
 * (`world/retirement.ts`); the card closes so the ceremony is visible.
 */
import { useEffect, useRef, useState } from "react";
import { Skull } from "lucide-react";

import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

import { findLead, useRetireAgent, useSocietyRoster, type SocietyAgent } from "../data";

/** An armed button falls back to safe after this long, in ms. */
const DISARM_MS = 6_000;

export interface RetireButtonProps {
  agent: SocietyAgent;
  /** Called once the row is archived — the card closes so the island is visible. */
  onRetired?: () => void;
  /**
   * `bar` is the profile face's action row: one button, everything else in its
   * tooltip. `rail` is the chat face's options column, which has room for the
   * sentence explaining what the button does.
   */
  variant?: "bar" | "rail";
}

export function RetireButton({ agent, onRetired, variant = "bar" }: RetireButtonProps) {
  const t = useT();
  const roster = useSocietyRoster();
  const retire = useRetireAgent();
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const disarm = useRef<ReturnType<typeof setTimeout> | null>(null);

  const isLead = agent.tier === "lead";

  // Never stay armed: not across a switch to another agent in the rail, not
  // after the card is closed, and not while someone is reading the rest of it.
  useEffect(() => {
    setArmed(false);
    setError(null);
  }, [agent.agentId]);

  useEffect(() => {
    if (!armed) return;
    disarm.current = setTimeout(() => setArmed(false), DISARM_MS);
    return () => {
      if (disarm.current) clearTimeout(disarm.current);
      disarm.current = null;
    };
  }, [armed]);

  if (isLead) {
    if (variant === "rail") {
      return <p className="text-xs text-muted-foreground">{t("society.card.retire_lead_blocked")}</p>;
    }
    return (
      <button
        type="button"
        className="ac-btn"
        disabled
        title={t("society.card.retire_lead_blocked")}
        data-testid="agent-card-retire"
      >
        <Skull className="h-3.5 w-3.5" aria-hidden />
        {t("society.card.retire")}
      </button>
    );
  }

  const run = async () => {
    if (!armed) {
      setArmed(true);
      setError(null);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await retire(agent, findLead(roster.data?.agents ?? []));
      onRetired?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setArmed(false);
    } finally {
      setBusy(false);
    }
  };

  const label = armed ? t("society.card.retire_confirm") : t("society.card.retire");
  const hint = armed ? t("society.card.retire_armed_hint") : t("society.card.retire_hint");

  const button = (
    <button
      type="button"
      className={cn("ac-btn", variant === "rail" && "w-full justify-center")}
      data-armed={armed || undefined}
      disabled={busy}
      title={error ?? hint}
      onClick={() => void run()}
      data-testid="agent-card-retire"
    >
      <Skull className="h-3.5 w-3.5" aria-hidden />
      {label}
    </button>
  );

  if (variant === "bar") {
    return (
      <>
        {error && <span className="ac-retire-error">{error}</span>}
        {button}
      </>
    );
  }

  return (
    <div className="space-y-2">
      {button}
      <p className="text-xs text-muted-foreground">{hint}</p>
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}

export default RetireButton;
