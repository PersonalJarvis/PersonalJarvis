"""Add useT() to inner component functions that need it."""
import os, re

BASE = "C:/Users/Administrator/Desktop/Personal Jarvis/jarvis/ui/web/frontend/src"

def add_to_function(rel, func_name_pattern):
    fp = os.path.join(BASE, rel).replace("\\", "/")
    with open(fp, encoding="utf-8") as f:
        s = f.read()
    # Find function declaration with body opening
    pattern = re.compile(
        r"(function " + func_name_pattern + r"\([^)]*\)\s*[:\w<>,\[\] |]*\s*\{\s*\n)"
    )
    out = []
    last = 0
    changed = 0
    for m in pattern.finditer(s):
        # Check next 200 chars for "const t = useT()"
        post = s[m.end():m.end()+250]
        if "const t = useT()" in post:
            continue
        out.append(s[last:m.end()])
        out.append("  const t = useT();\n")
        last = m.end()
        changed += 1
    out.append(s[last:])
    if changed:
        with open(fp, "w", encoding="utf-8") as f:
            f.write("".join(out))
    print(f"{rel} ({func_name_pattern}): {changed} additions")

add_to_function("components/ProviderSwitcher.tsx", "ProviderSwitcher")
add_to_function("components/sessions/SessionDetail.tsx", "SessionDetail")
add_to_function("components/sessions/SessionList.tsx", "hangupLabel")
add_to_function("views/ApiKeysView.tsx", "CodexConnectButton")
add_to_function("views/OutputsView.tsx", "OutputItem")
add_to_function("views/ProfileView.tsx", "ReviewsSection")
add_to_function("views/SkillsView.tsx", "SkillEditor")
add_to_function("views/SkillsView.tsx", "TopHits")

# SessionList - need it on hangupLabel too -- wait, hangupLabel is not a React component; it's a regular function.
# Pass t in or use a different approach: revert "läuft" to a global string-translator helper.  # i18n-allow: quotes the literal source string being referenced
# For simplicity let me check the SessionList line 117:

fp = os.path.join(BASE, "components/sessions/SessionList.tsx").replace("\\", "/")
with open(fp, encoding="utf-8") as f:
    s = f.read()
# Find hangupLabel and remove our useT addition (it's a non-React function)
s = re.sub(r"function hangupLabel\(reason: string\): string \{\s*\n  const t = useT\(\);\n",
           "function hangupLabel(reason: string): string {\n", s)
# Now line 117 uses t() inside hangupLabel. Replace with English direct.
s = s.replace('if (secs === null) return t("sessions.running");',
              'if (secs === null) return "running";')
with open(fp, "w", encoding="utf-8") as f:
    f.write(s)
print("SessionList hangupLabel cleaned")
