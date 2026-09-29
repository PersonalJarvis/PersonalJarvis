/**
 * The wardrobe checkpoint: dress your own character or any agent on foot —
 * office outfits in curated colourways, hair style and colour, skin tone and
 * eyewear. Your own look saves in this browser at once; an agent's look is a
 * draft with a live preview until you save it to the roster (the same
 * `avatar` PATCH the appearance editor uses).
 */
import { lazy, Suspense, useId, useMemo, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useT } from "@/i18n";
import type { SocietyAgent } from "../data";
import type { FigureRecipe } from "../figures/figureRecipe";
import { playerLook, playerRecipe, type PlayerProfile } from "./playerProfile";
import { EYEWEAR, HAIR_STYLES, toyLookFor, type OutfitId } from "./toyFigureModel";
import {
  colourwayIndexOf, dressIn, HAIR_COLOURS, OUTFITS, outfitById, randomLook, SKIN_TONES,
  withEyewear, withHair, withHairColour, withSkin,
} from "./wardrobe";
import "./wardrobe.css";

const AgentFigureViewer = lazy(() => import("../figures/AgentFigureViewer").then((m) => ({ default: m.AgentFigureViewer })));

const YOU = "__you__";

function Section({ label, children }: { label: string; children: ReactNode }) {
  const id = useId();
  return (
    <div className="wardrobe-section">
      <p className="office-subhead" id={id}>{label}</p>
      <div className="wardrobe-row" role="group" aria-labelledby={id}>{children}</div>
    </div>
  );
}

function Swatch({ colours, label, pressed, onClick }: { colours: string[]; label: string; pressed: boolean; onClick: () => void }) {
  const background = colours.length > 1
    ? `linear-gradient(135deg, ${colours[0]} 0 50%, ${colours[1]} 50% 100%)`
    : colours[0];
  return <button type="button" className="wardrobe-swatch" style={{ background }} aria-label={label} title={label} aria-pressed={pressed} onClick={onClick} />;
}

/** The editor itself: works on any biped recipe and reports every change. */
function LookEditor({ recipe, onChange }: { recipe: FigureRecipe; onChange: (next: FigureRecipe) => void }) {
  const t = useT();
  const look = toyLookFor(recipe, "");
  const way = colourwayIndexOf(recipe);
  const outfit = outfitById(look.outfit);
  const same = (a: string, b: string) => a.toLowerCase() === b.toLowerCase();
  return (
    <>
      <Section label={t("society.office.wardrobe_outfit")}>
        <div className="wardrobe-outfits">
          {OUTFITS.map((o) => (
            <button key={o.id} type="button" className="wardrobe-outfit" aria-pressed={look.outfit === o.id}
              onClick={() => onChange(dressIn(recipe, o.id as OutfitId, 0))}>
              <i aria-hidden style={{ background: `linear-gradient(135deg, ${o.colourways[0].primary} 0 55%, ${o.colourways[0].inner} 55% 100%)` }} />
              {t(`society.office.outfit_${o.id}`)}
            </button>
          ))}
        </div>
      </Section>
      <Section label={t("society.office.wardrobe_colourway")}>
        {outfit.colourways.map((w, i) => (
          <Swatch key={w.id} colours={[w.primary, w.inner === w.primary ? w.secondary : w.inner]} pressed={way === i}
            label={t(`society.office.colourway_${w.id}`)} onClick={() => onChange(dressIn(recipe, look.outfit, i))} />
        ))}
      </Section>
      <Section label={t("society.office.hair_label")}>
        {HAIR_STYLES.map((style) => (
          <button key={style} type="button" className="office-chip" aria-pressed={look.hairStyle === style} onClick={() => onChange(withHair(recipe, style))}>
            {t(`society.office.hair_${style}`)}
          </button>
        ))}
      </Section>
      <Section label={t("society.office.wardrobe_hair_colour")}>
        {HAIR_COLOURS.map((c, i) => (
          <Swatch key={c} colours={[c]} pressed={same(look.hair, c)} label={`${t("society.office.wardrobe_hair_colour")} ${i + 1}`}
            onClick={() => onChange(withHairColour(recipe, c))} />
        ))}
      </Section>
      <Section label={t("society.office.wardrobe_skin")}>
        {SKIN_TONES.map((c, i) => (
          <Swatch key={c} colours={[c]} pressed={same(look.skin, c)} label={`${t("society.office.wardrobe_skin")} ${i + 1}`}
            onClick={() => onChange(withSkin(recipe, c))} />
        ))}
      </Section>
      <Section label={t("society.office.wardrobe_eyewear")}>
        {EYEWEAR.map((e) => (
          <button key={e} type="button" className="office-chip" aria-pressed={look.eyewear === e} onClick={() => onChange(withEyewear(recipe, e))}>
            {t(`society.office.eyewear_${e}`)}
          </button>
        ))}
      </Section>
    </>
  );
}

/** Agents the wardrobe can dress: two-legged figures (Gigi and other spirits fly in their own shape). */
function dressable(agents: SocietyAgent[]): SocietyAgent[] {
  return agents.filter((a) => a.figure?.archetype === "biped" && !a.figure.model);
}

export function WardrobePanel({ profile, onProfile, agents, sample }: {
  profile: PlayerProfile; onProfile: (next: PlayerProfile) => void; agents: SocietyAgent[]; sample: boolean;
}) {
  const t = useT();
  const client = useQueryClient();
  const [target, setTarget] = useState<string>(YOU);
  const [drafts, setDrafts] = useState<Record<string, FigureRecipe>>({});
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<"idle" | "saved" | "error">("idle");
  const people = useMemo(() => dressable(agents), [agents]);
  const agent = people.find((a) => a.agentId === target) ?? null;

  const yours: FigureRecipe = { ...playerRecipe(profile), hairStyle: playerLook(profile).hairStyle };
  const recipe = agent ? drafts[agent.agentId] ?? agent.figure! : yours;
  const dirty = agent ? Boolean(drafts[agent.agentId]) && JSON.stringify(drafts[agent.agentId]) !== JSON.stringify(agent.figure) : false;

  const change = (next: FigureRecipe) => {
    setStatus("idle");
    // Your own look: saved at once. The legacy profile-level hair moves into the recipe.
    if (!agent) onProfile({ ...profile, recipe: next, hairStyle: undefined });
    else setDrafts((d) => ({ ...d, [agent.agentId]: next }));
  };

  const save = async () => {
    if (!agent) return;
    setSaving(true);
    setStatus("idle");
    try {
      const response = await fetch(`/api/society/agents/${encodeURIComponent(agent.agentId)}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ avatar: recipe }),
      });
      if (!response.ok) throw new Error(`wardrobe save ${response.status}`);
      await Promise.all([client.invalidateQueries({ queryKey: ["society", "roster"] }), client.invalidateQueries({ queryKey: ["mars"] })]);
      setDrafts((d) => { const { [agent.agentId]: _saved, ...rest } = d; return rest; });
      setStatus("saved");
    } catch (error) {
      // Keep the draft so nothing is lost; the message offers another try.
      console.warn("Wardrobe could not save the agent's look", error);
      setStatus("error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <p>{t("society.office.wardrobe_body")}</p>
      {people.length > 0 && (
        <Section label={t("society.office.wardrobe_who")}>
          <button type="button" className="office-chip" aria-pressed={target === YOU} onClick={() => { setTarget(YOU); setStatus("idle"); }}>
            {t("society.office.wardrobe_you")}
          </button>
          {people.map((a) => (
            <button key={a.agentId} type="button" className="office-chip" aria-pressed={target === a.agentId} title={a.name}
              onClick={() => { setTarget(a.agentId); setStatus("idle"); }}>
              <span className="wardrobe-chip-name">{a.name}</span>
            </button>
          ))}
        </Section>
      )}
      {agent ? (
        <div className="wardrobe-preview">
          <Suspense fallback={null}><AgentFigureViewer recipe={recipe} quiet /></Suspense>
        </div>
      ) : (
        <label className="office-field">
          <span>{t("society.office.wardrobe_name")}</span>
          <input value={profile.name} maxLength={40} placeholder={t("society.office.you")} autoComplete="off" spellCheck={false}
            onChange={(e) => onProfile({ ...profile, name: e.target.value })} />
          <span>{t("society.office.wardrobe_saved")}</span>
        </label>
      )}
      <LookEditor recipe={recipe} onChange={change} />
      <button type="button" className="office-action" onClick={() => change(randomLook(recipe, `${target}:${Math.random()}`))}>
        {t("society.office.wardrobe_random")}
      </button>
      {agent && (
        <>
          <button type="button" className="office-action office-action-primary" disabled={!dirty || saving || sample} aria-busy={saving} onClick={() => void save()}>
            {t(saving ? "society.card.saving" : "society.office.wardrobe_save_agent").replace("{0}", agent.name)}
          </button>
          {sample && <p className="office-hint">{t("society.office.wardrobe_sample_hint")}</p>}
          {status === "saved" && <p className="office-success" role="status">{t("society.office.wardrobe_saved_agent").replace("{0}", agent.name)}</p>}
          {status === "error" && <p className="office-error" role="alert">{t("society.office.wardrobe_save_error")}</p>}
        </>
      )}
    </>
  );
}
