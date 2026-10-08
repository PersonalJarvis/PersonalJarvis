import { useState } from "react";
import { Lock, Shuffle, Sparkles, X } from "lucide-react";
import { useT } from "@/i18n";
import { AgentSymbol } from "../AgentSymbol";
import { ACCESSORY_IDS_BY_SLOT, OFFERED_SLOTS, randomAccessories, type AccessoryChoice, type AccessorySlot } from "./accessories";
import { lookOpen, nextLook, useAgentLooks } from "./agentLooks";
import type { CompanionAppearance } from "./appearance";

/** Picker order: what people change most comes first; empty slots stay hidden. */
const PICKER_SLOTS = (["outfit", "head", "face", "mouth", "neck", "back", "held"] as AccessorySlot[])
  .filter(slot => OFFERED_SLOTS.includes(slot));

/**
 * One tile per item, each showing this agent already wearing it. Looks open
 * with the agent's level; a locked tile says when it opens, and a look the
 * agent already wears stays selectable whatever its level.
 */
export function AccessoryPicker({ value, onChange, agentId }: { value: CompanionAppearance; onChange: (next: AccessoryChoice) => void; agentId?: string }) {
  const t = useT();
  const [slot, setSlot] = useState<AccessorySlot>(PICKER_SLOTS[0] ?? "head");
  const looks = useAgentLooks(agentId);
  const worn = value.accessories;
  const wearable = (id: string) => lookOpen(looks, id) || Object.values(worn).includes(id);
  const upcoming = nextLook(looks);
  const choose = (id: string | undefined) => {
    const next = { ...worn };
    if (id) next[slot] = id; else delete next[slot];
    onChange(next);
  };
  const surprise = () => {
    const rolled = randomAccessories();
    onChange(Object.fromEntries(Object.entries(rolled).filter(([, id]) => id && wearable(id))) as AccessoryChoice);
  };
  const tile = "relative grid h-[76px] w-[76px] place-items-center rounded-lg border focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
  return <div data-testid="accessory-picker">
    <div className="mb-2 flex items-center justify-between gap-2">
      <span className="text-sm font-medium">{t("society.companion.accessories")}</span>
      <div className="flex gap-1">
        <button type="button" onClick={surprise} className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-secondary hover:text-foreground">
          <Shuffle size={14} />{t("society.companion.randomize")}
        </button>
        <button type="button" disabled={!Object.keys(worn).length} onClick={() => onChange({})} className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-secondary hover:text-foreground disabled:opacity-40">
          <X size={14} />{t("society.companion.remove_all")}
        </button>
      </div>
    </div>
    {looks.unlocks && (
      <p className="mb-3 flex items-center gap-1.5 rounded-lg bg-secondary/60 px-3 py-2 text-xs text-muted-foreground" data-testid="look-progress">
        <Sparkles size={14} className="shrink-0 text-primary" aria-hidden />
        {upcoming
          ? t("society.companion.next_look").replace("{0}", String(looks.level)).replace("{1}", t(`society.companion.items.${upcoming.id}`)).replace("{2}", String(upcoming.level))
          : t("society.companion.all_looks_open")}
      </p>
    )}
    <div className="mb-3 flex flex-wrap gap-1.5" role="tablist" aria-label={t("society.companion.accessories")}>
      {PICKER_SLOTS.map(s => <button key={s} type="button" role="tab" aria-selected={slot === s} onClick={() => setSlot(s)}
        className={`relative rounded-full border px-2.5 py-1 text-xs ${slot === s ? "border-transparent bg-primary text-primary-foreground" : "border-border bg-background text-foreground hover:bg-secondary"}`}>
        {t(`society.companion.slots.${s}`)}
        {worn[s] && <span aria-hidden className="ml-1.5 inline-block h-1.5 w-1.5 rounded-full bg-current align-middle" />}
      </button>)}
    </div>
    <div className="flex flex-wrap gap-2" role="group" aria-label={t(`society.companion.slots.${slot}`)}>
      <button type="button" aria-pressed={!worn[slot]} aria-label={t("society.companion.none")} title={t("society.companion.none")} onClick={() => choose(undefined)}
        className={`${tile} text-xs text-muted-foreground ${!worn[slot] ? "border-foreground bg-secondary" : "border-border hover:bg-secondary"}`}>
        {t("society.companion.none")}
      </button>
      {/* Open looks first, then the rest in the order the agent will earn them. */}
      {[...ACCESSORY_IDS_BY_SLOT[slot]].sort((a, b) => (looks.unlocks?.[a] ?? 0) - (looks.unlocks?.[b] ?? 0)).map(id => {
        const name = t(`society.companion.items.${id}`);
        const active = worn[slot] === id;
        const open = wearable(id);
        const at = looks.unlocks?.[id];
        const label = open ? name : `${name} · ${t("society.companion.unlocks_at").replace("{0}", String(at))}`;
        return <button key={id} type="button" aria-pressed={active} aria-label={label} title={label} disabled={!open} onClick={() => choose(id)}
          data-locked={open ? undefined : "true"}
          className={`${tile} ${active ? "border-foreground bg-secondary" : open ? "border-border hover:bg-secondary" : "cursor-not-allowed border-dashed border-border"}`}>
          <span className={open ? undefined : "opacity-35 grayscale"}>
            <AgentSymbol shape={value.shape} color={value.color} eyes={value.eyes} skin={value.skin} accessories={{ ...worn, [slot]: id }} size={60} />
          </span>
          {!open && <span className="absolute bottom-1 left-1/2 inline-flex -translate-x-1/2 items-center gap-0.5 rounded-full bg-background/90 px-1.5 py-0.5 text-[10px] font-semibold text-foreground shadow-sm">
            <Lock size={9} aria-hidden />{t("society.level.lv").replace("{0}", String(at))}
          </span>}
        </button>;
      })}
    </div>
  </div>;
}
