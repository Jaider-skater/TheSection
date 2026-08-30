"""The Section Flask app. Applies patches/security-defensive-hardening.diff at import."""
from apply_security_diff import patched_source

exec(compile(patched_source(), __file__, 'exec'), globals())
