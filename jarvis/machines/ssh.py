"""Optional SSH/SFTP transport with explicitly confirmed host keys."""

from __future__ import annotations

import asyncio
import base64
import shlex
from typing import Any

from .connections import wait_for_connection
from .models import MachineCommand, MachineResult
from .runner import MAX_FILE_BYTES


def ssh_module() -> Any:
    try:
        import asyncssh
    except ImportError as exc:
        raise RuntimeError(
            "SSH support is not installed. Install personal-jarvis[remote] on the hub."
        ) from exc
    return asyncssh


async def probe_key(host: str, port: int) -> dict[str, str]:
    ssh = ssh_module()
    await wait_for_connection()
    key = await asyncio.wait_for(ssh.get_server_host_key(host, port=port, config=None), 10)
    if key is None:
        raise ConnectionError("SSH server did not present a host key")
    return {
        "host_key": key.export_public_key().decode().strip(),
        "fingerprint": key.get_fingerprint(),
    }


async def execute_ssh(machine: dict[str, Any], command: MachineCommand) -> MachineResult:
    from jarvis.core.config import get_secret

    ssh = ssh_module()
    settings = machine["settings"]
    private_key = get_secret("machine_ssh_" + machine["id"])
    if not private_key:
        raise PermissionError("Reconnect this SSH device's private key in the app")
    trusted = ssh.import_public_key(settings["host_key"])
    await wait_for_connection()
    async with ssh.connect(
        settings["host"],
        port=settings["port"],
        username=settings["username"],
        client_keys=[ssh.import_private_key(private_key)],
        known_hosts=([trusted], [], []),
        agent_path=None,
        connect_timeout=15,
        login_timeout=15,
        config=None,
    ) as connection:
        if command.operation == "shell":
            if command.grant.scope != "account":
                raise PermissionError("Plain SSH does not supply workspace isolation")
            cwd = str(command.args.get("path") or command.grant.workspace)
            text = str(command.args.get("command") or "")
            if machine["capabilities"]["os"] == "windows":
                script = (
                    "[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); "
                    "Set-Location -LiteralPath '"
                    + cwd.replace("'", "''")
                    + "' -ErrorAction Stop; "
                    + text
                )
                line = (
                    "powershell.exe -NoProfile -NonInteractive -EncodedCommand "
                    + base64.b64encode(script.encode("utf-16le")).decode()
                )
            else:
                line = "cd " + shlex.quote(cwd) + " && sh -c " + shlex.quote(text)
            process = await connection.create_process(line, encoding="utf-8")
            output: dict[str, Any] = {"stdout": "", "stderr": ""}

            async def drain(stream, label):
                while chunk := await stream.read(8192):
                    output[label] = (output[label] + chunk)[-MAX_FILE_BYTES:]

            readers = [
                asyncio.create_task(drain(process.stdout, "stdout")),
                asyncio.create_task(drain(process.stderr, "stderr")),
            ]
            try:
                await asyncio.wait_for(process.wait_closed(), command.timeout_s)
                await asyncio.gather(*readers)
                return MachineResult(
                    job_id=command.job_id,
                    success=process.exit_status == 0,
                    output={
                        "output": (output["stdout"] + output["stderr"])[-MAX_FILE_BYTES:],
                        "exit_code": process.exit_status,
                    },
                    error=None if process.exit_status == 0 else "Remote shell failed",
                )
            finally:
                if process.exit_status is None:
                    process.terminate()
                process.close()
                for task in readers:
                    task.cancel()
                await asyncio.gather(*readers, return_exceptions=True)
        async with connection.start_sftp_client() as sftp:
            import posixpath

            root = await sftp.realpath(command.grant.workspace)
            raw = str(command.args.get("path") or ".")
            path = raw if posixpath.isabs(raw) else posixpath.join(root, raw)
            if command.operation == "write":
                parent = await sftp.realpath(posixpath.dirname(path))
                path = posixpath.join(parent, posixpath.basename(path))
                if await sftp.islink(path):
                    raise PermissionError("Refusing to overwrite an SFTP symlink")
            else:
                path = await sftp.realpath(path)
            if (
                command.grant.scope == "workspace"
                and path != root
                and not path.startswith(root.rstrip("/") + "/")
            ):
                raise PermissionError("Path leaves the permitted workspace")
            if command.operation == "read":
                async with sftp.open(path, "rb") as stream:
                    data = await stream.read(MAX_FILE_BYTES + 1)
                if len(data) > MAX_FILE_BYTES:
                    raise ValueError("File exceeds the inline transfer limit")
                output = {"path": path, "text": data.decode("utf-8")}
            elif command.operation == "list":
                output = {
                    "path": path,
                    "entries": [
                        {"name": entry.filename, "directory": entry.attrs.type == 2}
                        for entry in (await sftp.readdir(path))[:1000]
                    ],
                }
            elif command.operation == "write":
                data = str(command.args.get("text", "")).encode("utf-8")
                if len(data) > MAX_FILE_BYTES:
                    raise ValueError("File exceeds the inline transfer limit")
                async with sftp.open(path, "wb") as stream:
                    await stream.write(data)
                output = {"path": path, "bytes": len(data)}
            else:
                raise PermissionError("SSH supports only shell and file operations")
            return MachineResult(job_id=command.job_id, success=True, output=output)
