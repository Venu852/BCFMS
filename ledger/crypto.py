"""Cryptographic primitives for the BCFMS chain.

SHA-256 for record/transaction/block hashing, secp256k1 ECDSA for signatures and
Keccak-256 for Ethereum-style account addresses. AES-256-GCM / Fernet protect
secrets and evidence files at rest.
"""
import base64
import hashlib
import json
import os

from Crypto.Hash import keccak
from cryptography.exceptions import InvalidSignature
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    Prehashed,
    decode_dss_signature,
    encode_dss_signature,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.conf import settings

CURVE = ec.SECP256K1()
ZERO_HASH = "0x" + "0" * 64


# --- hashing -------------------------------------------------------------
def sha256_hex(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return "0x" + hashlib.sha256(data).hexdigest()


def canonical(obj):
    """Deterministic JSON encoding, so the same record always hashes the same."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def hash_obj(obj):
    return sha256_hex(canonical(obj))


def keccak256(data):
    return keccak.new(digest_bits=256, data=data).digest()


def merkle_root(leaves):
    """Merkle root over a list of 0x-prefixed hashes (odd levels duplicate the last leaf)."""
    if not leaves:
        return ZERO_HASH
    level = list(leaves)
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [sha256_hex(level[i] + level[i + 1]) for i in range(0, len(level), 2)]
    return level[0]


# --- accounts and signatures ---------------------------------------------
def generate_keypair():
    """Return (private_key_hex, public_key_hex); the public key is the 64-byte x||y point."""
    private = ec.generate_private_key(CURVE)
    numbers = private.private_numbers()
    public = numbers.public_numbers
    private_hex = format(numbers.private_value, "064x")
    public_hex = format(public.x, "064x") + format(public.y, "064x")
    return private_hex, public_hex


def address_from_public_key(public_hex):
    return "0x" + keccak256(bytes.fromhex(public_hex))[-20:].hex()


def contract_address(name):
    return "0x" + keccak256(f"bcfms:contract:{name}".encode())[-20:].hex()


def _digest(digest_hex):
    return bytes.fromhex(digest_hex[2:] if digest_hex.startswith("0x") else digest_hex)


def sign(private_hex, digest_hex):
    private = ec.derive_private_key(int(private_hex, 16), CURVE)
    der = private.sign(_digest(digest_hex), ec.ECDSA(Prehashed(hashes.SHA256())))
    r, s = decode_dss_signature(der)
    return "0x" + format(r, "064x") + format(s, "064x")


def verify(public_hex, digest_hex, signature_hex):
    try:
        sig = signature_hex[2:] if signature_hex.startswith("0x") else signature_hex
        r, s = int(sig[:64], 16), int(sig[64:128], 16)
        x, y = int(public_hex[:64], 16), int(public_hex[64:128], 16)
        public = ec.EllipticCurvePublicNumbers(x, y, CURVE).public_key()
        public.verify(encode_dss_signature(r, s), _digest(digest_hex), ec.ECDSA(Prehashed(hashes.SHA256())))
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False


# --- secrets at rest -----------------------------------------------------
def _vault_key():
    return hashlib.sha256(("bcfms-vault:" + settings.BCFMS_VAULT_KEY).encode()).digest()


def encrypt_secret(plaintext):
    return Fernet(base64.urlsafe_b64encode(_vault_key())).encrypt(plaintext.encode()).decode()


def decrypt_secret(token):
    return Fernet(base64.urlsafe_b64encode(_vault_key())).decrypt(token.encode()).decode()


def encrypt_bytes(data, associated=b""):
    """AES-256-GCM; the 12-byte nonce is prepended to the ciphertext."""
    nonce = os.urandom(12)
    return nonce + AESGCM(_vault_key()).encrypt(nonce, data, associated)


def decrypt_bytes(blob, associated=b""):
    return AESGCM(_vault_key()).decrypt(blob[:12], blob[12:], associated)
