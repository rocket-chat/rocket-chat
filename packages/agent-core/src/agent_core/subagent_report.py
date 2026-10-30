"""A child's structured report: read from its ``report`` call, stored whole, shown clamped.

The parent reads a rendering in which every field has a hard cap that no single finding or
summary can get around (AGENTS.md §3.D), and that says what was cut and where the whole report
is: the transcript row and the ``subagent_completed`` event carry it in full.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from specifications.interfaces.agent import (
    FINDING_SEVERITIES,
    SubagentChanges,
    SubagentFinding,
    SubagentReport,
    SubagentTaskResult,
)

REPORTED_STATUSES = ("completed", "partial", "blocked")
# What is stored: generous, but finite, so a runaway report cannot bloat a row or an event.
STORED_TEXT_CHARS = 20_000
STORED_ITEM_CHARS = 2_000
STORED_ITEMS = 200
# What the parent reads.
SUMMARY_CHARS = 4_000
FINDINGS_SHOWN = 20
FINDING_CHARS = 400
LIST_ITEMS_SHOWN = 10
LIST_ITEM_CHARS = 300
DIFF_STAT_CHARS = 1_500
DATA_CHARS = 2_000
RENDERED_MAX_CHARS = 12_000


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _strings(value: Any) -> list[str]:
    items = value if isinstance(value, list) else []
    return [_text(item, STORED_ITEM_CHARS) for item in items[:STORED_ITEMS] if str(item).strip()]


def _line_number(value: Any) -> int | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return int(value)
    return int(value) if isinstance(value, str) and value.isdigit() else None


def _finding(raw: Any) -> SubagentFinding | None:
    if not isinstance(raw, dict):
        return None
    line = raw.get("line")
    severity = str(raw.get("severity") or "info").strip().lower()
    return SubagentFinding(
        file=_text(raw.get("file"), 500),
        line=_line_number(line),
        severity=severity if severity in FINDING_SEVERITIES else "info",
        evidence=_text(raw.get("evidence"), STORED_ITEM_CHARS),
    )


def schema_problem(data: Any, schema: dict[str, Any] | None) -> str | None:
    """Why ``data`` does not follow the role's output schema (its required keys and their basic
    types), or ``None`` when it does or the role has none."""
    if not schema:
        return None
    if not isinstance(data, dict):
        return "`data` must be an object that follows the role's output schema"
    missing = [key for key in schema.get("required") or [] if key not in data]
    if missing:
        return f"`data` is missing {', '.join(map(str, missing))}"
    kinds: dict[str, type | tuple[type, ...]] = {
        "string": str, "number": (int, float), "integer": int, "boolean": bool,
        "array": list, "object": dict,
    }  # fmt: skip
    for key, spec in (schema.get("properties") or {}).items():
        expected = kinds.get(str(spec.get("type"))) if isinstance(spec, dict) else None
        if key in data and expected is not None and not isinstance(data[key], expected):
            return f"`data.{key}` must be of type {spec.get('type')}"
    return None


def parse_report(arguments: dict[str, Any]) -> SubagentReport:
    """The report a child's ``report`` call describes, bounded for storage."""
    status = str(arguments.get("status") or "completed").strip().lower()
    findings = [
        f for raw in (arguments.get("findings") or [])[:STORED_ITEMS] if (f := _finding(raw))
    ]
    data = arguments.get("data")
    return SubagentReport(
        status=status if status in REPORTED_STATUSES else "completed",
        summary=_text(arguments.get("summary"), STORED_TEXT_CHARS),
        findings=findings,
        open_questions=_strings(arguments.get("open_questions")),
        next_steps=_strings(arguments.get("next_steps")),
        data=data if isinstance(data, dict) else None,
    )


def report_to_dict(report: SubagentReport | None) -> dict[str, Any] | None:
    return asdict(report) if report is not None else None


def report_from_dict(raw: dict[str, Any] | None) -> SubagentReport | None:
    if not isinstance(raw, dict):
        return None
    changes = raw.get("changes")
    report = parse_report(raw)
    report.status = str(raw.get("status") or report.status)
    if isinstance(changes, dict):
        report.changes = SubagentChanges(
            branch=changes.get("branch"),
            diff_stat=str(changes.get("diff_stat") or ""),
            files=[str(f) for f in changes.get("files") or []],
        )
    return report


def _clamped(text: str, limit: int) -> str:
    text = " ".join(text.split()) if "\n" not in text else text.strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}… [{len(text) - limit} more characters cut]"


def _list_section(title: str, items: list[str]) -> list[str]:
    if not items:
        return []
    lines = [f"{title}:"] + [f"- {_clamped(i, LIST_ITEM_CHARS)}" for i in items[:LIST_ITEMS_SHOWN]]
    if len(items) > LIST_ITEMS_SHOWN:
        lines.append(f"- ... {len(items) - LIST_ITEMS_SHOWN} more not shown")
    return lines


def _findings_section(findings: list[SubagentFinding]) -> list[str]:
    if not findings:
        return []
    order = {severity: rank for rank, severity in enumerate(FINDING_SEVERITIES)}
    ranked = sorted(findings, key=lambda f: order.get(f.severity, len(order)))
    lines = [f"Findings ({len(findings)}):"]
    for finding in ranked[:FINDINGS_SHOWN]:
        where = f"{finding.file}:{finding.line}" if finding.line else finding.file or "(no file)"
        lines.append(f"- [{finding.severity}] {where}: {_clamped(finding.evidence, FINDING_CHARS)}")
    if len(findings) > FINDINGS_SHOWN:
        lines.append(f"- ... {len(findings) - FINDINGS_SHOWN} lower-ranked findings not shown")
    return lines


def _changes_section(result: SubagentTaskResult) -> list[str]:
    changes = result.report.changes if result.report is not None else None
    if changes is None and not result.branch:
        return (
            [f"Changed files: {', '.join(result.changed_files[:30])}"]
            if result.changed_files
            else []
        )
    branch = (changes.branch if changes else None) or result.branch
    lines = []
    if branch:
        lines.append(
            f"Branch: {branch} (not merged: call `merge_subagent_branch` with subagent_id "
            f"{result.subagent_id} to review and merge it, or leave it)"
        )
    if changes is not None and changes.diff_stat:
        lines.append(f"Diff stat:\n{_clamped(changes.diff_stat, DIFF_STAT_CHARS)}")
    return lines


def _header(result: SubagentTaskResult) -> list[str]:
    return [
        f"[{result.role.upper()} SUBAGENT REPORT] id={result.subagent_id}",
        f"Task: {_clamped(result.task, 500)}",
        f"Status: {result.status} ({result.turns_taken} steps, {result.tool_calls_count} tools, "
        f"{result.duration_ms} ms, ${result.cost:.4f}, model {result.model or 'unknown'})",
    ]


def render_for_parent(result: SubagentTaskResult) -> str:
    """What the parent reads of a child's result: every field clamped, the whole within
    ``RENDERED_MAX_CHARS``, and a note of where the full report is."""
    report = result.report
    summary = report.summary if report is not None and report.summary else result.summary
    lines = [*_header(result), "", _clamped(summary, SUMMARY_CHARS)]
    if report is not None:
        lines += _findings_section(report.findings)
    lines += _changes_section(result)
    if report is not None:
        lines += _list_section("Open questions", report.open_questions)
        lines += _list_section("Next steps", report.next_steps)
        if report.data:
            lines.append(f"Data: {_clamped(str(report.data), DATA_CHARS)}")
    text = "\n".join(lines)
    if len(text) > RENDERED_MAX_CHARS:
        text = (
            f"{text[:RENDERED_MAX_CHARS]}\n[... report cut at {RENDERED_MAX_CHARS} characters; "
            "the whole report is in the session's Subagents tab]"
        )
    return text
