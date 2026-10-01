import test from "node:test";
import assert from "node:assert/strict";
import {inspectPage} from "../../jarvis/assets/chrome-extension/page.mjs";

const store = {}, calls = [], tabs = new Map();
let nextTab = 40, idleState = "active", onTarget = null;
let page = {url: "https://example.com/", identity: "", blocked: false, observation_id: "fresh"};
const event = () => ({addListener() {}});
const storage = {setAccessLevel: async () => {}, get: async (key) => ({[key]: store[key]}),
  set: async (value) => Object.assign(store, value), remove: async (key) => { delete store[key]; }};
globalThis.chrome = {
  storage: {local: storage, session: storage},
  idle: {queryState: async () => idleState},
  tabs: {
    create: async (value) => { const tab = {id: nextTab++, windowId: 1, active: true, url: value.url}; tabs.set(tab.id, tab); return tab; },
    get: async (id) => { if (!tabs.has(id)) throw new Error("missing"); return tabs.get(id); },
    update: async (id, value) => { Object.assign(tabs.get(id), value); calls.push(["navigate", id, value]); },
    remove: async (id) => { tabs.delete(id); calls.push(["remove", id]); },
    onRemoved: event(),
  },
  windows: {get: async () => ({focused: true, state: "normal"}), update: async () => {}},
  debugger: {
    attach: async (target) => { calls.push(["attach", target.tabId]); },
    detach: async (target) => { calls.push(["detach", target.tabId]); },
    sendCommand: async (target, method, args) => {
      calls.push([method, target.tabId, args]);
      if (method === "Page.getFrameTree") return {frameTree: {frame: {id: "main"}}};
      if (method === "Page.createIsolatedWorld") return {executionContextId: 1};
      if (method === "Runtime.evaluate") {
        const mode = JSON.parse(args.expression.match(/\}\)\(("[^"]+")/)[1]);
        if (mode === "target") {
          onTarget?.();
          return {result: {value: {x: 10, y: 10, href: "", target: "", editable: true}}};
        }
        return {result: {value: {...page}}};
      }
      if (method === "Page.captureScreenshot") return {data: "fake-jpeg"};
      return {};
    }, onDetach: event(),
  },
  declarativeNetRequest: {updateSessionRules: async (value) => { calls.push(["rules", value]); }},
  runtime: {id: "test-extension", getURL: (path) => `chrome-extension://test-extension/${path}`, onMessage: event(), onStartup: event()},
  alarms: {onAlarm: event(), create: async () => {}, clear: async () => {}},
};
const {dispatch} = await import("../../jarvis/assets/chrome-extension/background.mjs");

async function session(domains = ["example.com"]) {
  const id = nextTab.toString(16).padStart(32, "0");
  const result = await dispatch({op: "ensure", args: {session_id: id, allowed_domains: domains}});
  const call = (op, args = {}) => dispatch({op, args: {session_id: id, ...args}});
  return {id, tabId: Number(result.target), call};
}

test("ensure creates a visible owned tab and installs redirect-blocking rules before debugger attachment", async () => {
  const start = calls.length;
  const current = await session();
  assert.equal(tabs.get(current.tabId).active, true);
  const created = calls.slice(start);
  const rule = created.find(([name]) => name === "rules")[1].addRules[0];
  assert.deepEqual(rule.condition.tabIds, [current.tabId]);
  assert.deepEqual(rule.condition.resourceTypes, ["main_frame", "sub_frame"]);
  assert.deepEqual(rule.condition.excludedRequestDomains, ["example.com"]);
  assert.ok(created.findIndex(([name]) => name === "rules") < created.findIndex(([name]) => name === "attach"));
  await current.call("shutdown");
  assert.equal(tabs.has(current.tabId), false);
});

test("actions never attach to unrelated tabs or accept arbitrary CDP", async () => {
  await assert.rejects(dispatch({op: "action", args: {session_id: "a".repeat(32), action: {navigate: {url: "https://example.com"}}, tabId: 1}}));
  const current = await session();
  await assert.rejects(current.call("Runtime.evaluate", {expression: "untrusted"}));
  await assert.rejects(current.call("action", {action: {evaluate: {expression: "untrusted"}}}));
  assert.equal(calls.some(([op, id]) => op === "attach" && id === 1), false);
  await current.call("shutdown");
});

test("stale account or page invalidates previously approved actions", async () => {
  const current = await session();
  page = {url: "https://example.com/", identity: "@original", blocked: false, observation_id: "fresh"};
  await current.call("observe");
  page.identity = "@different";
  const before = calls.length;
  await assert.rejects(current.call("action", {action: {click: {index: 1}}, expected_url: page.url,
    expected_identity: "@original", observation_id: "fresh"}));
  assert.equal(calls.slice(before).some(([op]) => op.startsWith("Input.")), false);
  await current.call("shutdown");
});

test("a dispatched action consumes its observation and cannot be replayed", async () => {
  const current = await session();
  page = {url: "https://example.com/", identity: "", blocked: false, observation_id: "fresh"};
  await current.call("observe");
  const action = {action: {click: {index: 1}}, expected_url: page.url, observation_id: "fresh"};
  await current.call("action", action);
  const before = calls.length;
  await assert.rejects(current.call("action", action));
  assert.equal(calls.slice(before).some(([op]) => op.startsWith("Input.")), false);
  await current.call("shutdown");
});

test("an account switch during target lookup stops the click before mousePressed", async () => {
  const current = await session();
  page = {url: "https://example.com/", identity: "@original", blocked: false, observation_id: "fresh"};
  await current.call("observe");
  onTarget = () => { page.identity = "@changed"; };
  const before = calls.length;
  try {
    await assert.rejects(current.call("action", {action: {click: {index: 1}}, expected_url: page.url,
      expected_identity: "@original", observation_id: "fresh"}));
    assert.equal(calls.slice(before).some(([op, _id, args]) => op === "Input.dispatchMouseEvent" && args.type === "mousePressed"), false);
  } finally { onTarget = null; await current.call("shutdown"); }
});

test("a locked desktop blocks inspection and screenshots without opening another browser", async () => {
  const current = await session();
  idleState = "locked";
  const before = calls.length;
  try {
    await assert.rejects(current.call("observe"));
    await assert.rejects(current.call("snapshot"));
    assert.equal(calls.slice(before).some(([op]) => op === "Page.captureScreenshot" || op === "Runtime.evaluate"), false);
  } finally { idleState = "active"; await current.call("shutdown"); }
});

test("manual takeover detaches and blocks all model observations and credentials", async () => {
  const current = await session();
  await current.call("takeover", {enabled: true});
  assert.ok(calls.some(([op, id]) => op === "detach" && id === current.tabId));
  await assert.rejects(current.call("observe"));
  await assert.rejects(current.call("snapshot"));
  await assert.rejects(current.call("action", {manual: true, action: {text: {text: "private"}}}));
  assert.equal(calls.some(([op]) => op === "Input.insertText"), false);
  await current.call("shutdown");
});

test("authentication pages never produce screenshots", async () => {
  const current = await session();
  page = {url: "https://example.com/login", blocked: true};
  const before = calls.length;
  const result = await current.call("snapshot");
  assert.equal(result.state.login_required, true);
  assert.equal(result.frame, undefined);
  assert.equal(calls.slice(before).some(([op]) => op === "Page.captureScreenshot"), false);
  await current.call("shutdown");
});

test("DOM password detection returns before reading page text or input values", () => {
  const password = {type: "password", getBoundingClientRect: () => ({width: 20, height: 20}),
    get value() { throw new Error("A password value must never be read"); }};
  globalThis.location = new URL("https://example.com/form");
  globalThis.getComputedStyle = () => ({visibility: "visible", display: "block"});
  globalThis.document = {querySelectorAll: () => [password], get body() { throw new Error("Page text was read during login"); }};
  assert.deepEqual(inspectPage("observe"), {blocked: true, url: "https://example.com/form"});
  delete globalThis.document; delete globalThis.location; delete globalThis.getComputedStyle;
});

test("pointer cleanup does not inspect the login page", () => {
  let removed = false;
  globalThis.document = {getElementById: () => ({remove: () => { removed = true; }}),
    querySelectorAll: () => { throw new Error("No login inspection during pointer removal"); }};
  assert.deepEqual(inspectPage("clear"), {});
  assert.equal(removed, true);
  delete globalThis.document;
});
