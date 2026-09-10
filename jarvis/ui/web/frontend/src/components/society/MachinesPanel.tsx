import { useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Monitor, RefreshCw, X } from "lucide-react";
import {
  machineRequest,
  type Machine,
  type MachineGrant,
  type MachineJob,
} from "@/lib/machinesApi";
import { Button } from "@/components/ui/button";

const field =
  "w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground";

export function MachinesButton({ agentId }: { agentId?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger asChild>
        <Button variant="outline" size="sm">
          <Monitor className="mr-2 h-4 w-4" />
          Connected computers
        </Button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[60] bg-scrim/60" />
        <Dialog.Content className="fixed inset-4 z-[61] mx-auto flex max-w-5xl flex-col overflow-hidden rounded-xl border border-border bg-card text-foreground shadow-float md:inset-10">
          <header className="flex items-center justify-between border-b border-border p-5">
            <div>
              <Dialog.Title className="font-display text-xl">
                Connected computers
              </Dialog.Title>
              <Dialog.Description className="mt-1 text-sm text-muted-foreground">
                Pair your computers and choose what each agent may access.
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <Button
                variant="ghost"
                size="icon"
                aria-label="Close connected computers"
              >
                <X className="h-4 w-4" />
              </Button>
            </Dialog.Close>
          </header>
          {open && <MachinesContent agentId={agentId} />}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function MachinesContent({ agentId }: { agentId?: string }) {
  const cache = useQueryClient();
  const machines = useQuery({
    queryKey: ["machines"],
    refetchInterval: () => 5000 + Math.random() * 1000,
    queryFn: () => machineRequest<{ machines: Machine[] }>(""),
    retry: false,
  });
  const jobs = useQuery({
    queryKey: ["machine-jobs", agentId],
    refetchInterval: () => 5000 + Math.random() * 1000,
    queryFn: () =>
      machineRequest<{ jobs: MachineJob[] }>(
        `/jobs${agentId ? `?agent_id=${encodeURIComponent(agentId)}` : ""}`,
      ),
    retry: false,
  });
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<Machine | null>(null);
  const [ssh, setSsh] = useState(false);
  async function act(action: () => Promise<unknown>) {
    setError("");
    setNotice("");
    setBusy(true);
    try {
      await action();
      await Promise.all(
        [
          "machines",
          "machine-jobs",
          "machine-job",
          "machine-placements",
          "machine-roster",
          "machine-grants",
          "society",
        ].map((key) => cache.invalidateQueries({ queryKey: [key] })),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Request failed");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="space-y-6 overflow-y-auto p-5">
      {(error || machines.error || jobs.error) && (
        <p
          role="alert"
          className="rounded border border-destructive p-3 text-sm text-destructive"
        >
          {error || String(machines.error || jobs.error)}
        </p>
      )}
      {notice && (
        <p role="status" className="text-sm">
          {notice}
        </p>
      )}
      <section className="space-y-3 rounded-lg border border-border p-4">
        <h2 className="font-semibold">Add a computer</h2>
        <div className="flex flex-wrap gap-2">
          <Button
            size="sm"
            variant={ssh ? "outline" : "default"}
            onClick={() => setSsh(false)}
          >
            Jarvis connector
          </Button>
          <Button
            size="sm"
            variant={ssh ? "default" : "outline"}
            onClick={() => setSsh(true)}
          >
            Existing SSH
          </Button>
        </div>
        {ssh ? (
          <SshSetup
            busy={busy}
            act={act}
            onSaved={() =>
              setNotice(
                "SSH computer saved. Grant access to an agent to use it.",
              )
            }
          />
        ) : (
          <>
            <p className="text-sm text-muted-foreground">
              On the other computer, install PersonalJarvis and run its
              connector. Use a reachable HTTPS hub with a valid certificate.
            </p>
            <label className="block text-sm">
              Computer name
              <input
                className={field}
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="My Linux VPS"
              />
            </label>
            <Button
              disabled={busy || !name.trim()}
              onClick={() =>
                void act(async () => {
                  const result = await machineRequest<{ code: string }>(
                    "/pairing",
                    "POST",
                    { name },
                  );
                  setCode(result.code);
                })
              }
            >
              Create pairing code
            </Button>
            {code && (
              <div className="space-y-2 rounded-md bg-secondary p-3 text-sm">
                <p>
                  Enter this once in the connector's hidden prompt. It expires
                  after ten minutes.
                </p>
                <code className="block break-all select-all">{code}</code>
                <pre className="overflow-auto">
                  python -m jarvis.machines --hub wss://YOUR-HUB --pair
                </pre>
                <p>
                  For desktop access, add --desktop own, --desktop attached, or
                  --desktop both on the computer you want to control. Attached
                  means your visible desktop.
                </p>
                <Button size="sm" variant="outline" onClick={() => setCode("")}>
                  Hide code
                </Button>
              </div>
            )}
          </>
        )}
      </section>
      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="font-semibold">Your computers</h2>
          <Button
            size="sm"
            variant="ghost"
            disabled={busy}
            onClick={() => void act(async () => undefined)}
          >
            <RefreshCw className="mr-2 h-4 w-4" />
            Refresh
          </Button>
        </div>
        {machines.isLoading ? (
          <p>Loading computers…</p>
        ) : !machines.data?.machines.length ? (
          <p className="text-sm text-muted-foreground">
            No computers paired yet.
          </p>
        ) : (
          <ul className="divide-y divide-border rounded-lg border border-border">
            {machines.data.machines.map((machine) => (
              <li
                key={machine.id}
                className="flex flex-wrap items-center gap-3 p-4"
              >
                <Monitor className="h-5 w-5 shrink-0" />
                <div className="min-w-0 flex-1">
                  <h3 className="font-medium">{machine.name}</h3>
                  <p className="text-xs text-muted-foreground">
                    {machine.capabilities.os} ·{" "}
                    {machine.transport === "ssh"
                      ? "SSH · checked when used"
                      : machine.online
                        ? "Connected"
                        : "Offline"}{" "}
                    · {machine.capabilities.shell_name}
                  </p>
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setSelected(machine)}
                >
                  Access
                </Button>
                {machine.capabilities.desktop && (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy || !machine.online}
                    onClick={() =>
                      void act(async () => {
                        await machineRequest(
                          `/${machine.id}/desktop/stop`,
                          "POST",
                        );
                        setNotice("Desktop stop requested.");
                      })
                    }
                  >
                    Stop desktop
                  </Button>
                )}
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy}
                  onClick={() =>
                    void act(async () => {
                      await machineRequest(`/${machine.id}`, "DELETE");
                      setNotice(`${machine.name} was revoked.`);
                      if (selected?.id === machine.id) setSelected(null);
                    })
                  }
                >
                  Revoke
                </Button>
              </li>
            ))}
          </ul>
        )}
        {selected && (
          <GrantEditor
            key={`${selected.id}:${agentId ?? ""}`}
            machine={selected}
            agentId={agentId}
            busy={busy}
            act={act}
            onSaved={() =>
              setNotice(
                "Access profile saved. Ask the agent to work on this computer in its chat.",
              )
            }
          />
        )}
      </section>
      <AgentLocations
        agentId={agentId}
        machines={machines.data?.machines || []}
        busy={busy}
        act={act}
      />
      <section className="space-y-3">
        <h2 className="font-semibold">Remote activity</h2>
        <p className="text-sm text-muted-foreground">
          An uncertain outcome means the connection was lost. Check the result
          before repeating the action.
        </p>
        {!jobs.data?.jobs.length && (
          <p className="text-sm text-muted-foreground">No remote work yet.</p>
        )}
        {jobs.data?.jobs.map((job) => (
          <MachineJobRow key={job.id} job={job} busy={busy} act={act} />
        ))}
      </section>
    </div>
  );
}

type Action = (fn: () => Promise<unknown>) => Promise<void>;

function MachineJobRow({
  job,
  busy,
  act,
}: {
  job: MachineJob;
  busy: boolean;
  act: Action;
}) {
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const details = useQuery({
    queryKey: ["machine-job", job.id, job.state],
    enabled: open,
    queryFn: () => machineRequest<{ job: MachineJob }>(`/jobs/${job.id}`),
    retry: false,
  });
  return (
    <details
      className="rounded border border-border p-3 text-sm"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>
        {job.agent_id} · {job.state} ·{" "}
        {new Date(job.created * 1000).toLocaleString()}
      </summary>
      {open && (
        <>
          {details.error && (
            <p role="alert">Could not load the remote result.</p>
          )}
          <JobResult result={details.data?.job.result || null} />
          {["queued", "running", "uncertain"].includes(job.state) && (
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() =>
                void act(() => machineRequest(`/jobs/${job.id}/cancel`, "POST"))
              }
            >
              Request stop
            </Button>
          )}
          {job.state === "uncertain" && (
            <div className="mt-3 space-y-2">
              <p>
                Verify the outcome on the target before allowing this action to
                be repeated.
              </p>
              <label className="block">
                What did you verify?
                <input
                  className={field}
                  value={note}
                  onChange={(event) => setNote(event.target.value)}
                />
              </label>
              <div className="flex flex-wrap gap-2">
                {(["succeeded", "failed"] as const).map((outcome) => (
                  <Button
                    key={outcome}
                    size="sm"
                    variant="outline"
                    disabled={busy || !note.trim()}
                    onClick={() =>
                      void act(() =>
                        machineRequest(`/jobs/${job.id}/resolve`, "POST", {
                          outcome,
                          note,
                        }),
                      )
                    }
                  >
                    I verified: {outcome}
                  </Button>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </details>
  );
}
function GrantEditor({
  machine,
  agentId,
  busy,
  act,
  onSaved,
}: {
  machine: Machine;
  agentId?: string;
  busy: boolean;
  act: Action;
  onSaved: () => void;
}) {
  const [agent, setAgent] = useState(agentId || "");
  const [workspace, setWorkspace] = useState("");
  const [account, setAccount] = useState(false);
  const [shell, setShell] = useState(false);
  const [files, setFiles] = useState(true);
  const [desktop, setDesktop] = useState<MachineGrant["desktop"]>("none");
  const [task, setTask] = useState("");
  const profiles = useQuery({
    queryKey: ["machine-grants", agent],
    enabled: !!agent,
    queryFn: () =>
      machineRequest<{ grants: MachineGrant[] }>(
        `/grants/${encodeURIComponent(agent)}`,
      ),
    retry: false,
  });
  useEffect(() => {
    const saved = profiles.data?.grants.find(
      (grant) => grant.machine_id === machine.id,
    );
    setWorkspace(saved?.workspace || "");
    setAccount(saved?.scope === "account");
    setShell(saved?.shell || false);
    setFiles(saved?.files ?? true);
    setDesktop(saved?.desktop || "none");
  }, [profiles.data, machine.id, agent]);
  const roster = useQuery({
    queryKey: ["machine-roster"],
    queryFn: async () => {
      const res = await fetch("/api/society/agents");
      if (!res.ok) throw new Error("Could not load agents");
      return res.json() as Promise<{
        agents: { agent_id: string; name: string }[];
      }>;
    },
  });
  return (
    <form
      className="space-y-3 rounded-lg border border-border p-4"
      onSubmit={(e) => {
        e.preventDefault();
        void act(async () => {
          await machineRequest("/grants", "PUT", {
            agent_id: agent,
            machine_id: machine.id,
            workspace,
            scope: account ? "account" : "workspace",
            shell,
            files,
            desktop,
          } satisfies MachineGrant);
          onSaved();
        });
      }}
    >
      <h3 className="font-semibold">Access to {machine.name}</h3>
      {roster.error && <p role="alert">Could not load agents.</p>}
      <label className="block text-sm">
        Agent
        <select
          className={field}
          value={agent}
          onChange={(e) => setAgent(e.target.value)}
        >
          <option value="">Select an agent</option>
          {roster.data?.agents.map((row) => (
            <option key={row.agent_id} value={row.agent_id}>
              {row.name}
            </option>
          ))}
        </select>
      </label>
      <label className="block text-sm">
        Workspace on this computer
        <input
          className={field}
          value={workspace}
          onChange={(e) => setWorkspace(e.target.value)}
          placeholder={
            machine.capabilities.os === "windows"
              ? "C:\\Users\\name\\work"
              : "/home/name/work"
          }
          required
        />
      </label>
      <label className="flex gap-2 text-sm">
        <input
          type="checkbox"
          checked={account}
          onChange={(e) => {
            setAccount(e.target.checked);
            if (!e.target.checked) setShell(false);
          }}
        />
        Allow access across the connected user account
      </label>
      <label className="flex gap-2 text-sm">
        <input
          type="checkbox"
          checked={files}
          onChange={(e) => setFiles(e.target.checked)}
        />
        Read and write files
      </label>
      <label className="flex gap-2 text-sm">
        <input
          type="checkbox"
          checked={shell}
          disabled={!account && !machine.capabilities.workspace_isolation}
          onChange={(e) => setShell(e.target.checked)}
        />
        Run shell commands
      </label>
      {!account && !machine.capabilities.workspace_isolation && (
        <p className="text-xs text-muted-foreground">
          This computer cannot isolate shell commands to a folder. Shell access
          requires the account-wide profile.
        </p>
      )}
      <label className="block text-sm">
        Desktop access
        <select
          className={field}
          value={desktop}
          onChange={(e) =>
            setDesktop(e.target.value as MachineGrant["desktop"])
          }
        >
          <option value="none">Disabled</option>
          <option value="own" disabled={!machine.capabilities.isolated_desktop}>
            Own isolated desktop
          </option>
          <option value="attached" disabled={!machine.capabilities.desktop}>
            Visible user desktop
          </option>
        </select>
      </label>
      {desktop === "attached" && (
        <p className="text-sm text-muted-foreground">
          The agent can see your screen and use your mouse and keyboard. One
          agent at a time.
        </p>
      )}
      <Button disabled={busy || !agent || !workspace.trim()} type="submit">
        Save access profile
      </Button>
      <label className="block text-sm">
        Task on this computer
        <textarea
          className={field}
          value={task}
          onChange={(e) => setTask(e.target.value)}
          placeholder="What should this agent do here?"
        />
      </label>
      <Button
        type="button"
        variant="outline"
        disabled={
          busy ||
          !agent ||
          !task.trim() ||
          !profiles.data?.grants.some(
            (grant) => grant.machine_id === machine.id,
          )
        }
        onClick={() =>
          void act(async () => {
            await machineRequest(
              `/agents/${encodeURIComponent(agent)}/task`,
              "POST",
              { machine_id: machine.id, task },
            );
            setTask("");
          })
        }
      >
        Run task here
      </Button>
      <p className="text-xs text-muted-foreground">
        Progress appears in the agent's existing chat. Its normal host stays the
        same.
      </p>
    </form>
  );
}

function JobResult({ result }: { result: string | null }) {
  let image: string | undefined;
  let text = result || "No confirmed result received.";
  try {
    if (result) {
      const parsed = JSON.parse(result) as { output?: { image_png?: string } };
      image = parsed.output?.image_png;
      if (image && parsed.output) {
        delete parsed.output.image_png;
        text = JSON.stringify(parsed, null, 2);
      }
    }
  } catch {
    /* Display non-JSON results as plain text. */
  }
  return (
    <>
      {image && (
        <img
          className="mt-2 max-h-80 rounded border border-border object-contain"
          src={`data:image/png;base64,${image}`}
          alt="Remote desktop observation"
        />
      )}
      <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap break-words">
        {text}
      </pre>
    </>
  );
}

function AgentLocations({
  agentId,
  machines,
  busy,
  act,
}: {
  agentId?: string;
  machines: Machine[];
  busy: boolean;
  act: Action;
}) {
  const roster = useQuery({
    queryKey: ["machine-roster"],
    queryFn: async () => {
      const res = await fetch("/api/society/agents");
      if (!res.ok) throw new Error("Could not load agents");
      return res.json() as Promise<{
        agents: { agent_id: string; name: string; tier: string }[];
      }>;
    },
  });
  const placements = useQuery({
    queryKey: ["machine-placements"],
    refetchInterval: () => 5000 + Math.random() * 1000,
    queryFn: () =>
      machineRequest<{
        placements: { agent_id: string; host_id: string; moving: number }[];
        transfers: {
          id: string;
          agent_id: string;
          source: string;
          target: string;
          state: string;
          manifest: string;
        }[];
      }>("/placements"),
    retry: false,
  });
  const [selected, setSelected] = useState<string[]>(agentId ? [agentId] : []);
  const [target, setTarget] = useState("local");
  const [copyName, setCopyName] = useState("");
  const [copyFiles, setCopyFiles] = useState("");
  const [results, setResults] = useState<string[]>([]);
  const names = new Map(machines.map((machine) => [machine.id, machine.name]));
  names.set("local", "Hub computer");
  async function perform(copy: boolean) {
    setResults([]);
    for (const id of selected) {
      const name =
        roster.data?.agents.find((row) => row.agent_id === id)?.name || id;
      try {
        if (copy) {
          await machineRequest(
            `/agents/${encodeURIComponent(id)}/clone`,
            "POST",
            {
              name:
                selected.length === 1 && copyName.trim()
                  ? copyName.trim()
                  : `${name} copy`,
              files: copyFiles
                .split("\n")
                .map((path) => path.trim())
                .filter(Boolean),
            },
          );
          setResults((old) => [
            ...old,
            `${name}: independent copy created, paused on the hub. Assign its access profile before moving it.`,
          ]);
        } else {
          await machineRequest(
            `/agents/${encodeURIComponent(id)}/move`,
            "POST",
            { host_id: target },
          );
          setResults((old) => [
            ...old,
            `${name}: move queued for ${names.get(target) || target}. Follow transfer history for the result.`,
          ]);
        }
      } catch (error) {
        setResults((old) => [
          ...old,
          `${name}: ${error instanceof Error ? error.message : "Action failed"}`,
        ]);
      }
    }
  }
  return (
    <section className="space-y-3 rounded-lg border border-border p-4">
      <h2 className="font-semibold">Where agents run</h2>
      <p className="text-sm text-muted-foreground">
        Moving preserves identity, chat and routines. Work finishes first; files
        are checked before the host changes. API agents can run on connectors;
        CLI seats need their own host runtime.
      </p>
      {(roster.error || placements.error) && (
        <p role="alert">Could not load agent locations.</p>
      )}
      <div className="max-h-48 space-y-2 overflow-auto">
        {roster.data?.agents.map((agent) => {
          const placement = placements.data?.placements.find(
            (row) => row.agent_id === agent.agent_id,
          );
          return (
            <label
              className="flex items-center gap-2 text-sm"
              key={agent.agent_id}
            >
              <input
                type="checkbox"
                checked={selected.includes(agent.agent_id)}
                onChange={(e) =>
                  setSelected((old) =>
                    e.target.checked
                      ? [...old, agent.agent_id]
                      : old.filter((id) => id !== agent.agent_id),
                  )
                }
              />
              {agent.name}
              <span className="ml-auto text-muted-foreground">
                {placement?.moving
                  ? "Moving…"
                  : names.get(placement?.host_id || "local") || "Offline host"}
              </span>
            </label>
          );
        })}
      </div>
      <label className="block text-sm">
        Move to
        <select
          className={field}
          value={target}
          onChange={(e) => setTarget(e.target.value)}
        >
          <option value="local">Hub computer</option>
          {machines
            .filter((machine) => machine.capabilities.agent_runtime)
            .map((machine) => (
              <option
                key={machine.id}
                value={machine.id}
                disabled={!machine.online}
              >
                {machine.name}
                {machine.online ? "" : " (offline)"}
              </option>
            ))}
        </select>
      </label>
      <Button
        disabled={busy || !selected.length}
        onClick={() => void act(() => perform(false))}
      >
        Move selected agents
      </Button>
      {selected.length === 1 && (
        <label className="block text-sm">
          Name for an independent copy
          <input
            className={field}
            maxLength={40}
            value={copyName}
            onChange={(e) => setCopyName(e.target.value)}
          />
        </label>
      )}
      <label className="block text-sm">
        Files to include in copies (optional)
        <textarea
          className={field}
          value={copyFiles}
          onChange={(event) => setCopyFiles(event.target.value)}
          placeholder="Relative file paths, one per line"
        />
      </label>
      <Button
        variant="outline"
        disabled={busy || !selected.length}
        onClick={() => void act(() => perform(true))}
      >
        Create independent copies
      </Button>
      <ul aria-live="polite" className="space-y-1 text-sm">
        {results.map((result, index) => (
          <li key={index}>{result}</li>
        ))}
      </ul>
      {placements.data?.transfers.length ? (
        <details>
          <summary className="text-sm">Transfer history</summary>
          <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
            {placements.data.transfers.map((transfer) => (
              <li key={transfer.id}>
                {transfer.agent_id}: {transfer.state} ·{" "}
                {names.get(transfer.source) || transfer.source} →{" "}
                {names.get(transfer.target) || transfer.target}
                {transferError(transfer.manifest) && (
                  <p role="alert">{transferError(transfer.manifest)}</p>
                )}
                {["queued", "preparing"].includes(transfer.state) && (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={busy}
                    onClick={() =>
                      void act(() =>
                        machineRequest(
                          `/transfers/${transfer.id}/cancel`,
                          "POST",
                        ),
                      )
                    }
                  >
                    Cancel transfer
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}

function transferError(manifest: string): string {
  try {
    const value = JSON.parse(manifest) as { error?: unknown };
    return typeof value.error === "string" ? value.error : "";
  } catch {
    return "Transfer details could not be read.";
  }
}

function SshSetup({
  busy,
  act,
  onSaved,
}: {
  busy: boolean;
  act: Action;
  onSaved: () => void;
}) {
  const [host, setHost] = useState("");
  const [port, setPort] = useState(22);
  const [username, setUsername] = useState("");
  const [os, setOs] = useState("linux");
  const [key, setKey] = useState("");
  const [probe, setProbe] = useState<{
    host_key: string;
    fingerprint: string;
  } | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  return (
    <div className="space-y-3">
      <label className="block text-sm">
        Host
        <input
          className={field}
          value={host}
          onChange={(e) => {
            setHost(e.target.value);
            setProbe(null);
            setConfirmed(false);
          }}
        />
      </label>
      <label className="block text-sm">
        Port
        <input
          type="number"
          min={1}
          max={65535}
          className={field}
          value={port}
          onChange={(e) => {
            setPort(Number(e.target.value));
            setProbe(null);
            setConfirmed(false);
          }}
        />
      </label>
      <label className="block text-sm">
        SSH user
        <input
          className={field}
          value={username}
          onChange={(e) => setUsername(e.target.value)}
        />
      </label>
      <label className="block text-sm">
        Operating system
        <select
          className={field}
          value={os}
          onChange={(e) => setOs(e.target.value)}
        >
          <option value="linux">Linux</option>
          <option value="macos">macOS</option>
          <option value="windows">Windows</option>
        </select>
      </label>
      <Button
        variant="outline"
        disabled={busy || !host}
        onClick={() =>
          void act(async () => {
            setProbe(
              await machineRequest("/ssh/probe", "POST", { host, port }),
            );
            setConfirmed(false);
          })
        }
      >
        Read host fingerprint
      </Button>
      {probe && (
        <>
          <code className="block break-all text-xs">{probe.fingerprint}</code>
          <label className="flex gap-2 text-sm">
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(e) => setConfirmed(e.target.checked)}
            />
            I verified this fingerprint with the computer's SSH host key.
          </label>
        </>
      )}
      <label className="block text-sm">
        Private key
        <textarea
          className={field}
          autoComplete="off"
          spellCheck={false}
          value={key}
          onChange={(e) => setKey(e.target.value)}
          placeholder="Stored in the hub's credential store"
        />
      </label>
      <Button
        disabled={busy || !confirmed || !key || !username}
        onClick={() =>
          void act(async () => {
            await machineRequest("/ssh", "POST", {
              host,
              port,
              username,
              os,
              name: host,
              host_key: probe?.host_key,
              private_key: key,
            });
            setKey("");
            onSaved();
          })
        }
      >
        Connect SSH computer
      </Button>
    </div>
  );
}
