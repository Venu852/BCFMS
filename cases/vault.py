"""Off-chain evidence store.

Files are content-addressed (named by their SHA-256, the same idea as an IPFS CID)
and encrypted with AES-256-GCM. Only the hash goes on-chain.
"""
import hashlib

from django.conf import settings

from ledger.crypto import decrypt_bytes, encrypt_bytes


def store(data):
    """Encrypt and store `data`; returns (sha256 with 0x prefix, relative storage path)."""
    digest = hashlib.sha256(data).hexdigest()
    relative = f"{digest[:2]}/{digest}.enc"
    path = settings.BCFMS_EVIDENCE_VAULT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(encrypt_bytes(data, digest.encode()))
    return "0x" + digest, relative


def load(evidence):
    """Decrypt an evidence file. Raises if the ciphertext was modified on disk."""
    blob = (settings.BCFMS_EVIDENCE_VAULT / evidence.storage_path).read_bytes()
    return decrypt_bytes(blob, evidence.sha256[2:].encode())
