"""Static HTML trace viewer for audit JSONL records."""

from __future__ import annotations

import html
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any


def render_trace_html(records: Sequence[Mapping[str, Any]], *, title: str = "Leos Trace") -> str:
    """Render audit records into a self-contained HTML trace."""

    event_counts = Counter(str(record.get("event_type", "unknown")) for record in records)
    rows = "\n".join(_render_event_row(index, record) for index, record in enumerate(records, start=1))
    counts = "\n".join(
        f"<li><code>{html.escape(event_type)}</code>: {count}</li>"
        for event_type, count in sorted(event_counts.items())
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; line-height: 1.4; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border: 1px solid #ccc; padding: 0.45rem; vertical-align: top; }}
    th {{ background: #f5f5f5; text-align: left; }}
    code, pre {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }}
    pre {{ white-space: pre-wrap; margin: 0; }}
    .summary {{ display: flex; gap: 2rem; align-items: flex-start; }}
  </style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <section class="summary">
    <div>
      <h2>Summary</h2>
      <p>Total events: {len(records)}</p>
    </div>
    <div>
      <h2>Event Types</h2>
      <ul>{counts}</ul>
    </div>
  </section>
  <h2>Timeline</h2>
  <table>
    <thead>
      <tr>
        <th>#</th>
        <th>Event</th>
        <th>Message</th>
        <th>Payload</th>
      </tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>
</body>
</html>
"""


def render_trace_markdown(records: Sequence[Mapping[str, Any]]) -> str:
    event_counts = Counter(str(record.get("event_type", "unknown")) for record in records)
    final_status = _final_goal_status(records)
    lines = ["# Leos Trace", "", f"Total events: {len(records)}"]
    if final_status:
        lines.append(f"Final goal status: `{final_status}`")
    lines.extend(["", "## Event Types"])
    if event_counts:
        for event_type, count in sorted(event_counts.items()):
            lines.append(f"- `{event_type}`: {count}")
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Timeline",
            "",
            "| # | Event | Goal | Plan | Step | Risk | Permissions | Decision | Status | Details |",
            "|---:|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for index, record in enumerate(records, start=1):
        event_type = str(record.get("event_type", "unknown")).replace("|", "\\|")
        message = str(record.get("message", "")).replace("|", "\\|").replace("\n", " ")
        payload = record.get("payload", {})
        payload = payload if isinstance(payload, Mapping) else {}
        permissions = payload.get("permissions") or payload.get("required_permissions")
        decision = payload.get("decision") or payload.get("approval_result") or payload.get("rule_name")
        status = payload.get("goal_status") or payload.get("phase") or payload.get("error_type")
        lines.append(
            f"| {index} | `{event_type}` | {_cell(payload.get('goal_id') or payload.get('goal'))} | "
            f"{_cell(payload.get('plan_id'))} | {_cell(payload.get('step_id') or payload.get('tool'))} | "
            f"{_cell(payload.get('risk'))} | {_cell(permissions)} | {_cell(decision)} | {_cell(status)} | {message} |"
        )
    return "\n".join(lines) + "\n"


def _render_event_row(index: int, record: Mapping[str, Any]) -> str:
    event_type = html.escape(str(record.get("event_type", "unknown")))
    message = html.escape(str(record.get("message", "")))
    payload = html.escape(json.dumps(record.get("payload", {}), ensure_ascii=False, indent=2, default=str))
    return f"""<tr>
  <td>{index}</td>
  <td><code>{event_type}</code></td>
  <td>{message}</td>
  <td><pre>{payload}</pre></td>
</tr>"""


def _cell(value: Any) -> str:
    if value is None:
        return ""
    text = json.dumps(value, ensure_ascii=False, default=str) if isinstance(value, (dict, list, tuple)) else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _final_goal_status(records: Sequence[Mapping[str, Any]]) -> str:
    for record in reversed(records):
        payload = record.get("payload", {})
        if isinstance(payload, Mapping) and payload.get("goal_status"):
            return str(payload["goal_status"])
    return ""
