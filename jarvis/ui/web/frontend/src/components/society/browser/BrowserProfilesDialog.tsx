import { useEffect, useId, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Plus, RotateCw, X } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { useLocaleChunk, useT } from "@/i18n";
import { BrandedSelect } from "@/components/ui/select";
import {
  BROWSER_PROFILES_QUERY, BROWSER_PROFILE_CHANGED_EVENT, BrowserProfileError, bindAgentBrowserProfile, createBrowserProfile,
  pairBrowserProfile, parseBrowserDomains, removeBrowserProfile, shareBrowserProfile,
  updateBrowserProfile, useBrowserProfiles,
  type BrowserProfile, type BrowserProfileKind, type BrowserProfilePairing, type BrowserProfilesSnapshot,
} from "@/lib/browserProfiles";
import { cn } from "@/lib/utils";

const button = "inline-flex items-center justify-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground hover:bg-secondary disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
const input = "w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
type SaveSnapshot = (data: BrowserProfilesSnapshot) => void;

function useProfileAction() {
  const t = useT();
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("");
  const [failed, setFailed] = useState(false);
  async function run(action: () => Promise<void>) {
    setPending(true);
    setMessage("");
    setFailed(false);
    try {
      await action();
      setMessage(t("society.browser_profiles.saved"));
    } catch (error) {
      setFailed(true);
      setMessage(t(error instanceof BrowserProfileError && error.status === 409
        ? "society.browser_profiles.busy_error" : "society.browser_profiles.save_error"));
    } finally {
      setPending(false);
    }
  }
  return { pending, run, feedback: message ? <p role={failed ? "alert" : "status"} className={cn("text-xs", failed ? "text-destructive" : "text-muted-foreground")}>{message}</p> : null };
}

function ProfileFields({ name, setName, domains, setDomains, kind }: {
  name: string; setName: (value: string) => void; domains: string; setDomains: (value: string) => void; kind: BrowserProfileKind;
}) {
  const t = useT();
  const id = useId();
  return <>
    <label className="grid gap-1 text-xs font-medium" htmlFor={`${id}-name`}>
      {t("society.browser_profiles.name")}
      <input id={`${id}-name`} className={input} required maxLength={80} autoComplete="off"
        value={name} onChange={(event) => setName(event.target.value)} />
    </label>
    <label className="grid gap-1 text-xs font-medium" htmlFor={`${id}-domains`}>
      {t("society.browser_profiles.domains")}
      <input id={`${id}-domains`} className={input} required={kind === "chrome"} autoComplete="off" placeholder="x.com"
        value={domains} onChange={(event) => setDomains(event.target.value)} aria-describedby={`${id}-domains-hint`} />
    </label>
    <p id={`${id}-domains-hint`} className="text-xs text-muted-foreground">{t(kind === "chrome" ? "society.browser_profiles.domains_hint" : "society.browser_profiles.managed_domains_hint")}</p>
  </>;
}

function AgentBinding({ agentId, data, onSaved }: { agentId: string; data: BrowserProfilesSnapshot; onSaved: SaveSnapshot }) {
  const t = useT();
  const binding = data.bindings[agentId];
  const original = binding?.mode === "profile" && binding.profile_id ? `profile:${binding.profile_id}` : (binding?.mode ?? "inherit");
  const [choice, setChoice] = useState(original);
  const action = useProfileAction();
  const agent = data.agents.find((item) => item.agent_id === agentId);
  const expectedId = binding?.effective_profile_id ?? (binding?.mode === "profile" ? binding.profile_id
    : binding?.mode === "own" ? null : data.default_profile_id);
  const effective = data.profiles.find((profile) => profile.id === expectedId);
  const defaultProfile = data.profiles.find((profile) => profile.id === data.default_profile_id);
  const unavailable = t("society.browser_profiles.profile_unavailable");
  const effectiveLabel = effective?.name ?? (expectedId ? unavailable : t("society.browser_profiles.own"));
  return <form className="grid gap-3 rounded-lg border border-border bg-secondary/30 p-4" onSubmit={(event) => {
    event.preventDefault();
    void action.run(async () => {
      const mode = choice.startsWith("profile:") ? "profile" : choice === "own" ? "own" : "inherit";
      onSaved(await bindAgentBrowserProfile(agentId, mode, mode === "profile" ? choice.slice(8) : null));
    });
  }}>
    <h3 className="text-sm font-semibold">{t("society.browser_profiles.agent_profile").replace("{0}", agent?.name ?? agentId)}</h3>
    <BrandedSelect ariaLabel={t("society.browser_profiles.agent_choice")} value={choice} disabled={action.pending}
      onValueChange={setChoice} options={[
        { value: "inherit", label: `${t("society.browser_profiles.inherit")} · ${defaultProfile?.name ?? (data.default_profile_id ? unavailable : t("society.browser_profiles.own"))}` },
        { value: "own", label: t("society.browser_profiles.own") },
        ...data.profiles.map((profile) => ({ value: `profile:${profile.id}`, label: profile.name })),
        ...(binding?.mode === "profile" && binding.profile_id && !data.profiles.some((profile) => profile.id === binding.profile_id)
          ? [{ value: original, label: unavailable, disabled: true }] : []),
      ]} />
    <p className="text-xs text-muted-foreground">{t("society.browser_profiles.effective").replace("{0}", effectiveLabel)}</p>
    <div><button type="submit" className={button} disabled={action.pending || choice === original}>{t("society.browser_profiles.save_agent")}</button></div>
    {action.feedback}
  </form>;
}

function PairingPanel({ profileId, connected }: { profileId: string; connected: boolean }) {
  const t = useT();
  const action = useProfileAction();
  const [pairing, setPairing] = useState<BrowserProfilePairing | null>(null);
  const [expired, setExpired] = useState(false);
  useEffect(() => {
    if (!pairing) return;
    const timer = window.setTimeout(() => { setPairing(null); setExpired(true); }, pairing.expires_in * 1000);
    return () => window.clearTimeout(timer);
  }, [pairing]);
  useEffect(() => { if (connected) setPairing(null); }, [connected]);
  return <section className="grid gap-3 rounded-lg border border-border bg-secondary/30 p-4">
    <h3 className="text-sm font-semibold">{t("society.browser_profiles.connect_chrome")}</h3>
    <p className="text-xs text-muted-foreground">{t("society.browser_profiles.extension_preview")}</p>
    <p className="text-xs text-muted-foreground">{t("society.browser_profiles.chrome_limits")}</p>
    <ol className="list-decimal space-y-2 pl-5 text-xs text-muted-foreground">
      <li><a className="font-medium text-foreground underline underline-offset-2" href="/api/society/browser/extension.zip" download>{t("society.browser_profiles.download")}</a>{" "}{t("society.browser_profiles.unpack")}</li>
      <li>{t("society.browser_profiles.install")}</li>
      <li>{t("society.browser_profiles.pair_hint")}</li>
    </ol>
    <div><button type="button" className={button} disabled={action.pending} onClick={() => void action.run(async () => {
      setPairing(await pairBrowserProfile(profileId));
      setExpired(false);
    })}>{t("society.browser_profiles.generate_code")}</button></div>
    {pairing && <div className="grid gap-2 rounded-md border border-border bg-background p-3 text-xs">
      <span>{t("society.browser_profiles.server")}</span><code className="break-all select-all">{pairing.server_url}</code>
      <span>{t("society.browser_profiles.code")}</span>
      <code className="select-all text-xl font-semibold tracking-widest" aria-label={t("society.browser_profiles.code")}>{pairing.pairing_code}</code>
      <p className="text-muted-foreground">{t("society.browser_profiles.code_expiry").replace("{0}", String(Math.ceil(pairing.expires_in / 60)))}</p>
    </div>}
    {expired && <p role="status" className="text-xs text-muted-foreground">{t("society.browser_profiles.code_expired")}</p>}
    {action.feedback}
  </section>;
}

function ProfileEditor({ profile, data, onSaved, onRemoved }: {
  profile: BrowserProfile; data: BrowserProfilesSnapshot; onSaved: SaveSnapshot; onRemoved: () => void;
}) {
  const t = useT();
  const [name, setName] = useState(profile.name);
  const [domains, setDomains] = useState(profile.allowed_domains.join(", "));
  const [scope, setScope] = useState<"all" | "selected">(profile.is_default ? "all" : "selected");
  const [agents, setAgents] = useState(profile.agent_ids);
  const [removing, setRemoving] = useState(false);
  const action = useProfileAction();
  const scopeName = useId();
  return <div className="grid gap-4">
    <form className="grid gap-3" onSubmit={(event) => {
      event.preventDefault();
      void action.run(async () => onSaved(await updateBrowserProfile(profile.id, { name: name.trim(), allowed_domains: parseBrowserDomains(domains) })));
    }}>
      <ProfileFields name={name} setName={setName} domains={domains} setDomains={setDomains} kind={profile.kind} />
      <div><button className={button} type="submit" disabled={action.pending || !name.trim() || (profile.kind === "chrome" && !parseBrowserDomains(domains).length)}>{t("society.browser_profiles.save_details")}</button></div>
    </form>
    {profile.kind === "chrome" && <PairingPanel profileId={profile.id} connected={profile.connected} />}
    <form className="grid gap-3 border-t border-border pt-4" onSubmit={(event) => {
      event.preventDefault();
      void action.run(async () => onSaved(await shareBrowserProfile(profile.id, { scope, agent_ids: scope === "all" ? [] : agents })));
    }}>
      <fieldset className="grid gap-2" disabled={action.pending}>
        <legend className="mb-2 text-sm font-semibold">{t("society.browser_profiles.share_with")}</legend>
        <label className="flex items-center gap-2 text-sm"><input type="radio" name={scopeName} className="accent-primary" checked={scope === "all"} onChange={() => setScope("all")} />{t("society.browser_profiles.all")}</label>
        <p className="pl-5 text-xs text-muted-foreground">{t("society.browser_profiles.all_hint")}</p>
        <label className="flex items-center gap-2 text-sm"><input type="radio" name={scopeName} className="accent-primary" checked={scope === "selected"} onChange={() => setScope("selected")} />{t("society.browser_profiles.selected")}</label>
        {scope === "selected" && <p className="pl-5 text-xs text-muted-foreground">{t("society.browser_profiles.selected_hint")}</p>}
        {scope === "selected" && <div className="grid max-h-44 gap-2 overflow-y-auto rounded-md border border-border p-3">
          {data.agents.map((agent) => <label key={agent.agent_id} className="flex items-center gap-2 text-sm">
            <input type="checkbox" className="accent-primary" checked={agents.includes(agent.agent_id)} onChange={(event) => {
              setAgents((current) => event.target.checked ? [...current, agent.agent_id] : current.filter((id) => id !== agent.agent_id));
            }} />{agent.name}
          </label>)}
          {!data.agents.length && <p className="text-xs text-muted-foreground">{t("society.browser_profiles.no_agents")}</p>}
        </div>}
      </fieldset>
      <div><button type="submit" className={button} disabled={action.pending}>{t("society.browser_profiles.save_sharing")}</button></div>
    </form>
    {action.feedback}
    <div className="border-t border-border pt-3">
      {removing ? <div className="grid gap-2">
        <p className="text-xs text-muted-foreground">{t("society.browser_profiles.remove_hint")}</p>
        <div className="flex gap-2">
          <button type="button" className={cn(button, "text-destructive")} disabled={action.pending} onClick={() => void action.run(async () => {
            onSaved(await removeBrowserProfile(profile.id)); onRemoved();
          })}>{t("society.browser_profiles.confirm_remove")}</button>
          <button type="button" className={button} disabled={action.pending} onClick={() => setRemoving(false)}>{t("society.browser_profiles.cancel")}</button>
        </div>
      </div> : <button type="button" className="text-xs text-destructive underline underline-offset-2" onClick={() => setRemoving(true)}>{t("society.browser_profiles.remove")}</button>}
    </div>
  </div>;
}

function NewProfile({ onCreated, onCancel }: { onCreated: (profile: BrowserProfile) => Promise<void>; onCancel: () => void }) {
  const t = useT();
  const [kind, setKind] = useState<BrowserProfileKind>("chrome");
  const [name, setName] = useState("");
  const [domains, setDomains] = useState("");
  const action = useProfileAction();
  return <form className="grid gap-3 rounded-lg border border-border p-4" onSubmit={(event) => {
    event.preventDefault();
    void action.run(async () => onCreated(await createBrowserProfile({ name: name.trim(), kind, allowed_domains: parseBrowserDomains(domains) })));
  }}>
    <h3 className="text-sm font-semibold">{t("society.browser_profiles.new")}</h3>
    <BrandedSelect ariaLabel={t("society.browser_profiles.kind")} value={kind} onValueChange={(value) => setKind(value as BrowserProfileKind)} disabled={action.pending}
      options={[
        { value: "chrome", label: t("society.browser_profiles.chrome") },
        { value: "managed", label: t("society.browser_profiles.managed") },
      ]} />
    <p className="text-xs text-muted-foreground">{t(kind === "chrome" ? "society.browser_profiles.chrome_hint" : "society.browser_profiles.managed_hint")}</p>
    <ProfileFields name={name} setName={setName} domains={domains} setDomains={setDomains} kind={kind} />
    <div className="flex gap-2">
      <button type="submit" className={button} disabled={action.pending || !name.trim() || (kind === "chrome" && !parseBrowserDomains(domains).length)}>{t("society.browser_profiles.create")}</button>
      <button type="button" className={button} disabled={action.pending} onClick={onCancel}>{t("society.browser_profiles.cancel")}</button>
    </div>
    {action.feedback}
  </form>;
}

export default function BrowserProfilesDialog({ agentId, onClose }: { agentId?: string; onClose: () => void }) {
  const t = useT();
  useLocaleChunk("society");
  const queryClient = useQueryClient();
  const query = useBrowserProfiles();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const data = query.data;
  const selected = data?.profiles.find((profile) => profile.id === selectedId) ?? data?.profiles[0];
  const onSaved = (snapshot: BrowserProfilesSnapshot) => {
    const previous = queryClient.getQueryData<BrowserProfilesSnapshot>(BROWSER_PROFILES_QUERY);
    const changedProfiles = new Set(previous?.profiles.filter((profile) => {
      const next = snapshot.profiles.find((entry) => entry.id === profile.id);
      return !next || next.allowed_domains.join(",") !== profile.allowed_domains.join(",");
    }).map((profile) => profile.id));
    const agentIds = snapshot.agents.filter(({ agent_id }) => {
      const before = previous?.bindings[agent_id];
      const after = snapshot.bindings[agent_id];
      return before?.effective_profile_id !== after?.effective_profile_id ||
        (before?.effective_profile_id != null && changedProfiles.has(before.effective_profile_id));
    }).map(({ agent_id }) => agent_id);
    queryClient.setQueryData(BROWSER_PROFILES_QUERY, snapshot);
    if (agentIds.length) {
      window.dispatchEvent(new CustomEvent(BROWSER_PROFILE_CHANGED_EVENT, { detail: { agentIds } }));
      void queryClient.invalidateQueries({ queryKey: ["society", "browser-open"] });
    }
  };
  return <Dialog.Root open onOpenChange={(open) => { if (!open) onClose(); }}>
    <Dialog.Portal>
      <Dialog.Overlay className="fixed inset-0 z-[150] bg-scrim/60 backdrop-blur-sm" />
      <Dialog.Content className="fixed left-1/2 top-1/2 z-[151] flex max-h-[90dvh] w-[min(46rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-xl border border-border bg-background text-foreground shadow-xl">
        <div className="flex items-start justify-between gap-4 border-b border-border p-5">
          <div className="grid gap-1.5">
            <Dialog.Title className="font-display text-lg font-semibold">{t("society.browser_profiles.title")}</Dialog.Title>
            <Dialog.Description className="text-sm text-muted-foreground">{t("society.browser_profiles.intro")}</Dialog.Description>
          </div>
          <Dialog.Close className={button} aria-label={t("society.browser_profiles.close")}><X size={16} /></Dialog.Close>
        </div>
        <div className="grid gap-5 overflow-y-auto p-5">
          <p className="text-xs text-muted-foreground">{t("society.browser_profiles.persistence")}</p>
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className={button} disabled={!data || creating} onClick={() => setCreating(true)}><Plus size={14} />{t("society.browser_profiles.new")}</button>
            <button type="button" className={button} disabled={query.isFetching} onClick={() => void query.refetch()}><RotateCw size={14} />{t("society.browser_profiles.refresh")}</button>
          </div>
          {query.isLoading && <p role="status" className="text-sm text-muted-foreground">{t("society.browser_profiles.loading")}</p>}
          {query.isError && <p role="alert" className="text-sm text-destructive">{t("society.browser_profiles.load_error")}</p>}
          {data && <>
            {agentId && <AgentBinding key={`${agentId}:${JSON.stringify(data.bindings[agentId])}`} agentId={agentId} data={data} onSaved={onSaved} />}
            {creating && <NewProfile onCancel={() => setCreating(false)} onCreated={async (profile) => {
              await queryClient.invalidateQueries({ queryKey: BROWSER_PROFILES_QUERY });
              setSelectedId(profile.id); setCreating(false);
            }} />}
            {!data.profiles.length && !creating && <p className="rounded-lg border border-dashed border-border p-5 text-sm text-muted-foreground">{t("society.browser_profiles.empty")}</p>}
            {!!data.profiles.length && <div className="grid gap-3">
              <BrandedSelect ariaLabel={t("society.browser_profiles.choose_profile")} value={selected?.id ?? ""} onValueChange={setSelectedId}
                options={data.profiles.map((profile) => ({ value: profile.id, label: `${profile.name}${profile.is_default ? ` · ${t("society.browser_profiles.default")}` : ""}` }))} />
              {selected && <>
                <p role="status" className="text-xs text-muted-foreground">{t(selected.kind === "chrome"
                  ? selected.connected ? "society.browser_profiles.connected" : "society.browser_profiles.disconnected"
                  : "society.browser_profiles.managed_status")}</p>
                <ProfileEditor key={`${selected.id}:${selected.name}:${selected.allowed_domains.join(",")}:${selected.agent_ids.join(",")}:${selected.is_default}`} profile={selected} data={data} onSaved={onSaved} onRemoved={() => setSelectedId(null)} />
              </>}
            </div>}
          </>}
        </div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
