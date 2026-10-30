"""Private-path exclusion shared by source admission and execution snapshots."""
import fnmatch
from pathlib import PurePosixPath


def private_credential_path(rel: str) -> bool:
    """Unrelated ignored build inputs survive; credentials never enter by default."""
    return any(fnmatch.fnmatch(part.lower(), pattern) for part in PurePosixPath(rel).parts
               for pattern in ("credentials*", "secrets*", "*private_key*", "*.pem", "*.key", "*.enc", "*.p12", "*.pfx",
                               "*.env", "*.env.*", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"))
