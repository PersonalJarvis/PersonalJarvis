# Agent code sandbox

Choose **Code sandbox** when creating a Jarvis agent, or change its execution
environment in the agent card while it is idle. Use an API or local model.
Native coding CLIs and external agent runtimes are refused in this mode.

Docker must be running with Linux containers. The agent card checks readiness;
the explicit **Download runtime** action downloads the Python runtime image.
Missing Docker or an image never falls back to commands on the host.

The model and conversation remain in Jarvis. Its only machine-facing tool is
society_sandbox: run a command, write a UTF-8 file, read a file, or list a
directory. Other tools, plugins, desktop control and delegation are excluded.
Existing grants, denials, approval rules and read-only modes still apply.

Each identity gets a separate Docker volume. Every command gets a disposable,
unprivileged container with networking disabled, a read-only root filesystem,
no host mounts, no credentials and no Docker socket. Limits are one CPU,
512 MB memory, 128 processes, 64 MB temporary storage, 16 MB per written file,
and at most 15 minutes per command. Persistent volume storage has no aggregate
quota; this is container isolation, not a separate kernel per agent.

The chat Stop button cancels the command and removes its container. An internal
deadline also limits work if the Jarvis host crashes. Files persist separately
from the host workspace. Refresh files in the agent card and click a result to
download it (up to 1 MB). Changing back to local execution retains the sandbox
files; it does not copy them onto the host.

The existing dynamic CLI exposes the mounted society sandbox readiness, image
preparation, file listing and download routes. The OpenAPI document lists their
operation names. Image preparation is an explicit work-starting operation.

Validation: policy and schema contracts; real Docker file creation, generated
Python tests, file export, network/root-write refusal, timeout and cancellation.
The live Docker integration test is opt-in with JARVIS_SANDBOX_LIVE=1.
