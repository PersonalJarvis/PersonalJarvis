"""Owner-facing commands for explicitly opted-in Ultra Agent Swarm teams."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import typer

from jarvis.cli_ctl import invoke, options
from jarvis.cli_ctl.client import ApiError, JarvisClient

MAX_BACKUP_BYTES = 512 * 1024 * 1024

app = typer.Typer(
    no_args_is_help=True, help="Independent Swarm teams: create, inspect and control."
)


class RecordKind(StrEnum):
    TASKS = "tasks"
    AGENTS = "agents"
    MESSAGES = "messages"
    EVENTS = "events"
    ARTIFACTS = "artifacts"
    REPUTATION = "reputation"
    PUBLICATIONS = "publications"
    DECISIONS = "decisions"
    CHECKPOINTS = "checkpoints"


def _team_id(value: str) -> str:
    if len(value) != 32 or any(character not in "0123456789abcdef" for character in value):
        raise typer.BadParameter("Use the 32-character team id returned by swarm list or create.")
    return value


def _request_key(value: str) -> str:
    if not 1 <= len(value) <= 100 or not value.strip():
        raise typer.BadParameter("Use a nonempty operation key of at most 100 characters.")
    return value


def _optional_team_id(value: str | None) -> str | None:
    return _team_id(value) if value is not None else None


def _storage_generation(value: str | None) -> str | None:
    if value is not None and len(value) > 64:
        raise typer.BadParameter("Storage generations contain at most 64 characters.")
    return value


@app.command("list")
def list_teams(
    limit: int = typer.Option(50, "--limit", min=1, max=200),
    offset: int = typer.Option(0, "--offset", min=0, max=10000000),
) -> None:
    """List explicitly created teams, without creating any team storage."""
    invoke.run("GET", "/api/swarm/teams", params={"limit": limit, "offset": offset})


@app.command()
def create(
    spec_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Create from a UTF-8 TeamCreate JSON file (up to 1 MiB); budgets are decimal strings."""
    from pydantic import ValidationError

    from jarvis.core.swarm_types import TeamCreate

    try:
        with spec_file.open("rb") as source:
            content = source.read(1048577)
        if len(content) > 1048576:
            raise typer.BadParameter("Team specification exceeds 1 MiB.")
        spec = TeamCreate.model_validate_json(content.decode("utf-8-sig"))
    except (OSError, UnicodeError, ValidationError) as error:
        # Validation errors may include rejected input values; never echo those.
        raise typer.BadParameter(
            "Use a UTF-8 TeamCreate JSON object with name, goal, request_key "
            "and valid limits/policy."
        ) from error
    invoke.run(
        "POST",
        "/api/swarm/teams",
        body=spec.model_dump(mode="json"),
        assume_yes=yes,
        dry_run=dry_run,
        dangerous=True,
    )


@app.command()
def show(team_id: str = typer.Argument(..., callback=_team_id)) -> None:
    """Read one team's saved state and exact accounting."""
    invoke.run("GET", f"/api/swarm/teams/{team_id}")


def _read_preparation_file(path: Path) -> str:
    try:
        with path.open("rb") as source:
            content = source.read(1048577)
        if len(content) > 1048576:
            raise typer.BadParameter("Preparation input exceeds 1 MiB.")
        return content.decode("utf-8-sig")
    except (OSError, UnicodeError):
        raise typer.BadParameter("Use a readable UTF-8 preparation file.") from None


@app.command()
def prepare(
    spec_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Create clarification questions from a TeamCreate JSON file; never launch workers."""
    from pydantic import ValidationError

    from jarvis.core.swarm_types import TeamCreate

    try:
        spec = TeamCreate.model_validate_json(_read_preparation_file(spec_file))
    except ValidationError:
        raise typer.BadParameter(
            "Use a valid TeamCreate JSON object with an original goal."
        ) from None
    if spec.tasks:
        raise typer.BadParameter("Preparation accepts a goal without pre-created execution tasks.")
    spec.preparation_required = True
    invoke.run(
        "POST",
        "/api/swarm/preparations",
        body=spec.model_dump(mode="json"),
        assume_yes=yes,
        dry_run=dry_run,
        dangerous=True,
        request_timeout_s=300,
    )


@app.command()
def preparation(team_id: str = typer.Argument(..., callback=_team_id)) -> None:
    """Read saved questions, answers, plan revision and approval digest."""
    invoke.run("GET", f"/api/swarm/teams/{team_id}/preparation")


@app.command()
def clarify(
    team_id: str = typer.Argument(..., callback=_team_id),
    expected_storage_generation: str = typer.Option(
        ..., "--expected-storage-generation", callback=_storage_generation
    ),
    request_key: str = typer.Option(..., "--request-key", callback=_request_key),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Begin or retry clarification for an existing unlaunched team."""
    invoke.run(
        "POST",
        f"/api/swarm/teams/{team_id}/preparation",
        body={
            "expected_storage_generation": expected_storage_generation,
            "request_key": request_key,
        },
        assume_yes=yes,
        dry_run=dry_run,
        dangerous=True,
        request_timeout_s=300,
    )


@app.command("plan")
def plan_preparation(
    team_id: Annotated[str, typer.Argument(callback=_team_id)],
    answers_file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    expected_revision: int = typer.Option(..., "--expected-revision", min=1),
    expected_storage_generation: str = typer.Option(
        ..., "--expected-storage-generation", callback=_storage_generation
    ),
    request_key: str = typer.Option(..., "--request-key", callback=_request_key),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Submit a JSON question-ID/answer object and generate a saved plan for review."""
    import json

    from pydantic import ValidationError

    from jarvis.core.protocols import PreparationAnswers

    try:
        body = PreparationAnswers(
            expected_revision=expected_revision,
            expected_storage_generation=expected_storage_generation,
            request_key=request_key,
            answers=json.loads(_read_preparation_file(answers_file)),
        )
    except (ValueError, ValidationError):
        raise typer.BadParameter(
            "Use a JSON object containing the saved question IDs and nonempty answers."
        ) from None
    invoke.run(
        "POST",
        f"/api/swarm/teams/{team_id}/preparation/answers",
        body=body.model_dump(mode="json"),
        assume_yes=yes,
        dry_run=dry_run,
        dangerous=True,
        request_timeout_s=300,
    )


@app.command("launch")
def launch_preparation(
    team_id: str = typer.Argument(..., callback=_team_id),
    expected_revision: int = typer.Option(..., "--expected-revision", min=1),
    expected_storage_generation: str = typer.Option(
        ..., "--expected-storage-generation", callback=_storage_generation
    ),
    digest: str = typer.Option(
        ..., "--digest", help="Exact plan digest returned by swarm preparation."
    ),
    request_key: str = typer.Option(..., "--request-key", callback=_request_key),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Approve the exact saved plan and launch it without replacing its tasks."""
    from pydantic import ValidationError

    from jarvis.core.protocols import PreparationLaunch

    try:
        body = PreparationLaunch(
            expected_revision=expected_revision,
            expected_storage_generation=expected_storage_generation,
            digest=digest,
            request_key=request_key,
        )
    except ValidationError:
        raise typer.BadParameter(
            "Use the exact revision, storage generation and 64-character plan digest."
        ) from None
    invoke.run(
        "POST",
        f"/api/swarm/teams/{team_id}/launch",
        body=body.model_dump(mode="json"),
        assume_yes=yes,
        dry_run=dry_run,
        dangerous=True,
    )


def _control_command(action: str) -> None:
    def command(
        team_id: str = typer.Argument(..., callback=_team_id),
        expected_version: int | None = typer.Option(None, "--expected-version", min=1),
        expected_storage_generation: str | None = typer.Option(
            None,
            "--expected-storage-generation",
            callback=_storage_generation,
            help="Generation from the selected snapshot; an empty string selects legacy storage.",
        ),
        yes: bool = options.yes_opt(),
        dry_run: bool = options.dry_opt(),
    ) -> None:
        body: dict[str, object] = {}
        if expected_version is not None:
            body["expected_version"] = expected_version
        if expected_storage_generation is not None:
            body["expected_storage_generation"] = expected_storage_generation
        invoke.run(
            "POST",
            f"/api/swarm/teams/{team_id}/{action}",
            body=body,
            assume_yes=yes,
            dry_run=dry_run,
            dangerous=True,
        )

    command.__doc__ = f"{action.capitalize()} the selected team with explicit owner authorization."
    app.command(action)(command)


for _action in ("start", "pause", "resume", "stop", "cancel", "archive"):
    _control_command(_action)


@app.command()
def world(
    team_id: str = typer.Argument(..., callback=_team_id),
    group: str = typer.Option("", "--group", help="Optional group within this team."),
) -> None:
    """Read a bounded team-local world snapshot."""
    if len(group) > 100:
        raise typer.BadParameter("Group ids contain at most 100 characters.")
    invoke.run("GET", f"/api/swarm/teams/{team_id}/world", params={"group": group})


@app.command()
def records(
    team_id: Annotated[str, typer.Argument(callback=_team_id)],
    kind: Annotated[RecordKind, typer.Argument()],
    limit: int = typer.Option(50, "--limit", min=1, max=200),
    offset: int = typer.Option(0, "--offset", min=0, max=10000000),
) -> None:
    """Inspect one bounded page of team-local work, messages or evidence."""
    invoke.run(
        "GET",
        f"/api/swarm/teams/{team_id}/{kind.value}",
        params={"limit": limit, "offset": offset},
    )


@app.command("export")
def export_backup(
    team_id: Annotated[str, typer.Argument(callback=_team_id)],
    output: Annotated[Path, typer.Option("--output", help="Destination ZIP file.")],
    request_key: str | None = typer.Option(None, "--request-key", help="Reuse on backup retries."),
    expected_version: int | None = typer.Option(None, "--expected-version", min=1),
    force: bool = typer.Option(False, "--force", help="Replace an existing output file."),
    timeout: float = typer.Option(300, "--timeout", min=0.1, max=3600),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Create and stream a verified portable backup into the selected file."""
    key = _request_key(request_key) if request_key is not None else uuid4().hex
    path = f"/api/swarm/teams/{team_id}/backup"
    body: dict[str, object] = {"request_key": key}
    if expected_version is not None:
        body["expected_version"] = expected_version

    def transfer(client: JarvisClient) -> dict[str, object]:
        client.download_target(output, overwrite=force)
        receipt = client.request("POST", path, json=body, timeout_s=timeout)
        if not isinstance(receipt, dict) or receipt.get("team_id") != team_id:
            raise ApiError("The backup response does not belong to the selected team.", 502)
        backup_id = receipt.get("backup_id")
        if (
            not isinstance(backup_id, str)
            or len(backup_id) != 32
            or any(char not in "0123456789abcdef" for char in backup_id)
        ):
            raise ApiError("The server returned an invalid backup identity.", 502)
        download_path = f"/api/swarm/teams/{team_id}/backups/{backup_id}"
        if receipt.get("download_url") != download_path:
            raise ApiError("The backup response contains an invalid download location.", 502)
        size = receipt.get("size_bytes")
        if (
            not isinstance(size, str)
            or not size.isascii()
            or not size.isdigit()
            or len(size) > 20
            or not 0 < int(size) <= MAX_BACKUP_BYTES
        ):
            raise ApiError("The backup response contains an invalid archive length.", 502)
        saved = client.download_file(
            download_path,
            output,
            expected_size=int(size),
            expected_sha256=None if receipt.get("sha256") == "" else receipt.get("sha256"),
            max_bytes=MAX_BACKUP_BYTES,
            overwrite=force,
            timeout_s=timeout,
        )
        return {"team_id": team_id, "backup_id": backup_id, "request_key": key, **saved}

    invoke.run(
        "POST",
        path,
        body=body,
        assume_yes=yes,
        dry_run=dry_run,
        dangerous=True,
        operation=transfer,
    )


@app.command("restore")
def restore_backup(
    file: Annotated[Path, typer.Argument(help="Portable backup ZIP, at most 512 MiB.")],
    request_key: Annotated[str, typer.Option("--request-key", callback=_request_key)],
    replace_team_id: str | None = typer.Option(
        None,
        "--replace-team-id",
        callback=_optional_team_id,
        help="Explicitly replace this existing team, retaining its recovery quarantine.",
    ),
    timeout: float = typer.Option(300, "--timeout", min=0.1, max=3600),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Stream an owner-authorized restore; reuse the operation key when retrying."""
    fields = {"request_key": request_key}
    if replace_team_id is not None:
        fields["replace_team_id"] = replace_team_id

    def transfer(client: JarvisClient):
        return client.upload_file(
            "/api/swarm/restores",
            file,
            fields=fields,
            max_bytes=MAX_BACKUP_BYTES,
            timeout_s=timeout,
        )

    invoke.run(
        "POST",
        "/api/swarm/restores",
        body={"file": str(file), **fields},
        assume_yes=yes,
        dry_run=dry_run,
        dangerous=True,
        operation=transfer,
    )


@app.command()
def storage(team_id: str = typer.Argument(..., callback=_team_id)) -> None:
    """Inspect selected team backups, pending restores and storage limits."""
    invoke.run("GET", f"/api/swarm/teams/{team_id}/storage")


@app.command()
def restores() -> None:
    """List validated restores that can be resumed after an interrupted replacement."""
    invoke.run("GET", "/api/swarm/restores")


@app.command("resume-restore")
def resume_restore(
    restore_id: Annotated[str, typer.Argument(callback=_team_id)],
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Resume one owned, previously validated restore operation."""
    invoke.run(
        "POST",
        f"/api/swarm/restores/{restore_id}/resume",
        assume_yes=yes,
        dry_run=dry_run,
        dangerous=True,
        request_timeout_s=300,
    )


@app.command()
def delete(
    team_id: Annotated[str, typer.Argument(callback=_team_id)],
    request_key: Annotated[str, typer.Option("--request-key", callback=_request_key)],
    expected_version: int | None = typer.Option(None, "--expected-version", min=1),
    expected_storage_generation: str | None = typer.Option(
        None,
        "--expected-storage-generation",
        callback=_storage_generation,
        help="Generation from the selected snapshot; an empty string selects legacy storage.",
    ),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Delete exactly this team workspace while retaining published results."""
    body: dict[str, object] = {"confirm_team_id": team_id, "request_key": request_key}
    if expected_version is not None:
        body["expected_version"] = expected_version
    if expected_storage_generation is not None:
        body["expected_storage_generation"] = expected_storage_generation
    invoke.run(
        "DELETE",
        f"/api/swarm/teams/{team_id}",
        body=body,
        assume_yes=yes,
        dry_run=dry_run,
        dangerous=True,
        request_timeout_s=300,
    )


@app.command()
def retention(
    team_id: Annotated[str, typer.Argument(callback=_team_id)],
    before_days: int = typer.Option(30, "--before-days", min=1, max=36500),
    yes: bool = options.yes_opt(),
    dry_run: bool = options.dry_opt(),
) -> None:
    """Prune expired messages, orphan staging, old backups and recovery quarantines."""
    invoke.run(
        "POST",
        f"/api/swarm/teams/{team_id}/retention",
        body={"before_days": before_days},
        assume_yes=yes,
        dry_run=dry_run,
        dangerous=True,
        request_timeout_s=300,
    )
