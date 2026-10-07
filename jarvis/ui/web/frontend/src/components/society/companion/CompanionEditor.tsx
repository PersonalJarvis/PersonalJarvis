import { useT } from "@/i18n";
import { Switch } from "@/components/ui/switch";
import { AgentSymbol } from "../AgentSymbol";
import { AccessoryPicker } from "./AccessoryPicker";
import { COMPANION_COLORS, COMPANION_SHAPES, type CompanionAppearance } from "./appearance";
import gigiMark from "@/assets/gigi-companion-avatar.png";
import { lazy, Suspense } from "react";
const CompanionPreview = lazy(() => import("./CompanionPreview").then(m => ({ default: m.CompanionPreview })));

export function CompanionEditor({ value, onChange, disabled = false, lead = false, preview3d = true, agentId }: {
  value: CompanionAppearance; onChange: (next: CompanionAppearance) => void; disabled?: boolean; lead?: boolean;
  /** The agent whose level opens its looks; without one nothing is locked. */
  agentId?: string;
  /** The 3D turntable needs its own WebGL context; dialogs outside the map skip it. */
  preview3d?: boolean;
}) {
  const t = useT();
  const update = (patch: Partial<CompanionAppearance>) => onChange({ ...value, ...patch });
  return <fieldset disabled={disabled} className="flex min-w-0 flex-col gap-5 p-4" data-testid="companion-editor">
    <div className="flex items-center gap-5 rounded-xl bg-secondary/50 p-5">
      {lead ? <img src={gigiMark} width={88} height={88} alt="" /> : <AgentSymbol {...value} size={88} />}
      <div><h3 className="font-medium">{t("society.companion.title")}</h3><p className="mt-1 text-sm text-muted-foreground">{t("society.companion.hint")}</p></div>
    </div>
    {preview3d && <Suspense fallback={null}><CompanionPreview appearance={value} lead={lead} /></Suspense>}
    {!lead && <><div><span className="mb-2 block text-sm font-medium">{t("society.companion.shape")}</span>
      <div className="flex flex-wrap gap-2">{COMPANION_SHAPES.map(shape => <button key={shape} type="button"
        aria-label={t(`society.companion.shapes.${shape}`)} aria-pressed={value.shape === shape}
        onClick={() => update({ shape })} className={`grid h-12 w-12 place-items-center rounded-lg border focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${value.shape === shape ? "border-foreground bg-secondary" : "border-border hover:bg-secondary"}`}>
        <AgentSymbol shape={shape} color={value.color} eyes={value.eyes} accessories={value.accessories} size={36} />
      </button>)}</div>
    </div>
    <div><span className="mb-2 block text-sm font-medium">{t("society.companion.color")}</span>
      <div className="flex flex-wrap items-center gap-2">{COMPANION_COLORS.map(color => <button key={color} type="button" aria-label={`${t("society.companion.color")} ${color}`} aria-pressed={value.color === color}
        onClick={() => update({ color })} className={`h-8 w-8 rounded-full border-2 ${value.color === color ? "border-foreground ring-2 ring-background" : "border-transparent"}`} style={{ background: color }} />)}
        <input type="color" aria-label={t("society.companion.custom_color")} value={value.color} onChange={e => update({ color: e.target.value })} className="h-8 w-10 rounded border border-border bg-background" />
      </div>
    </div>
    <div className="flex items-center justify-between gap-3 text-sm"><span>{t("society.companion.eyes")}</span>
      {/* Plain toggles: a popup menu inside a modal dialog can strand its pointer lock. */}
      <div className="flex gap-1.5" role="group" aria-label={t("society.companion.eyes")}>
        {(["dots", "lines"] as const).map(eyes => <button key={eyes} type="button" aria-pressed={value.eyes === eyes} onClick={() => update({ eyes })}
          className={`rounded-full border px-2.5 py-1 text-xs ${value.eyes === eyes ? "border-transparent bg-primary text-primary-foreground" : "border-border bg-background text-foreground hover:bg-secondary"}`}>
          {t(`society.companion.${eyes}`)}
        </button>)}
      </div>
    </div>
    <AccessoryPicker value={value} onChange={accessories => update({ accessories })} agentId={agentId} />
    </>}
    <label className="flex items-center justify-between gap-3 text-sm">{t("society.companion.visible")}
      <Switch checked={value.enabled} onCheckedChange={enabled => update({ enabled })} aria-label={t("society.companion.visible")} />
    </label>
  </fieldset>;
}
