import { useState } from "react";
import { Shuffle, X } from "lucide-react";
import { useT } from "@/i18n";
import { AgentSymbol } from "../AgentSymbol";
import { ACCESSORY_IDS_BY_SLOT, randomAccessories, type AccessoryChoice, type AccessorySlot } from "./accessories";
import type { CompanionAppearance } from "./appearance";

/** Picker order: what people change most comes first. */
const PICKER_SLOTS: AccessorySlot[] = ["head", "face", "mouth", "neck", "outfit", "back", "held"];

/** One tile per item, each showing this agent already wearing it. */
export function AccessoryPicker({ value, onChange }: { value: CompanionAppearance; onChange: (next: AccessoryChoice) => void }) {
  const t = useT();
  const [slot, setSlot] = useState<AccessorySlot>("head");
  const worn = value.accessories;
  const choose = (id: string | undefined) => {
    const next = { ...worn };
    if (id) next[slot] = id; else delete next[slot];
    onChange(next);
  };
  const tile = "grid h-14 w-14 place-items-center rounded-lg border focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
  return <div data-testid="accessory-picker">
    <div className="mb-2 flex items-center justify-between gap-2">
      <span className="text-sm font-medium">{t("society.companion.accessories")}</span>
      <div className="flex gap-1">
        <button type="button" onClick={() => onChange(randomAccessories())} className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-secondary hover:text-foreground">
          <Shuffle size={14} />{t("society.companion.randomize")}
        </button>
        <button type="button" disabled={!Object.keys(worn).length} onClick={() => onChange({})} className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-secondary hover:text-foreground disabled:opacity-40">
          <X size={14} />{t("society.companion.remove_all")}
        </button>
      </div>
    </div>
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
      {ACCESSORY_IDS_BY_SLOT[slot].map(id => {
        const label = t(`society.companion.items.${id}`);
        const active = worn[slot] === id;
        return <button key={id} type="button" aria-pressed={active} aria-label={label} title={label} onClick={() => choose(id)}
          className={`${tile} ${active ? "border-foreground bg-secondary" : "border-border hover:bg-secondary"}`}>
          <AgentSymbol shape={value.shape} color={value.color} eyes={value.eyes} accessories={{ ...worn, [slot]: id }} size={46} />
        </button>;
      })}
    </div>
  </div>;
}
