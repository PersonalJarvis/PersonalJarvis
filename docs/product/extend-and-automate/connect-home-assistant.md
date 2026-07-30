---
title: "Connect Home Assistant"
slug: connect-home-assistant
summary: Connect a self-hosted Home Assistant, protect its long-lived token, and use approved smart-home reads and actions safely.
section: "Extend and automate"
section_order: 5
order: 10
diataxis: howto
status: active
owner: maintainers
last_reviewed: 2026-07-30
phase: "-"
audience: end-user
tags: [home-assistant, plugins, smart-home, access-token, safety, self-hosting]
related: [plugins, skills, workflows-and-commands, credentials-and-secrets]
---

The Home Assistant plugin reads device states and requests smart-home
changes. It needs your server address and one long-lived access token; it does
not install or reconfigure Home Assistant.

## Before You Start

- Confirm that Home Assistant is reachable from the app host.
- Create a dedicated, non-admin Home Assistant user. A token
  inherits the permissions of the account that creates it.
- Treat the ten-year token as a password the app does not rotate. Enter it only
  in the protected field, never chat, voice, skills, workflows, or screenshots.

## Connect Home Assistant

1. Open **Plugins > Browse**, search for **Home Assistant**, and select
   **Connect plugin**.
2. Select **Open Home Assistant tokens**. In your Home Assistant profile, use
   **Security > Long-lived access tokens > Create token**, then copy it.
3. Enter the base address you normally use, including `https://` or `http://`
   and its port when needed. The app removes copied dashboard paths and queries.
4. Paste the token into the protected token field and select **Connect**.
5. Confirm that the card shows **Connected** and **Live**.

There is no separate **Test** button. **Connect** validates the server and token
before saving. **Connected** means a credential exists; **Live** means the tool
is callable. Neither proves every device is online.

## Know What Each Control Does

| Control or state | What it changes | What it does not do |
|---|---|---|
| **Connect** | Validates and saves the address and token, then exposes the tool | It does not change devices or install an add-on |
| **Connected / Live** | Reports the saved connection and callable tool | It is not a continuous server or device health check |
| Home Assistant skill **On / Off** | Enables or disables the built-in skill's matching and usage guidance | It does not delete the token or disconnect the plugin |
| **Remove** in Plugins | Deletes the app's local credential and removes access from new requests | It cannot invalidate this long-lived token inside Home Assistant |
| Delete the token in Home Assistant | Revokes it at the source | It does not remove the stale local card |

The Plugins card has no Enable or Disable action. The skill switch is only a
routing preference. Use **Remove** to stop local access, then delete the token
in Home Assistant for complete revocation. Remove and reconnect to replace the
address, account, or token.

## Read and Control Your Home

Name the room or device clearly. The connector can:

- find Home Assistant entities, optionally narrowing them to lights, switches,
  climate controls, covers, sensors, or locks;
- read one entity's state, friendly name, brightness, temperature, battery, or
  unit; and
- call a service for one entity, such as changing a light, switch, thermostat,
  cover, lock, or scene.

Available changes depend on that entity's Home Assistant services. The skill
finds the entity first, prefers one device over an area, and reports what Home
Assistant says actually changed.

The default risk tier is **Ask** because a call can physically affect a home.
Approve only the expected device, room, and change. Safety rules can block an
action; a deliberate allowlist can reduce prompts. Home Assistant account
permissions remain the outer boundary.

## Protect the Network and Your Privacy

Prefer HTTPS with a certificate trusted by the app host. A missing scheme
defaults to HTTPS. Explicit HTTP is accepted, but sends the token and home data
without transport encryption; use it only on a trusted private network, never
the public internet.

Fix certificate-name or trust-chain errors instead of bypassing verification.
A remote or headless host needs a secure route to Home Assistant. Local host
names may not resolve inside containers or across a VPN.

Requests go directly from the app host to your address, and the token stays in
credential storage. Returned device states can become context for the active
Brain; a hosted Brain may process that result. Self-hosting Home Assistant does
not make the whole answer offline.

The HTTP connector works on Windows, macOS, Linux, and headless hosts that can
reach the server. Voice is optional; chat uses the same tool.

## How It Fits Together

| Related feature | Relationship to Home Assistant |
|---|---|
| **Plugins** | Owns the server address, protected token flow, connection badges, and removal. Connecting the card exposes the native tool. |
| **Skills** | The bundled skill teaches the assistant when to find an entity, ask before acting, and report real state changes. Its switch does not own the credential. |
| **Workflows** | The current workflow bootstrap lacks the safety executor needed for this tool, so do not rely on scheduled Home Assistant steps yet. |
| **Credentials and Secrets** | Explains where the token is stored and why removal in the app and revocation in Home Assistant are separate actions. |

The path is: request, skill match, live tool, safety decision, server call, and
the state Home Assistant reports.

## Check That It Works

1. Confirm **Connected** and **Live** on the plugin card.
2. Ask a read-only question about one known sensor or device and explicitly
   mention Home Assistant.
3. Confirm that the answer includes the current friendly name and state rather
   than a generic explanation.
4. Try one reversible change on a single device. Approve only when the prompt
   names the expected device and action, then verify the change in Home
   Assistant itself.

## Troubleshooting

| What you see | Likely cause | What to do |
|---|---|---|
| **Could not reach Home Assistant** during Connect | Wrong address, stopped server, blocked port, VPN, container, or local-name resolution | Open the same address from the app host, fix its route or name, then connect again. |
| Certificate or TLS error | The certificate is expired, untrusted, or does not match the address | Repair the certificate or trust chain; do not switch to public HTTP as a shortcut. |
| Token rejected or HTTP 401 | The token is mistyped, deleted, or belongs to the wrong server | Create a new long-lived token for the dedicated account and reconnect. |
| **Connected** but no **Live** badge | The running app has not exposed the shipped native tool | Confirm the app is current, restart it, and reconnect if the badge remains absent. |
| Device cannot be found | Its entity was renamed, removed, or the request was ambiguous | Ask for a narrow list by device type, then retry with the friendly room and device name. |
| Read works but a change fails | The user lacks permission or that entity does not offer the requested service | Check the account and entity in Home Assistant; choose an action it exposes. |
| It works on desktop but not headless | The headless host cannot reach the home LAN or resolve its local name | Give that host a secure network route and a resolvable server address. |

## Next Steps

- Read [Plugins](plugins) for marketplace states and connection management.
- Review the paired playbook under [Skills](skills).
- Read [Workflows and App Commands](workflows-and-commands) before planning
  automation around the current workflow limits.
- Use [Credentials and Secrets](credentials-and-secrets) when replacing or
  revoking the long-lived token.
