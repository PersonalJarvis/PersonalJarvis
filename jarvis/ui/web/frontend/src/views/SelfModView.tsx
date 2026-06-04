// SelfModView (Phase 7.6) — read-only Audit-UI für die Self-Mod-Pipeline.
// Plan-§7.6 Drei Tabs: History (Audit-Log), Mutable (Allowlist), Backups.
//
// SelfModView ist read-only für Mutationen — Plan-§Out-of-Scope: kein
// "Edit Setting"-Button. Mutation läuft ausschließlich via Voice/Chat.
//
// Sensitive Pfade werden serverseitig redacted (Plan-§AP-2 Defense-in-Depth)
// und zusätzlich clientseitig mit einer "***"-Badge visuell markiert.

import { useEffect, useMemo, useState } from "react";

type AuditEvent = {
  ts: string;
  audit_id: string;
  source: string;
  requested_by: string;
  path: string;
  old_value: unknown;
  new_value: unknown;
  ok: boolean;
  rolled_back: boolean;
  error: string | null;
  voice_confirmation?: {
    transcript: string;
    confidence: number;
    timestamp_utc: string;
  };
};

type MutableSpec = {
  path: string;
  pydantic_model_name: string;
  field_name: string;
  risk_tier: "safe" | "ask";
  needs_restart: boolean;
  description: string;
  sensitive: boolean;
};

type BackupRef = {
  filename: string;
  path: string;
  timestamp: string;
  size_bytes: number;
  age_seconds: number;
};

type Tab = "history" | "mutable" | "backups";

const SENSITIVE_MARKERS = [
  "api_key",
  "api-key",
  "password",
  "token",
  "secret",
  "credential",
  "bearer",
  "oauth",
];

function isSensitivePath(path: string): boolean {
  const lower = path.toLowerCase();
  if (
    lower.startsWith("security.") ||
    lower.startsWith("mcp_server.") ||
    lower.startsWith("harness.")
  ) {
    return true;
  }
  return SENSITIVE_MARKERS.some((m) => lower.includes(m));
}

function RedactedBadge() {
  return (
    <span
      style={{
        display: "inline-block",
        padding: "0 6px",
        borderRadius: 4,
        background: "#666",
        color: "#fff",
        fontSize: 11,
        fontWeight: 600,
      }}
      title="Wert maskiert (Plan-§AP-2 Defense-in-Depth)"
    >
      ***
    </span>
  );
}

function ValueCell({ path, value }: { path: string; value: unknown }) {
  if (value === null || value === undefined) {
    return <span style={{ color: "#999" }}>—</span>;
  }
  if (isSensitivePath(path)) {
    return <RedactedBadge />;
  }
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return <code style={{ fontSize: 12 }}>{text}</code>;
}

// ----------------------------------------------------------------------
// History-Tab
// ----------------------------------------------------------------------

function AuditFilters({
  filter,
  onChange,
}: {
  filter: { actor?: string; action?: string; success_only: boolean };
  onChange: (f: { actor?: string; action?: string; success_only: boolean }) => void;
}) {
  return (
    <div style={{ display: "flex", gap: 12, marginBottom: 12 }}>
      <select
        value={filter.actor ?? ""}
        onChange={(e) =>
          onChange({ ...filter, actor: e.target.value || undefined })
        }
      >
        <option value="">Alle Actors</option>
        <option value="hauptjarvis">Hauptjarvis</option>
        <option value="openclaw">OpenClaw</option>
        <option value="user">User</option>
        <option value="system">System</option>
      </select>
      <label>
        <input
          type="checkbox"
          checked={filter.success_only}
          onChange={(e) =>
            onChange({ ...filter, success_only: e.target.checked })
          }
        />
        Nur erfolgreiche
      </label>
    </div>
  );
}

function AuditLogTable() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [filter, setFilter] = useState<{
    actor?: string;
    action?: string;
    success_only: boolean;
  }>({ success_only: false });
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<AuditEvent | null>(null);

  useEffect(() => {
    setLoading(true);
    const params = new URLSearchParams({ limit: "50" });
    if (filter.actor) params.set("actor", filter.actor);
    if (filter.action) params.set("action", filter.action);
    if (filter.success_only) params.set("success_only", "true");
    fetch(`/api/self-mod/audit?${params.toString()}`)
      .then((r) => r.json())
      .then((data) => setEvents(data.events ?? []))
      .catch(() => setEvents([]))
      .finally(() => setLoading(false));
  }, [filter]);

  return (
    <div>
      <AuditFilters filter={filter} onChange={setFilter} />
      {loading && <p>Lade …</p>}
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ borderBottom: "1px solid #444" }}>
            <th style={{ textAlign: "left", padding: 4 }}>Timestamp</th>
            <th style={{ textAlign: "left", padding: 4 }}>Actor</th>
            <th style={{ textAlign: "left", padding: 4 }}>Path</th>
            <th style={{ textAlign: "left", padding: 4 }}>Old → New</th>
            <th style={{ textAlign: "left", padding: 4 }}>Outcome</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e) => (
            <tr
              key={e.audit_id}
              style={{ borderBottom: "1px solid #222", cursor: "pointer" }}
              onClick={() => setSelected(e)}
            >
              <td style={{ padding: 4, fontSize: 12 }}>{e.ts}</td>
              <td style={{ padding: 4, fontSize: 12 }}>{e.requested_by}</td>
              <td style={{ padding: 4, fontSize: 12 }}>{e.path}</td>
              <td style={{ padding: 4, fontSize: 12 }}>
                <ValueCell path={e.path} value={e.old_value} />
                {" → "}
                <ValueCell path={e.path} value={e.new_value} />
              </td>
              <td style={{ padding: 4, fontSize: 12 }}>
                {e.ok ? (
                  <span style={{ color: "#4f4" }}>OK</span>
                ) : e.rolled_back ? (
                  <span style={{ color: "#f80" }}>ROLLED BACK</span>
                ) : (
                  <span style={{ color: "#f44" }}>{e.error ?? "FAIL"}</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {selected && (
        <AuditEventDetail event={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}

function AuditEventDetail({
  event,
  onClose,
}: {
  event: AuditEvent;
  onClose: () => void;
}) {
  const sensitive = isSensitivePath(event.path);
  // Server hat schon redacted; clientseitig markieren wir das visuell.
  const display = useMemo(() => {
    const copy: AuditEvent = { ...event };
    if (sensitive) {
      copy.old_value = "***";
      copy.new_value = "***";
    }
    return copy;
  }, [event, sensitive]);
  return (
    <aside
      style={{
        position: "fixed",
        right: 0,
        top: 0,
        bottom: 0,
        width: 480,
        background: "#1a1a1a",
        padding: 16,
        boxShadow: "-4px 0 12px rgba(0,0,0,0.5)",
        overflow: "auto",
      }}
    >
      <button onClick={onClose} style={{ marginBottom: 12 }}>
        × Schließen
      </button>
      <h3>Audit-Event</h3>
      {sensitive && (
        <p style={{ color: "#fa0" }}>
          ⚠ Sensitive Pfad — Werte serverseitig redacted (Plan-§AP-2).
        </p>
      )}
      <pre style={{ fontSize: 11, background: "#000", padding: 8 }}>
        {JSON.stringify(display, null, 2)}
      </pre>
    </aside>
  );
}

// ----------------------------------------------------------------------
// Mutable-Tab
// ----------------------------------------------------------------------

function MutableSpecsList() {
  const [specs, setSpecs] = useState<MutableSpec[]>([]);
  useEffect(() => {
    fetch("/api/self-mod/mutable")
      .then((r) => r.json())
      .then((d) => setSpecs(d.specs ?? []))
      .catch(() => setSpecs([]));
  }, []);
  return (
    <ul style={{ listStyle: "none", padding: 0 }}>
      {specs.map((s) => (
        <li
          key={s.path}
          style={{ padding: 8, borderBottom: "1px solid #333" }}
        >
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <code>{s.path}</code>
            <span
              style={{
                padding: "0 6px",
                borderRadius: 4,
                background: s.risk_tier === "safe" ? "#070" : "#770",
                color: "#fff",
                fontSize: 11,
              }}
            >
              {s.risk_tier.toUpperCase()}
            </span>
            {s.needs_restart && (
              <span style={{ fontSize: 11, color: "#f80" }}>
                ↻ Restart
              </span>
            )}
          </div>
          <div style={{ fontSize: 12, color: "#aaa", marginTop: 4 }}>
            {s.description}
          </div>
        </li>
      ))}
    </ul>
  );
}

// ----------------------------------------------------------------------
// Backups-Tab
// ----------------------------------------------------------------------

function BackupsList() {
  const [backups, setBackups] = useState<BackupRef[]>([]);
  useEffect(() => {
    fetch("/api/self-mod/backups")
      .then((r) => r.json())
      .then((d) => setBackups(d.backups ?? []))
      .catch(() => setBackups([]));
  }, []);
  return (
    <table style={{ width: "100%", borderCollapse: "collapse" }}>
      <thead>
        <tr style={{ borderBottom: "1px solid #444" }}>
          <th style={{ textAlign: "left", padding: 4 }}>Filename</th>
          <th style={{ textAlign: "left", padding: 4 }}>Timestamp</th>
          <th style={{ textAlign: "left", padding: 4 }}>Size</th>
          <th style={{ textAlign: "left", padding: 4 }}>Age</th>
        </tr>
      </thead>
      <tbody>
        {backups.map((b) => (
          <tr key={b.filename} style={{ borderBottom: "1px solid #222" }}>
            <td style={{ padding: 4, fontSize: 12 }}>
              <code>{b.filename}</code>
            </td>
            <td style={{ padding: 4, fontSize: 12 }}>{b.timestamp}</td>
            <td style={{ padding: 4, fontSize: 12 }}>
              {(b.size_bytes / 1024).toFixed(1)} KB
            </td>
            <td style={{ padding: 4, fontSize: 12 }}>
              {(b.age_seconds / 3600).toFixed(1)} h
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ----------------------------------------------------------------------
// Main View
// ----------------------------------------------------------------------

export default function SelfModView() {
  const [tab, setTab] = useState<Tab>("history");
  return (
    <div style={{ padding: 16 }}>
      <h2>Self-Modification</h2>
      <p style={{ fontSize: 13, color: "#aaa" }}>
        Read-only Sicht auf Audit-Trail, mutierbare Settings und Backups.
        Mutationen erfolgen ausschließlich via Voice/Chat — diese View
        zeigt nur, was Jarvis an sich selbst geändert hat.
      </p>
      <nav style={{ display: "flex", gap: 8, marginBottom: 16 }}>
        {(["history", "mutable", "backups"] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            style={{
              padding: "6px 12px",
              background: tab === t ? "#444" : "#222",
              color: "#fff",
              border: "none",
              borderRadius: 4,
              cursor: "pointer",
            }}
          >
            {t === "history" ? "History" : t === "mutable" ? "Settings" : "Backups"}
          </button>
        ))}
      </nav>
      {tab === "history" && <AuditLogTable />}
      {tab === "mutable" && <MutableSpecsList />}
      {tab === "backups" && <BackupsList />}
    </div>
  );
}
