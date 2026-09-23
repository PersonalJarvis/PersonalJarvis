import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { DatabaseSync } from "node:sqlite";
import { webcrypto } from "node:crypto";
import worker from "../src/index.mjs";

const crypto = globalThis.crypto ?? webcrypto;
const base = "https://broker.example.test";
const b64url = (bytes) => Buffer.from(bytes).toString("base64url");

function database() {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(readFileSync(new URL("../schema.sql", import.meta.url), "utf8"));
  return {
    sqlite,
    prepare(query) {
      let values = [];
      return {
        bind(...args) { values = args; return this; },
        first() { return sqlite.prepare(query).get(...values) ?? null; },
        run() { return sqlite.prepare(query).run(...values); },
      };
    },
  };
}

function setup() {
  const db = database();
  const env = {
    DB: db,
    BROKER_BASE_URL: base,
    BROKER_ENCRYPTION_KEY: Buffer.from(crypto.getRandomValues(new Uint8Array(32))).toString("base64"),
    PUBLISHER_ASANA_OAUTH_CLIENT_ID: "asana-test-client",
    PUBLISHER_ASANA_OAUTH_CLIENT_SECRET: "asana-test-secret",
    PUBLISHER_SLACK_OAUTH_CLIENT_ID: "slack-test-client",
  };
  async function call(path, method = "POST", body) {
    return worker.fetch(new Request(`${base}${path}`, {
      method,
      ...(body ? { body: JSON.stringify(body), headers: { "Content-Type": "application/json" } } : {}),
    }), env);
  }
  return { call, db, env };
}

async function begin(call) {
  const verifier = b64url(crypto.getRandomValues(new Uint8Array(32)));
  const challenge = b64url(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier))));
  const response = await call("/start", "POST", {
    provider: "asana", challenge, loopback_uri: "http://127.0.0.1:43123/oauth/broker",
    client_state: b64url(crypto.getRandomValues(new Uint8Array(32))),
  });
  return { response, verifier, data: await response.json() };
}

test("broker persists a browser-bound grant without redirecting credentials", async () => {
  const { call, db, env } = setup();
  const originalFetch = globalThis.fetch;
  const tokenBodies = [];
  globalThis.fetch = async (_url, options) => {
    tokenBodies.push(Object.fromEntries(options.body));
    return Response.json({ access_token: `dummy-access-${tokenBodies.length}`, refresh_token: `dummy-refresh-${tokenBodies.length}`, expires_in: 1 });
  };
  try {
    const started = await begin(call);
    assert.equal(started.response.status, 200);
    assert.equal(started.data.expires_in, 300);
    assert.equal(JSON.stringify(started.data).includes("asana-test-secret"), false);
    const auth = new URL(started.data.authorization_url);
    assert.equal(auth.hostname, "app.asana.com");
    assert.equal(auth.searchParams.get("redirect_uri"), `${base}/callback`);
    const callback = await call(`/callback?state=${auth.searchParams.get("state")}&code=dummy-code`, "GET");
    assert.equal(callback.status, 302);
    const local = new URL(callback.headers.get("Location"));
    assert.equal(local.origin, "http://127.0.0.1:43123");
    assert.equal(local.searchParams.has("access_token"), false);
    assert.equal(local.searchParams.has("refresh_token"), false);
    assert.equal(local.href.includes("dummy-access"), false);
    assert.equal((await call(`/callback?state=${auth.searchParams.get("state")}&code=replayed`, "GET")).status, 400);
    const proof = { flow_id: started.data.flow_id, verifier: started.verifier };
    assert.equal((await call("/redeem", "POST", proof)).status, 403);
    const wrong = await call("/redeem", "POST", { ...proof, verifier: b64url(crypto.getRandomValues(new Uint8Array(32))), handoff_code: local.searchParams.get("code") });
    assert.equal(wrong.status, 403);
    const redeemed = await call("/redeem", "POST", { ...proof, handoff_code: local.searchParams.get("code") });
    assert.equal(redeemed.status, 200);
    const grant = await redeemed.json();
    assert.equal(grant.access_token, "dummy-access-1");
    assert.equal((await call("/redeem", "POST", { ...proof, handoff_code: local.searchParams.get("code") })).status, 410);
    const protectedRow = db.sqlite.prepare("SELECT payload FROM grants").get();
    assert.equal(protectedRow.payload.includes("dummy-refresh"), false);
    assert.equal((await call("/refresh", "POST", { provider: "discord", handle: grant.refresh_handle })).status, 403);
    const refreshed = await call("/refresh", "POST", { provider: "asana", handle: grant.refresh_handle });
    assert.equal(refreshed.status, 200);
    assert.equal((await refreshed.json()).access_token, "dummy-access-2");
    assert.equal(tokenBodies[1].refresh_token, "dummy-refresh-1");
    env.PUBLISHER_ASANA_OAUTH_CLIENT_ID = "replacement-client";
    assert.equal((await call("/refresh", "POST", { provider: "asana", handle: grant.refresh_handle })).status, 409);
    env.PUBLISHER_ASANA_OAUTH_CLIENT_ID = "asana-test-client";
    assert.equal((await call("/disconnect", "POST", { provider: "asana", handle: grant.refresh_handle })).status, 200);
    assert.equal((await call("/refresh", "POST", { provider: "asana", handle: grant.refresh_handle })).status, 401);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("invalid callbacks, cancellation and remote handoff URLs fail closed", async () => {
  const { call } = setup();
  const started = await begin(call);
  assert.equal(started.response.status, 200);
  assert.equal((await call("/cancel", "POST", { flow_id: started.data.flow_id, verifier: started.verifier })).status, 200);
  assert.equal((await call("/redeem", "POST", { flow_id: started.data.flow_id, verifier: started.verifier })).status, 410);
  const verifier = b64url(crypto.getRandomValues(new Uint8Array(32)));
  const challenge = b64url(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier))));
  const bad = await call("/start", "POST", { provider: "asana", challenge, loopback_uri: "https://attacker.example/oauth/broker", client_state: verifier });
  assert.equal(bad.status, 422);
  assert.equal((await bad.text()).includes("attacker.example"), false);
});

test("provider denial consumes state without minting a grant", async () => {
  const { call, db } = setup();
  const started = await begin(call);
  const state = new URL(started.data.authorization_url).searchParams.get("state");
  const denied = await call(`/callback?state=${state}&error=access_denied`, "GET");
  assert.equal(denied.status, 302);
  const local = new URL(denied.headers.get("Location"));
  assert.equal(local.searchParams.get("error"), "access_denied");
  assert.equal(local.searchParams.has("code"), false);
  assert.equal((await call(`/callback?state=${state}&code=replay`, "GET")).status, 400);
  const redeemed = await call("/redeem", "POST", { flow_id: started.data.flow_id, verifier: started.verifier });
  assert.deepEqual(await redeemed.json(), { state: "error", error: "denied" });
  assert.equal(db.sqlite.prepare("SELECT COUNT(*) AS n FROM grants").get().n, 0);
});

test("registration and issuing-client binding fail closed", async () => {
  const { call, env } = setup();
  const started = await begin(call);
  assert.equal(started.response.status, 200);
  delete env.PUBLISHER_ASANA_OAUTH_CLIENT_SECRET;
  assert.equal((await begin(call)).response.status, 503);
  assert.equal((await call("/refresh", "POST", { provider: "asana", handle: "x".repeat(64) })).status, 401);
});

test("unknown host, oversized request and provider error details are suppressed", async () => {
  const { call, env } = setup();
  const offHost = await worker.fetch(new Request("https://evil.example/healthz"), env);
  assert.equal(offHost.status, 400);
  const oversized = await call("/start", "POST", { provider: "asana", padding: "A".repeat(5000) });
  assert.equal(oversized.status, 422);
  assert.equal((await oversized.text()).includes("A".repeat(100)), false);
});

test("Slack public PKCE uses user scopes and a nested user grant without a secret", async () => {
  const { call } = setup();
  const verifier = b64url(crypto.getRandomValues(new Uint8Array(32)));
  const pkce = b64url(new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier))));
  const originalFetch = globalThis.fetch;
  let sent;
  globalThis.fetch = async (_url, options) => {
    sent = Object.fromEntries(options.body);
    return Response.json({ ok: true, authed_user: { access_token: "dummy-user-access", refresh_token: "dummy-user-refresh", expires_in: 3600 } });
  };
  try {
    const start = await call("/start", "POST", { provider: "slack", challenge: pkce, loopback_uri: "http://127.0.0.1:43123/oauth/broker", client_state: verifier });
    assert.equal(start.status, 200);
    const flow = await start.json();
    const auth = new URL(flow.authorization_url);
    assert.equal(auth.searchParams.has("user_scope"), true);
    assert.equal(auth.searchParams.has("scope"), false);
    assert.equal(auth.searchParams.get("code_challenge_method"), "S256");
    const callback = await call(`/callback?state=${auth.searchParams.get("state")}&code=dummy-code`, "GET");
    assert.equal(callback.status, 302);
    assert.equal(sent.client_secret, undefined);
    assert.equal(sent.client_id, "slack-test-client");
    const handoff = new URL(callback.headers.get("Location")).searchParams.get("code");
    const grant = await call("/redeem", "POST", { flow_id: flow.flow_id, verifier, handoff_code: handoff });
    assert.equal((await grant.json()).access_token, "dummy-user-access");
  } finally {
    globalThis.fetch = originalFetch;
  }
});
