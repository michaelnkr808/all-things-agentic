"""Same prompt, two roles, side by side (Michael).

A single run shows you an answer and some locks. It does not show you the
*shape* of what a role costs you, because you never see the other answer.
This runs one prompt twice under two roles and diffs the results: which
files each side reached, which it was refused, what each answered, and how
well each answer was anchored.

Downward only
-------------
The obvious version of this feature is a privilege escalation: let a user
name any role and run as it. So the comparison role must be one whose
department grants are a **subset** of the caller's own. An admin may look
down at what an analyst would have got; an analyst cannot look up.

That is not a UI restriction — ``comparable_roles`` computes it from the
permissions config, and ``check_comparable`` is what the endpoint calls
before anything runs. Both sides still go through every gate for their own
role; nothing here bypasses a permission check, it just runs two of them.
"""

from __future__ import annotations

import asyncio
from typing import Callable

from agentic.gatherers.permissions import PermissionsConfig


class CompareError(ValueError):
    """A comparison the server refuses (unknown role, or not downward)."""


def comparable_roles(caller_role: str, perms: PermissionsConfig) -> list[str]:
    """Roles the caller may compare against: those seeing no more than it does.

    Equal-scope roles are excluded along with wider ones — comparing against
    a role with the identical department set produces two identical runs and
    costs twice as much to learn nothing.
    """
    if not perms.role_known(caller_role):
        return []
    mine = set(perms.departments_for_role(caller_role))
    return sorted(
        role
        for role in perms.roles
        if role != caller_role and set(perms.departments_for_role(role)) < mine
    )


def check_comparable(caller_role: str, other_role: str, perms: PermissionsConfig) -> None:
    """Raise unless `other_role` is a strictly narrower role than the caller's."""
    if not perms.role_known(other_role):
        raise CompareError(f"unknown role {other_role!r}")
    allowed = comparable_roles(caller_role, perms)
    if other_role not in allowed:
        raise CompareError(
            f"{caller_role!r} may not compare against {other_role!r}: a comparison "
            f"role must see strictly fewer departments. "
            f"Available: {', '.join(allowed) or '(none)'}"
        )


def _sources(state: dict) -> set[str]:
    return set(state.get("sources") or [])


def build_diff(
    yours: dict,
    theirs: dict,
    your_role: str,
    their_role: str,
    your_denied: list[str],
    their_denied: list[str],
) -> dict:
    """The comparison itself: what one role reached that the other did not."""
    a, b = _sources(yours), _sources(theirs)

    def side(state: dict, role: str, denied: list[str]) -> dict:
        return {
            "role": role,
            "approved": state.get("approved"),
            "attempts": state.get("attempts"),
            "answer": state.get("obsidian_markdown", ""),
            "departments": list(state.get("departments_used") or []),
            "sources": sorted(_sources(state)),
            "denied": sorted(set(denied)),
            "citations": state.get("citations"),
        }

    return {
        "yours": side(yours, your_role, your_denied),
        "theirs": side(theirs, their_role, their_denied),
        "only_yours": sorted(a - b),
        "only_theirs": sorted(b - a),
        "shared": sorted(a & b),
    }


async def run_comparison(
    prompt: str,
    your_role: str,
    their_role: str,
    execute: Callable,
    emit: Callable[[str, dict], None],
    **kwargs,
) -> dict:
    """Run both sides concurrently, tagging every event with the side it came
    from, and emit the diff at the end.

    ``execute`` is injected (runs.execute_run) so this module stays testable
    without a pipeline. Each side runs under its own role and its own audit
    context; a failure on one side does not discard the other's work, because
    a comparison that half-succeeded is still worth showing.
    """
    denied: dict[str, list[str]] = {"yours": [], "theirs": []}

    def tagged(side: str, role: str):
        def _emit(event: str, payload: dict) -> None:
            if event == "gatherer_result":
                denied[side].extend(payload.get("denied") or [])
            emit(event, {**payload, "side": side, "as_role": role})

        return _emit

    async def one(side: str, role: str) -> dict | Exception:
        try:
            return await execute(prompt, role, emit=tagged(side, role), **kwargs)
        except Exception as exc:  # reported per side, not fatal to the pair
            emit("error", {"message": str(exc), "side": side, "as_role": role})
            return exc

    emit("compare_started", {"prompt": prompt, "yours": your_role, "theirs": their_role})
    a, b = await asyncio.gather(
        one("yours", your_role), one("theirs", their_role)
    )

    if isinstance(a, Exception) or isinstance(b, Exception):
        failed = your_role if isinstance(a, Exception) else their_role
        emit("compare_failed", {"message": f"the {failed!r} side did not finish"})
        raise a if isinstance(a, Exception) else b

    diff = build_diff(a, b, your_role, their_role, denied["yours"], denied["theirs"])
    emit("compare_result", diff)
    return diff
