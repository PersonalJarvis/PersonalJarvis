# Cross-Platform macOS Surface — Fix Tracker

Tracker for the macOS desktop-surface work that BUG-056/057 deferred to
"main-thread or own-process hosting". Referenced from `jarvis/ui/tray.py`
and `docs/BUGS.md` (BUG-056, BUG-057).

## Done

| Item | Status | Detail / Reference |
|---|---|---|
| Menu-bar tray icon | Done 2026-07-17 | `pystray.Icon(..., darwin_nsapplication=...)` + `run_detached()` on the AppKit main thread; worker-thread mutations marshaled via `PyObjCTools.AppHelper.callAfter` (`jarvis/ui/tray.py`, BUG-056 follow-up). |
| Mascot/orb subprocess host | Done 2026-07-17 | Mascot/orb renders in the jarvisbar subprocess host as `SubprocessMascotOverlay` (`jarvis/ui/jarvisbar/subprocess_overlay.py`); `OrbOverlay` gained macOS Aqua-Tk alpha transparency (`-transparent` root) (BUG-057 follow-up). |
| JarvisBar subprocess host | Pre-existing | The own-process Tk host the mascot work builds on (BUG-057). |
| macOS audio ducking | Done 2026-07-17 | AppleScript (`osascript`) ducking of known players (Music, Spotify) with an opt-in master-volume fallback; every call wrapped, degrades per player (`jarvis/audio/ducking/macos.py`). |
| WebRTC VAD tier | Done 2026-07-17 | The middle fallback tier (Silero ONNX → WebRTC VAD → RMS energy) is actually wired in `jarvis/audio/vad.py` — previously aspirational (BUG-061 follow-up). |
| Native stub launcher | Done 2026-07-17 | In-repo compiled C stub (`jarvis/setup/macos_stub_launcher.c`) linked against the exact runtime dylib replaces py2app; launches on framework AND uv-standalone Pythons (BUG-076; numbered BUG-064 on the Mac line before the registers were merged). |

## Open

| Item | Notes |
|---|---|
| BUG-058 on-device confirmation | The PortAudio single-flight + Quartz event-tap hardening is audit-based; a real-Mac first-boot run through onboarding is still pending. |
| Full-screen-Spaces overlay limitation | The subprocess bar/orb does not appear over full-screen Spaces; needs an NSWindow collection-behavior host to join Spaces. |
| Browser/VLC not ducked | The AppleScript ducking tier only covers players with a scriptable app volume (Music, Spotify); browsers and VLC fall through to the opt-in master-volume fallback. |
| Template-style tray icon | The menu-bar icon should ship a macOS template image so it adapts to dark/light menu bars instead of a fixed-color bitmap. |
