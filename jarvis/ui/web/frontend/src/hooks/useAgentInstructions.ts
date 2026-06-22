import { useCallback, useEffect, useState } from "react";

/**
 * Agent-instructions config from GET /api/settings/agent-instructions.
 *
 * This is the user's own standing-instructions file (an AGENTS.md / CLAUDE.md
 * equivalent), named after the assistant (`filename`, e.g. "Alex.md"). `content`
 * is the current text ("" when none), `exists` drives the Active/Empty badge, and
 * `template` is a starter the UI can load into an empty editor.
 */
export interface AgentInstructionsConfig {
  content: string;
  exists: boolean;
  filename: string;
  template: string;
  char_count: number;
}

export interface AgentInstructionsSaveResult extends AgentInstructionsConfig {
  ok: boolean;
  removed?: boolean;
  restart_required: boolean;
}

/**
 * Loads /api/settings/agent-instructions and exposes save()/reset(). Mirrors
 * useSystemPrompt. The file applies on the assistant's next turn (no restart),
 * so there is no restart plumbing here.
 */
export function useAgentInstructions() {
  const [config, setConfig] = useState<AgentInstructionsConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    setError(null);
    try {
      const res = await fetch("/api/settings/agent-instructions");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: AgentInstructionsConfig = await res.json();
      setConfig(data);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refetch();
  }, [refetch]);

  const save = useCallback(
    async (content: string): Promise<AgentInstructionsSaveResult> => {
      const isClearing = content.trim().length === 0;
      const res = await fetch(
        "/api/settings/agent-instructions",
        isClearing
          ? { method: "DELETE" }
          : {
              method: "PUT",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ content }),
            },
      );
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(body.detail ?? `HTTP ${res.status}`);
      }
      const result = body as AgentInstructionsSaveResult;
      setConfig(result);
      return result;
    },
    [],
  );

  const reset = useCallback(async (): Promise<AgentInstructionsSaveResult> => {
    const res = await fetch("/api/settings/agent-instructions", { method: "DELETE" });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(body.detail ?? `HTTP ${res.status}`);
    }
    const result = body as AgentInstructionsSaveResult;
    setConfig(result);
    return result;
  }, []);

  return { config, loading, error, refetch, save, reset };
}
