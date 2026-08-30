"""The Section Flask app.

Implementation is stored in app_parts/ (the monolith exceeds GitHub MCP
create_or_update_file payload size). Concatenated source is the in-place
hardened app.py; the reviewable patch is patches/security-defensive-hardening.diff.
"""
from pathlib import Path

_src = ''.join(
    p.read_text()
    for p in sorted(Path(__file__).resolve().parent.joinpath('app_parts').glob('part*.txt'))
)
exec(compile(_src, __file__, 'exec'), globals())
