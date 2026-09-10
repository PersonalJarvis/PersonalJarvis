/** Display-only cache. Authentication and model changes remain server-owned. */
import { z } from "zod";
import type { AgentChatCatalog, AgentConnectionRow, CuratedModel } from "@/lib/agentChatApi";
import type { SocietyProviderRow } from "@/lib/societyApi";

export const MODEL_MENU_SNAPSHOT_KEY = "jarvis.society.model-menu.v1";
const MAX_BYTES = 2_000_000;
const text = z.string().max(1024);
const model = z.object({ id: text, label: text, efforts: z.array(text).optional(), note: text.optional() });
const snapshotSchema = z.object({
  version: z.literal(1), savedAt: z.number().finite().positive(),
  catalog: z.object({
    default_cwd: z.unknown().transform(() => ""), shell: z.unknown().transform(() => ""),
    providers: z.array(z.object({
      id: text, label: text, family: text, runner: text,
      models_source: z.enum(["live", "curated"]), curated_models: z.array(model).max(20_000),
      default_model: text, keyless: z.boolean(), native_resume: z.boolean(),
      effort_levels: z.array(text), default_effort: text,
      permission_modes: z.array(z.object({ id: text, label: text, description: text })),
      default_permission_mode: text, cli_installed: z.boolean().nullable(), agent: text.optional(),
    })).max(100),
  }),
  connections: z.array(z.object({
    jarvis: text, label: text.optional(), key_set: z.boolean(), api_key_set: z.boolean().optional(),
    oauth_connected: z.boolean().optional(), is_active_brain: z.boolean().default(false), keyless: z.boolean().optional(),
  })).max(100),
  providers: z.array(z.object({
    id: text, label: text, family: text, runner: text, subscription: z.boolean(), keyless: z.boolean(),
    platform: text.nullable(), accounts: z.array(z.object({
      id: text, label: text, connected: z.boolean(), mode: text,
      message: z.unknown().transform(() => ""), email: z.unknown().transform(() => null),
      tier: z.unknown().transform(() => null), warning: z.unknown().transform(() => null),
    })).max(100),
  })).max(100),
  live: z.record(z.array(model).max(20_000)),
  liveUpdatedAt: z.record(z.number().finite().nonnegative()).default({}),
});

export interface ModelMenuSnapshot {
  version: 1;
  savedAt: number;
  catalog: AgentChatCatalog;
  connections: AgentConnectionRow[];
  providers: SocietyProviderRow[];
  live: Record<string, CuratedModel[]>;
  liveUpdatedAt?: Record<string, number>;
}

let memory: ModelMenuSnapshot | null | undefined;
let lastSource: ModelMenuSnapshot | undefined;

export function readModelMenuSnapshot(): ModelMenuSnapshot | null {
  if (memory !== undefined) return memory;
  memory = null;
  try {
    const raw = localStorage.getItem(MODEL_MENU_SNAPSHOT_KEY);
    if (!raw || raw.length > MAX_BYTES) return null;
    const result = snapshotSchema.safeParse(JSON.parse(raw));
    if (result.success && result.data.savedAt <= Date.now() + 60_000) memory = result.data;
  } catch {
    // Optional display cache: blocked storage or invalid JSON uses live discovery.
  }
  return memory;
}

export function writeModelMenuSnapshot(snapshot: ModelMenuSnapshot): void {
  if (lastSource?.catalog === snapshot.catalog && lastSource.connections === snapshot.connections
    && lastSource.providers === snapshot.providers && lastSource.savedAt === snapshot.savedAt
    && Object.keys(lastSource.live).length === Object.keys(snapshot.live).length
    && Object.keys(snapshot.live).every((id) => lastSource!.live[id] === snapshot.live[id]
      && lastSource!.liveUpdatedAt?.[id] === snapshot.liveUpdatedAt?.[id])) return;
  // Strip unknown fields, emails, paths and diagnostic messages before storage.
  const result = snapshotSchema.safeParse(snapshot);
  if (!result.success) return;
  lastSource = snapshot;
  memory = result.data;
  try {
    const raw = JSON.stringify(result.data);
    if (raw.length <= MAX_BYTES) localStorage.setItem(MODEL_MENU_SNAPSHOT_KEY, raw);
  } catch {
    // Private mode / full storage: the in-memory snapshot still works.
  }
}

export function clearModelMenuSnapshot(): void {
  memory = undefined;
  lastSource = undefined;
  try { localStorage.removeItem(MODEL_MENU_SNAPSHOT_KEY); } catch {
    // Storage can be disabled; clearing memory is sufficient in that case.
  }
}
