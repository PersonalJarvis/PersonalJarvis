import { useEffect, useId, useState, type ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { ChevronDown, Globe, Plug, Plus, RotateCw, UserRound, X } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { useLocaleChunk, useT } from "@/i18n";
import {
  BROWSER_PROFILES_QUERY, BROWSER_PROFILE_CHANGED_EVENT, BrowserProfileError, bindAgentBrowserProfile, createBrowserProfile,
  pairBrowserProfile, parseBrowserDomains, removeBrowserProfile, shareBrowserProfile,
  updateBrowserProfile, useBrowserProfiles,
  type BrowserBindingMode, type BrowserProfile, type BrowserProfileKind, type BrowserProfilePairing, type BrowserProfilesSnapshot,
} from "@/lib/browserProfiles";
import { cn } from "@/lib/utils";

const button = "inline-flex items-center justify-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground hover:bg-secondary disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
const primaryButton = "inline-flex items-center justify-center gap-1.5 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
const input = "w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
const choiceCard = "flex cursor-pointer items-start gap-3 rounded-lg border border-border bg-background p-3 hover:bg-secondary/50 has-[:checked]:border-primary has-[:checked]:bg-primary/5 has-[:disabled]:cursor-default";
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

function KindIcon({ kind }: { kind: BrowserProfileKind | "own" }) {
  const Icon = kind === "chrome" ? Plug : kind === "own" ? UserRound : Globe;
  return <span aria-hidden className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-md bg-secondary text-muted-foreground"><Icon size={15} /></span>;
}

function Choice({ name, checked, disabled, onSelect, kind, title, hint }: {
  name: string; checked: boolean; disabled?: boolean; onSelect: () => void; kind: BrowserProfileKind | "own"; title: string; hint: string;
}) {
  return <label className={choiceCard}>
    <input type="radio" name={name} className="mt-2 accent-primary" checked={checked} disabled={disabled} onChange={onSelect} />
    <KindIcon kind={kind} />
    <span className="grid gap-0.5">
      <span className="text-sm font-medium">{title}</span>
      <span className="text-xs text-muted-foreground">{hint}</span>
    </span>
  </label>;
}

function profileHint(profile: BrowserProfile, t: (key: string) => string) {
  return profile.kind === "chrome"
    ? t("society.browser_profiles.chrome_profile_hint").replace("{0}", profile.allowed_domains.join(", "))
    : t("society.browser_profiles.managed_profile_hint");
}

/** One plain question for the agent the dialog was opened from; a pick saves at once. */
function AgentChoice({ agentId, data, onSaved }: { agentId: string; data: BrowserProfilesSnapshot; onSaved: SaveSnapshot }) {
  const t = useT();
  const name = useId();
  const binding = data.bindings[agentId];
  const current = binding?.mode === "profile" && binding.profile_id ? `profile:${binding.profile_id}` : (binding?.mode ?? "inherit");
  const action = useProfileAction();
  const agent = data.agents.find((item) => item.agent_id === agentId);
  const defaultProfile = data.profiles.find((profile) => profile.id === data.default_profile_id);
  const missing = (binding?.mode === "profile" && !data.profiles.some((profile) => profile.id === binding.profile_id))
    || (binding?.mode === "inherit" && !!data.default_profile_id && !defaultProfile);
  // The default profile is offered as "Shared browser"; listing it again would show the same name twice.
  const others = data.profiles.filter((profile) => !profile.is_default || current === `profile:${profile.id}`);
  const choose = (mode: BrowserBindingMode, profileId: string | null = null) => void action.run(async () => {
    onSaved(await bindAgentBrowserProfile(agentId, mode, profileId));
  });
  return <section className="grid gap-3 rounded-xl border border-border bg-secondary/30 p-4">
    <h3 className="text-sm font-semibold">{t("society.browser_profiles.agent_title").replace("{0}", agent?.name ?? agentId)}</h3>
    {missing && <p role="alert" className="text-xs text-destructive">{t("society.browser_profiles.agent_needs_choice")}</p>}
    <div role="radiogroup" aria-label={t("society.browser_profiles.agent_title").replace("{0}", agent?.name ?? agentId)} className="grid gap-2">
      <Choice name={name} kind="managed" checked={current === "inherit" && !missing} disabled={action.pending || !defaultProfile}
        onSelect={() => choose("inherit")} title={t("society.browser_profiles.use_shared")}
        hint={defaultProfile ? t("society.browser_profiles.use_shared_hint").replace("{0}", defaultProfile.name) : t("society.browser_profiles.no_shared")} />
      <Choice name={name} kind="own" checked={current === "own"} disabled={action.pending}
        onSelect={() => choose("own")} title={t("society.browser_profiles.own")} hint={t("society.browser_profiles.own_hint")} />
      {others.map((profile) => <Choice key={profile.id} name={name} kind={profile.kind} checked={current === `profile:${profile.id}`}
        disabled={action.pending} onSelect={() => choose("profile", profile.id)} title={profile.name} hint={profileHint(profile, t)} />)}
    </div>
    {action.feedback}
  </section>;
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
  return <details open={!connected} className="group grid gap-3 rounded-lg border border-border p-3">
    <summary className="flex cursor-pointer list-none items-center justify-between text-sm font-semibold">
      {t(connected ? "society.browser_profiles.reconnect_chrome" : "society.browser_profiles.connect_chrome")}
      <ChevronDown size={14} aria-hidden className="transition-transform group-open:rotate-180" />
    </summary>
    <div className="mt-3 grid gap-3">
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
      <p className="text-xs text-muted-foreground">{t("society.browser_profiles.extension_preview")} {t("society.browser_profiles.chrome_limits")}</p>
      {action.feedback}
    </div>
  </details>;
}

function DomainsField({ kind, domains, setDomains }: { kind: BrowserProfileKind; domains: string; setDomains: (value: string) => void }) {
  const t = useT();
  const id = useId();
  const field = <>
    <label className="grid gap-1 text-xs font-medium" htmlFor={id}>
      {t("society.browser_profiles.domains")}
      <input id={id} className={input} required={kind === "chrome"} autoComplete="off" placeholder="x.com, github.com"
        value={domains} onChange={(event) => setDomains(event.target.value)} aria-describedby={`${id}-hint`} />
    </label>
    <p id={`${id}-hint`} className="text-xs text-muted-foreground">{t(kind === "chrome" ? "society.browser_profiles.domains_hint" : "society.browser_profiles.managed_domains_hint")}</p>
  </>;
  // Most Jarvis browser profiles never need a website limit, so it stays folded away.
  if (kind === "chrome") return field;
  return <details open={!!domains.trim()} className="grid gap-2">
    <summary className="cursor-pointer text-xs font-medium text-muted-foreground">{t("society.browser_profiles.limit_sites")}</summary>
    <div className="mt-2 grid gap-1">{field}</div>
  </details>;
}

function NameField({ name, setName }: { name: string; setName: (value: string) => void }) {
  const t = useT();
  const id = useId();
  return <label className="grid gap-1 text-xs font-medium" htmlFor={id}>
    {t("society.browser_profiles.name")}
    <input id={id} className={input} required maxLength={80} autoComplete="off" value={name} onChange={(event) => setName(event.target.value)} />
  </label>;
}

function UsedBy({ profile, data }: { profile: BrowserProfile; data: BrowserProfilesSnapshot }) {
  const t = useT();
  const names = data.agents.filter((agent) => profile.agent_ids.includes(agent.agent_id)).map((agent) => agent.name);
  if (profile.is_default && data.agents.length > 0 && names.length === data.agents.length) return <>{t("society.browser_profiles.used_by_all")}</>;
  if (!names.length) return <>{t(profile.is_default ? "society.browser_profiles.used_by_new" : "society.browser_profiles.used_by_none")}</>;
  const shown = names.length > 3 ? t("society.browser_profiles.used_by_more").replace("{0}", names.slice(0, 3).join(", ")).replace("{1}", String(names.length - 3)) : names.join(", ");
  return <>{t("society.browser_profiles.used_by").replace("{0}", shown)}</>;
}

function ProfileUsage({ profile, data, onSaved }: { profile: BrowserProfile; data: BrowserProfilesSnapshot; onSaved: SaveSnapshot }) {
  const t = useT();
  const [agents, setAgents] = useState(profile.agent_ids);
  const [confirming, setConfirming] = useState(false);
  const action = useProfileAction();
  const changed = agents.length !== profile.agent_ids.length || agents.some((id) => !profile.agent_ids.includes(id));
  const removed = profile.agent_ids.some((id) => !agents.includes(id));
  if (profile.is_default) return <section className="grid gap-1 border-t border-border pt-4">
    <h4 className="text-sm font-semibold">{t("society.browser_profiles.usage_title")}</h4>
    <p className="text-xs text-muted-foreground">{t("society.browser_profiles.default_usage")}</p>
  </section>;
  return <section className="grid gap-3 border-t border-border pt-4">
    <h4 className="text-sm font-semibold">{t("society.browser_profiles.usage_title")}</h4>
    {confirming ? <div className="grid gap-2 rounded-md border border-border bg-secondary/40 p-3">
      <p className="text-xs">{t("society.browser_profiles.make_default_confirm").replace("{0}", profile.name)}</p>
      <div className="flex gap-2">
        <button type="button" className={primaryButton} disabled={action.pending} onClick={() => void action.run(async () => {
          onSaved(await shareBrowserProfile(profile.id, { scope: "all", agent_ids: [] }));
        })}>{t("society.browser_profiles.confirm_make_default")}</button>
        <button type="button" className={button} disabled={action.pending} onClick={() => setConfirming(false)}>{t("society.browser_profiles.cancel")}</button>
      </div>
    </div> : <div><button type="button" className={button} disabled={action.pending} onClick={() => setConfirming(true)}>{t("society.browser_profiles.make_default")}</button></div>}
    <form className="grid gap-2" onSubmit={(event) => {
      event.preventDefault();
      void action.run(async () => onSaved(await shareBrowserProfile(profile.id, { scope: "selected", agent_ids: agents })));
    }}>
      <p className="text-xs text-muted-foreground">{t("society.browser_profiles.pick_agents")}</p>
      <fieldset className="grid max-h-44 gap-2 overflow-y-auto rounded-md border border-border p-3" disabled={action.pending}>
        <legend className="sr-only">{t("society.browser_profiles.pick_agents")}</legend>
        {data.agents.map((agent) => <label key={agent.agent_id} className="flex items-center gap-2 text-sm">
          <input type="checkbox" className="accent-primary" checked={agents.includes(agent.agent_id)} onChange={(event) => {
            setAgents((current) => event.target.checked ? [...current, agent.agent_id] : current.filter((id) => id !== agent.agent_id));
          }} />{agent.name}
        </label>)}
        {!data.agents.length && <p className="text-xs text-muted-foreground">{t("society.browser_profiles.no_agents")}</p>}
      </fieldset>
      {removed && <p className="text-xs text-muted-foreground">{t("society.browser_profiles.pick_agents_hint")}</p>}
      <div><button type="submit" className={button} disabled={action.pending || !changed}>{t("society.browser_profiles.save_agents")}</button></div>
    </form>
    {action.feedback}
  </section>;
}

function ProfileEditor({ profile, data, onSaved, onRemoved }: {
  profile: BrowserProfile; data: BrowserProfilesSnapshot; onSaved: SaveSnapshot; onRemoved: () => void;
}) {
  const t = useT();
  const [name, setName] = useState(profile.name);
  const [domains, setDomains] = useState(profile.allowed_domains.join(", "));
  const [removing, setRemoving] = useState(false);
  const details = useProfileAction();
  const removal = useProfileAction();
  const parsed = parseBrowserDomains(domains);
  const dirty = name.trim() !== profile.name || parsed.join(",") !== profile.allowed_domains.join(",");
  return <div className="grid gap-4 border-t border-border p-4">
    {profile.kind === "chrome" && <PairingPanel profileId={profile.id} connected={profile.connected} />}
    <form className="grid gap-3" onSubmit={(event) => {
      event.preventDefault();
      void details.run(async () => onSaved(await updateBrowserProfile(profile.id, { name: name.trim(), allowed_domains: parsed })));
    }}>
      <NameField name={name} setName={setName} />
      <DomainsField kind={profile.kind} domains={domains} setDomains={setDomains} />
      <div><button className={button} type="submit" disabled={details.pending || !dirty || !name.trim() || (profile.kind === "chrome" && !parsed.length)}>{t("society.browser_profiles.save_details")}</button></div>
      {details.feedback}
    </form>
    <ProfileUsage key={profile.agent_ids.join(",")} profile={profile} data={data} onSaved={onSaved} />
    <div className="border-t border-border pt-3">
      {removing ? <div className="grid gap-2">
        <p className="text-xs text-muted-foreground">{t(profile.is_default ? "society.browser_profiles.remove_default_hint" : "society.browser_profiles.remove_hint")}</p>
        <div className="flex gap-2">
          <button type="button" className={cn(button, "text-destructive")} disabled={removal.pending} onClick={() => void removal.run(async () => {
            onSaved(await removeBrowserProfile(profile.id)); onRemoved();
          })}>{t("society.browser_profiles.confirm_remove")}</button>
          <button type="button" className={button} disabled={removal.pending} onClick={() => setRemoving(false)}>{t("society.browser_profiles.cancel")}</button>
        </div>
        {removal.feedback}
      </div> : <button type="button" className="text-xs text-destructive underline underline-offset-2" onClick={() => setRemoving(true)}>{t("society.browser_profiles.remove")}</button>}
    </div>
  </div>;
}

function ProfileCard({ profile, data, open, onToggle, onSaved, onRemoved }: {
  profile: BrowserProfile; data: BrowserProfilesSnapshot; open: boolean; onToggle: () => void; onSaved: SaveSnapshot; onRemoved: () => void;
}) {
  const t = useT();
  const panel = useId();
  const badge = (text: ReactNode, tone = "bg-secondary text-muted-foreground") =>
    <span className={cn("rounded-full px-2 py-0.5 text-[11px] font-medium", tone)}>{text}</span>;
  return <article className="rounded-xl border border-border bg-background" data-testid={`browser-profile-${profile.id}`}>
    <button type="button" className="flex w-full items-start gap-3 rounded-xl p-4 text-left hover:bg-secondary/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      aria-expanded={open} aria-controls={panel} onClick={onToggle}>
      <KindIcon kind={profile.kind} />
      <span className="grid min-w-0 flex-1 gap-1">
        <span className="flex flex-wrap items-center gap-2">
          <span className="truncate text-sm font-semibold">{profile.name}</span>
          {profile.is_default && badge(t("society.browser_profiles.default"), "bg-primary/10 text-primary")}
          {badge(t(profile.kind === "chrome" ? "society.browser_profiles.kind_chrome" : "society.browser_profiles.kind_managed"))}
          {profile.kind === "chrome" && badge(t(profile.connected ? "society.browser_profiles.status_connected" : "society.browser_profiles.status_disconnected"),
            profile.connected ? "bg-success/10 text-success" : "bg-warning/10 text-warning")}
        </span>
        <span className="text-xs text-muted-foreground"><UsedBy profile={profile} data={data} /></span>
      </span>
      <span className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
        {t(open ? "society.browser_profiles.done" : "society.browser_profiles.manage")}
        <ChevronDown size={14} aria-hidden className={cn("transition-transform", open && "rotate-180")} />
      </span>
    </button>
    {open && <div id={panel}>
      <ProfileEditor key={`${profile.id}:${profile.name}:${profile.allowed_domains.join(",")}:${profile.is_default}`} profile={profile} data={data} onSaved={onSaved} onRemoved={onRemoved} />
    </div>}
  </article>;
}

function NewProfile({ initialKind, onCreated, onCancel }: { initialKind: BrowserProfileKind; onCreated: (profile: BrowserProfile) => Promise<void>; onCancel: () => void }) {
  const t = useT();
  const kindName = useId();
  const [kind, setKind] = useState<BrowserProfileKind>(initialKind);
  const [name, setName] = useState("");
  const [domains, setDomains] = useState("");
  const action = useProfileAction();
  const parsed = parseBrowserDomains(domains);
  return <form className="grid gap-3 rounded-xl border border-primary/40 p-4" onSubmit={(event) => {
    event.preventDefault();
    void action.run(async () => onCreated(await createBrowserProfile({ name: name.trim(), kind, allowed_domains: parsed })));
  }}>
    <h3 className="text-sm font-semibold">{t("society.browser_profiles.new_title")}</h3>
    <div role="radiogroup" aria-label={t("society.browser_profiles.kind")} className="grid gap-2">
      <Choice name={kindName} kind="managed" checked={kind === "managed"} disabled={action.pending} onSelect={() => setKind("managed")}
        title={t("society.browser_profiles.new_managed")} hint={t("society.browser_profiles.new_managed_hint")} />
      <Choice name={kindName} kind="chrome" checked={kind === "chrome"} disabled={action.pending} onSelect={() => setKind("chrome")}
        title={t("society.browser_profiles.new_chrome")} hint={t("society.browser_profiles.new_chrome_hint")} />
    </div>
    <NameField name={name} setName={setName} />
    <DomainsField kind={kind} domains={domains} setDomains={setDomains} />
    <div className="flex gap-2">
      <button type="submit" className={primaryButton} disabled={action.pending || !name.trim() || (kind === "chrome" && !parsed.length)}>{t("society.browser_profiles.create")}</button>
      <button type="button" className={button} disabled={action.pending} onClick={onCancel}>{t("society.browser_profiles.cancel")}</button>
    </div>
    {action.feedback}
  </form>;
}

function HowItWorks() {
  const t = useT();
  return <details className="group rounded-lg border border-border px-4 py-3">
    <summary className="flex cursor-pointer list-none items-center justify-between text-sm font-medium">
      {t("society.browser_profiles.how_title")}
      <ChevronDown size={14} aria-hidden className="transition-transform group-open:rotate-180" />
    </summary>
    <ul className="mt-3 grid gap-2 text-xs text-muted-foreground">
      <li><strong className="text-foreground">{t("society.browser_profiles.use_shared")}:</strong> {t("society.browser_profiles.how_shared")}</li>
      <li><strong className="text-foreground">{t("society.browser_profiles.how_sign_in_title")}:</strong> {t("society.browser_profiles.how_sign_in")}</li>
      <li><strong className="text-foreground">{t("society.browser_profiles.own")}:</strong> {t("society.browser_profiles.how_own")}</li>
      <li><strong className="text-foreground">{t("society.browser_profiles.kind_chrome")}:</strong> {t("society.browser_profiles.how_chrome")}</li>
      <li>{t("society.browser_profiles.persistence")}</li>
    </ul>
  </details>;
}

export default function BrowserProfilesDialog({ agentId, onClose, connectChrome = false }: {
  agentId?: string; onClose: () => void; connectChrome?: boolean;
}) {
  const t = useT();
  useLocaleChunk("society");
  const queryClient = useQueryClient();
  const query = useBrowserProfiles();
  const [openId, setOpenId] = useState<string | null | undefined>(undefined);
  const [creating, setCreating] = useState(false);
  const [setupDismissed, setSetupDismissed] = useState(false);
  const data = query.data;
  const firstChrome = data?.profiles.find((profile) => profile.kind === "chrome");
  const showCreate = creating || (connectChrome && !!data && !firstChrome && !setupDismissed);
  // Google recovery opens the Chrome profile it asks the user to connect; otherwise every card starts folded.
  const expanded = openId === undefined ? (connectChrome ? firstChrome?.id ?? null : null) : openId;
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
      <Dialog.Content className="fixed left-1/2 top-1/2 z-[151] flex max-h-[90dvh] w-[min(42rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-xl border border-border bg-background text-foreground shadow-xl">
        <div className="flex items-start justify-between gap-4 border-b border-border p-5">
          <div className="grid gap-1.5">
            <Dialog.Title className="font-display text-lg font-semibold">{t("society.browser_profiles.title")}</Dialog.Title>
            <Dialog.Description className="text-sm text-muted-foreground">{t("society.browser_profiles.intro")}</Dialog.Description>
          </div>
          <Dialog.Close className={button} aria-label={t("society.browser_profiles.close")}><X size={16} /></Dialog.Close>
        </div>
        <div className="grid gap-5 overflow-y-auto p-5">
          {connectChrome && <p role="note" className="rounded-lg border border-border bg-secondary/30 p-4 text-sm">
            {t("society.browser_profiles.google_signin_recovery")}
          </p>}
          {query.isLoading && <p role="status" className="text-sm text-muted-foreground">{t("society.browser_profiles.loading")}</p>}
          {query.isError && <p role="alert" className="text-sm text-destructive">{t("society.browser_profiles.load_error")}</p>}
          {data && <>
            {agentId && <AgentChoice agentId={agentId} data={data} onSaved={onSaved} />}
            <section className="grid gap-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-sm font-semibold">{t("society.browser_profiles.profiles")}</h3>
                <div className="flex items-center gap-2">
                  <button type="button" className={button} disabled={query.isFetching} onClick={() => void query.refetch()}
                    aria-label={t("society.browser_profiles.refresh")} title={t("society.browser_profiles.refresh")}><RotateCw size={14} /></button>
                  <button type="button" className={button} disabled={showCreate} onClick={() => setCreating(true)}><Plus size={14} />{t("society.browser_profiles.new")}</button>
                </div>
              </div>
              {showCreate && <NewProfile initialKind={connectChrome ? "chrome" : "managed"}
                onCancel={() => { setCreating(false); setSetupDismissed(true); }} onCreated={async (profile) => {
                  await queryClient.invalidateQueries({ queryKey: BROWSER_PROFILES_QUERY });
                  setOpenId(profile.id); setCreating(false); setSetupDismissed(true);
                }} />}
              {!data.profiles.length && !showCreate && <p className="rounded-lg border border-dashed border-border p-5 text-sm text-muted-foreground">{t("society.browser_profiles.empty")}</p>}
              {data.profiles.map((profile) => <ProfileCard key={profile.id} profile={profile} data={data} open={expanded === profile.id}
                onToggle={() => setOpenId(expanded === profile.id ? null : profile.id)} onSaved={onSaved} onRemoved={() => setOpenId(null)} />)}
            </section>
            <HowItWorks />
          </>}
        </div>
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}
