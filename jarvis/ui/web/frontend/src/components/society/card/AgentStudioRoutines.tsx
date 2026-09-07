import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useT } from "@/i18n";
import { useAgentRoutines } from "../cardData";

export function AgentStudioRoutines({ agentId, sample, onGuardChange }: {
  agentId: string;
  sample: boolean;
  onGuardChange: (dirty: boolean, busy: boolean) => void;
}) {
  const t = useT();
  const client = useQueryClient();
  const routines = useAgentRoutines(sample ? null : agentId);
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [prompt, setPrompt] = useState("");
  const [hours, setHours] = useState("24");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const dirty = Boolean(title || prompt || hours !== "24");
  useEffect(() => { onGuardChange(dirty, busy); }, [dirty, busy, onGuardChange]);
  const reset = () => { setTitle(""); setPrompt(""); setHours("24"); setError(""); setOpen(false); };
  const valid = title.trim() && prompt.trim() && Number.isFinite(Number(hours)) && Number(hours) >= 1;
  const create = async () => {
    if (!valid || sample || busy) return;
    setBusy(true); setError("");
    try {
      const response = await fetch(`/api/society/agents/${encodeURIComponent(agentId)}/routines`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: title.trim(), prompt: prompt.trim(), schedule: { kind: "every", interval_seconds: Number(hours) * 3600 } }),
      });
      if (!response.ok) throw new Error(`${t("society.studio.routine_error")} (HTTP ${response.status})`);
      reset();
      void client.invalidateQueries({ queryKey: ["society", "agent-routines", agentId] });
    } catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setBusy(false); }
  };
  return <section className="as-stack">
    <div className="as-section-title"><h3>{t("society.card.routines")}</h3>
      <button type="button" className="as-text-button" disabled={sample || open} onClick={() => setOpen(true)}><Plus size={15} aria-hidden />{t("society.studio.add_routine")}</button>
    </div>
    {open ? <fieldset disabled={busy} className="as-stack">
      <label className="as-field"><span>{t("society.studio.routine_title")}</span><input autoFocus value={title} maxLength={200} onChange={(event) => setTitle(event.target.value)} /></label>
      <label className="as-field"><span>{t("society.studio.routine_task")}</span><textarea rows={3} maxLength={16000} value={prompt} onChange={(event) => setPrompt(event.target.value)} /></label>
      <label className="as-field"><span>{t("society.studio.routine_hours")}</span><input type="number" min="1" step="1" value={hours} onChange={(event) => setHours(event.target.value)} /></label>
      <div className="as-actions"><button type="button" className="as-primary" disabled={!valid || busy} onClick={() => void create()}>{t(busy ? "society.card.saving" : "society.studio.add_routine")}</button>
        <button type="button" className="as-secondary" onClick={reset}>{t("society.studio.discard")}</button></div>
      {error ? <p role="alert" className="as-error">{error}</p> : null}
    </fieldset> : null}
    {routines.data?.length ? <ul className="as-activity">{routines.data.map((routine) => <li key={routine.id}><strong>{routine.title.replace(/^\[agent:[^\]]+\]\s*/i, "")}</strong><p>{routine.schedule}</p></li>)}</ul> : <p className="as-note">{t("society.card.no_routines")}</p>}
  </section>;
}
