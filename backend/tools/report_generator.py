"""tools/report_generator.py — verified-data report orchestration.

May 22 2026 (item 12 commit 2). Sits BETWEEN the pipeline helpers in
template_pipeline.py / analytical_findings.py and the docx assembler
in report_writer_docx.py. The endpoints in main.py call this module;
this module is the only place that strings the full eight-step flow
together:

  1. Resolve the template + the staged findings + the latest payload
  2. Build verified_data from the live payload
  3. Cross-check live ↔ staged → mark mismatches inline
  4. Fetch team_activity + cross-check Bob + Molly UAT sums
  5. Source citations (template_pipeline.source_citations) →
     persist into citations_cache
  6. Validate thesis — BLOCKING gate. A fail returns immediately
     without calling the writer or persisting a generation row.
  7. Rank findings (already done in stage_findings but recomputed
     here so a manual findings edit since staging is honoured)
  8. Substitute placeholders → call_claude → strip banner
  9. Post-check (numbers + citations + word counts) → flag count
 10. Build the appendix context dict
 11. Persist one report_generations row → return the full draft
     payload to the caller

After generation, the editor calls back into:

  iterate_text(...)        — Rephrase / Tighten / Expand / Ask
  resolve_bob_block(...)   — replace one [BOB] marker with Bob's text
  run_final_check(...)     — re-run post-checks against the current
                             paper_md and update flag_count

Downloads are gated by flag_count == 0 — set by run_final_check; the
download endpoints in main.py read the row before serving bytes.

FAIL-OPEN end to end. A generation that hits a thesis block, or a
generation that raises during writer call, is recorded with a
sentinel paper_md so the UI can render a useful error state.
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import Any

import structlog

log = structlog.get_logger(__name__)


_BOB_BLOCK_RE = re.compile(
    r"\[(DATA REQUIRED|CITATION REQUIRED|DATA MISMATCH|"
    r"UNVERIFIED NUMBER|CITATION UNVERIFIED|BOB)"
    r"(?:[^\[\]]*)\]")


def extract_bob_blocks(paper_md: str) -> list[dict[str, Any]]:
    """Returns every callout marker in the draft as a structured list
    the editor can render as interactive blocks.

    Each entry: {marker, kind, description, position}.
    """
    out: list[dict[str, Any]] = []
    for m in _BOB_BLOCK_RE.finditer(paper_md or ""):
        full = m.group(0)
        kind = m.group(1)
        # Description is the marker body after the kind label.
        desc = full[1:-1]  # drop brackets
        # Strip a leading "KIND — " / "KIND: " / "KIND " prefix.
        for sep in (" — ", ": ", " - ", " "):
            if desc.startswith(kind + sep):
                desc = desc[len(kind) + len(sep):]
                break
        out.append({
            "marker":      full,
            "kind":        kind,
            "description": desc.strip() or kind,
            "position":    m.start(),
        })
    return out


def count_bob_blocks(paper_md: str) -> int:
    return len(_BOB_BLOCK_RE.findall(paper_md or ""))


# ── Stage 1 — assemble the writer input ─────────────────────────────────────


async def _assemble_inputs(
    template_id: str,
) -> dict[str, Any]:
    """Pulls template, staged findings, latest payload, citations,
    activity, and validation summary. Pure data assembly — no LLM.

    Returns a dict carrying every component the writer call + the
    appendix builder need. Fails open: any DB error logs and produces
    an empty default for that key.
    """
    from tools.analytical_findings import (
        gather_payload_from_db, get_latest_findings,
    )
    from tools.cache import get_latest_strategy_hash
    from tools.report_templates import get_template
    from tools.template_pipeline import (
        live_from_payload, cross_check, fetch_team_activity,
        cross_check_team_activity, validate_thesis, rank_findings,
        macro_validated,
    )

    template = await get_template(template_id)
    if not template:
        return {"error": "template_not_found"}

    data_hash = await get_latest_strategy_hash()
    payload = await gather_payload_from_db(data_hash)
    live = live_from_payload(payload)

    findings_row = await get_latest_findings()
    staged_md = (findings_row or {}).get("findings_md") or ""
    verified_data, mismatches = cross_check(live, staged_md)

    findings = (findings_row or {}).get("findings") or []
    ranked = rank_findings(findings)

    activity = await fetch_team_activity()
    activity_flags = cross_check_team_activity(activity)

    macro_obj = payload.get("macro_digest") or {}
    mv = macro_validated(macro_obj.get("summary_text"))
    verified_data["macro_validated"] = mv

    validation_summary = await _latest_audit_summary()

    return {
        "template":            template,
        "data_hash":           data_hash,
        "payload":             payload,
        "verified_data":       verified_data,
        "mismatches":          mismatches,
        "findings":            findings,
        "ranked_findings":     ranked,
        "findings_row":        findings_row,
        "team_activity":       activity,
        "activity_flags":      activity_flags,
        "validation_summary":  validation_summary,
        "macro_validated":     mv,
    }


async def _latest_audit_summary() -> dict[str, Any]:
    """Reads the most recent completed audit run and shapes it to the
    keys the appendix builder consumes. Fail-open — a cold DB / no
    audit returns an empty dict."""
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return {}
        async with AsyncSessionLocal() as s:
            r = await s.execute(text(
                "SELECT statistical_status, qa_status, completed_at "
                "FROM audit_runs "
                "WHERE statistical_status IS NOT NULL "
                "ORDER BY completed_at DESC NULLS LAST "
                "LIMIT 1"))
            row = r.fetchone()
            if not row:
                return {}
            stat = row[0] or "unknown"
            qa = row[1] or "unknown"
            when = row[2].isoformat() if row[2] is not None else "—"
        return {
            "layer1_status": stat, "layer1_count": "—", "layer1_date": when,
            "layer2_status": stat, "layer2_count": "—", "layer2_date": when,
            "layer3_status": qa,   "layer3_count": "—", "layer3_date": when,
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("audit_summary_read_failed", error=str(exc))
        return {}


# ── Stage 2 — citation sourcing (separate step so the UI can render
# progress; called by the endpoint after assemble) ─────────────────────────


async def source_template_citations(
    template_id: str,
) -> dict[str, Any]:
    """Drives the concept list off the template and persists each row
    in citations_cache. Returns the citations dict the writer + the
    appendix builder consume."""
    from tools.report_templates import get_template
    from tools.template_pipeline import (
        source_citations, persist_citations,
    )
    tmpl = await get_template(template_id)
    if not tmpl:
        return {}
    concepts = tmpl.get("concepts") or []
    citations = await source_citations(concepts)
    await persist_citations(citations)
    return citations


# ── Stage 3 — writer invocation ─────────────────────────────────────────────


def _call_writer_sync(
    system_prompt: str, max_tokens: int = 3000,
) -> str:
    """Runs the academic writer once on a fully substituted prompt.

    Sync because call_claude is sync. The caller runs this in
    asyncio.to_thread so the async event loop is not blocked.

    The harness is intentionally NOT used here — the report writer
    has its own post-generation regex scan + Bob's interactive
    editing loop, and a harness retry would interpose evaluator
    rewrites between the prompt and Bob's first read of the draft.
    The user's amendment puts the human in the iteration loop.

    Returns the writer's raw output, or a sentinel string on a
    test-environment / API-failure path. Never raises.
    """
    try:
        from agents.base import call_claude, SONNET_MODEL
    except Exception as exc:  # noqa: BLE001
        log.warning("writer_import_failed", error=str(exc))
        return _writer_unavailable_draft()

    try:
        raw = call_claude(
            model=SONNET_MODEL,
            system_prompt=system_prompt,
            user_message=(
                "Generate the full draft now. Write it as a complete, "
                "submission-ready document. Do NOT summarise or "
                "outline — write the actual paper."),
            max_tokens=max_tokens,
        )
        return (raw or "").strip() or _writer_unavailable_draft()
    except Exception as exc:  # noqa: BLE001
        ref = uuid.uuid4().hex[:8]
        log.warning("writer_call_failed", ref=ref, error=str(exc))
        return _writer_unavailable_draft(ref)


def _writer_unavailable_draft(ref: str | None = None) -> str:
    """Sentinel draft used in the test environment and on API
    failures. Carries enough section structure for the post-check
    regex to find headings, plus a single [BOB] block so the UI can
    show the editor flow against a known shape."""
    suffix = f" (ref: {ref})" if ref else ""
    return (
        "## 1. Data and Methodology\n\n"
        f"[BOB — writer unavailable{suffix}; replace this section "
        "with the methodology paragraph.]\n\n"
        "## 2. Preliminary Results and Diagnostics\n\n"
        f"[BOB — writer unavailable{suffix}; replace this section "
        "with the results paragraph.]\n\n"
        "## 3. Roles and Division of Labor\n\n"
        f"[BOB — writer unavailable{suffix}; replace this section "
        "with the roles paragraph.]\n\n"
        "## 4. Next Steps and Open Questions\n\n"
        f"[BOB — writer unavailable{suffix}; replace this section "
        "with the next-steps paragraph.]\n")


# ── Stage 4 — post-generation checks ────────────────────────────────────────


def _post_check_summary(
    paper_md: str,
    verified_data: dict[str, Any],
    citations: dict[str, Any],
) -> dict[str, Any]:
    """Bundles the three regex post-checks plus the word-count
    report into the shape the row + the UI both consume."""
    from tools.template_pipeline import (
        post_check_citations, post_check_numbers, word_count_report,
    )
    unverified_numbers = post_check_numbers(paper_md, verified_data)
    inline_only, refs_only = post_check_citations(paper_md, citations)
    word_counts = word_count_report(paper_md)
    bob_blocks = extract_bob_blocks(paper_md)
    flags: list[dict[str, Any]] = []
    for n in unverified_numbers:
        flags.append({
            "kind": "unverified_number",
            "value": n.get("value"),
            "position": n.get("position"),
        })
    for c in inline_only:
        flags.append({"kind": "citation_unverified", "value": c})
    for sec, info in (word_counts.get("per_section") or {}).items():
        if info.get("status") == "red":
            flags.append({
                "kind": "word_count_over_budget",
                "section": sec,
                "words": info.get("words"),
                "budget": info.get("budget"),
            })
    for b in bob_blocks:
        flags.append({
            "kind": "bob_block",
            "marker": b["marker"],
            "description": b["description"],
            "position": b["position"],
        })
    return {
        "flags":               flags,
        "flag_count":          len(flags),
        "bob_blocks":          bob_blocks,
        "bob_block_count":     len(bob_blocks),
        "unverified_numbers":  unverified_numbers,
        "inline_only_cites":   inline_only,
        "refs_only_cites":     refs_only,
        "word_counts":         word_counts,
    }


# ── Stage 5 — persistence ───────────────────────────────────────────────────


async def _persist_generation_row(
    *,
    template_id: str,
    findings_cache_id: int | None,
    verified_data: dict[str, Any],
    thesis_passed: bool,
    word_counts: dict[str, Any],
    flag_count: int,
    paper_md: str,
    appendix_md: str,
    team_activity: dict[str, Any],
    validation_snapshot: dict[str, Any],
    citations_ids: list[int],
) -> int | None:
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as s:
            r = await s.execute(text(
                "INSERT INTO report_generations "
                "(template_id, findings_cache_id, citations_cache_ids, "
                " team_activity_snapshot, validation_snapshot, "
                " verified_data, thesis_validation_passed, "
                " word_counts, flag_count, paper_md, appendix_md) "
                "VALUES (:t, :f, :c, :ta, :vs, :vd, :tp, :wc, :fc, :p, :a) "
                "RETURNING id"
            ), {
                "t":  template_id,
                "f":  findings_cache_id,
                "c":  json.dumps(citations_ids or []),
                "ta": json.dumps(team_activity or {}, default=str),
                "vs": json.dumps(validation_snapshot or {}, default=str),
                "vd": json.dumps(verified_data or {}, default=str),
                "tp": bool(thesis_passed),
                "wc": json.dumps(word_counts or {}, default=str),
                "fc": int(flag_count or 0),
                "p":  paper_md,
                "a":  appendix_md,
            })
            new_id = r.scalar()
            await s.commit()
            return int(new_id) if new_id is not None else None
    except Exception as exc:  # noqa: BLE001
        log.warning("persist_generation_failed", error=str(exc))
        return None


async def get_generation(generation_id: int) -> dict[str, Any] | None:
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return None
        async with AsyncSessionLocal() as s:
            r = await s.execute(text(
                "SELECT id, template_id, findings_cache_id, "
                " citations_cache_ids, team_activity_snapshot, "
                " validation_snapshot, verified_data, "
                " thesis_validation_passed, word_counts, flag_count, "
                " paper_md, appendix_md, generated_at "
                "FROM report_generations WHERE id = :i"
            ), {"i": int(generation_id)})
            row = r.fetchone()
            if not row:
                return None
            return {
                "id":                          int(row[0]),
                "template_id":                 row[1],
                "findings_cache_id":           row[2],
                "citations_cache_ids":         _maybe_json(row[3], []),
                "team_activity_snapshot":      _maybe_json(row[4], {}),
                "validation_snapshot":         _maybe_json(row[5], {}),
                "verified_data":               _maybe_json(row[6], {}),
                "thesis_validation_passed":    bool(row[7]),
                "word_counts":                 _maybe_json(row[8], {}),
                "flag_count":                  int(row[9] or 0),
                "paper_md":                    row[10] or "",
                "appendix_md":                 row[11] or "",
                "generated_at":                (
                    row[12].isoformat() if row[12] is not None else None),
            }
    except Exception as exc:  # noqa: BLE001
        log.warning("get_generation_failed", error=str(exc))
        return None


async def delete_generation(generation_id: int) -> dict[str, Any]:
    """Hard-deletes a generation and every dependent row.

    May 24 2026 P5 — the Draft Selector dropdown's trash icon
    calls this. Removes report_generations + all dependent
    report_paper_versions rows + the pipeline-audit row that
    pointed at this generation. The citations_cache row is left
    alone (it's keyed on the concept, not the generation, and
    may be reused by other drafts).

    May 24 2026 update — idempotent contract. Returns a result
    dict so the endpoint can distinguish three cases without
    collapsing them into the same False signal:

        {"status": "deleted",        "rows": 1}  — row was removed
        {"status": "already_absent", "rows": 0}  — row didn't exist
                                                   (idempotent success)
        {"status": "error", "error": "..."}      — real DB failure

    The endpoint maps already_absent → 200 OK, error → 500. A
    Delete Draft re-click (or two concurrent deletes) should never
    surface as an error to the user; the prior False-for-both
    behaviour was the bug the user reported.
    """
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return {"status": "error",
                    "error": "Database unavailable."}
        async with AsyncSessionLocal() as s:
            # Drop dependents first. report_paper_versions has no FK
            # cascade in older deployments, so we delete it explicitly.
            # The pipeline audit row is the same; both tables tolerate
            # NULL writes if the FK is set up that way.
            try:
                await s.execute(text(
                    "DELETE FROM report_paper_versions "
                    "WHERE generation_id = :g"
                ), {"g": int(generation_id)})
            except Exception:
                # The table may not exist in older test environments.
                pass
            try:
                await s.execute(text(
                    "DELETE FROM pipeline_audits "
                    "WHERE generation_id = :g"
                ), {"g": int(generation_id)})
            except Exception:
                pass
            r = await s.execute(text(
                "DELETE FROM report_generations "
                "WHERE id = :g"
            ), {"g": int(generation_id)})
            await s.commit()
            rowcount = int(r.rowcount or 0)
            if rowcount > 0:
                log.info("report_generation_deleted",
                         generation_id=generation_id)
                return {"status": "deleted", "rows": rowcount}
            # Row didn't exist — idempotent success path. The
            # caller may have already deleted this draft (a
            # second click, a parallel session, a stale
            # frontend cache) and the user should not see an
            # error for it.
            log.info("report_generation_already_absent",
                     generation_id=generation_id)
            return {"status": "already_absent", "rows": 0}
    except Exception as exc:  # noqa: BLE001
        log.warning("delete_report_generation_failed",
                    error=str(exc),
                    generation_id=generation_id)
        return {"status": "error", "error": str(exc)}


async def list_generations_for_user(
    email: str,
    limit: int = 20,
    template_id: str | None = None,
) -> list[dict[str, Any]]:
    """Lists the most recent generations Bob (or any team member) has
    produced, newest first. Used by the Draft selector dropdown so
    the user can switch back to a prior draft instead of starting
    fresh every login.

    May 23 2026. report_generations carries no user_email column —
    ownership is established by JOINing through report_pipeline_audit
    (triggered_by). A single generation may have multiple audit rows
    (a Step 7 re-run); DISTINCT ON dedupes so each draft appears
    once. Newest by generated_at desc.

    Returns a slim preview shape (id, template_id, flag_count,
    word_count totals, generated_at, first-200-char preview). The
    editor fetches the full paper via get_generation when the user
    actually picks a draft."""
    if not email:
        return []
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return []
        # The template_id filter is optional — passing None lists every
        # template's drafts together (useful for the "all my drafts"
        # picker).
        params: dict[str, Any] = {"e": email, "n": int(limit)}
        tmpl_clause = ""
        if template_id:
            tmpl_clause = " AND g.template_id = :t"
            params["t"] = template_id
        async with AsyncSessionLocal() as s:
            r = await s.execute(text(
                "SELECT * FROM ("
                "  SELECT DISTINCT ON (g.id) "
                "    g.id, g.template_id, g.flag_count, g.word_counts, "
                "    g.generated_at, SUBSTR(g.paper_md, 1, 200) AS preview "
                "  FROM report_generations g "
                "  INNER JOIN report_pipeline_audit a "
                "    ON a.generation_id = g.id "
                "  WHERE a.triggered_by = :e" + tmpl_clause + " "
                "  ORDER BY g.id, a.run_at DESC"
                ") sub "
                "ORDER BY generated_at DESC NULLS LAST "
                "LIMIT :n"
            ), params)
            rows = r.fetchall()
        out: list[dict[str, Any]] = []
        for row in rows:
            wc = _maybe_json(row[3], {})
            # word_counts shape: {section_key: {count, status}, ...,
            # total: {count, status}}. Sum every section count as a
            # belt-and-braces total if "total" isn't named.
            total = 0
            if isinstance(wc, dict):
                tot = wc.get("total")
                if isinstance(tot, dict) and isinstance(tot.get("count"), int):
                    total = tot["count"]
                else:
                    total = sum(
                        v.get("count", 0)
                        for v in wc.values()
                        if isinstance(v, dict)
                        and isinstance(v.get("count"), int))
            out.append({
                "id":              int(row[0]),
                "template_id":     row[1],
                "flag_count":      int(row[2] or 0),
                "word_count_total": int(total),
                "generated_at": (
                    row[4].isoformat() if row[4] is not None else None),
                "preview": (row[5] or "").strip(),
            })
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("list_generations_for_user_failed", error=str(exc))
        return []


def _maybe_json(v: Any, fallback: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return fallback
    return v if v is not None else fallback


async def _update_paper_md(
    generation_id: int,
    paper_md: str,
    flag_count: int,
    word_counts: dict[str, Any],
) -> bool:
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return False
        async with AsyncSessionLocal() as s:
            await s.execute(text(
                "UPDATE report_generations "
                "SET paper_md = :p, flag_count = :f, word_counts = :w "
                "WHERE id = :i"
            ), {
                "p": paper_md,
                "f": int(flag_count or 0),
                "w": json.dumps(word_counts or {}, default=str),
                "i": int(generation_id),
            })
            await s.commit()
            return True
    except Exception as exc:  # noqa: BLE001
        log.warning("update_paper_md_failed", error=str(exc))
        return False


async def _load_citations_for_generation(
    generation_id: int | None = None,
) -> dict[str, dict[str, Any]]:
    """Reads citations_cache by generation_id when supplied, else the
    most recent one row per concept_id. Used by the appendix and the
    final-check citation cross-ref."""
    out: dict[str, dict[str, Any]] = {}
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        from tools.template_pipeline import _format_citation
        if AsyncSessionLocal is None:
            return {}
        async with AsyncSessionLocal() as s:
            if generation_id is not None:
                r = await s.execute(text(
                    "SELECT concept_id, author, year, title, "
                    " journal_or_institution, volume_issue_pages, "
                    " url, verification_status, search_query_used "
                    "FROM citations_cache "
                    "WHERE generation_id = :g"
                ), {"g": int(generation_id)})
            else:
                r = await s.execute(text(
                    "SELECT DISTINCT ON (concept_id) "
                    " concept_id, author, year, title, "
                    " journal_or_institution, volume_issue_pages, "
                    " url, verification_status, search_query_used "
                    "FROM citations_cache "
                    "ORDER BY concept_id, created_at DESC"))
            for row in r.fetchall():
                concept_id = row[0]
                entry = {
                    "concept_id":             concept_id,
                    "author":                 row[1],
                    "year":                   row[2],
                    "title":                  row[3],
                    "journal_or_institution": row[4],
                    "volume_issue_pages":     row[5],
                    "url":                    row[6],
                    "verification_status":    row[7],
                    "search_query_used":      row[8],
                }
                entry["formatted"] = (
                    _format_citation(entry)
                    if entry.get("verification_status") == "verified"
                    else None)
                out[concept_id] = entry
    except Exception as exc:  # noqa: BLE001
        log.warning("load_citations_failed", error=str(exc))
    return out


# ── Main orchestrator ────────────────────────────────────────────────────────


async def generate_paper(template_id: str) -> dict[str, Any]:
    """End-to-end. Returns a dict the endpoint surfaces.

    Failure modes:
      template_not_found      — 404 path
      thesis_validation_blocked — 422 path (does NOT persist)
      writer_unavailable      — persists with sentinel draft
    """
    inputs = await _assemble_inputs(template_id)
    if inputs.get("error") == "template_not_found":
        return {"error": "template_not_found"}

    # Thesis validation — BLOCKING.
    from tools.template_pipeline import validate_thesis, substitute_prompt
    thesis = validate_thesis(
        inputs["verified_data"], inputs["ranked_findings"])
    if not thesis["passed"]:
        return {
            "error":             "thesis_validation_blocked",
            "thesis_validation": thesis,
        }

    # Source citations + persist (returns the dict the writer reads).
    citations = await source_template_citations(template_id)
    citations_ids = await _ids_for_concepts(list(citations.keys()))

    # Build the writer prompt.
    template = inputs["template"]
    system_prompt = template.get("system_prompt") or ""
    substituted = substitute_prompt(
        system_prompt,
        inputs["verified_data"],
        inputs["ranked_findings"],
        citations,
        inputs["team_activity"],
        inputs["validation_summary"],
    )

    # Call the writer in a worker thread.
    raw = await asyncio.to_thread(
        _call_writer_sync, substituted, 3000)

    # ── May 24 2026 RW2 hotfix — strategy display-name substitution.
    # The prompt instructs the model to use display names, but for
    # any raw SCREAMING_SNAKE_CASE identifier the model leaves
    # behind, this post-processing pass rewrites it to the human
    # form (e.g. EQUAL_WEIGHT → Equal-Weight). Applied BEFORE the
    # post-check + word-count + persistence so every downstream
    # consumer reads the clean text. Idempotent — see
    # substitute_strategy_names docstring.
    try:
        from agents.academic_writer import substitute_strategy_names
        raw = substitute_strategy_names(raw)
    except Exception as exc:  # noqa: BLE001
        log.warning("strategy_substitution_failed", error=str(exc))

    # Post-check + word counts.
    checks = _post_check_summary(
        raw, inputs["verified_data"], citations)

    # Build appendix markdown for archive (the docx builder will
    # rebuild from the same context dict at download time).
    findings_row = inputs.get("findings_row") or {}
    appendix_context = _build_appendix_context(
        verified_data=inputs["verified_data"],
        ranked_findings=inputs["ranked_findings"],
        team_activity=inputs["team_activity"],
        validation_summary=inputs["validation_summary"],
        citations=citations,
        findings_metadata={
            "computed_at": (findings_row or {}).get("computed_at"),
            "data_hash":   (findings_row or {}).get("data_hash"),
            "audit_status": (
                inputs["validation_summary"].get("layer3_status")
                if inputs["validation_summary"] else None),
        },
    )
    appendix_md = _appendix_context_to_md(appendix_context)
    # Substitute strategy display names in the appendix too — the
    # appendix carries findings prose that references strategies
    # by name. Idempotent so the explicit second call is safe.
    try:
        from agents.academic_writer import substitute_strategy_names
        appendix_md = substitute_strategy_names(appendix_md)
    except Exception as exc:  # noqa: BLE001
        log.warning("appendix_strategy_substitution_failed",
                    error=str(exc))

    findings_cache_id = (findings_row or {}).get("id")
    gen_id = await _persist_generation_row(
        template_id=template_id,
        findings_cache_id=findings_cache_id,
        verified_data=inputs["verified_data"],
        thesis_passed=True,
        word_counts=checks["word_counts"],
        flag_count=checks["flag_count"],
        paper_md=raw,
        appendix_md=appendix_md,
        team_activity=inputs["team_activity"],
        validation_snapshot=inputs["validation_summary"],
        citations_ids=citations_ids,
    )

    return {
        "id":                 gen_id,
        "template_id":        template_id,
        "paper_md":           raw,
        "appendix_md":        appendix_md,
        "verified_data":      inputs["verified_data"],
        "ranked_findings":    inputs["ranked_findings"],
        "team_activity":      inputs["team_activity"],
        "activity_flags":     inputs["activity_flags"],
        "validation_summary": inputs["validation_summary"],
        "citations":          citations,
        "thesis_validation":  thesis,
        **checks,
    }


async def _ids_for_concepts(concept_ids: list[str]) -> list[int]:
    """Reads the inserted row ids back so report_generations.
    citations_cache_ids has the correct cross-references."""
    if not concept_ids:
        return []
    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is None:
            return []
        async with AsyncSessionLocal() as s:
            r = await s.execute(text(
                "SELECT DISTINCT ON (concept_id) id "
                "FROM citations_cache "
                "WHERE concept_id = ANY(:c) "
                "ORDER BY concept_id, created_at DESC"
            ), {"c": list(concept_ids)})
            return [int(row[0]) for row in r.fetchall()]
    except Exception as exc:  # noqa: BLE001
        log.warning("ids_for_concepts_failed", error=str(exc))
        return []


# ── Editor support ──────────────────────────────────────────────────────────


_ITERATION_SYSTEM = (
    "You are the academic writer iterating on an existing midpoint paper "
    "draft. You will receive a paragraph or sentence selection and an "
    "action instruction. Follow these absolute rules:\n\n"
    "• Do NOT introduce any number, percentage, or statistic that is "
    "not already present in the selection.\n"
    "• Do NOT introduce any inline citation that is not already "
    "present in the selection.\n"
    "• Do NOT change the meaning of the selection — only its "
    "phrasing, length, or structure as the action prescribes.\n"
    "• Match the document's tone: graduate-level academic finance, "
    "active voice, no hedging on supported findings, no use of the "
    "word 'interesting'.\n\n"
    "Return ONLY the rewritten text. No preamble, no explanation, no "
    "markdown fences.")


_ACTION_PROMPTS = {
    "rephrase": (
        "Rephrase the selection. Preserve all numbers and citations. "
        "Approximately the same word count."),
    "tighten": (
        "Tighten the selection. Same meaning in fewer words. Preserve "
        "all numbers and citations. Aim for a 25-35 percent word "
        "reduction."),
    "expand": (
        "Expand the selection with one additional sentence of "
        "interpretation or context. Do NOT introduce new numbers or "
        "citations beyond those already present."),
    "ask": (
        "Apply the following instruction to the selection, respecting "
        "every absolute rule above.\n\nInstruction: "),
}


def _iterate_sync(
    action: str, selection: str, instruction: str | None,
) -> str:
    """Synchronous worker for the iteration endpoint.

    Returns the rewritten text, or — on a writer-unavailable / test-
    environment path — a sentinel string the caller surfaces verbatim.
    Numbers and citations in the input are passed through verbatim
    when the writer is unavailable, so the editor remains usable
    against a cold environment.
    """
    if action not in _ACTION_PROMPTS:
        return selection
    try:
        from agents.base import call_claude, SONNET_MODEL
    except Exception:  # noqa: BLE001
        return selection

    if action == "ask":
        action_prompt = _ACTION_PROMPTS["ask"] + (instruction or "")
    else:
        action_prompt = _ACTION_PROMPTS[action]
    user_message = (
        f"{action_prompt}\n\nSELECTION:\n{selection}\n\n"
        "Return ONLY the rewritten text.")

    try:
        raw = call_claude(
            model=SONNET_MODEL,
            system_prompt=_ITERATION_SYSTEM,
            user_message=user_message,
            max_tokens=600,
        )
        return (raw or "").strip() or selection
    except Exception as exc:  # noqa: BLE001
        log.warning("iterate_failed", action=action, error=str(exc))
        return selection


async def iterate_text(
    generation_id: int,
    action: str,
    selection: str,
    *,
    instruction: str | None = None,
) -> dict[str, Any]:
    """Returns {rewritten, original, word_delta}. Does NOT persist —
    the editor patches paper_md via PATCH /paper-md once Bob accepts.
    Forbidden-number scan: the rewritten text is re-scanned against
    the generation's verified_data so the caller can warn before
    accepting."""
    rewritten = await asyncio.to_thread(
        _iterate_sync, action, selection, instruction)
    gen = await get_generation(generation_id)
    verified_data = (gen or {}).get("verified_data") or {}
    citations = await _load_citations_for_generation(generation_id)
    new_numbers = _new_numbers_introduced(
        selection, rewritten, verified_data)
    new_citations = _new_citations_introduced(
        selection, rewritten, citations)
    return {
        "original":              selection,
        "rewritten":             rewritten,
        "word_delta":            (
            len(rewritten.split()) - len(selection.split())),
        "new_unverified_numbers":   new_numbers,
        "new_unverified_citations": new_citations,
    }


def _new_numbers_introduced(
    before: str, after: str, verified_data: dict[str, Any],
) -> list[float]:
    from tools.template_pipeline import post_check_numbers
    before_set = {n["value"] for n in post_check_numbers(
        before, verified_data)}
    after_flagged = post_check_numbers(after, verified_data)
    return [n["value"] for n in after_flagged
            if n["value"] not in before_set]


def _new_citations_introduced(
    before: str, after: str, citations: dict[str, Any],
) -> list[str]:
    from tools.template_pipeline import post_check_citations
    before_set = set(post_check_citations(before, citations)[0])
    after_flagged = post_check_citations(after, citations)[0]
    return [c for c in after_flagged if c not in before_set]


async def resolve_bob_block(
    generation_id: int, marker: str, replacement: str,
) -> dict[str, Any]:
    """Replaces the FIRST occurrence of `marker` in paper_md with the
    user-supplied replacement text, re-runs the post-check, persists
    the new paper_md + flag_count + word_counts."""
    gen = await get_generation(generation_id)
    if not gen:
        return {"error": "generation_not_found"}
    paper_md = gen.get("paper_md") or ""
    if marker not in paper_md:
        return {"error": "marker_not_found", "marker": marker}
    new_md = paper_md.replace(marker, replacement, 1)
    citations = await _load_citations_for_generation(generation_id)
    checks = _post_check_summary(
        new_md, gen.get("verified_data") or {}, citations)
    saved = await _update_paper_md(
        generation_id, new_md,
        checks["flag_count"], checks["word_counts"])
    return {
        "saved":            bool(saved),
        "paper_md":         new_md,
        **checks,
    }


async def update_paper_md(
    generation_id: int, paper_md: str,
    *,
    expected_revision: int | None = None,
    saved_by_email: str | None = None,
    source: str = "auto_edit",
    create_snapshot: bool = True,
) -> dict[str, Any]:
    """Inline editor save path. The frontend PATCHes the whole
    paper_md on every keystroke debounce — we re-run the post-check
    and persist.

    Concurrent-edit detection (item 2, May 23 2026):
      If `expected_revision` is supplied AND the row's current
      paper_revision does not match, the function returns
      {"error": "revision_mismatch", "current_revision": <int>,
       "expected_revision": <int>} so the endpoint can return 409.

    Version snapshots:
      Every successful save records a snapshot in
      report_paper_versions and bumps paper_revision. Pass
      `create_snapshot=False` only when the caller has already
      taken the snapshot itself (e.g. the restore path).
    """
    gen = await get_generation(generation_id)
    if not gen:
        return {"error": "generation_not_found"}

    # Optimistic concurrency: compare expected_revision against the
    # current value when the caller supplies one. The default (no
    # check) keeps the auto-save loop from blocking on its own
    # writes; the explicit Save Version action in the editor passes
    # the value it last saw.
    if expected_revision is not None:
        from tools.paper_versions import check_revision
        current = await check_revision(generation_id)
        if current is not None and int(current) != int(expected_revision):
            return {
                "error":              "revision_mismatch",
                "current_revision":   current,
                "expected_revision":  int(expected_revision),
            }

    citations = await _load_citations_for_generation(generation_id)
    checks = _post_check_summary(
        paper_md, gen.get("verified_data") or {}, citations)
    saved = await _update_paper_md(
        generation_id, paper_md,
        checks["flag_count"], checks["word_counts"])
    new_revision: int | None = None
    snapshot: dict[str, Any] | None = None
    if saved:
        from tools.paper_versions import (
            bump_paper_revision, save_version,
        )
        new_revision = await bump_paper_revision(generation_id)
        # Performance fix (item 6, May 23 2026): the auto-save loop
        # fires this endpoint every ~30s on debounce; creating a
        # version snapshot on every keystroke round-trips
        # report_paper_versions for no real value (Bob can't even
        # see the intermediate auto-saves on the version panel).
        # Snapshots fire on the meaningful save kinds (manual,
        # auto_iterate, auto_resolve_bob, restore); auto_edit is
        # the debounced keystroke path and skips the write.
        snapshot_worth_taking = (
            create_snapshot and source != "auto_edit")
        if snapshot_worth_taking:
            snapshot = await save_version(
                generation_id, paper_md,
                saved_by_email=saved_by_email,
                source=source,
                flag_count=checks["flag_count"],
                word_counts=checks["word_counts"])
    return {
        "saved":    bool(saved),
        "paper_md": paper_md,
        "paper_revision": new_revision,
        "snapshot": snapshot,
        **checks,
    }


async def rebalance_paper(generation_id: int) -> dict[str, Any]:
    """May 24 2026 — two-pass draft generation Pass 2 (MVP).

    After Bob adjudicates every [BOB] block (Accept / Edit /
    Reject), the section word counts are off-budget because the
    Pass-1 prose was sized assuming full [BOB] integrations. This
    helper re-runs the writer over the CURRENT paper_md with a
    rebalance instruction: rewrite each off-budget section to land
    within its word limit (see _SECTION_BUDGETS) while keeping
    every inline citation and specific number intact.

    Returns the updated paper_md + the new word_counts. The
    paper_md is persisted via _update_paper_md and snapshotted to
    version_history with source='two_pass_rebalance' so Bob can
    revert if the rebalance over-trimmed.
    """
    from tools.template_pipeline import word_count_report, _SECTION_BUDGETS
    gen = await get_generation(generation_id)
    if not gen:
        return {"error": "generation_not_found"}
    paper_md = gen.get("paper_md") or ""
    if not paper_md.strip():
        return {"error": "empty_paper"}

    # Identify off-budget sections so the prompt is specific.
    counts = word_count_report(paper_md)
    per = counts.get("per_section") or {}
    targets: list[tuple[int, int, int]] = []
    for sec_num, budget in _SECTION_BUDGETS.items():
        info = per.get(sec_num) or {}
        words = int(info.get("words") or 0)
        # Within ±10% — no work needed.
        if words and abs(words - budget) / budget <= 0.10:
            continue
        if words:
            targets.append((sec_num, words, budget))
    if not targets:
        # Everything in range — nothing to do.
        return {
            "saved":      False,
            "paper_md":   paper_md,
            "rebalanced": False,
            "note":       "All sections within ±10% of budget.",
            **_post_check_summary(
                paper_md, gen.get("verified_data") or {},
                await _load_citations_for_generation(generation_id)),
        }

    # Build the rebalance instruction. Compact + specific — names
    # each off-budget section and its delta so the writer knows
    # exactly what to do.
    instructions = (
        "REBALANCE PASS — the user has integrated every [BOB] "
        "block and now needs the section bodies brought to their "
        "exact word budgets.\n\n"
        "Rebalance the FOLLOWING sections in the draft below. For "
        "each section: rewrite the BODY ONLY (do NOT alter "
        "section headings, do NOT alter inline citation references, "
        "do NOT change specific numbers or statistics). Land each "
        "section within ±5 words of its target. Keep the existing "
        "prose voice and academic register from the writer prompt.\n\n"
    )
    for sec_num, words, budget in targets:
        delta = budget - words
        verb = "trim" if delta < 0 else "expand"
        instructions += (
            f"  Section {sec_num}: currently {words} words → "
            f"target {budget} words ({verb} by {abs(delta)})\n")
    instructions += (
        "\nReturn the COMPLETE updated paper_md with every section "
        "present (sections not in the list above are untouched). "
        "Do NOT add a preface or epilogue; return only the paper.")

    user_message = instructions + "\n\n=== CURRENT DRAFT ===\n\n" + paper_md

    try:
        rewritten = await asyncio.to_thread(
            _call_writer_sync, user_message, 3500)
    except Exception as exc:  # noqa: BLE001
        log.warning("rebalance_writer_call_failed", error=str(exc))
        return {"error": "writer_unavailable", "detail": str(exc)}

    # Defensive: if the writer returned nothing usable, fall back to
    # the unchanged paper rather than wiping Bob's draft.
    if not rewritten or not rewritten.strip():
        return {
            "error":    "writer_returned_empty",
            "paper_md": paper_md,
        }

    # Apply strategy display-name substitution (RW2 pass).
    try:
        from agents.academic_writer import substitute_strategy_names
        rewritten = substitute_strategy_names(rewritten)
    except Exception:  # noqa: BLE001
        pass

    # Persist + snapshot for revert.
    citations = await _load_citations_for_generation(generation_id)
    checks = _post_check_summary(
        rewritten, gen.get("verified_data") or {}, citations)
    await _update_paper_md(
        generation_id, rewritten,
        checks["flag_count"], checks["word_counts"])
    try:
        from tools.paper_versions import save_version, bump_paper_revision
        await bump_paper_revision(generation_id)
        await save_version(
            generation_id, rewritten,
            saved_by_email=None,
            source="two_pass_rebalance",
            flag_count=checks["flag_count"],
            word_counts=checks["word_counts"],
            label="Pass 2 — word-count rebalance")
    except Exception as exc:  # noqa: BLE001
        log.warning("rebalance_snapshot_failed", error=str(exc))

    return {
        "saved":      True,
        "paper_md":   rewritten,
        "rebalanced": True,
        "targets":    [
            {"section": s, "before": w, "target": b}
            for s, w, b in targets],
        **checks,
    }


async def run_final_check(generation_id: int) -> dict[str, Any]:
    """Re-runs the three post-checks against the current paper_md.
    Same checks that ran at generation time — the editor's iteration
    + Bob's manual edits may have added flags or removed them. Updates
    flag_count on the row so the download endpoints can gate."""
    gen = await get_generation(generation_id)
    if not gen:
        return {"error": "generation_not_found"}
    citations = await _load_citations_for_generation(generation_id)
    paper_md = gen.get("paper_md") or ""
    verified_data = gen.get("verified_data") or {}
    checks = _post_check_summary(paper_md, verified_data, citations)
    await _update_paper_md(
        generation_id, paper_md,
        checks["flag_count"], checks["word_counts"])
    return {
        "passed":     checks["flag_count"] == 0,
        "flag_count": checks["flag_count"],
        **checks,
    }


# ── Appendix context assembly ───────────────────────────────────────────────


def _build_appendix_context(
    *, verified_data: dict[str, Any],
    ranked_findings: list[dict[str, Any]],
    team_activity: dict[str, Any],
    validation_summary: dict[str, Any],
    citations: dict[str, Any],
    findings_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Shapes the context dict the docx appendix builder consumes."""
    from datetime import datetime, timezone
    return {
        "verified_data":       verified_data,
        "ranked_findings":     ranked_findings,
        "team_activity":       team_activity,
        "validation_summary":  validation_summary,
        "citations_cache":     citations,
        "findings_metadata":   findings_metadata or {},
        "generated_at":        datetime.now(timezone.utc).isoformat(),
    }


def _appendix_context_to_md(ctx: dict[str, Any]) -> str:
    """Renders the appendix context as plain markdown for archival
    inside report_generations.appendix_md. The docx builder reads
    from the JSON context — this string is for human inspection /
    UI preview only."""
    parts: list[str] = ["# Appendix\n"]
    parts.append("## Appendix A — Platform Overview")
    vd = ctx.get("verified_data") or {}
    parts.append(f"Study period: {vd.get('study_period_start')} "
                 f"to {vd.get('study_period_end')}")
    parts.append(f"Monthly observations: {vd.get('n_months')}")
    parts.append("")
    parts.append("## Appendix B — Full Analytical Findings")
    for i, f in enumerate(ctx.get("ranked_findings") or [], 1):
        parts.append(
            f"### F{i} — {f.get('title', '')} "
            f"({f.get('nugget_strength', 'LOW')})")
        parts.append(f"FINDING: {f.get('finding', '')}")
        for e in (f.get("evidence") or []):
            parts.append(f"  • {e}")
        parts.append(f"IMPLICATION: {f.get('implication', '')}")
        if f.get("surprise"):
            parts.append(
                f"SURPRISE: {f.get('surprise_reason') or 'yes'}")
        parts.append("")
    parts.append("## Appendix C — Team Activity Log")
    activity = ctx.get("team_activity") or {}
    for k, v in sorted(activity.items()):
        parts.append(f"  {k}: {v}")
    parts.append("")
    parts.append("## Appendix D — Independent Data Validation Summary")
    vs = ctx.get("validation_summary") or {}
    for layer in ("layer1", "layer2", "layer3"):
        parts.append(
            f"  {layer}: status={vs.get(layer + '_status', '—')} "
            f"checks={vs.get(layer + '_count', '—')} "
            f"date={vs.get(layer + '_date', '—')}")
    return "\n".join(parts)


# ── Bytes accessors (used by the download endpoints) ────────────────────────


async def render_paper_bytes(generation_id: int) -> bytes | None:
    """Builds the paper docx from the persisted paper_md + the
    citation cache. Returns None on a missing generation.

    Dispatches between the APA paper formatter and the executive
    brief memo formatter based on the template's format_spec.
    memo_style flag (set by migration 034 on the executive_brief_
    fna670 template row). The APA formatter is the default — every
    template that doesn't opt into memo_style gets the APA layout
    Bob expects for the midpoint paper."""
    gen = await get_generation(generation_id)
    if not gen:
        return None
    from tools.template_pipeline import _format_citation  # noqa: F401
    citations = await _load_citations_for_generation(generation_id)
    refs_md = _references_md(citations)

    # Look up the template's format_spec to decide which renderer.
    memo_style = False
    try:
        from tools.report_templates import get_template
        tmpl = await get_template(gen.get("template_id") or "")
        if tmpl:
            fs = tmpl.get("format_spec") or {}
            memo_style = bool(fs.get("memo_style"))
    except Exception:  # noqa: BLE001
        memo_style = False

    if memo_style:
        from tools.report_writer_docx_brief import build_brief_docx
        return await asyncio.to_thread(
            build_brief_docx, gen["paper_md"], references_md=refs_md)
    from tools.report_writer_docx import build_paper_docx
    return await asyncio.to_thread(
        build_paper_docx, gen["paper_md"], references_md=refs_md)


async def render_appendix_bytes(generation_id: int) -> bytes | None:
    """Builds the appendix docx from a fresh context assembly — we
    rebuild from the persisted snapshots so the appendix always
    matches the row, even if Bob edited paper_md after generation."""
    gen = await get_generation(generation_id)
    if not gen:
        return None
    from tools.report_writer_docx import build_appendix_docx
    citations = await _load_citations_for_generation(generation_id)
    context = _build_appendix_context(
        verified_data=gen.get("verified_data") or {},
        ranked_findings=gen.get("verified_data", {}).get(
            "ranked_findings", []) or [],
        team_activity=gen.get("team_activity_snapshot") or {},
        validation_summary=gen.get("validation_snapshot") or {},
        citations=citations,
        findings_metadata={
            "computed_at": gen.get("generated_at"),
            "data_hash":   None,
            "audit_status": (
                (gen.get("validation_snapshot") or {})
                .get("layer3_status")),
        },
    )
    # Pull ranked findings from the findings cache where verified_data
    # didn't carry them — earlier generations may not.
    if not context["ranked_findings"]:
        from tools.analytical_findings import get_latest_findings
        row = await get_latest_findings()
        if row:
            context["ranked_findings"] = row.get("ranked_findings") or []
    return await asyncio.to_thread(build_appendix_docx, context)


_REVIEW_SYSTEM = (
    "You are a rigorous but constructive pre-submission reviewer for "
    "the FNA670 Industry Practicum midpoint paper. You are NOT the "
    "grader — your job is to help the team submit the strongest "
    "possible version of the paper.\n\n"
    "You will receive the draft, the rubric criteria, the "
    "verified_data the writer drew from, the ranked findings the "
    "writer was told to emphasise, and the word budgets per section. "
    "Score each rubric criterion separately. Cite specific text from "
    "the draft as evidence — never invent evidence. Be specific in "
    "every gap and every suggestion (not 'improve the analysis' but "
    "'Section 2 does not interpret the CVaR finding in terms of its "
    "capital-planning implication').\n\n"
    "Return a JSON object with this exact shape (and no other text):\n"
    "{\n"
    '  "per_criterion": [\n'
    '    {"criterion_id": "...", "score": "strong|developing|needs_work", '
    '"evidence": "...", "gap": "...", "suggestion": "..."}\n'
    "  ],\n"
    '  "data_gaps":         ["..."],\n'
    '  "citation_gaps":     ["..."],\n'
    '  "thesis_coherence":  ["..."],\n'
    '  "tone_violations":   ["..."],\n'
    '  "length_compliance": ["..."],\n'
    '  "readiness":         "ready_to_submit|needs_minor_revision|needs_significant_revision",\n'
    '  "summary":           "one-paragraph overall assessment"\n'
    "}\n\n"
    "Readiness rules:\n"
    "  ready_to_submit            — all four criteria strong or "
    "developing, zero data gaps, zero citation gaps, thesis coherent.\n"
    "  needs_minor_revision       — one or two criteria developing "
    "with specific fixable gaps; minor tone or length issues.\n"
    "  needs_significant_revision — any criterion needs_work, multiple "
    "data or citation gaps, or thesis drift.")


def _review_sync(
    paper_md: str,
    rubric: dict[str, Any],
    verified_data: dict[str, Any],
    ranked_findings: list[dict[str, Any]],
    word_counts: dict[str, Any],
) -> dict[str, Any]:
    """Synchronous worker for the review endpoint.

    Returns the parsed review dict, or — on a writer-unavailable
    / test-environment path — a deterministic stub the UI can render.
    Never raises.
    """
    try:
        from agents.base import call_claude, SONNET_MODEL
    except Exception:  # noqa: BLE001
        return _review_unavailable_stub(rubric)

    context = {
        "rubric_criteria":    rubric.get("criteria") or [],
        "verified_data":      verified_data,
        "ranked_findings":    ranked_findings,
        "word_counts":        word_counts,
        "section_structure": {
            "section_1": "Data and Methodology (~250 words)",
            "section_2": "Preliminary Results and Diagnostics (~300 words)",
            "section_3": "Roles and Division of Labor (~150 words)",
            "section_4": "Next Steps and Open Questions (~125 words)",
        },
    }
    user_message = (
        f"Draft to review:\n\n---\n{paper_md}\n---\n\n"
        "Context (rubric, verified data, findings, word counts):\n"
        f"{json.dumps(context, indent=2, default=str)}\n\n"
        "Score every criterion and produce the JSON object now. No "
        "preamble. No markdown fences.")

    try:
        raw = call_claude(
            model=SONNET_MODEL,
            system_prompt=_REVIEW_SYSTEM,
            user_message=user_message,
            max_tokens=2000,
        )
        parsed = _parse_review_json(raw)
        if parsed is None:
            ref = uuid.uuid4().hex[:8]
            log.warning("review_parse_failed", ref=ref)
            return _review_unavailable_stub(rubric, ref=ref)
        return parsed
    except Exception as exc:  # noqa: BLE001
        ref = uuid.uuid4().hex[:8]
        log.warning("review_call_failed", ref=ref, error=str(exc))
        return _review_unavailable_stub(rubric, ref=ref)


def _parse_review_json(raw: str) -> dict[str, Any] | None:
    if not raw:
        return None
    s = raw.strip()
    m = re.match(r"^```(?:json)?\s*\n?(.*?)```\s*$", s, flags=re.DOTALL)
    if m:
        s = m.group(1).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return None


def _review_unavailable_stub(
    rubric: dict[str, Any], ref: str | None = None,
) -> dict[str, Any]:
    """Used when the review agent is unreachable. Renders the rubric
    criteria with developing scores and a single tone-neutral
    suggestion so the editor stays usable."""
    suffix = f" (ref: {ref})" if ref else ""
    return {
        "per_criterion": [
            {"criterion_id": c.get("criterion_id"),
             "score": "developing",
             "evidence": "(review agent unavailable; manual review required)",
             "gap": (
                 f"Automated review could not complete{suffix}. "
                 "Read the criterion and self-assess."),
             "suggestion": (
                 "Re-run the academic review once the writer is "
                 "available, or perform a manual rubric check.")}
            for c in (rubric.get("criteria") or [])
        ],
        "data_gaps":         [],
        "citation_gaps":     [],
        "thesis_coherence":  [],
        "tone_violations":   [],
        "length_compliance": [],
        "readiness": "needs_minor_revision",
        "summary": (
            f"Automated review unavailable{suffix}; readiness defaulted "
            "to needs_minor_revision pending manual review."),
    }


async def run_academic_review(
    generation_id: int,
) -> dict[str, Any]:
    """End-to-end review path. Loads the generation, finds the active
    rubric for its template, calls the review agent, persists the
    payload + readiness on the row, returns the response shape the
    endpoint surfaces.

    The download gate is a SOFT gate — the row stores the readiness
    so the endpoint can decide; the endpoint allows download with
    `acknowledge_warning=True` when readiness is
    needs_significant_revision."""
    from datetime import datetime, timezone
    from tools.report_rubrics import get_latest_rubric

    gen = await get_generation(generation_id)
    if not gen:
        return {"error": "generation_not_found"}
    rubric = await get_latest_rubric(gen["template_id"])
    if not rubric:
        return {"error": "rubric_not_found",
                "template_id": gen["template_id"]}

    # Load latest ranked findings (the editor may have re-staged
    # since the generation; fall back to the generation's own
    # verified_data).
    from tools.analytical_findings import get_latest_findings
    findings_row = await get_latest_findings() or {}
    ranked = findings_row.get("ranked_findings") or []

    payload = await asyncio.to_thread(
        _review_sync,
        gen.get("paper_md") or "",
        rubric,
        gen.get("verified_data") or {},
        ranked,
        gen.get("word_counts") or {},
    )
    payload["rubric_version"] = rubric.get("version")
    payload["rubric_id"] = rubric.get("id")
    readiness = payload.get("readiness") or "needs_minor_revision"

    try:
        from sqlalchemy import text
        from database import AsyncSessionLocal
        if AsyncSessionLocal is not None:
            async with AsyncSessionLocal() as s:
                await s.execute(text(
                    "UPDATE report_generations "
                    "SET academic_review = :r, "
                    "    academic_readiness = :rd, "
                    "    academic_review_at = :at "
                    "WHERE id = :i"
                ), {
                    "r":  json.dumps(payload, default=str),
                    "rd": readiness,
                    "at": datetime.now(timezone.utc),
                    "i":  int(generation_id),
                })
                await s.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("persist_review_failed", error=str(exc))

    return payload


def _references_md(citations: dict[str, Any]) -> str:
    """Builds an alphabetical References block from verified
    citations only. The appendix renders its own; the paper docx
    appends this one at the end of the body."""
    verified = [
        c for c in (citations or {}).values()
        if c.get("verification_status") == "verified"]
    if not verified:
        return ""
    verified.sort(key=lambda c: (c.get("author") or "").lower())
    return "\n\n".join(c.get("formatted") or "" for c in verified)
