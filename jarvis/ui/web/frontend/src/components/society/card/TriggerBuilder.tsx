import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { BrandedSelect } from "@/components/ui/select";
import { useLocaleChunk, useT } from "@/i18n";

export const TRIGGER_GROUPS = {
  human: ["manual", "chat", "form"],
  time: ["every", "calendar", "cron", "after_delay", "at_time"],
  api: ["webhook", "mcp"],
  external: ["github", "linear", "gmail", "slack", "stripe"],
  stream: ["sse", "kafka", "rabbitmq", "mqtt", "redis"],
  system: ["file", "on_event", "workflow_activated", "workflow_failed"],
  internal: ["workflow", "event_hook"],
} as const;
type Group = keyof typeof TRIGGER_GROUPS;

/** Jarvis' trigger editor: choose an entry point, then its connection or input. */
export function TriggerBuilder({ onChange, initialValue, timeOnly = false }: {
  onChange: (value: Record<string, unknown> | null) => void;
  initialValue?: Record<string, unknown>;
  timeOnly?: boolean;
}) {
  const t = useT();
  useLocaleChunk("society");
  const [group, setGroup] = useState<Group>(() => {
    const kind = String(initialValue?.type ?? "every");
    return (Object.entries(TRIGGER_GROUPS).find(([, kinds]) => (kinds as readonly string[]).includes(kind))?.[0] ?? "time") as Group;
  });
  const [kind, setKind] = useState<string>(String(initialValue?.type ?? "every"));
  const [time, setTime] = useState(String(initialValue?.local_time ?? "08:00"));
  const [timezone, setTimezone] = useState(() => String(initialValue?.timezone ?? Intl.DateTimeFormat().resolvedOptions().timeZone));
  const [amount, setAmount] = useState(String(Number(initialValue?.interval_seconds ?? initialValue?.delay_seconds ?? 18000) / 3600));
  const [unit, setUnit] = useState("3600");
  const [expression, setExpression] = useState(String(initialValue?.expression ?? "0 8 * * 1-5"));
  const [date, setDate] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [topic, setTopic] = useState("");
  const [consumerGroup, setConsumerGroup] = useState("");
  const [path, setPath] = useState("");
  const [pattern, setPattern] = useState("*");
  const [recursive, setRecursive] = useState(false);
  const [eventName, setEventName] = useState("");
  const [filters, setFilters] = useState("{}");
  const [fields, setFields] = useState('{"message":{"label":"Message","kind":"text","required":true}}');
  const [upstreamKind, setUpstreamKind] = useState("task");
  const [upstreamId, setUpstreamId] = useState("");
  const [when, setWhen] = useState("succeeded");
  const upstream = useQuery({
    queryKey: ["routine-upstream", upstreamKind], enabled: kind === "workflow" || kind.startsWith("workflow_"), retry: false,
    queryFn: async () => {
      const response = await fetch(upstreamKind === "task" ? "/api/tasks" : "/api/workflows");
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const body = await response.json();
      return (body.tasks ?? body.workflows ?? []) as { id: string; title?: string; name?: string }[];
    },
  });
  const value = useMemo(() => {
    try {
      const conditions = JSON.parse(filters);
      if (!conditions || typeof conditions !== "object" || Array.isArray(conditions)) return null;
      if (kind === "every" || kind === "after_delay") {
        const seconds = Number(amount) * Number(unit);
        return seconds > 0 ? { type: kind, [kind === "every" ? "interval_seconds" : "delay_seconds"]: seconds } : null;
      }
      if (kind === "calendar") return { type: kind, local_time: time, timezone };
      if (kind === "cron") return { type: kind, expression, timezone };
      if (kind === "at_time") return date ? { type: kind, iso_timestamp: new Date(date).toISOString() } : null;
      if (kind === "webhook" || group === "external") return { type: "webhook", provider: group === "external" ? kind : "generic", conditions };
      if (kind === "on_event" || kind === "event_hook") return eventName ? { type: kind, event_name: eventName, ...(kind === "event_hook" ? { conditions } : { max_firings: null }) } : null;
      const source: Record<string, unknown> = { kind: kind.startsWith("workflow_") ? "workflow" : kind };
      if (group === "stream") {
        if (!endpoint || (kind !== "sse" && !topic)) return null;
        Object.assign(source, { endpoint, ...(kind !== "sse" ? { topic, group: consumerGroup } : {}) });
      }
      if (kind === "file") {
        if (!path) return null;
        Object.assign(source, { path, pattern, recursive });
      }
      if (kind === "form") source.form_fields = JSON.parse(fields);
      if (kind === "workflow" || kind.startsWith("workflow_")) {
        if (!upstreamId) return null;
        Object.assign(source, { upstream_id: upstreamId, upstream_kind: upstreamKind, when: kind === "workflow_activated" ? "activated" : kind === "workflow_failed" ? "failed" : when });
      }
      return { type: "source", source, conditions };
    } catch { return null; /* Invalid editor input is never submitted. */ }
  }, [kind, group, filters, amount, unit, time, timezone, expression, date, endpoint, topic, consumerGroup, path, pattern, recursive, fields, upstreamKind, upstreamId, when, eventName]);
  useEffect(() => {
    // Keep calendar restrictions and hook options the compact editor does not expose.
    onChange(value && initialValue?.type === value.type ? { ...initialValue, ...value } : value);
  }, [onChange, value, initialValue]);
  const field = "w-full rounded-md border border-border bg-background px-2 py-1.5 text-[12px] text-foreground";
  const label = (key: string) => t(`society.triggers.${key}`);
  const input = (name: string, current: string, set: (v: string) => void, type = "text") => <label className="block text-[11px] text-muted-foreground">{label(name)}<input type={type} aria-label={label(name)} value={current} onChange={(e) => set(e.target.value)} className={field} /></label>;
  return <div className="space-y-2" data-testid="trigger-builder">
    {!timeOnly && <BrandedSelect value={group} onValueChange={(next) => { setGroup(next as Group); setKind(TRIGGER_GROUPS[next as Group][0]); }} options={Object.keys(TRIGGER_GROUPS).map((key) => ({ value: key, label: label(`groups.${key}`) }))} testId="routine-trigger-group" ariaLabel={label("group")} />}
    <BrandedSelect value={kind} onValueChange={(next) => { setKind(next); if (next.startsWith("workflow_")) { setUpstreamKind("workflow"); setUpstreamId(""); } }} options={TRIGGER_GROUPS[group].filter((key) => !timeOnly || ["every", "calendar", "cron"].includes(key)).map((key) => ({ value: key, label: label(`kinds.${key}`) }))} testId="agent-routines-kind" ariaLabel={label("kind")} />
    {(kind === "every" || kind === "after_delay") && <div className="flex gap-2">{input("amount", amount, setAmount, "number")}<BrandedSelect ariaLabel={label("hours")} value={unit} onValueChange={setUnit} options={[{ value: "60", label: label("minutes") }, { value: "3600", label: label("hours") }, { value: "86400", label: label("days") }]} /></div>}
    {kind === "calendar" && input("time", time, setTime, "time")}
    {kind === "cron" && input("expression", expression, setExpression)}
    {(kind === "calendar" || kind === "cron") && input("timezone", timezone, setTimezone)}
    {kind === "at_time" && input("date", date, setDate, "datetime-local")}
    {group === "stream" && <>{input("endpoint", endpoint, setEndpoint)}{kind !== "sse" && <>{input("topic", topic, setTopic)}{input("consumer_group", consumerGroup, setConsumerGroup)}</>}<p className="text-[11px] text-muted-foreground">{label("credentials_hint")}</p></>}
    {kind === "file" && <>{input("path", path, setPath)}{input("pattern", pattern, setPattern)}<label className="text-[11px]"><input type="checkbox" checked={recursive} onChange={(e) => setRecursive(e.target.checked)} /> {label("recursive")}</label></>}
    {(kind === "on_event" || kind === "event_hook") && input("event_name", eventName, setEventName)}
    {kind === "form" && <label className="block text-[11px]">{label("form_fields")}<textarea className={field} aria-label={label("form_fields")} value={fields} onChange={(e) => setFields(e.target.value)} rows={4} /></label>}
    {(kind === "workflow" || kind.startsWith("workflow_")) && <><BrandedSelect ariaLabel={label("workflow")} value={upstreamKind} onValueChange={(next) => { setUpstreamKind(next); setUpstreamId(""); }} options={[{ value: "task", label: label("routine") }, { value: "workflow", label: label("workflow") }]} /><BrandedSelect value={upstreamId} onValueChange={setUpstreamId} options={(upstream.data ?? []).map((row) => ({ value: row.id, label: row.title ?? row.name ?? row.id }))} ariaLabel={label("upstream")} />{kind === "workflow" && <BrandedSelect ariaLabel={label("when.succeeded")} value={when} onValueChange={setWhen} options={["succeeded", "failed", "activated"].map((key) => ({ value: key, label: label(`when.${key}`) }))} />}{upstream.error ? <p role="alert">{label("upstream_unavailable")}</p> : null}</>}
    {!['every', 'calendar', 'cron', 'after_delay', 'at_time', 'on_event'].includes(kind) && <label className="block text-[11px]">{label("filters")}<textarea className={field} value={filters} onChange={(e) => setFilters(e.target.value)} aria-label={label("filters")} rows={2} /></label>}
  </div>;
}
