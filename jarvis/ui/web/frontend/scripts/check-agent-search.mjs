// Opt-in model qualification, not an ordinary unit test: downloads public model
// weights once, then runs on one CPU thread with synthetic terminal tasks.
// Run from the frontend directory: node scripts/check-agent-search.mjs
import assert from "node:assert/strict";
import { env, pipeline } from "@huggingface/transformers";
import { AGENT_SEARCH_MODEL, AGENT_SEARCH_REVISION, cosineSimilarity, relevantAgentMatches } from "../src/components/agentic/sidePanel/agentSearch.ts";

env.allowLocalModels = false;
const extractor = await pipeline("feature-extraction", AGENT_SEARCH_MODEL, {
  revision: AGENT_SEARCH_REVISION,
  dtype: "q8",
  device: "cpu",
  session_options: { intraOpNumThreads: 1, interOpNumThreads: 1 },
});
const tasks = [
  ["auth", "Repair plugin OAuth token refresh and reconnect after the login expires."],
  ["voice", "Prevent voice calls from hanging up unexpectedly during a conversation."],
  ["performance", "Investigate high CPU usage and slow application startup."],
  ["theme", "Improve the blue shimmer effect around the selected terminal."],
  ["video", "Replace the README demo with a new screen recording."],
  ["security", "Audit the codebase for security vulnerabilities."],
];
const examples = [
  ["The one fixing expired sign-ins for integrations", "auth"],
  ["Find the task about conversations ending on their own", "voice"],
  ["Reduce processor load and make the app open faster", "performance"],
  ["Which task updates the showcase movie?", "video"],
  ["Repair plugin OAuth token refresh and reconnect after the login expires.", "auth"],
  ["Chocolate cake recipe with strawberries", null],
  ["Book a seaside hotel for our holiday", null],
  ["Grow tomatoes on the balcony", null],
  ["Fix database migrations", null],
  ["Read incoming email and draft a reply", null],
  ["Build a shopping cart for the website", null],
  ["Install PostgreSQL database", null],
  ["Configure printer drivers", null],
  // i18n-allow: multilingual search quality fixtures.
  ["Das Gespr\u00e4ch bricht von alleine ab", "voice"],
  ["Abgelaufene Logins bei Plugins reparieren", "auth"],
  ["Die Anwendung startet langsam und verbraucht zu viel Rechenleistung", "performance"],
  ["Ein Rezept f\u00fcr Erdbeerkuchen", null],
];
try {
  const failures = [];
  const cache = new Map();
  const embed = async (text) => {
    if (!cache.has(text)) cache.set(text, Array.from((await extractor(text, { pooling: "mean", normalize: true })).data));
    return cache.get(text);
  };
  for (const [query, expected] of examples) {
    const queryVector = await embed(query);
    const scores = [];
    for (const [id, text] of tasks) scores.push({ id, score: cosineSimilarity(queryVector, await embed(text)) });
    const results = relevantAgentMatches(scores);
    console.log(JSON.stringify({ query, expected, results, best: scores.sort((a, b) => b.score - a.score)[0] }));
    if ((results[0]?.id ?? null) !== expected || results.some(({ id }) => id !== expected)) {
      failures.push({ query, expected, actual: results });
    }
  }
  assert.deepEqual(failures, []);
  console.log("AGENT_SEMANTIC_SEARCH_OK");
} finally {
  await extractor.dispose();
}
