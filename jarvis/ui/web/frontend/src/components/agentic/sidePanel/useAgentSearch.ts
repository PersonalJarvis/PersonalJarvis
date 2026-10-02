import { useEffect, useMemo, useRef, useState } from "react";
import type { AgentSearchDocument, AgentSearchMatch, AgentSearchRequest, AgentSearchResponse } from "./agentSearch";
import { lexicalAgentMatches, mergeAgentMatches, needsSemanticSearch } from "./agentLexicalSearch";

type SearchStatus = "idle" | "loading" | "searching" | "ready" | "error";
interface SearchState {
  key: string;
  status: SearchStatus;
  matches: AgentSearchMatch[];
}

export function useAgentSearch(query: string, documents: AgentSearchDocument[]) {
  const trimmed = query.trim();
  // Polling changes timestamps frequently, but only changed task text requires
  // new embeddings. A stable content key also invalidates old-scope results.
  const key = JSON.stringify([trimmed, documents]);
  const lexical = useMemo(() => {
    const [currentQuery, currentDocuments] = JSON.parse(key) as [string, AgentSearchDocument[]];
    return lexicalAgentMatches(currentQuery, currentDocuments);
  }, [key]);
  const semanticEnabled = needsSemanticSearch(trimmed, documents, lexical);
  const worker = useRef<Worker | null>(null);
  const sequence = useRef(0);
  const [retry, setRetry] = useState(0);
  const [state, setState] = useState<SearchState>({ key: "", status: "idle", matches: [] });

  useEffect(() => () => {
    worker.current?.terminate();
    worker.current = null;
  }, []);

  useEffect(() => {
    const id = ++sequence.current;
    let timeout: ReturnType<typeof setTimeout> | undefined;
    let idleTimeout: ReturnType<typeof setTimeout> | undefined;
    let disposed = false;
    const cancel: AgentSearchRequest = { type: "cancel", id };
    worker.current?.postMessage(cancel);
    const [searchQuery, searchDocuments] = JSON.parse(key) as [string, AgentSearchDocument[]];
    if (!semanticEnabled || !searchQuery || searchDocuments.length === 0) {
      // An empty search restores the original list immediately and releases RAM.
      worker.current?.terminate();
      worker.current = null;
      return;
    }

    const fail = () => {
      if (disposed || sequence.current !== id) return;
      clearTimeout(timeout);
      worker.current?.terminate();
      worker.current = null;
      setState({ key, status: "error", matches: [] });
    };
    const timer = setTimeout(() => {
      try {
        if (!worker.current) {
          worker.current = new Worker(new URL("./agentSearch.worker.ts", import.meta.url), { type: "module" });
        }
        setState({ key, status: "searching", matches: [] });
        worker.current.onmessage = (event: MessageEvent<AgentSearchResponse>) => {
          const answer = event.data;
          if (disposed || answer.id !== id || sequence.current !== id) return;
          if (answer.type === "error") { fail(); return; }
          if (answer.type === "result") {
            clearTimeout(timeout);
            setState({ key, status: "ready", matches: answer.matches });
            // The IDE can stay mounted behind another view. Keep the visible
            // results, but release the model's RAM after a minute without work.
            idleTimeout = setTimeout(() => {
              worker.current?.terminate();
              worker.current = null;
            }, 60_000);
          } else {
            setState({ key, status: answer.type, matches: [] });
          }
        };
        worker.current.onerror = fail;
        worker.current.onmessageerror = fail;
        timeout = setTimeout(fail, 180_000);
        const request: AgentSearchRequest = { type: "search", id, query: searchQuery, documents: searchDocuments };
        worker.current.postMessage(request);
      } catch {
        // Worker creation can be unavailable in restricted or older WebViews.
        fail();
      }
    }, 300);
    return () => {
      disposed = true;
      clearTimeout(timer);
      clearTimeout(timeout);
      clearTimeout(idleTimeout);
    };
  }, [key, retry, semanticEnabled]);

  const status: SearchStatus = !trimmed ? "idle" : !semanticEnabled || documents.length === 0 ? "ready" : state.key === key ? state.status : "searching";
  return {
    status,
    matches: mergeAgentMatches(lexical, semanticEnabled && state.key === key && status === "ready" ? state.matches : []),
    retry: () => setRetry((value) => value + 1),
  };
}
