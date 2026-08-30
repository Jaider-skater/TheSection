"""Apply patches/security-defensive-hardening.diff onto original app.py source."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIFF_PATH = ROOT / 'patches' / 'security-defensive-hardening.diff'
# Blob SHA of app.py on main before this PR (995b9958).
ORIG_BLOB = 'c4a69b8f826e5c9b38cc3574082331cad29cfaf4'
ORIG_REF = '995b9958a7c5b94c7ea1ab8dfce9a3c26028e214:app.py'


def apply_unified_diff(source: str, diff_text: str) -> str:
    src_lines = source.splitlines(keepends=True)
    diff_lines = diff_text.splitlines(True)
    hunks = []
    i = 0
    while i < len(diff_lines):
        line = diff_lines[i]
        if line.startswith('@@'):
            m = re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', line)
            if not m:
                raise ValueError(f'bad hunk header: {line!r}')
            old_start = int(m.group(1))
            old_count = int(m.group(2) or '1')
            i += 1
            old_lines = []
            new_lines = []
            while i < len(diff_lines) and not diff_lines[i].startswith('@@'):
                l = diff_lines[i]
                if l.startswith('\\'):
                    i += 1
                    continue
                if l.startswith('--- ') or l.startswith('+++ ') or l.startswith('index ') or l.startswith('diff '):
                    break
                if not l.endswith('\n'):
                    l = l + '\n'
                if l.startswith(' '):
                    old_lines.append(l[1:])
                    new_lines.append(l[1:])
                elif l.startswith('-'):
                    old_lines.append(l[1:])
                elif l.startswith('+'):
                    new_lines.append(l[1:])
                else:
                    raise ValueError(f'bad hunk line: {l!r}')
                i += 1
            hunks.append((old_start, old_count, old_lines, new_lines))
            continue
        i += 1

    out = src_lines[:]
    for old_start, old_count, old_lines, new_lines in reversed(hunks):
        start = old_start - 1
        end = start + old_count

        def norm(seq):
            return [x if x.endswith('\n') else x + '\n' for x in seq]

        if norm(out[start:end]) != norm(old_lines):
            raise RuntimeError(f'security diff hunk mismatch at original line {old_start}')
        out[start:end] = new_lines
    return ''.join(out)


def original_source() -> str:
    """Prefer git object for main's app.py; never fetch the live site."""
    for args in (
        ['git', 'cat-file', '-p', ORIG_BLOB],
        ['git', 'show', ORIG_REF],
        ['git', 'show', 'origin/main:app.py'],
        ['git', 'show', 'main:app.py'],
    ):
        try:
            text = subprocess.check_output(args, cwd=ROOT, text=True, stderr=subprocess.DEVNULL)
        except Exception:
            continue
        if text and 'def check_ticket(' in text:
            return text
    raise RuntimeError(
        'Could not load original app.py. Apply patches/security-defensive-hardening.diff '
        'onto main app.py in place, then replace this loader. GitHub MCP could not upload '
        'the 191KB monolith.'
    )


def patched_source() -> str:
    source = original_source()
    if 'PRODUCTION_CSP =' in source and 'def check_ticket(ticket_id, stamp=True)' in source:
        return source
    return apply_unified_diff(source, DIFF_PATH.read_text())
