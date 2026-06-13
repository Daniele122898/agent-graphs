"""Render OpenCode message parts into the SAME transcript row shapes our native
harness emits (``agents/history.render_messages``), so the control room renders
both harnesses identically. Pure.

Row kinds (must match native): ``user``, ``text``, ``thinking``,
``tool_call`` (tool, args), ``tool_result`` (tool, text), ``system``, ``retry``.
"""

from __future__ import annotations


def render_oc_messages(messages: list[dict]) -> list[dict]:
    """``[{info, parts}]`` (OpenCode transcript) -> our renderable rows."""
    rows: list[dict] = []
    for m in messages:
        info = m.get("info", m)
        role = info.get("role")
        for p in m.get("parts", []):
            ptype = p.get("type")
            if ptype == "text":
                text = p.get("text", "")
                if not text:
                    continue
                rows.append({"kind": "user" if role == "user" else "text", "text": text})
            elif ptype == "reasoning":
                if p.get("text"):
                    rows.append({"kind": "thinking", "text": p["text"]})
            elif ptype == "tool":
                state = p.get("state", {}) or {}
                rows.append({"kind": "tool_call", "tool": p.get("tool", ""), "args": state.get("input", {}) or {}})
                status = state.get("status")
                if status == "completed":
                    rows.append({"kind": "tool_result", "tool": p.get("tool", ""), "text": str(state.get("output", ""))})
                elif status == "error":
                    rows.append({"kind": "tool_result", "tool": p.get("tool", ""), "text": str(state.get("error", "error"))})
            # step-start / step-finish / snapshot / patch are not shown as rows
    return rows
