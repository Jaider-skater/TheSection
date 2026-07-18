# Assembles full app from _app_parts (avoids oversized single-file deploys).
from pathlib import Path

_parts_dir = Path(__file__).resolve().parent / "_app_parts"
_names = sorted(p.name for p in _parts_dir.glob("part_*.py"))
if not _names:
    raise RuntimeError("Missing _app_parts/part_*.py — app cannot start")
_code = "".join((_parts_dir / name).read_text(encoding="utf-8") for name in _names)
exec(compile(_code, str(_parts_dir / "assembled.py"), "exec"), globals())
