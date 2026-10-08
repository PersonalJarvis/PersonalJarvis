/**
 * "Keep working when this PC closes" — the one opt-in that makes closing the
 * app safe for running coding agents: on quit, every IDE workspace with an
 * agent running on this machine moves to the chosen computer (folder,
 * uncommitted edits and conversation included) and carries on there in tmux.
 * Windows computers are not offered: without tmux their agents stop with the
 * app's connection.
 */
import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { useT } from "@/i18n";
import type { Computer } from "@/lib/computersApi";
import { SettingsSelect } from "@/views/settings/SettingsLayout";
import { Row } from "./surface";

const ENDPOINT = "/api/agentic-ide/offload-on-quit";
/** The select's value for "no computer"; a computer id never takes this shape. */
const OFF = "__off__";

export function KeepWorking({ computers }: { computers: Computer[] }) {
  const t = useT();
  const [value, setValue] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    void fetch(ENDPOINT)
      .then((res) => (res.ok ? res.json() : null))
      .then((body: { computer_id?: string | null } | null) => {
        if (!alive) return;
        setValue(body?.computer_id ?? null);
        setLoaded(true);
      })
      .catch(() => {
        if (alive) setLoaded(true);
      });
    return () => {
      alive = false;
    };
  }, []);

  async function choose(next: string | null) {
    setSaving(true);
    setError(null);
    try {
      const res = await fetch(ENDPOINT, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ computer_id: next }),
      });
      if (!res.ok) throw new Error(`${res.status}`);
      setValue(next);
    } catch {
      setError(t("computers.keep_failed"));
    } finally {
      setSaving(false);
    }
  }

  // A Windows computer has no tmux: agents moved there at quit would stop with
  // this PC's connection, so it is no target (the backend refuses it as well).
  const isWindows = (computer: Computer) => computer.facts?.os_id === "windows";
  const hasWindows = computers.some(isWindows);
  const options = [
    { value: OFF, label: t("computers.keep_off") },
    ...computers
      .filter((computer) => computer.health.status !== "provisioning" && !isWindows(computer))
      .map((computer) => ({ value: computer.id, label: computer.name })),
  ];

  return (
    <Row
      title={
        <span className="inline-flex items-center gap-2">
          {t("computers.keep_title")}
          {saving && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" aria-hidden />}
        </span>
      }
      description={t("computers.keep_body")}
      status={hasWindows ? t("computers.keep_windows_note") : undefined}
      control={
        <SettingsSelect
          value={value ?? OFF}
          options={options}
          onValueChange={(next) => void choose(next === OFF ? null : next)}
          ariaLabel={t("computers.keep_title")}
          disabled={!loaded || saving}
          testId="computers-keep-working"
          className="h-8 min-w-[12rem] justify-between border border-border bg-secondary/40"
        />
      }
    >
      {error && <p role="alert" className="pb-1 text-sm text-destructive">{error}</p>}
    </Row>
  );
}
