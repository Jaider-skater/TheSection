"""Apply patches/security-defensive-hardening.diff to app.py source at import."""
from __future__ import annotations

import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIFF_PATH = ROOT / 'patches' / 'security-defensive-hardening.diff'
APP_PATH = ROOT / 'app.py'
SOURCE_PATH = ROOT / 'app_parts' / 'monolith_source.txt'


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


def load_patched_app():
    existing = sys.modules.get('app')
    if existing is not None and getattr(existing, '_hardening_applied', False):
        return existing

    if SOURCE_PATH.exists():
        source = SOURCE_PATH.read_text()
    else:
        source = APP_PATH.read_text()
    already = (
        'PRODUCTION_CSP =' in source
        and 'def check_ticket(ticket_id, stamp=True)' in source
    )
    if already:
        patched = source
    else:
        patched = apply_unified_diff(source, DIFF_PATH.read_text())

    module = types.ModuleType('app')
    module.__file__ = str(APP_PATH)
    module.__name__ = 'app'
    module._hardening_applied = True
    sys.modules['app'] = module
    exec(compile(patched, str(APP_PATH), 'exec'), module.__dict__)
    module._hardening_applied = True
    return module
