// Fixed functions evaluated in a fresh isolated world of the one owned tab.
// Arguments are JSON data. No model-provided expression or selector is evaluated.
export function inspectPage(mode, input = {}) {
  if (mode === "clear") {
    document.getElementById("jarvis-browser-pointer")?.remove();
    return {};
  }
  const visible = (element) => {
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden" && style.display !== "none";
  };
  const sensitive = (element) => /password|one.?time.?code|cc-number|cc-csc|otp|secret|token|verification.?code|security.?code/i.test(
    `${element.type || ""} ${element.autocomplete || ""} ${element.name || ""} ${element.id || ""}`,
  );
  const login = /\/(login|signin|sign-in|oauth|authorize|password|two_factor|challenge)(\/|$|\?)/i.test(location.pathname)
    || /accounts\.(google|microsoft)\./i.test(location.hostname)
    || [...document.querySelectorAll("input")].some((element) => sensitive(element) && visible(element));
  const state = globalThis.__jarvisOwnedTab ||= {elements: [], observation: ""};
  if (login) {
    state.elements = [];
    state.observation = "";
    return {blocked: true, url: location.origin + location.pathname};
  }
  const redact = (value) => String(value || "").replace(/(?:sk-|ghp_|xox[baprs]-)[A-Za-z0-9_-]{12,}/g, "[redacted]")
    .replace(/\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b/g, "[redacted]");
  const account = document.querySelector('[data-testid="SideNav_AccountSwitcher_Button"]');
  const identity = account ? (account.innerText.match(/@[A-Za-z0-9_]+/) || [""])[0] : "";
  if (mode === "guard") return {url: location.href, identity, blocked: false, width: innerWidth, height: innerHeight};
  if (mode === "observe") {
    state.elements = [...document.querySelectorAll("a,button,input,textarea,select,[role=button],[contenteditable=true]")]
      .filter((element) => visible(element) && !sensitive(element)).slice(0, 200);
    state.observation = crypto.randomUUID();
    // Read visible text nodes only; form values and hidden application data stay in Chrome.
    const texts = [];
    const walker = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_TEXT);
    let node, size = 0;
    while ((node = walker.nextNode()) && size < 18000) {
      const parent = node.parentElement;
      if (!parent || parent.closest("input,textarea,script,style,[contenteditable=true]") || !visible(parent)) continue;
      const text = node.textContent.trim();
      if (text) { texts.push(text); size += text.length; }
    }
    return {url: location.href, title: redact(document.title).slice(0, 300), identity,
      observation_id: state.observation, text: redact(texts.join(" ")).slice(0, 18000),
      elements: state.elements.map((element, i) => ({index: i + 1, tag: element.tagName.toLowerCase(),
        label: redact(element.getAttribute("aria-label") || element.innerText || element.getAttribute("placeholder")).slice(0, 180),
        href: element.tagName === "A" ? element.href : undefined}))};
  }
  if (mode === "target") {
    if (location.href !== input.expected_url || identity !== input.expected_identity) {
      throw new Error("Page or account changed");
    }
    if (!input.observation_id || state.observation !== input.observation_id) throw new Error("Observation expired");
    const element = state.elements[input.index - 1];
    if (!element || !element.isConnected || !visible(element) || sensitive(element)) throw new Error("Element expired");
    if (input.focused && document.activeElement !== element && !element.contains(document.activeElement)) {
      throw new Error("Input focus changed");
    }
    if (element.matches("input[type=file],select")) throw new Error("Unsupported input");
    const label = `${element.getAttribute("aria-label") || ""} ${element.innerText || ""}`;
    if (element.closest('[data-testid="SideNav_AccountSwitcher_Button"]')
        || /\b(log\s?out|sign\s?out|switch account|add account|change password)\b/i.test(label)) {
      throw new Error("Account changes require manual control");
    }
    const rect = element.getBoundingClientRect();
    const x = rect.x + rect.width / 2, y = rect.y + rect.height / 2;
    if (x < 0 || y < 0 || x > innerWidth || y > innerHeight) throw new Error("Element outside viewport");
    const top = document.elementFromPoint(x, y);
    if (top !== element && !element.contains(top)) throw new Error("Element is covered");
    const anchor = element.closest("a");
    const form = element.closest("form");
    const href = anchor?.href || (element.type === "submit" ? form?.action : "") || "";
    return {x, y, href, target: anchor?.target || form?.target || "", editable: element.matches("input,textarea,[contenteditable=true]")};
  }
  if (mode === "pointer") {
    let pointer = document.getElementById("jarvis-browser-pointer");
    if (!pointer) {
      pointer = document.createElement("div");
      pointer.id = "jarvis-browser-pointer";
      pointer.textContent = "↖ Jarvis";
      pointer.style.cssText = "position:fixed;z-index:2147483647;pointer-events:none;color-scheme:light dark;background:Highlight;color:HighlightText;border:2px solid Canvas;border-radius:5px;padding:3px;font:12px sans-serif;";
      document.documentElement.append(pointer);
    }
    pointer.style.left = `${input.x}px`;
    pointer.style.top = `${input.y}px`;
    return {};
  }
  throw new Error("Unsupported inspection");
}
