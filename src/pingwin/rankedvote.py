"""Ranked-choice polls: each voter ranks the options, and the count is an instant runoff.

Round by round, every ballot counts for its highest-ranked option still in the race. An option with more than
half of those votes wins. Otherwise the option(s) with the fewest votes drop out (a tie for last place goes
to whoever had fewer votes in the round before) and their ballots move to their next choice. Ballots with
no choices left sit out. If every option left has the same count, it's a tie.

Pure functions, so they're easy to test; the movie night cog posts the poll and its Vote button.
"""

from __future__ import annotations

from typing import Any

MAX_RANKS = 5  # a Discord form holds five questions


def clean(ballot: list[str], options: list[str]) -> list[str]:
    """A ballot with unknown options and repeats taken out, in the voter's order."""
    out: list[str] = []
    for c in ballot:
        if c in options and c not in out:
            out.append(c)
    return out


def first_choices(ballots: dict[str, list[str]], options: list[str]) -> dict[str, int]:
    """How many voters put each option first."""
    counts = {c: 0 for c in options}
    for b in ballots.values():
        b = clean(b, options)
        if b:
            counts[b[0]] += 1
    return counts


def instant_runoff(ballots: dict[str, list[str]], options: list[str]) -> dict[str, Any]:
    """Count the ballots. Returns {"winners": [option ids] (more than one on a tie, none without votes),
    "rounds": [{"counts": {id: votes}, "out": [ids dropped after it]}], "voters": n,
    "final": {id: votes in the last round, 0 for those that dropped out}}."""
    votes = [b for b in (clean(b, options) for b in ballots.values()) if b]
    result: dict[str, Any] = {"winners": [], "rounds": [], "voters": len(votes), "final": {c: 0 for c in options}}
    if not votes:
        return result
    left = list(options)
    while True:
        counts = {c: 0 for c in left}
        for b in votes:
            top = next((c for c in b if c in counts), None)
            if top is not None:
                counts[top] += 1
        active = sum(counts.values())
        best = max(counts.values())
        low = min(counts.values())
        if best * 2 > active or len(left) == 1:
            result["rounds"].append({"counts": counts, "out": []})
            result["winners"] = [c for c in left if counts[c] == best][:1]
            break
        if low == best:  # everyone left is level
            result["rounds"].append({"counts": counts, "out": []})
            result["winners"] = list(left)
            break
        out = [c for c in left if counts[c] == low]
        for earlier in reversed(result["rounds"]):  # a tie for last: fewer votes in the round before goes out
            if len(out) == 1:
                break
            fewest = min(earlier["counts"].get(c, 0) for c in out)
            out = [c for c in out if earlier["counts"].get(c, 0) == fewest]
        result["rounds"].append({"counts": counts, "out": out})
        left = [c for c in left if c not in out]
    result["final"].update(result["rounds"][-1]["counts"])
    return result


def rounds_text(result: dict[str, Any], label) -> str:
    """'Round 1: Alien 4 · Saw 3 · Rubber 2, Rubber is out' and so on, one line per round. `label` turns an
    option id into its name."""
    lines = []
    for i, r in enumerate(result["rounds"], 1):
        counts = sorted(r["counts"].items(), key=lambda kv: -kv[1])
        line = f"Round {i}: " + " · ".join(f"{label(c)} {n}" for c, n in counts)
        if r["out"]:
            names = " and ".join(label(c) for c in r["out"])
            line += f", {names} {'is' if len(r['out']) == 1 else 'are'} out"
        lines.append(line)
    return "\n".join(lines)
