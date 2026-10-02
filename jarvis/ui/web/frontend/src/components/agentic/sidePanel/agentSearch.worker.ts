import { env } from "onnxruntime-web/wasm";
import wasmUrl from "onnxruntime-web/ort-wasm-simd-threaded.wasm?url";
import wasmModuleUrl from "onnxruntime-web/ort-wasm-simd-threaded.mjs?url";
import { createAgentEncoder, type AgentEncoder } from "./agentSearchEncoder";
import {
  AGENT_SEARCH_MODEL,
  AGENT_SEARCH_REVISION,
  cosineSimilarity,
  relevantAgentMatches,
  type AgentSearchRequest,
  type AgentSearchResponse,
} from "./agentSearch";

// Download public model assets only. Terminal text never leaves this worker.
// One CPU thread and a serial queue keep inference off the UI and avoid sharing
// a native session concurrently (AP-24). Terminating the worker releases it.
env.wasm.wasmPaths = { wasm: wasmUrl, mjs: wasmModuleUrl };

let extractor: AgentEncoder | null = null;
let latestId = 0;
let pending: Extract<AgentSearchRequest, { type: "search" }> | null = null;
let running = false;
const vectors = new Map<string, number[]>();
const MAX_CACHED_TEXTS = 512;

function send(message: AgentSearchResponse): void {
  self.postMessage(message);
}

async function embed(text: string): Promise<number[]> {
  const cached = vectors.get(text);
  if (cached) return cached;
  const vector = await extractor!.embed(text);
  if (vectors.size >= MAX_CACHED_TEXTS) vectors.delete(vectors.keys().next().value!);
  vectors.set(text, vector);
  return vector;
}

async function loadAsset(file: string): Promise<Uint8Array> {
  const url = `https://huggingface.co/${AGENT_SEARCH_MODEL}/resolve/${AGENT_SEARCH_REVISION}/${file}`;
  let cache: Cache | undefined;
  try {
    cache = await caches.open("jarvis-terminal-search-v1");
    const cached = await cache.match(url);
    if (cached) return new Uint8Array(await cached.arrayBuffer());
  } catch {
    // CacheStorage can be unavailable in restricted WebViews. Search still
    // works online; this warning contains no task text or response body.
    console.warn("Terminal search model cache is unavailable");
  }
  const response = await fetch(url, { credentials: "omit", referrerPolicy: "no-referrer" });
  if (!response.ok) throw new Error("Search model download failed");
  if (cache) {
    try { await cache.put(url, response.clone()); }
    catch { console.warn("Terminal search model could not be cached"); }
  }
  return new Uint8Array(await response.arrayBuffer());
}

async function drain(): Promise<void> {
  if (running) return;
  running = true;
  try {
    while (pending) {
      const request = pending;
      pending = null;
      try {
        if (!extractor) {
          send({ type: "loading", id: request.id });
          extractor = await createAgentEncoder(loadAsset);
        }
        if (request.id !== latestId) continue;
        send({ type: "searching", id: request.id });
        const queryVector = await embed(request.query);
        const matches = [];
        for (const document of request.documents) {
          let score = 0;
          for (const text of document.texts) {
            if (request.id !== latestId) break;
            score = Math.max(score, cosineSimilarity(queryVector, await embed(text)));
          }
          if (request.id !== latestId) break;
          matches.push({ id: document.id, score });
        }
        if (request.id === latestId) send({ type: "result", id: request.id, matches: relevantAgentMatches(matches) });
      } catch {
        // The UI reports failure and destroys this worker before retrying, so
        // a failed native session is never reused. Do not log private queries.
        send({ type: "error", id: latestId });
        pending = null;
      }
    }
  } finally {
    running = false;
  }
}

self.onmessage = (event: MessageEvent<AgentSearchRequest>) => {
  latestId = event.data.id;
  pending = event.data.type === "search" ? event.data : null;
  void drain();
};
