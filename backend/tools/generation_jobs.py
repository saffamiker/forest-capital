"""
tools/generation_jobs.py

In-memory job registry for async document generation. The three
generation endpoints (midpoint paper, executive brief, presentation
deck) take 30-90 seconds; rather than holding the HTTP request open,
they create a job, kick generation off as a background task, and return
202 immediately. The frontend polls GET /api/v1/jobs/{id}.

Job lifecycle: pending → running → complete | failed | cancelled.

Storage is a module-level dict — jobs are transient. No database table:
if the server restarts, in-flight jobs are lost and the user simply
regenerates. Jobs are pruned after two hours (checked on every read) so
the dict stays bounded; the rendered file bytes are held on the job
until then so the download endpoint can serve them.

All access is on the FastAPI event loop (the endpoints and the
background generation tasks all run there), so the plain dict needs no
lock.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

_JOB_TTL = timedelta(hours=2)

# job_id → job dict. Keys prefixed with '_' are internal (the rendered
# file, the asyncio task handle) and never serialised to the API.
_jobs: dict[str, dict[str, Any]] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _prune() -> None:
    """Drops jobs older than the two-hour TTL — keeps the dict bounded."""
    cutoff = _now() - _JOB_TTL
    for job_id in [jid for jid, job in _jobs.items()
                   if job["created_at"] < cutoff]:
        _jobs.pop(job_id, None)


def create_job(document_type: str, owner_email: str) -> dict[str, Any]:
    """Registers a new pending job and returns it."""
    job_id = uuid.uuid4().hex
    job: dict[str, Any] = {
        "job_id": job_id,
        "document_type": document_type,
        "owner_email": owner_email,
        "status": "pending",
        "draft_id": None,
        "download_url": None,
        "error": None,
        "created_at": _now(),
        "completed_at": None,
        # Internal — never exposed by public_view().
        "_file_bytes": None,
        "_filename": None,
        "_media_type": None,
        "_task": None,
    }
    _jobs[job_id] = job
    return job


def get_job(job_id: str) -> dict[str, Any] | None:
    """The job, or None if unknown / expired. Prunes expired jobs."""
    _prune()
    return _jobs.get(job_id)


def update_job(job_id: str, **fields: Any) -> dict[str, Any] | None:
    """Patches a job in place. Returns the job, or None if unknown."""
    job = _jobs.get(job_id)
    if job is None:
        return None
    job.update(fields)
    return job


def list_jobs(owner_email: str) -> list[dict[str, Any]]:
    """This user's jobs — most recent first, the last 10 only."""
    _prune()
    owned = [j for j in _jobs.values() if j["owner_email"] == owner_email]
    owned.sort(key=lambda j: j["created_at"], reverse=True)
    return owned[:10]


def public_view(job: dict[str, Any]) -> dict[str, Any]:
    """The JSON-safe projection of a job — no file bytes, no task handle."""
    return {
        "job_id": job["job_id"],
        "document_type": job["document_type"],
        "owner_email": job["owner_email"],
        "status": job["status"],
        "draft_id": job["draft_id"],
        "download_url": job["download_url"],
        "error": job["error"],
        "created_at": job["created_at"].isoformat(),
        "completed_at": (job["completed_at"].isoformat()
                         if job["completed_at"] else None),
    }
