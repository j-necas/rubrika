"""robots.txt per RFC 9309: groups by product token, * and $ wildcards, longest match (allow wins ties),
paths compared in RFC 3986 normal form (§ 2.2.2). Crawl-delay is not in RFC 9309 but is an explicit wish
about server load; it is returned exactly and never shortened."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from urllib.parse import quote

_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_PCT = re.compile(r"%([0-9A-Fa-f]{2})")


def normalize(text: str) -> str:
    """Percent-encode non-ASCII as UTF-8, upper-case hex, decode only unreserved characters."""
    out, i = [], 0
    while i < len(text):
        m = _PCT.match(text, i)
        if m:
            ch = chr(int(m[1], 16))
            out.append(ch if ch in _UNRESERVED else "%" + m[1].upper())
            i += 3
            continue
        ch = text[i]
        out.append(ch if ord(ch) < 128 else quote(ch, safe=""))
        i += 1
    return "".join(out)


@dataclass(frozen=True)
class Rule:
    allow: bool
    length: int
    regex: re.Pattern


def _rule(allow: bool, pattern: str) -> Rule:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    # Wildcards exist only in the raw syntax; each literal segment is normalised on its own.
    parts = [normalize(p) for p in body.split("*")]
    regex = ".*".join(re.escape(p) for p in parts) + ("$" if anchored else "")
    return Rule(allow, len("*".join(parts)) + anchored, re.compile(regex, re.DOTALL))


@dataclass
class Group:
    agents: list[str] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    crawl_delay: float | None = None


class Robots:
    def __init__(self, groups: list[Group]):
        self.groups = groups

    @classmethod
    def empty(cls) -> "Robots":
        return cls([])

    @classmethod
    def parse(cls, text: str) -> "Robots":
        groups: list[Group] = []
        current: Group | None = None
        collecting = False
        for raw in text.removeprefix("\ufeff").splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (part.strip() for part in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if not collecting:
                    current = Group()
                    groups.append(current)
                    collecting = True
                current.agents.append(value.lower())
                continue
            collecting = False
            if current is None:
                continue  # rules before any user-agent line belong to no group
            if key in ("allow", "disallow"):
                if value:
                    current.rules.append(_rule(key == "allow", value))
            elif key == "crawl-delay":
                try:
                    delay = float(value)
                except ValueError:
                    continue
                if math.isfinite(delay) and delay >= 0:
                    current.crawl_delay = delay

        return cls(groups)

    def _groups(self, user_agent: str) -> tuple[list[Group], list[Group]]:
        token = user_agent.split("/", 1)[0].strip().lower()
        specific = [g for g in self.groups if token and token in g.agents]
        star = [g for g in self.groups if "*" in g.agents]
        return specific, star

    def allowed(self, user_agent: str, target: str) -> bool:
        path = normalize(target or "/")
        if path == "/robots.txt":
            return True
        specific, star = self._groups(user_agent)
        best: Rule | None = None
        for group in specific or star:
            for rule in group.rules:
                if rule.regex.match(path) and (best is None or rule.length > best.length
                                               or (rule.length == best.length and rule.allow)):
                    best = rule
        return best is None or best.allow

    def crawl_delay(self, user_agent: str) -> float | None:
        specific, star = self._groups(user_agent)
        for groups in (specific, star):  # a specific group without Crawl-delay falls back to *
            delays = [g.crawl_delay for g in groups if g.crawl_delay is not None]
            if delays:
                return max(delays)
        return None
