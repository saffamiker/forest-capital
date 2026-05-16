"""
tools/academic_context.py

Stores and serves uploaded academic reference documents — the midpoint
rubric, the final-presentation requirements, and any other reference
material. Every AI agent injects the full text of every stored document
as system context on each invocation, so the council, advisors, writers
and QA agents always produce analysis with the academic evaluation
criteria in view.

Only the server-side-extracted plain text is persisted (table
academic_documents, migration 008) — never the raw PDF/binary.

INJECTION MODEL
  get_academic_context() is SYNCHRONOUS: it reads a process-wide
  in-process cache, never the database, because the agent call wrappers
  (agents/base.py call_claude, and the Gemini/Grok callers) are
  synchronous. The cache is refreshed by refresh_academic_context() on
  app startup and after every upload/delete, so it stays current under
  the single-worker deployment this project already assumes for its
  other in-process caches (FF factors, HMM model, get_full_history memo).
"""
from __future__ import annotations

import io
import logging

try:
    import structlog
    log = structlog.get_logger(__name__)
except ImportError:  # pragma: no cover
    log = logging.getLogger(__name__)  # type: ignore[assignment]

# The document types the upload UI offers.
DOCUMENT_TYPES: tuple[str, ...] = (
    "midpoint_requirements",
    "final_presentation_requirements",
    "midpoint_draft",
    "presentation_slides",
    "presentation_script",
    "other",
)

# Human-readable banner label per type — agents see these in the injected block.
_TYPE_LABELS: dict[str, str] = {
    "midpoint_requirements": "MIDPOINT CHECK-IN REQUIREMENTS",
    "final_presentation_requirements": "FINAL PRESENTATION REQUIREMENTS",
    "midpoint_draft": "MIDPOINT DRAFT",
    "presentation_slides": "PRESENTATION SLIDES",
    "presentation_script": "PRESENTATION SCRIPT",
    "other": "REFERENCE DOCUMENT",
}

_DB_AVAILABLE = False
try:
    from database import AsyncSessionLocal
    _DB_AVAILABLE = AsyncSessionLocal is not None
except Exception:  # pragma: no cover
    pass

# Process-wide cache of the formatted system-context block.
_CONTEXT_CACHE: dict[str, str] = {"text": ""}


# ── Text extraction ───────────────────────────────────────────────────────────

def extract_document_text(filename: str, raw: bytes) -> str:
    """
    Extract plain text from an uploaded PDF via pypdf.

    PDF-ONLY by design. Markdown (.md) uploads are handled entirely
    upstream in the /api/v1/documents/academic/upload endpoint — it reads
    .md bytes as UTF-8 directly and never calls this function. Any other
    extension is rejected with a 400 before reaching here. So this
    function only ever receives PDF content; it has no text branch.

    Raises ValueError when a PDF yields no extractable text (e.g. a
    scanned image-only PDF) so the upload endpoint can return a clear 422
    rather than storing an empty row. `filename` is retained in the
    signature for call-site stability.
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise ValueError("PDF support unavailable — pypdf not installed") from exc
    reader = PdfReader(io.BytesIO(raw))
    text = "\n".join((page.extract_text() or "") for page in reader.pages).strip()
    if not text:
        raise ValueError(
            "No text could be extracted from the PDF — it may be a "
            "scanned image. Upload a text-based PDF."
        )
    return text


# ── Formatting ────────────────────────────────────────────────────────────────

def format_academic_context(docs: list[dict]) -> str:
    """
    Render the stored documents into one system-context block, each
    clearly labelled by document_type. Returns an empty string when no
    documents are stored so callers can append it unconditionally.
    """
    if not docs:
        return ""
    blocks: list[str] = []
    for d in docs:
        label = _TYPE_LABELS.get(d.get("document_type", "other"), "REFERENCE DOCUMENT")
        blocks.append(f"--- {label}: {d.get('name', 'document')} ---\n{d.get('content_text', '')}")
    return (
        "\n\n=== ACADEMIC CONTEXT ===\n"
        "The following documents define the academic evaluation criteria "
        "for this project. Keep them in view when producing any analysis, "
        "feedback, or recommendation.\n\n"
        + "\n\n".join(blocks)
    )


def get_academic_context() -> str:
    """Synchronous accessor — returns the cached formatted context block
    (empty until refresh_academic_context() has run at least once)."""
    return _CONTEXT_CACHE["text"]


def inject_academic_context(system_prompt: str) -> str:
    """Append the academic-context block to a system prompt. A no-op when
    no documents are stored, so every agent can call it unconditionally."""
    ctx = get_academic_context()
    return system_prompt + ctx if ctx else system_prompt


# ── Database access ───────────────────────────────────────────────────────────

async def list_academic_documents() -> list[dict]:
    """Lightweight list for the upload UI — metadata only, no content text."""
    if not _DB_AVAILABLE:
        return []
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            rows = await session.execute(
                text(
                    "SELECT id, name, document_type, "
                    "       length(content_text) AS char_count, uploaded_at "
                    "FROM academic_documents ORDER BY uploaded_at DESC"
                )
            )
            return [
                {
                    "id": str(r[0]),
                    "name": r[1],
                    "document_type": r[2],
                    "char_count": int(r[3] or 0),
                    "uploaded_at": r[4].isoformat() if r[4] else None,
                }
                for r in rows.fetchall()
            ]
    except Exception as exc:
        log.warning("academic_documents_list_error", error=str(exc))
        return []


async def _read_all_with_content() -> list[dict]:
    """Full rows including content_text — used to rebuild the context cache."""
    if not _DB_AVAILABLE:
        return []
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            rows = await session.execute(
                text(
                    "SELECT name, document_type, content_text "
                    "FROM academic_documents ORDER BY uploaded_at"
                )
            )
            return [
                {"name": r[0], "document_type": r[1], "content_text": r[2]}
                for r in rows.fetchall()
            ]
    except Exception as exc:
        log.warning("academic_documents_read_error", error=str(exc))
        return []


async def insert_academic_document(
    name: str, document_type: str, content_text: str,
) -> str | None:
    """Persists a new document and returns its id. Refreshes the cache."""
    if not _DB_AVAILABLE:
        return None
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            row = await session.execute(
                text(
                    "INSERT INTO academic_documents (name, document_type, content_text) "
                    "VALUES (:n, :t, :c) RETURNING id"
                ),
                {"n": name, "t": document_type, "c": content_text},
            )
            new_id = row.scalar()
            await session.commit()
        await refresh_academic_context()
        log.info("academic_document_inserted", name=name, document_type=document_type)
        return str(new_id) if new_id else None
    except Exception as exc:
        log.warning("academic_document_insert_error", error=str(exc))
        return None


async def delete_academic_document(doc_id: str) -> bool:
    """Deletes a document by id and refreshes the cache."""
    if not _DB_AVAILABLE:
        return False
    try:
        from sqlalchemy import text
        async with AsyncSessionLocal() as session:  # type: ignore[union-attr]
            result = await session.execute(
                text("DELETE FROM academic_documents WHERE id = :id"),
                {"id": doc_id},
            )
            await session.commit()
        await refresh_academic_context()
        return bool(result.rowcount)
    except Exception as exc:
        log.warning("academic_document_delete_error", error=str(exc))
        return False


async def refresh_academic_context() -> None:
    """Rebuilds the in-process context cache from the database. Called on
    app startup and after every upload/delete."""
    docs = await _read_all_with_content()
    _CONTEXT_CACHE["text"] = format_academic_context(docs)
    log.info("academic_context_refreshed", n_documents=len(docs))
