"""Operational governance: system protection modes, ABAC task grants, and artifact binary backup/GC."""
from __future__ import annotations

import gzip
import hashlib
import hmac
import io
import json
import os
import tarfile
import tempfile
import uuid
import zlib
from datetime import timedelta
from pathlib import Path, PurePosixPath
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_session
from .models import Artifact, Membership, SystemState, TaskGrant, now
from .security import Actor, as_utc, authenticate, owner_only
from .services import Conflict, emit, get_system_mode, require_project, require_task, require_writable_system

router = APIRouter(prefix="/api/v1", tags=["Operations & Security Governance"])

MAX_ARCHIVE_RESTORE_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_FILES = 1000
MAX_ARCHIVE_MEMBER_BYTES = 5 * 1024 * 1024
MAX_ARCHIVE_PAYLOAD_BYTES = 44 * 1024 * 1024
MAX_ARCHIVE_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_EXPANDED_BYTES = MAX_ARCHIVE_PAYLOAD_BYTES + MAX_ARCHIVE_MANIFEST_BYTES


def _is_canonical_id(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def _read_exact(stream, size: int, *, allow_clean_eof: bool = False) -> bytes | None:
    result = bytearray()
    while len(result) < size:
        chunk = stream.read(min(64 * 1024, size - len(result)))
        if not chunk:
            if allow_clean_eof and not result:
                return None
            raise HTTPException(422, "Truncated gzip tar archive")
        result.extend(chunk)
    return bytes(result)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


class SystemModeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["normal", "degraded", "readonly_protect"]
    reason: str = Field(default="", max_length=2000)


class TaskGrantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str
    granted_scopes: list[str] = Field(default_factory=list, max_length=16)
    max_sensitivity: Literal["public", "internal", "confidential", "restricted"] = "restricted"
    expires_in_hours: int = Field(default=24, ge=1, le=720)


class ArtifactGCIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dry_run: bool = True
    keep_versions: int = Field(default=2, ge=1, le=50)
    include_rejected: bool = True


def _grant_public(g: TaskGrant) -> dict:
    return {
        "id": g.id,
        "project_id": g.project_id,
        "task_id": g.task_id,
        "agent_id": g.agent_id,
        "granted_scopes": g.granted_scopes,
        "max_sensitivity": g.max_sensitivity,
        "expires_at": g.expires_at,
        "revoked": g.revoked,
        "created_at": g.created_at,
    }


@router.get("/system/mode")
def system_mode(actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    state = db.get(SystemState, "default")
    if state is None:
        return {"mode": "normal", "reason": "", "updated_at": None}
    return {"mode": state.mode, "reason": state.reason, "updated_at": state.updated_at}


@router.put("/system/mode")
def update_system_mode(body: SystemModeIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    reason = body.reason.strip()
    if body.mode != "normal" and len(reason) < 5:
        raise HTTPException(422, "Provide a reason (>= 5 chars) when entering degraded or readonly_protect mode")
    state = db.get(SystemState, "default")
    if state is None:
        state = SystemState(id="default", mode=body.mode, reason=reason, updated_at=now())
        db.add(state)
    else:
        state.mode = body.mode
        state.reason = reason
        state.updated_at = now()
    emit(db, actor, "system.mode_changed", "default", payload={"mode": body.mode, "reason": reason})
    db.commit()
    return {"mode": state.mode, "reason": state.reason, "updated_at": state.updated_at}


@router.post("/tasks/{task_id}/grants", status_code=201)
def create_task_grant(task_id: str, body: TaskGrantIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    require_writable_system(db)
    task = require_task(db, task_id, actor)
    membership = db.scalar(
        select(Membership).where(Membership.project_id == task.project_id, Membership.agent_id == body.agent_id)
    )
    if membership is None:
        raise HTTPException(422, "Agent must belong to the task's project")
    grant = TaskGrant(
        project_id=task.project_id,
        task_id=task.id,
        agent_id=body.agent_id,
        granted_scopes=body.granted_scopes,
        max_sensitivity=body.max_sensitivity,
        expires_at=now() + timedelta(hours=body.expires_in_hours),
    )
    db.add(grant)
    db.flush()
    emit(db, actor, "task.grant_created", grant.id, task.project_id, {
        "task_id": task.id,
        "agent_id": body.agent_id,
        "max_sensitivity": body.max_sensitivity,
    })
    db.commit()
    return _grant_public(grant)


@router.get("/tasks/{task_id}/grants")
def list_task_grants(task_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    task = require_task(db, task_id, actor)
    query = select(TaskGrant).where(TaskGrant.task_id == task.id).order_by(TaskGrant.created_at.desc())
    if not actor.is_owner:
        query = query.where(TaskGrant.agent_id == actor.id)
    return [_grant_public(g) for g in db.scalars(query)]


@router.post("/tasks/{task_id}/grants/{grant_id}/revoke")
def revoke_task_grant(task_id: str, grant_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    owner_only(actor)
    task = require_task(db, task_id, actor)
    grant = db.scalar(select(TaskGrant).where(TaskGrant.id == grant_id, TaskGrant.task_id == task.id).with_for_update())
    if grant is None:
        raise HTTPException(404, "Task grant not found")
    grant.revoked = True
    emit(db, actor, "task.grant_revoked", grant.id, task.project_id, {"task_id": task.id, "agent_id": grant.agent_id})
    db.commit()
    return _grant_public(grant)


@router.get("/projects/{project_id}/artifacts/archive")
def export_artifact_archive(project_id: str, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    """Export verified local artifact binaries for a project as a manifest-backed tar.gz bundle."""
    owner_only(actor)
    require_project(db, project_id, actor)
    base = Path(os.environ.get("OTTERNEST_ARTIFACT_DIR", "./var/artifacts")).resolve()
    project_root = (base / project_id).resolve()
    artifacts = db.scalars(
        select(Artifact).where(Artifact.project_id == project_id, Artifact.deleted_at.is_(None)).order_by(Artifact.created_at, Artifact.id)
    ).all()

    manifest_items: list[dict] = []
    file_entries: list[tuple[str, bytes]] = []
    payload_bytes = 0
    for art in artifacts:
        if not art.uri or art.uri.startswith("unavailable://"):
            continue
        try:
            path = Path(art.uri).resolve(strict=True)
        except OSError:
            continue
        if not project_root.is_relative_to(base) or not path.is_relative_to(project_root) or not path.is_file():
            continue
        try:
            if path.stat().st_size > MAX_ARCHIVE_MEMBER_BYTES:
                continue
        except OSError:
            continue
        if len(manifest_items) >= MAX_ARCHIVE_FILES:
            raise HTTPException(413, "Artifact archive exceeds the 1000 file limit")
        try:
            with path.open("rb") as artifact_file:
                data = artifact_file.read(MAX_ARCHIVE_MEMBER_BYTES + 1)
        except OSError:
            continue
        if len(data) > MAX_ARCHIVE_MEMBER_BYTES:
            continue
        if payload_bytes + len(data) > MAX_ARCHIVE_PAYLOAD_BYTES:
            raise HTTPException(413, "Artifact archive exceeds the 44 MiB payload limit")
        payload_bytes += len(data)
        digest = hashlib.sha256(data).hexdigest()
        if not hmac.compare_digest(digest, art.checksum):
            raise Conflict(f"Artifact {art.id} failed SHA-256 verification during archive export")
        arc_path = f"files/{art.id}"
        file_entries.append((arc_path, data))
        manifest_items.append({
            "artifact_id": art.id,
            "task_id": art.task_id,
            "family_id": art.family_id,
            "version": art.version,
            "name": art.name,
            "checksum": art.checksum,
            "size_bytes": len(data),
            "sensitivity": art.sensitivity,
            "archive_path": arc_path,
        })

    manifest_bytes = json.dumps({
        "schema_version": "otternest.artifacts.v1",
        "project_id": project_id,
        "exported_at": now().isoformat(),
        "items": manifest_items,
    }, ensure_ascii=False, indent=2).encode("utf-8")
    if len(manifest_bytes) > MAX_ARCHIVE_MANIFEST_BYTES:
        raise HTTPException(413, "Artifact archive manifest exceeds the 2 MiB limit")

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo("manifest.json")
        info.size = len(manifest_bytes)
        tf.addfile(info, io.BytesIO(manifest_bytes))
        for arc_path, data in file_entries:
            f_info = tarfile.TarInfo(arc_path)
            f_info.size = len(data)
            tf.addfile(f_info, io.BytesIO(data))

    archive_bytes = buf.getvalue()
    if len(archive_bytes) > MAX_ARCHIVE_RESTORE_BYTES:
        raise HTTPException(413, "Artifact archive exceeds the 50 MiB transfer limit")
    emit(db, actor, "artifact.archive_exported", project_id, project_id, {"file_count": len(manifest_items)})
    db.commit()
    return Response(
        content=archive_bytes,
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="otternest-artifacts-{project_id[:8]}.tar.gz"'},
    )


@router.post("/projects/{project_id}/artifacts/restore")
async def restore_artifact_archive(project_id: str, request: Request, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    """Restore artifact binaries from an otternest.artifacts.v1 tar.gz archive matching checksums."""
    owner_only(actor)
    require_project(db, project_id, actor)
    require_writable_system(db)
    archive_stream = tempfile.SpooledTemporaryFile(max_size=1024 * 1024)
    size = 0
    try:
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_ARCHIVE_RESTORE_BYTES:
                raise HTTPException(413, "Artifact archive exceeds 50 MiB")
            archive_stream.write(chunk)
        archive_stream.seek(0)

        files_by_path: dict[str, bytes] = {}
        expanded_bytes = 0
        payload_bytes = 0
        member_count = 0
        try:
            with gzip.GzipFile(fileobj=archive_stream, mode="rb") as tar_stream:
                while True:
                    header = _read_exact(tar_stream, 512, allow_clean_eof=True)
                    if header is None:
                        raise HTTPException(422, "Archive is missing its tar end marker")
                    if header == bytes(512):
                        next_header = _read_exact(tar_stream, 512)
                        if next_header != bytes(512):
                            raise HTTPException(422, "Invalid tar end marker")
                        trailing = tar_stream.read(10 * 1024 + 1)
                        if len(trailing) > 10 * 1024 or any(trailing):
                            raise HTTPException(422, "Unexpected data after tar end marker")
                        break
                    member_count += 1
                    if member_count > MAX_ARCHIVE_FILES + 1:
                        raise HTTPException(422, "Archive exceeds the 1000 file limit")
                    member = tarfile.TarInfo.frombuf(header, encoding="utf-8", errors="strict")
                    if member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE):
                        raise HTTPException(422, "Archive contains an unsupported member type")
                    name = member.name
                    if not isinstance(name, str) or "\\" in name or "\x00" in name:
                        raise HTTPException(422, "Unsafe archive member path")
                    pure_path = PurePosixPath(name)
                    raw_parts = name.split("/")
                    if pure_path.is_absolute() or any(part in {"", ".", ".."} for part in raw_parts):
                        raise HTTPException(422, "Unsafe archive member path")
                    if name == "manifest.json":
                        max_member_size = MAX_ARCHIVE_MANIFEST_BYTES
                    elif len(raw_parts) == 2 and raw_parts[0] == "files" and _is_canonical_id(raw_parts[1]):
                        max_member_size = MAX_ARCHIVE_MEMBER_BYTES
                    else:
                        raise HTTPException(422, "Unexpected archive member path")
                    if name in files_by_path:
                        raise HTTPException(422, "Duplicate archive member path")
                    if not member.isfile() or member.size < 0:
                        raise HTTPException(422, "Archive contains a non-regular member")
                    if member.size > max_member_size:
                        raise HTTPException(422, "Archive member exceeds size limit")
                    expanded_bytes += member.size
                    if expanded_bytes > MAX_ARCHIVE_EXPANDED_BYTES:
                        raise HTTPException(422, "Archive exceeds the 46 MiB expanded data limit")
                    if name != "manifest.json":
                        payload_bytes += member.size
                        if payload_bytes > MAX_ARCHIVE_PAYLOAD_BYTES:
                            raise HTTPException(422, "Archive exceeds the 44 MiB payload limit")
                    files_by_path[name] = _read_exact(tar_stream, member.size) or b""
                    padding_size = (-member.size) % 512
                    if padding_size and any(_read_exact(tar_stream, padding_size) or b""):
                        raise HTTPException(422, "Invalid tar member padding")
        except HTTPException:
            raise
        except (tarfile.TarError, OSError, EOFError, zlib.error, ValueError, UnicodeError):
            raise HTTPException(422, "Invalid or truncated gzip tar archive") from None

        if "manifest.json" not in files_by_path:
            raise HTTPException(422, "Archive missing manifest.json")
        try:
            manifest = json.loads(files_by_path["manifest.json"].decode("utf-8"), object_pairs_hook=_unique_json_object)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
            raise HTTPException(422, "Invalid manifest.json") from None
        if not isinstance(manifest, dict):
            raise HTTPException(422, "Invalid manifest.json")
        if manifest.get("schema_version") != "otternest.artifacts.v1":
            raise HTTPException(422, "Unsupported artifact archive schema")
        if not _is_canonical_id(manifest.get("project_id")) or not isinstance(manifest.get("exported_at"), str):
            raise HTTPException(422, "Invalid artifact archive manifest metadata")
        items = manifest.get("items")
        if not isinstance(items, list) or len(items) > MAX_ARCHIVE_FILES:
            raise HTTPException(422, "Invalid artifact archive item list")

        expected_paths = {"manifest.json"}
        seen_artifact_ids: set[str] = set()
        validated_items: list[tuple[str, str, int]] = []
        for item in items:
            if not isinstance(item, dict):
                raise HTTPException(422, "Invalid artifact archive item")
            artifact_id = item.get("artifact_id")
            arc_path = item.get("archive_path")
            expected_sha = item.get("checksum")
            expected_size = item.get("size_bytes")
            if not _is_canonical_id(artifact_id) or artifact_id in seen_artifact_ids:
                raise HTTPException(422, "Invalid or duplicate artifact ID in manifest")
            if not isinstance(arc_path, str) or arc_path != f"files/{artifact_id}" or arc_path not in files_by_path:
                raise HTTPException(422, "Manifest references a missing or invalid archive member")
            if not isinstance(expected_sha, str) or len(expected_sha) != 64 or any(ch not in "0123456789abcdef" for ch in expected_sha):
                raise HTTPException(422, "Invalid artifact checksum in manifest")
            if isinstance(expected_size, bool) or not isinstance(expected_size, int) or not 0 <= expected_size <= MAX_ARCHIVE_MEMBER_BYTES:
                raise HTTPException(422, "Invalid artifact size in manifest")
            data = files_by_path[arc_path]
            if len(data) != expected_size:
                raise HTTPException(422, f"Size mismatch in archive for {arc_path}")
            actual_sha = hashlib.sha256(data).hexdigest()
            if not hmac.compare_digest(actual_sha, expected_sha):
                raise HTTPException(422, f"Checksum mismatch in archive for {arc_path}")
            seen_artifact_ids.add(artifact_id)
            expected_paths.add(arc_path)
            validated_items.append((arc_path, expected_sha, expected_size))
        if set(files_by_path) != expected_paths:
            raise HTTPException(422, "Archive members do not match the manifest")
    finally:
        archive_stream.close()

    base = Path(os.environ.get("OTTERNEST_ARTIFACT_DIR", "./var/artifacts")).resolve()
    expected_project_root = base / project_id
    project_root = expected_project_root.resolve()
    if project_root != expected_project_root or project_root == base or project_root.parent != base:
        raise HTTPException(422, "Unsafe project artifact directory")

    project_artifacts = db.scalars(select(Artifact).where(Artifact.project_id == project_id)).all()
    by_checksum: dict[str, list[Artifact]] = {}
    for art in project_artifacts:
        by_checksum.setdefault(art.checksum, []).append(art)

    restore_by_id: dict[str, tuple[Artifact, bytes]] = {}
    skipped = 0
    for arc_path, expected_sha, _ in validated_items:
        data = files_by_path[arc_path]
        matches = by_checksum.get(expected_sha, [])
        if not matches:
            skipped += 1
            continue
        for art in matches:
            if not _is_canonical_id(art.id):
                raise HTTPException(422, "Unsafe destination path")
            dest = project_root / art.id
            if dest.parent != project_root or dest.is_symlink() or (dest.exists() and not dest.is_file()):
                raise HTTPException(422, "Unsafe destination path")
            if art.id not in restore_by_id and len(restore_by_id) >= MAX_ARCHIVE_FILES:
                raise HTTPException(422, "Archive matches more than 1000 destination artifacts")
            restore_by_id[art.id] = (art, data)

    restore_bytes = sum(len(data) for _, data in restore_by_id.values())
    if restore_bytes > MAX_ARCHIVE_PAYLOAD_BYTES:
        raise HTTPException(413, "Restore destinations exceed the 44 MiB write limit")
    require_writable_system(db)

    staged: list[tuple[str, Path, Artifact, bytes]] = []
    backup_paths: list[Path] = []
    backups: list[tuple[Path, Path]] = []
    installed: list[Path] = []
    if restore_by_id:
        try:
            project_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            if project_root.resolve() != project_root:
                raise OSError("Project artifact directory changed during restore")
            for artifact_id, (art, data) in restore_by_id.items():
                dest = project_root / artifact_id
                if dest.is_symlink() or (dest.exists() and not dest.is_file()):
                    raise OSError("Unsafe destination file")
                fd, staged_name = tempfile.mkstemp(prefix=".restore-", suffix=".tmp", dir=project_root)
                staged.append((staged_name, dest, art, data))
                with os.fdopen(fd, "wb") as staged_file:
                    staged_file.write(data)
                    staged_file.flush()
                    os.fsync(staged_file.fileno())
            for staged_name, dest, _, _ in staged:
                if dest.exists():
                    backup_fd, backup_name = tempfile.mkstemp(prefix=".restore-backup-", suffix=".tmp", dir=project_root)
                    os.close(backup_fd)
                    backup_path = Path(backup_name)
                    backup_paths.append(backup_path)
                    os.replace(dest, backup_path)
                    backups.append((backup_path, dest))
                os.replace(staged_name, dest)
                installed.append(dest)
        except OSError:
            for dest in reversed(installed):
                try:
                    dest.unlink()
                except OSError:
                    pass
            for backup_path, dest in reversed(backups):
                try:
                    os.replace(backup_path, dest)
                except OSError:
                    pass
            for staged_name, _, _, _ in staged:
                try:
                    os.unlink(staged_name)
                except OSError:
                    pass
            backed_up_paths = {backup_path for backup_path, _ in backups}
            for backup_path in backup_paths:
                if backup_path not in backed_up_paths:
                    try:
                        backup_path.unlink()
                    except OSError:
                        pass
            raise HTTPException(500, "Unable to write restored artifact files") from None
        for backup_path in backup_paths:
            try:
                backup_path.unlink()
            except OSError:
                pass

    restored_ids: list[str] = []
    for _, dest, art, data in staged:
        art.uri = str(dest)
        art.size_bytes = len(data)
        art.deleted_at = None
        restored_ids.append(art.id)

    emit(db, actor, "artifact.archive_restored", project_id, project_id, {"restored_count": len(restored_ids), "skipped": skipped})
    db.commit()
    return {"project_id": project_id, "restored_ids": restored_ids, "restored_count": len(restored_ids), "skipped": skipped}


@router.post("/projects/{project_id}/artifacts/gc")
def gc_project_artifacts(project_id: str, body: ArtifactGCIn, actor: Actor = Depends(authenticate), db: Session = Depends(get_session)):
    """Garbage-collect superseded unaccepted or rejected artifact binaries while preserving audit metadata."""
    owner_only(actor)
    require_project(db, project_id, actor)
    if not body.dry_run:
        require_writable_system(db)
    rows = db.scalars(
        select(Artifact)
        .where(Artifact.project_id == project_id, Artifact.deleted_at.is_(None))
        .order_by(Artifact.family_id, Artifact.version.desc(), Artifact.created_at.desc())
    ).all()

    by_family: dict[str, list[Artifact]] = {}
    for art in rows:
        by_family.setdefault(art.family_id, []).append(art)

    candidates: list[Artifact] = []
    for fam_rows in by_family.values():
        for idx, art in enumerate(fam_rows):
            if art.review_status == "accepted":
                continue
            if art.review_status == "rejected" and body.include_rejected:
                candidates.append(art)
                continue
            if idx >= body.keep_versions:
                candidates.append(art)

    base = Path(os.environ.get("OTTERNEST_ARTIFACT_DIR", "./var/artifacts")).resolve()
    project_root = (base / project_id).resolve()
    reclaimed_bytes = 0
    reclaimable_bytes = 0
    deleted_ids: list[str] = []
    missing_ids: list[str] = []
    failed_ids: list[str] = []
    for art in candidates:
        try:
            if not art.uri or art.uri.startswith("unavailable://"):
                missing_ids.append(art.id)
            else:
                path = Path(art.uri).resolve()
                if project_root == base or not project_root.is_relative_to(base) or not path.is_relative_to(project_root):
                    raise OSError("Unsafe artifact path")
                try:
                    file_size = path.stat().st_size
                except FileNotFoundError:
                    missing_ids.append(art.id)
                else:
                    if not path.is_file():
                        raise OSError("Artifact is not a regular file")
                    if body.dry_run:
                        reclaimable_bytes += file_size
                        continue
                    path.unlink()
                    reclaimed_bytes += file_size
                    deleted_ids.append(art.id)
        except OSError:
            failed_ids.append(art.id)
            continue
        if not body.dry_run:
            art.deleted_at = now()

    if not body.dry_run and candidates:
        emit(db, actor, "artifact.gc_completed", project_id, project_id, {
            "deleted_count": len(deleted_ids),
            "deleted_ids": deleted_ids,
            "missing_ids": missing_ids,
            "failed_ids": failed_ids,
            "reclaimed_bytes": reclaimed_bytes,
        })
        db.commit()
    return {
        "project_id": project_id,
        "dry_run": body.dry_run,
        "candidate_count": len(candidates),
        "candidate_ids": [art.id for art in candidates],
        "deleted_ids": deleted_ids,
        "missing_ids": missing_ids,
        "failed_ids": failed_ids,
        "reclaimable_bytes": reclaimable_bytes,
        "reclaimed_bytes": reclaimed_bytes,
    }
