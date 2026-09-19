import { useRef, useState, type FormEvent } from "react";
import { BrandedSelect } from "@/components/ui/select";
import { createPreparationTeam } from "./preparationApi";
import { useSwarmText } from "./strings";
import type { Capabilities, TeamCreate, TeamRecord } from "./types";

const TOOLS = ["fetch_url", "run_javascript", "write_artifact", "read_artifact", "search_team", "send_message", "read_messages", "ack_message", "register_team_tool", "install_dependency"];

export function usdToMicro(value: string): string | null {
  if (!value.trim()) return null;
  if (!/^\d+(\.\d{1,6})?$/.test(value)) throw new Error("Invalid USD amount");
  const [whole, fraction = ""] = value.split(".");
  const amount = BigInt(whole) * 1000000n + BigInt(fraction.padEnd(6, "0"));
  if (amount.toString().length > 31) throw new Error("USD limit is too large");
  return amount.toString();
}
export function CreateTeamForm({ capability, onCreated, onClose, initial, onSubmitTeam = createPreparationTeam, title, submitLabel }: {
  capability: Capabilities | null; onCreated: (team: TeamRecord) => void; onClose?: () => void;
  initial?: Pick<TeamCreate, "name" | "goal" | "acceptance">;
  onSubmitTeam?: (spec: TeamCreate) => Promise<TeamRecord>; title?: string; submitLabel?: string;
}) {
  const t = useSwarmText();
  const requestKey = useRef(crypto.randomUUID());
  const lastPayload = useRef("");
  const lastSpec = useRef<TeamCreate | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [internet, setInternet] = useState(true);
  const [goal, setGoal] = useState(initial?.goal ?? "");
  const [promptFile, setPromptFile] = useState("");
  const [money, setMoney] = useState("");
  const [mode, setMode] = useState("auto");
  const distributed = capability?.distributed as { available?: boolean } | undefined;
  async function importPrompt(file?: File) {
    if (!file) return;
    setError(""); setBusy(true);
    try {
      if (!/\.(txt|md|markdown)$/i.test(file.name) || file.size > 80000) throw new Error(t("promptFileInvalid"));
      const text = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result ?? ""));
        reader.onerror = () => reject(new Error(t("promptFileInvalid")));
        reader.readAsText(file, "UTF-8");
      });
      if (!text.trim() || text.length > 20000 || /\u0000|\uFFFD/.test(text)) throw new Error(t("promptFileInvalid"));
      setGoal(text); setPromptFile(file.name);
    } catch (failure) { setError(failure instanceof Error ? failure.message : String(failure)); }
    finally { setBusy(false); }
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const data = new FormData(event.currentTarget);
    const field = (name: string) => String(data.get(name) ?? "").trim();
    try {
      if (!field("goal")) throw new Error(t("goalRequired"));
      if (!["budget", "workers", "networkLimit", "artifactLimit"].every(key => /^[1-9]\d{0,30}$/.test(field(key)))) throw new Error(t("invalidBudget"));
      if (!["runtime", "attempts", "outputTokens", "toolCalls"].every(key => Number.isSafeInteger(Number(field(key))) && Number(field(key)) > 0)) throw new Error(t("invalidBudget"));
      if (field("concurrency") && (!Number.isSafeInteger(Number(field("concurrency"))) || Number(field("concurrency")) < 1)) throw new Error(t("invalidBudget"));
      // Freeze automatic resolution for a retry. Provider/capacity discovery
      // changing in the background is not a new instruction from the owner.
      const serialized = JSON.stringify({ fields: Object.fromEntries(data), tools: data.getAll("tool"), internet });
      const repeat = serialized === lastPayload.current ? lastSpec.current : null;
      const mode = field("mode") === "distributed" || (field("mode") === "auto" && distributed?.available) ? "distributed" : "local";
      const input: TeamCreate = repeat ?? {
        name: field("name") || field("goal").replace(/\s+/g, " ").slice(0, 80), goal: field("goal"), acceptance: field("acceptance"), request_key: requestKey.current,
        mode,
        limits: { token_budget: field("budget"), monetary_limit_microusd: usdToMicro(field("money")),
          concurrency: field("concurrency") ? Number(field("concurrency")) : mode === "distributed" ? 1000 : 32, worker_limit: field("workers"),
          runtime_seconds: Number(field("runtime")) * 60, max_attempts: Number(field("attempts")), max_output_tokens: Number(field("outputTokens")), max_tool_calls: Number(field("toolCalls")) },
        policy: { internet, allowed_domains: internet ? field("domains").split(",").map(s => s.trim()).filter(Boolean) : [],
          tools: data.getAll("tool").map(String),
          dependency_registries: field("registries").split(",").map(s => s.trim()).filter(Boolean), max_network_bytes: field("networkLimit"), max_artifact_bytes: field("artifactLimit"), allow_dependencies: data.get("dependencies") === "on" },
        tasks: [], preparation_required: true,
      };
      // A failed request retries with its original key; edited input starts a new operation.
      if (lastPayload.current && serialized !== lastPayload.current) requestKey.current = crypto.randomUUID();
      input.request_key = requestKey.current; lastPayload.current = serialized; lastSpec.current = input;
      setBusy(true); setError("");
      onCreated(await onSubmitTeam(input));
    } catch (failure) { setError(failure instanceof Error ? failure.message : String(failure)); }
    finally { setBusy(false); }
  }
  return <form className="swarm-create" onSubmit={submit} aria-label={title ?? t("newTeam")}
    onDragOver={event => { if (!busy && event.dataTransfer.types.includes("Files")) event.preventDefault(); }}
    onDrop={event => { if (event.dataTransfer.files.length) { event.preventDefault(); if (!busy) void importPrompt(event.dataTransfer.files[0]); } }}>
    <div className="swarm-row"><h2>{title ?? t("newTeam")}</h2>{onClose && <button type="button" onClick={onClose} disabled={busy}>{t("dismiss")}</button>}</div>
    <label>{t("goal")}<textarea name="goal" rows={6} maxLength={20000} value={goal} onChange={event => { setGoal(event.target.value); setPromptFile(""); }} placeholder={t("goalExample")} required autoFocus /></label>
    <label className="swarm-prompt-file">{t("promptFile")}<input type="file" accept=".txt,.md,.markdown,text/plain,text/markdown" disabled={busy} onChange={event => { void importPrompt(event.target.files?.[0]); event.target.value = ""; }} /></label>
    {promptFile && <p className="swarm-muted" role="status">{t("promptLoaded")}: {promptFile}</p>}
    <p className="swarm-muted">{t("automaticHint")}</p>
    <details className="swarm-advanced"><summary>{t("advancedOptions")}</summary>
    <label>{t("name")}<input name="name" maxLength={120} defaultValue={initial?.name} /></label>
    <label>{t("acceptance")}<textarea name="acceptance" rows={3} maxLength={10000} defaultValue={initial?.acceptance} /></label>
    <p className="swarm-muted">{t("planHint")}</p>
    <div className="swarm-form-grid">
      <label>{t("budget")}<input name="budget" inputMode="numeric" defaultValue="250000" required /></label>
      <label>{t("money")}<input name="money" inputMode="decimal" value={money} onChange={event => setMoney(event.target.value)} /></label>
      <label>{t("concurrency")}<input name="concurrency" type="number" min={1} max={10000} placeholder={t("automatic")} /></label>
      <label>{t("workerLimit")}<input name="workers" inputMode="numeric" defaultValue="1000" required /></label>
      <label>{t("runtime")}<input name="runtime" type="number" min={1} max={525600} defaultValue={30} required /></label>
      <label>{t("mode")}<input type="hidden" name="mode" value={mode} /><BrandedSelect ariaLabel={t("mode")} value={mode} onValueChange={setMode} options={[
        { value: "auto", label: t("automatic") },
        { value: "local", label: t("local") },
        { value: "distributed", label: t("distributed"), disabled: !distributed?.available },
      ]} /></label>
    </div>
    <label className="swarm-check"><input type="checkbox" checked={internet} onChange={e => setInternet(e.target.checked)} />{t("internet")}</label>
    {internet && <label>{t("domains")}<input name="domains" /></label>}
    <label className="swarm-check"><input name="dependencies" type="checkbox" defaultChecked />{t("dependencies")}</label>
    <details><summary>{t("executionLimits")}</summary><div className="swarm-form-grid">
      <label>{t("attempts")}<input name="attempts" type="number" min={1} max={20} defaultValue={3} required /></label>
      <label>{t("outputTokens")}<input name="outputTokens" type="number" min={128} max={1048576} defaultValue={8192} required /></label>
      <label>{t("toolCalls")}<input name="toolCalls" type="number" min={1} max={1024} defaultValue={32} required /></label>
      <label>{t("networkLimit")}<input name="networkLimit" inputMode="numeric" defaultValue="20000000" required /></label>
      <label>{t("artifactLimit")}<input name="artifactLimit" inputMode="numeric" defaultValue="50000000" required /></label>
      <label>{t("registries")}<input name="registries" defaultValue="registry.npmjs.org" /></label>
    </div><fieldset><legend>{t("tools")}</legend><div className="swarm-form-grid">{TOOLS.map(tool => <label className="swarm-check" key={tool}><input type="checkbox" name="tool" value={tool} defaultChecked />{t(`tool_${tool}`)}</label>)}</div></fieldset></details>
    </details>
    <p className="swarm-muted">{money.trim() && <strong>{t("costCeiling")}: ${money} · </strong>}{t("defaultLimitHint")}</p>
    {error && <p role="alert" className="swarm-error">{error}</p>}
    <button className="swarm-primary" disabled={busy} type="submit">{busy ? t("loading") : submitLabel ?? t("clarifyGoal")}</button>
  </form>;
}
