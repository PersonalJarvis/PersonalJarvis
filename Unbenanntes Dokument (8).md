**\# Bug Report — Taskbar shows the \*\*Python (pythonw.exe) logo\*\* instead of the Jarvis icon**

\> Handoff for a coding agent on the public repo. Reconstructed from a live  
\> install on the maintainer's machine — no code changed, no push made.  
\> Companion to \`WAKE-WORD-BUG-REPORT.md\` / \`WAKE-WORD-CROSS-PLATFORM-PLAN.md\`.

**\#\# TL;DR**

The desktop app's **\*\*taskbar button shows the generic Python logo\*\***, not the  
Jarvis brand icon. The app is a **\*\*pywebview / WebView2\*\*** shell launched by raw  
**\*\*\`pythonw.exe\`\*\***, so every icon surface that isn't explicitly overridden falls  
back to the interpreter's icon. On the observed machine \**\*all the \*static\** icon  
layers are already correct\*\* — yet the taskbar still shows Python, which points  
the finger at two concrete gaps:

1\. **\*\*The taskbar icon is applied** *\*asynchronously after\** **the window is shown\*\***  
   (a polling thread), so at **\*\*autostart\*\*** the taskbar button is created and  
   bound to the \`pythonw.exe\` process icon **\*\*before\*\*** the class/window icon  
   lands — and Windows **\*\*caches that mapping for the session\*\*** (the exact race  
   the code's own comments warn about).  
2\. \*\*The autostart shortcut that actually launches the app carries neither the  
   AUMID nor the icon\*\* — it is written by a fallback path that bypasses the  
   icon/AUMID tagging, so Windows has nothing but the bare process to fall back  
   on while the runtime setter is still racing.

Underlying enabler: the app runs from **\*\*\`pythonw.exe\`\*\***, whose process icon *\*is\**  
the Python logo — so any fallback is Python-branded by construction.

**\#\# Environment**

| | |  
|---|---|  
| Repo / Commit | \`github.com/PersonalJarvis/PersonalJarvis\` · \`main\` · **\*\*\`ae1bcdf\`\*\*** (\`v1.0.3\`) |  
| OS | Windows 11 Pro, Build 26200 |  
| UI shell | pywebview \+ **\*\*WebView2\*\***, launched via \`pythonw.exe \-m jarvis.ui.web.launcher\` |  
| Launch path on this box | **\*\*Startup-folder fallback shortcut\*\*** (the Scheduled Task was UAC-declined → \`jarvis/autostart/windows.py:363\`) |

**\#\# Observed state (what is already correct)**

| Layer | State | Evidence |  
|---|---|---|  
| Icon file present | ✅ | \`jarvis/assets/icons/jarvis.ico\` (60 878 B); \`project\_icon\_path()\` resolves it |  
| AUMID toast identity (HKCU) | ✅ | \`HKCU\\Software\\Classes\\AppUserModelId\\PersonalJarvis.PersonalJarvis\` → \`DisplayName="Personal Jarvis"\`, \`IconResource=…\\jarvis.ico,0\` |  
| Start-Menu shortcut (names the button) | ✅ | \`…\\Start Menu\\Programs\\Personal Jarvis.lnk\` → **\*\*AUMID \= \`PersonalJarvis.PersonalJarvis\`\*\*** |  
| Runtime window/class icon setter | ✅ present | \`set\_window\_icon\_by\_title\` / \`set\_window\_icon\_for\_pid\` \+ polling thread, \`desktop\_app.py:2830, 2933-3029\` |  
| AUMID set before window | ✅ | \`ensure\_windows\_app\_identity()\` at \`launcher.py:795, 991\` and \`desktop\_app.py:38\` |

So this is **\*\*not\*\*** a missing-asset bug (contrast the wake-word report). The icon  
and identity plumbing is all there — the failure is in **\*\*timing\*\*** and in the  
**\*\*autostart shortcut\*\***.

**\#\# Broken / suspect state (the actual causes)**

| Layer | State | Evidence |  
|---|---|---|  
| **\*\*Autostart shortcut icon\*\*** | ❌ **\*\*empty\*\*** | \`…\\Startup\\Personal Jarvis.lnk\` → \`IconLocation \= ",0"\` (no icon) |  
| **\*\*Autostart shortcut AUMID\*\*** | ❌ **\*\*None\*\*** | property-store read of the Startup \`.lnk\` → \`AUMID \= None\` |  
| Written by | fallback path bypassing tagging | \`jarvis/autostart/windows.py:439\` "Windows autostart shortcut (fallback) written" — does **\*\*not\*\*** embed AUMID or \`jarvis.ico\` (unlike \`icon\_utils.ensure\_start\_menu\_shortcut\`) |  
| Icon applied only **\*\*post-\`shown\`, async\*\*** | ⚠️ race | icon setter runs as a **\*\*parallel polling thread\*\*** after the window exists (\`desktop\_app.py:2933\`), not synchronously before first paint |  
| Process icon | ⚠️ Python by construction | app runs from \`pythonw.exe\`; its \`FileDescription\`/icon is "Python" (\`icon\_utils.py:5, 31, 44\`) |

**\#\# Root-cause chain**

1\. App autostarts unattended at login from the **\*\*Startup fallback shortcut\*\***  
   (\`autostart/windows.py\`), which has **\*\*no AUMID and no icon\*\***.  
2\. pywebview creates the WebView2 host window. \`SetCurrentProcessExplicitApp­UserModelID\`  
   groups it, but pywebview has **\*\*no icon parameter\*\*** (\`icon\_utils.py:3-7\`), so  
   the window/class icon must be injected by hand **\*\*after\*\*** the window appears.  
3\. The injection runs on a **\*\*polling thread\*\*** (\`desktop\_app.py:2933-3029\`). At  
   boot the system is busy; the taskbar button for the window is created and  
   Windows binds it to the **\*\*process icon (\`pythonw.exe\` → Python)\*\*** *\*before\**  
   \`SetClassLongPtrW\`/\`WM\_SETICON\` lands.  
4\. Per the code's own note (\`icon\_utils.py:28-32, 283-285\`): \*"Without a class  
   icon set at first display, the taskbar falls back to the process icon and  
   **\*\*caches that mapping for the rest of the session.\*\***"\* → the button keeps the  
   Python logo even after the window icon is eventually set.  
5\. Because the launching shortcut also lacks the AUMID/icon, there is no  
   shortcut-level icon for Windows to use in the meantime.

This is why a **\*\*manual\*\*** launch may look fine but the **\*\*autostart\*\*** launch shows  
Python — the race is far likelier when the window appears during boot.

**\#\# Recommended fixes (for the repo)**

1\. \**\*Set the class/window icon synchronously \*before\** first paint, not via an  
   async poll.\*\* Hook pywebview's \`shown\` (or the moment the HWND is first  
   available) and apply \`\_apply\_icon\_to\_hwnd\` **\*\*inline\*\*** before yielding, so  
   Windows never creates the taskbar button with the process icon. Keep the  
   polling thread only as a backstop. (Files: \`desktop\_app.py:2830, 2933-3029\`,  
   \`icon\_utils.py:243\`.)  
2\. **\*\*Tag the autostart shortcut with the AUMID \+ \`jarvis.ico\`.\*\*** Route  
   \`jarvis/autostart/windows.py\`'s fallback-shortcut writer through the same  
   logic as \`icon\_utils.ensure\_start\_menu\_shortcut\` (embed  
   \`System.AppUserModel.ID\` and set \`IconLocation \= \<jarvis.ico\>,0\`). A bare  
   \`pythonw\` shortcut with an empty icon is the launch entry point on any box  
   where the Scheduled Task is UAC-declined — i.e. a very common case.  
3\. **\*\*Ship a branded launcher executable\*\*** (the PyInstaller \`jarvis.spec\` already  
   exists) so the **\*\*process icon itself is the Jarvis icon\*\***. Then every  
   fallback — before any runtime override — is Jarvis-branded instead of Python.  
   Running from raw \`pythonw.exe\` is the structural reason the fallback is ever  
   the Python logo.  
4\. **\*\*Bust the Windows icon cache on install/repair\*\*** (or document it). Windows  
   caches shortcut/taskbar icons aggressively (\`IconCache.db\` /  
   \`…\\Explorer\\iconcache\_\*.db\`); a stale cache from the pre-reinstall state can  
   mask any fix. Verify fixes after \`ie4uinit.exe \-show\` / an Explorer restart.  
5\. **\*\*De-duplicate the two \`Personal Jarvis.lnk\` shortcuts.\*\*** There are now two  
   (\`…\\Programs\\\` with AUMID \+ icon, \`…\\Programs\\Startup\\\` without). Two same-named  
   shortcuts with divergent AUMID/icon can confuse the shell's button  
   resolution; the autostart one should be the *\*same\** correctly-tagged artifact.

**\#\# Reproduce**

1\. Fresh v1.0.3 install on Windows; let the Scheduled Task be UAC-declined so the  
   **\*\*Startup fallback shortcut\*\*** is used (\`autostart/windows.py:363\`).  
2\. Reboot / log in → app autostarts unattended.  
3\. Observe the taskbar: the button shows the **\*\*Python logo\*\***, not Jarvis.  
4\. Confirm: \`…\\Startup\\Personal Jarvis.lnk\` has \`IconLocation=",0"\` and \`AUMID=None\`.

**\#\# Verification checklist (cross-platform note)**

\- **\*\*Windows:\*\*** taskbar \+ titlebar \+ Alt-Tab all show \`jarvis.ico\` after both a  
  manual launch **\*\*and\*\*** an autostart launch (post icon-cache refresh).  
\- **\*\*macOS:\*\*** the equivalent is the Dock icon — must come from the \`.app\`  
  bundle's \`CFBundleIconFile\`, not the Python framework icon (same class of bug  
  in a packaged build).  
\- **\*\*Linux:\*\*** the taskbar/dock icon comes from the \`.desktop\` file's \`Icon=\` key  
  \+ a matching \`StartupWMClass\`; a raw \`python \-m …\` launch shows the generic  
  interpreter icon there too.

**\#\# Key code anchors**

\`jarvis/ui/icon\_utils.py\` (all icon/AUMID/shortcut logic) · runtime setter  
\`jarvis/ui/desktop\_app.py:2830, 2933-3029\` · identity pin \`launcher.py:795, 991\`  
· **\*\*autostart fallback shortcut (untagged)\*\*** \`jarvis/autostart/windows.py:439\` ·  
icon assets \`jarvis/assets/icons/jarvis.ico\` · tests \`tests/unit/ui/test\_icon\_identity.py\`.

