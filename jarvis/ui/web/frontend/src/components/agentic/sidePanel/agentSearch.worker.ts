import { env, pipeline, type FeatureExtractionPipeline } from "@huggingface/transformers";
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
env.allowLocalModels = false;
env.backends.onnx.wasm!.numThreads = 1;
env.backends.onnx.wasm!.proxy = false;

let extractor: FeatureExtractionPipeline | null = null;
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
  const output = await extractor!(text, { pooling: "mean", normalize: true });
  const vector = Array.from(output.data, Number);
  if (vectors.size >= MAX_CACHED_TEXTS) vectors.delete(vectors.keys().next().value!);
  vectors.set(text, vector);
  return vector;
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
          extractor = await pipeline("feature-extraction", AGENT_SEARCH_MODEL, {
            revision: AGENT_SEARCH_REVISION,
            dtype: "q8",
            device: "wasm",
          });
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
