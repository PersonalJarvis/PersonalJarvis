import {actionShape, allowedUrl, domains, pairingCode, retryDelay, serverOrigin} from "./policy.mjs";
import {inspectPage} from "./page.mjs";

const sessions = new Map();
let socket = null, connecting = false, retryTimer = null, heartbeat = null;
let attempt = 0, budget = [], status = "Not connected";
const ready = chrome.storage.local.setAccessLevel({accessLevel: "TRUSTED_CONTEXTS"});
function requireTransport(session) {
  if (session.revoked || (session.transport && (session.transport !== socket || session.transport.readyState !== 1))) {
    throw new Error("The authorizing connection ended");
  }
}
const cdp = (session, method, params = {}, cleanup = false) => {
  if (!cleanup) requireTransport(session);
  return chrome.debugger.sendCommand({tabId: session.tabId}, method, params);
};
const safeError = "Chrome operation stopped. Check the visible tab, website permissions, and manual control.";

async function rules(session, enabled) {
  await chrome.declarativeNetRequest.updateSessionRules({removeRuleIds: [session.ruleId], addRules: enabled ? [{
    id: session.ruleId, priority: 1, action: {type: "block"}, condition: {
      regexFilter: "^https?://", tabIds: [session.tabId], resourceTypes: ["main_frame", "sub_frame"],
      ...(session.domains.length ? {excludedRequestDomains: session.domains} : {}),
    },
  }] : []});
}

async function visible(session) {
  requireTransport(session);
  if (await chrome.idle.queryState(15) === "locked") throw new Error("Unlock the desktop first");
  const tab = await chrome.tabs.get(session.tabId);
  const window = await chrome.windows.get(tab.windowId);
  if (!tab.active || !window.focused || window.state === "minimized") {
    throw new Error("Bring the Jarvis Chrome tab to the foreground");
  }
  if (!allowedUrl(tab.url || "about:blank", session.domains, true)) throw new Error("Website is not permitted");
  return tab;
}

async function evaluate(session, mode, input = {}) {
  const cleanup = mode === "clear";
  if (!cleanup) await visible(session);
  const tree = await cdp(session, "Page.getFrameTree", {}, cleanup);
  const world = await cdp(session, "Page.createIsolatedWorld", {
    frameId: tree.frameTree.frame.id, worldName: "jarvis-owned-tab",
  }, cleanup);
  const result = await cdp(session, "Runtime.evaluate", {
    expression: `(${inspectPage.toString()})(${JSON.stringify(mode)},${JSON.stringify(input)})`,
    contextId: world.executionContextId, returnByValue: true, awaitPromise: false,
  }, cleanup);
  if (result.exceptionDetails || !result.result?.value) throw new Error("Page inspection unavailable");
  return result.result.value;
}

async function attach(session) {
  await chrome.debugger.attach({tabId: session.tabId}, "1.3");
  session.attached = true;
  await cdp(session, "Page.enable");
  // Suppress script-created popups. Links to another window use the same owned tab.
  await cdp(session, "Page.addScriptToEvaluateOnNewDocument", {
    source: "Object.defineProperty(window,'open',{value:()=>null,writable:false,configurable:false});",
  });
  await cdp(session, "Page.setDownloadBehavior", {behavior: "deny"});
}

async function release(session, closeTab = false) {
  sessions.delete(session.id);
  if (session.attached) {
    try { await evaluate(session, "clear"); }
    catch { /* The owned tab may already be closed or detached. */ }
    session.attached = false;
    try { await chrome.debugger.detach({tabId: session.tabId}); }
    catch { /* A closed tab or Chrome's Stop button already detached it. */ }
  }
  await rules(session, false);
  if (closeTab) {
    try { await chrome.tabs.remove(session.tabId); }
    catch { /* Only this extension's owned tab is eligible for closing. */ }
  }
  await rememberTabs();
}

async function rememberTabs() {
  await chrome.storage.session.set({owned_tabs: [...sessions.values()].map(({tabId, ruleId}) => ({tabId, ruleId}))});
}

async function releaseAll() {
  const results = await Promise.allSettled([...sessions.values()].map((session) => release(session)));
  if (results.some((result) => result.status === "rejected")) status = "Some tabs could not be released; close them in Chrome";
}

function publish(session, value) {
  if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({...value, session_id: session.id}));
}

async function guard(session) {
  const value = await evaluate(session, "guard");
  if (!allowedUrl(value.url, session.domains, true)) throw new Error("Website is not permitted");
  return value;
}

async function navigate(session, url) {
  if (!allowedUrl(url, session.domains)) throw new Error("Website is not permitted");
  requireTransport(session);
  const tab = await chrome.tabs.get(session.tabId);
  await chrome.windows.update(tab.windowId, {focused: true});
  requireTransport(session);
  await chrome.tabs.update(session.tabId, {url, active: true});
  return {ok: true};
}

async function act(session, args) {
  const manual = args.manual === true;
  if (manual !== session.manual) throw new Error("Browser control changed");
  const [op, values] = actionShape(args.action, manual);
  if (manual) {
    // Login input must remain entirely in Chrome. Only manual navigation is bridged.
    if (op === "navigate") return navigate(session, values.url);
    throw new Error("Use the visible Chrome tab for manual input and login");
  }
  const before = await guard(session);
  if (before.blocked || before.url !== args.expected_url || before.identity !== (args.expected_identity || "")) {
    throw new Error("Page or account changed; observe again");
  }
  if (!args.observation_id || session.observation !== args.observation_id) throw new Error("Observation expired");
  // Consume the observation before dispatch. A timeout cannot replay an uncertain action.
  session.observation = "";
  if (op === "navigate") return navigate(session, values.url);
  if (op === "wait") return {ok: true};
  if (op === "scroll") {
    await cdp(session, "Input.dispatchMouseEvent", {type: "mouseWheel", x: 150, y: 150, deltaX: 0, deltaY: values.dy});
    return {ok: true};
  }
  const host = new URL(before.url).hostname;
  if ((host === "x.com" || host.endsWith(".x.com")) && !before.identity) {
    throw new Error("Cannot verify the active X account");
  }
  const targetArgs = {index: values.index, observation_id: args.observation_id,
    expected_url: before.url, expected_identity: before.identity};
  const target = await evaluate(session, "target", targetArgs);
  if (target.href && !allowedUrl(target.href, session.domains)) throw new Error("Target website is not permitted");
  if (target.target && target.target !== "_self") {
    if (op !== "click" || !target.href) throw new Error("New windows are unsupported");
    return navigate(session, target.href);
  }
  if (op === "input" && !target.editable) throw new Error("Target is not editable");
  await evaluate(session, "pointer", target);
  await cdp(session, "Input.dispatchMouseEvent", {type: "mouseMoved", x: target.x, y: target.y});
  const freshTarget = await evaluate(session, "target", targetArgs);
  if (freshTarget.x !== target.x || freshTarget.y !== target.y) throw new Error("Element moved before click");
  const finalGuard = await guard(session);
  if (finalGuard.blocked || finalGuard.url !== before.url || finalGuard.identity !== before.identity) {
    throw new Error("Page or account changed before click");
  }
  await cdp(session, "Input.dispatchMouseEvent", {type: "mousePressed", x: target.x, y: target.y, button: "left", clickCount: 1});
  await cdp(session, "Input.dispatchMouseEvent", {type: "mouseReleased", x: target.x, y: target.y, button: "left", clickCount: 1});
  if (op === "input") {
    // Never type after a focus click changed the page or revealed authentication.
    const current = await guard(session);
    if (current.blocked || current.url !== before.url || current.identity !== before.identity) throw new Error("Page changed before input");
    await evaluate(session, "target", {...targetArgs, focused: true});
    await cdp(session, "Input.insertText", {text: values.text});
  }
  return {ok: true};
}

export async function dispatch(message, transport = socket) {
  const args = message.args || {}, id = args.session_id;
  if (typeof id !== "string" || !/^[a-f0-9]{32}$/.test(id)) throw new Error("Invalid session");
  if (message.op === "ensure") {
    if (sessions.has(id)) throw new Error("Session already exists");
    const approved = domains(args.allowed_domains);
    const tab = await chrome.tabs.create({url: "about:blank", active: true});
    if (transport && (transport !== socket || transport.readyState !== 1)) {
      await chrome.tabs.remove(tab.id);
      throw new Error("The authorizing connection ended");
    }
    const session = {id, tabId: tab.id, ruleId: tab.id + 1, domains: approved, manual: false,
      attached: false, observation: "", transport, revoked: false};
    sessions.set(id, session);
    try {
      await chrome.windows.update(tab.windowId, {focused: true});
      await rules(session, true);
      await attach(session);
      await rememberTabs();
    } catch (error) { await release(session, true); throw error; }
    return {generation: id, target: String(tab.id), tabs: [{id: String(tab.id), url: "about:blank", title: "Jarvis"}], url: "about:blank"};
  }
  const session = sessions.get(id);
  if (!session) throw new Error("Session was disconnected; start a new task");
  if (message.op === "shutdown") { await release(session, true); return {ok: true}; }
  if (message.op === "takeover") {
    session.observation = "";
    if (args.enabled) {
      session.manual = true;
      await rules(session, false);
      if (session.attached) {
        try { await evaluate(session, "clear"); }
        catch { /* The page may be navigating; debugger detachment still ends capture. */ }
        session.attached = false;
        await chrome.debugger.detach({tabId: session.tabId});
      }
    } else {
      await visible(session);
      await rules(session, true);
      await attach(session);
      session.manual = false;
    }
    return {manual: session.manual, message: session.manual ? "Sign in directly in Chrome. The model and preview are paused." : ""};
  }
  if (message.op === "action") return act(session, args);
  if (session.manual) throw new Error("Chrome is under manual control");
  if (message.op === "observe") {
    const observation = await evaluate(session, "observe");
    if (!allowedUrl(observation.url, session.domains, true)) throw new Error("Website is not permitted");
    session.observation = observation.observation_id || "";
    return observation;
  }
  if (message.op === "snapshot") {
    const before = await guard(session);
    if (before.blocked) return {state: {url: before.url, login_required: true, preview_paused: true}};
    const frame = await cdp(session, "Page.captureScreenshot", {format: "jpeg", quality: 55, captureBeyondViewport: false});
    const after = await guard(session);
    if (after.blocked || after.url !== before.url) return {state: {preview_paused: true}};
    return {frame: {data: frame.data, format: "jpeg", width: before.width, height: before.height},
      state: {url: before.url, target: String(session.tabId), login_required: false, preview_paused: false}};
  }
  throw new Error("Unsupported operation");
}

function scheduleReconnect() {
  clearTimeout(retryTimer);
  const delay = retryDelay(attempt++);
  retryTimer = setTimeout(() => { void connect(); }, delay);
  // Chrome can suspend a worker when disconnected. One alarm owns the same budget.
  void chrome.alarms.create("jarvis-reconnect", {when: Date.now() + Math.max(30000, delay)});
}

async function connect() {
  await ready;
  if (connecting || socket) return;
  const {connection} = await chrome.storage.local.get("connection");
  if (!connection) return;
  const now = Date.now();
  budget = budget.filter((value) => value > now - 60000);
  if (budget.length >= 4) { scheduleReconnect(); return; }
  budget.push(now);
  connecting = true;
  try {
    const origin = serverOrigin(connection.server_url);
    const current = new WebSocket(`${origin.replace("http:", "ws:")}/api/society/browser/chrome/connect`);
    socket = current;
    current.onopen = () => {
      connecting = false;
      status = "Connecting";
      current.send(JSON.stringify({profile_id: connection.profile_id, token: connection.token, installation_id: connection.installation_id}));
      heartbeat = setInterval(() => {
        if (current.readyState === WebSocket.OPEN) current.send(JSON.stringify({kind: "ping"}));
      }, 20000);
    };
    // Serialize commands. No action survives the connection that authorized it.
    let chain = Promise.resolve();
    current.onmessage = (event) => {
      if (event.data.length > 1048576) { current.close(); return; }
      let message;
      try { message = JSON.parse(event.data); } catch { current.close(); return; }
      if (message.kind === "pong" || message.kind === "connected") {
        status = "Connected"; attempt = 0; return;
      }
      if (message.kind !== "command") { current.close(); return; }
      status = "Connected"; attempt = 0;
      chain = chain.then(async () => {
        if (socket !== current || current.readyState !== WebSocket.OPEN) return;
        try {
          const result = await dispatch(message, current);
          if (socket === current && current.readyState === WebSocket.OPEN) {
            current.send(JSON.stringify({kind: "response", id: message.id, ok: true, result}));
          }
        } catch {
          if (socket === current && current.readyState === WebSocket.OPEN) {
            current.send(JSON.stringify({kind: "response", id: message.id, ok: false, error: safeError}));
          }
        }
      });
    };
    current.onerror = () => current.close();
    current.onclose = () => {
      if (socket !== current) return;
      socket = null; connecting = false; clearInterval(heartbeat);
      status = "Disconnected; waiting for Jarvis";
      for (const session of sessions.values()) session.revoked = true;
      // Revoke immediately: the next input step fails even if dispatch is in flight.
      void releaseAll().finally(scheduleReconnect);
    };
  } catch {
    connecting = false; socket = null; status = "Connection unavailable"; scheduleReconnect();
  }
}

chrome.debugger.onDetach.addListener((source) => {
  for (const session of sessions.values()) {
    if (session.tabId === source.tabId && session.attached) {
      session.attached = false;
      publish(session, {kind: "disconnected"});
      void release(session);
    }
  }
});
chrome.tabs.onRemoved.addListener((tabId) => {
  for (const session of sessions.values()) if (session.tabId === tabId) {
    publish(session, {kind: "disconnected"}); void release(session);
  }
});
chrome.alarms.onAlarm.addListener((alarm) => { if (alarm.name === "jarvis-reconnect") void connect(); });
chrome.runtime.onStartup.addListener(() => { void connect(); });
chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (sender.id !== chrome.runtime.id || sender.url !== chrome.runtime.getURL("popup.html")) return false;
  const handle = async () => {
    await ready;
    if (message.op === "status") return {status};
    if (message.op === "disconnect") {
      await chrome.storage.local.remove("connection");
      clearTimeout(retryTimer); await chrome.alarms.clear("jarvis-reconnect");
      socket?.close(); await releaseAll(); status = "Not connected";
      return {ok: true, status};
    }
    if (message.op === "pair") {
      const server_url = serverOrigin(message.server_url);
      const code = pairingCode(message.code);
      const stored = await chrome.storage.local.get("installation_id");
      const installation_id = stored.installation_id || crypto.randomUUID();
      await chrome.storage.local.set({installation_id});
      const response = await fetch(`${server_url}/api/society/browser/chrome/pair`, {
        method: "POST", headers: {"Content-Type": "application/json"}, credentials: "omit",
        body: JSON.stringify({code, installation_id}), signal: AbortSignal.timeout(10000),
      });
      if (!response.ok) throw new Error("Pairing failed. Generate a new code in Jarvis.");
      const value = await response.json();
      if (typeof value.profile_id !== "string" || typeof value.token !== "string") throw new Error("Invalid pairing response");
      await chrome.storage.local.set({connection: {server_url, installation_id, profile_id: value.profile_id, token: value.token}});
      if (socket) socket.close(); else await connect();
      return {ok: true, status: "Paired. Connecting to Jarvis."};
    }
    throw new Error("Unsupported request");
  };
  void handle().then(reply, () => reply({ok: false, error: "Connection failed. Check the local address and generate a new pairing code."}));
  return true;
});

// A service-worker restart never reclaims an old tab or replays an old command.
void (async () => {
  await ready;
  const {owned_tabs = []} = await chrome.storage.session.get("owned_tabs");
  for (const tab of owned_tabs) {
    try { await chrome.debugger.detach({tabId: tab.tabId}); }
    catch { /* The previous process already detached its debugger. */ }
    await chrome.declarativeNetRequest.updateSessionRules({removeRuleIds: [tab.ruleId]});
  }
  await chrome.storage.session.remove("owned_tabs");
  await connect();
})();
