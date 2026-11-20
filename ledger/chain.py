"""The BCFMS chain engine: an Ethereum-style, account-based ledger with
Proof-of-Authority sealing.

* Accounts are secp256k1 key pairs; every transaction is signed and carries a nonce.
* A transaction calls a smart contract (see contracts.py) which mutates world state.
* Blocks commit to their transactions (Merkle root), receipts and state, link to
  their parent by hash and are signed by the in-turn authority node.

Validation works on plain dicts so the same code checks the real chain and the
tampered copies built by the attack lab.
"""
import copy
import threading
import time

from django.conf import settings
from django.db import transaction

from . import contracts
from .crypto import (
    ZERO_HASH,
    address_from_public_key,
    canonical,
    decrypt_secret,
    encrypt_secret,
    generate_keypair,
    hash_obj,
    merkle_root,
    sha256_hex,
    sign,
    verify,
)
from .models import Authority, Block, Transaction, WorldState

TX_CORE = ("chain_id", "sender", "to", "method", "args", "nonce", "timestamp")
HEADER = ("number", "timestamp", "parent_hash", "tx_root", "receipt_root", "state_root", "extra_hash", "validator", "gas_used")
SEALER_NODES = ("Police HQ Node", "Forensic Lab Node", "Judiciary Node")

_lock = threading.RLock()


class InvalidTransaction(Exception):
    """The transaction is malformed and can never be included in a block."""


def now_ms():
    return int(time.time() * 1000)


# --- state ----------------------------------------------------------------
class OverlayState:
    """Copy-on-write view of contract storage; `writes` holds the pending changes."""

    def __init__(self, base):
        self._base = base
        self.writes = {}

    def get(self, key, default=None):
        value = self.writes[key] if key in self.writes else self._base(key)
        return default if value is None else copy.deepcopy(value)

    def set(self, key, value):
        self.writes[key] = copy.deepcopy(value)

    def child(self):
        return OverlayState(self.get)

    def merge(self, child):
        self.writes.update(child.writes)


def db_state():
    cache = {}

    def read(key):
        if key not in cache:
            row = WorldState.objects.filter(key=key).first()
            cache[key] = row.value if row else None
        return cache[key]

    return OverlayState(read)


def read_state(key, default=None):
    row = WorldState.objects.filter(key=key).first()
    return default if row is None or row.value is None else row.value


# --- hashing --------------------------------------------------------------
def signing_hash(tx):
    return hash_obj({k: tx[k] for k in TX_CORE})


def compute_tx_hash(tx):
    return hash_obj({**{k: tx[k] for k in TX_CORE}, "public_key": tx["public_key"], "signature": tx["signature"]})


def receipt_hash(tx):
    return hash_obj({k: tx[k] for k in ("tx_hash", "status", "events", "gas_used")})


def compute_block_hash(block):
    return hash_obj({k: block[k] for k in HEADER})


def gas_for(tx):
    # Nominal Ethereum-style accounting: base cost plus a per-byte charge for calldata.
    return 21000 + 68 * len(canonical(tx["args"]))


# --- transactions ---------------------------------------------------------
def build_tx(sender, public_key, private_key, to, method, args, nonce, timestamp=None):
    tx = {
        "chain_id": settings.BCFMS_CHAIN_ID,
        "sender": sender,
        "to": to,
        "method": method,
        "args": args,
        "nonce": nonce,
        "timestamp": timestamp or now_ms(),
        "public_key": public_key,
    }
    tx["signature"] = sign(private_key, signing_hash(tx))
    tx["tx_hash"] = compute_tx_hash(tx)
    return tx


def apply_tx(tx, state):
    """Validate and execute `tx` on `state`. Returns (status, error, events).

    Raises InvalidTransaction for a bad signature or nonce; a contract revert is
    a valid-but-failed transaction that still consumes the nonce."""
    if address_from_public_key(tx["public_key"]) != tx["sender"]:
        raise InvalidTransaction("sender address does not match the public key")
    if not verify(tx["public_key"], signing_hash(tx), tx["signature"]):
        raise InvalidTransaction("invalid transaction signature")
    expected = state.get(f"nonce/{tx['sender']}", 0)
    if tx["nonce"] != expected:
        raise InvalidTransaction(
            f"nonce {tx['nonce']} already used or out of order (account nonce is {expected}) - replay rejected"
        )
    state.set(f"nonce/{tx['sender']}", expected + 1)
    scratch = state.child()
    try:
        events = contracts.call(scratch, tx)
    except contracts.Revert as exc:
        return False, str(exc), []
    state.merge(scratch)
    return True, "", events


# --- genesis and authorities ----------------------------------------------
def sealers():
    return list(Authority.objects.filter(kind=Authority.SEALER).order_by("order"))


def root_authority():
    return Authority.objects.get(kind=Authority.ROOT)


def ensure_genesis():
    if Block.objects.filter(number=0).exists():
        return
    with _lock, transaction.atomic():
        if Block.objects.filter(number=0).exists():
            return
        for order, name in enumerate(SEALER_NODES):
            private, public = generate_keypair()
            Authority.objects.create(
                name=name,
                kind=Authority.SEALER,
                order=order,
                address=address_from_public_key(public),
                public_key=public,
                encrypted_private_key=encrypt_secret(private),
            )
        private, public = generate_keypair()
        root = Authority.objects.create(
            name="Root Authority",
            kind=Authority.ROOT,
            order=99,
            address=address_from_public_key(public),
            public_key=public,
            encrypted_private_key=encrypt_secret(private),
        )
        alloc = {f"acl/role/{root.address}": contracts.ADMIN}
        extra = {
            "chain_id": settings.BCFMS_CHAIN_ID,
            "consensus": "Proof of Authority (round-robin sealers)",
            "authorities": [{"name": a.name, "address": a.address, "public_key": a.public_key} for a in sealers()],
            "contracts": contracts.ADDRESS,
            "alloc": alloc,
        }
        block = {
            "number": 0,
            "timestamp": now_ms(),
            "parent_hash": ZERO_HASH,
            "tx_root": ZERO_HASH,
            "receipt_root": ZERO_HASH,
            "state_root": hash_obj(alloc),
            "extra_hash": hash_obj(extra),
            "validator": "0x" + "0" * 40,
            "gas_used": 0,
        }
        Block.objects.create(**block, extra=extra, hash=compute_block_hash(block), signature="")
        WorldState.objects.bulk_create([WorldState(key=k, value=v) for k, v in alloc.items()])


def next_state_root(parent_root, writes):
    return sha256_hex(parent_root + hash_obj(writes))


# --- block production -----------------------------------------------------
def send_transaction(sender, public_key, private_key, contract, method, args, case_ref=""):
    """Sign, execute and seal a transaction into a new block. Returns the Transaction row."""
    started = time.perf_counter()
    ensure_genesis()
    with _lock, transaction.atomic():
        parent = Block.objects.select_for_update().order_by("-number").first()
        state = db_state()
        tx = build_tx(
            sender, public_key, private_key, contracts.ADDRESS[contract], method, args, state.get(f"nonce/{sender}", 0)
        )
        status, error, events = apply_tx(tx, state)
        tx.update(status=status, error=error, events=events, gas_used=gas_for(tx))

        nodes = sealers()
        number = parent.number + 1
        sealer = nodes[number % len(nodes)]
        block = {
            "number": number,
            "timestamp": max(now_ms(), parent.timestamp + 1),
            "parent_hash": parent.hash,
            "tx_root": merkle_root([tx["tx_hash"]]),
            "receipt_root": merkle_root([receipt_hash(tx)]),
            "state_root": next_state_root(parent.state_root, state.writes),
            "extra_hash": hash_obj({}),
            "validator": sealer.address,
            "gas_used": tx["gas_used"],
        }
        block_hash = compute_block_hash(block)
        row = Block.objects.create(
            **block, extra={}, hash=block_hash, signature=sign(decrypt_secret(sealer.encrypted_private_key), block_hash)
        )
        for key, value in state.writes.items():
            WorldState.objects.update_or_create(key=key, defaults={"value": value})
        return Transaction.objects.create(
            block=row,
            index=0,
            contract=contract,
            case_ref=case_ref,
            confirm_ms=round((time.perf_counter() - started) * 1000, 2),
            **{k: tx[k] for k in tx if k != "chain_id"},
        )


def simulate(tx, state_patch=None):
    """Dry-run an already-signed transaction against current state. Nothing is stored."""
    ensure_genesis()
    state = db_state()
    for key, value in (state_patch or {}).items():
        state.set(key, value)
    try:
        status, error, events = apply_tx(tx, state)
    except InvalidTransaction as exc:
        return {"accepted": False, "status": False, "error": str(exc), "events": []}
    return {"accepted": True, "status": status, "error": error, "events": events}


# --- validation -----------------------------------------------------------
def authority_keys():
    return {a.address: a.public_key for a in sealers()}, [a.address for a in sealers()]


def validate_blocks(blocks, keys, order, trusted_parent=None):
    """Check hashes, links, Merkle roots and signatures of `blocks` (dicts, ascending).

    Returns one {"number", "ok", "errors"} entry per block."""
    results = []
    previous = trusted_parent
    for block in blocks:
        errors = []
        txs = block.get("transactions", [])
        for i, tx in enumerate(txs):
            if compute_tx_hash(tx) != tx["tx_hash"]:
                errors.append(f"Transaction {i}: content no longer matches its hash (data was altered)")
            elif address_from_public_key(tx["public_key"]) != tx["sender"] or not verify(
                tx["public_key"], signing_hash(tx), tx["signature"]
            ):
                errors.append(f"Transaction {i}: sender signature is invalid")
        if block["number"] > 0 or txs:
            if merkle_root([tx["tx_hash"] for tx in txs]) != block["tx_root"]:
                errors.append("Merkle root does not match the block's transactions")
            if merkle_root([receipt_hash(tx) for tx in txs]) != block["receipt_root"]:
                errors.append("Receipt root does not match the transaction results")
        if hash_obj(block.get("extra", {})) != block["extra_hash"]:
            errors.append("Block extra data was altered")
        if compute_block_hash(block) != block["hash"]:
            errors.append("Block hash does not match the block header")
        if previous is not None and block["parent_hash"] != previous:
            errors.append("Broken link: parent hash does not match the previous block")
        if block["number"] > 0:
            if block["validator"] not in keys:
                errors.append("Sealed by an address that is not an authorised validator")
            else:
                if block["validator"] != order[block["number"] % len(order)]:
                    errors.append("Sealed out of turn (wrong validator for this block number)")
                if not verify(keys[block["validator"]], block["hash"], block["signature"]):
                    errors.append("Validator seal signature is invalid")
        results.append({"number": block["number"], "ok": not errors, "errors": errors})
        previous = block["hash"]
    return results


def verify_chain(deep=False):
    """Validate the whole stored chain. `deep` also re-executes every transaction
    from genesis and compares results and state roots."""
    ensure_genesis()
    started = time.perf_counter()
    keys, order = authority_keys()
    blocks = [b.to_dict() for b in Block.objects.order_by("number").prefetch_related("transactions")]
    results = validate_blocks(blocks, keys, order, trusted_parent=ZERO_HASH)
    if deep:
        alloc = blocks[0]["extra"].get("alloc", {})
        memory = dict(alloc)
        state = OverlayState(memory.get)
        root = hash_obj(alloc)
        if root != blocks[0]["state_root"]:
            results[0]["errors"].append("Genesis state root mismatch")
        for block, result in zip(blocks[1:], results[1:]):
            scratch = state.child()
            for i, tx in enumerate(block["transactions"]):
                try:
                    status, _, events = apply_tx(tx, scratch)
                except InvalidTransaction as exc:
                    result["errors"].append(f"Transaction {i}: {exc}")
                    continue
                if status != tx["status"] or events != tx["events"]:
                    result["errors"].append(f"Transaction {i}: re-execution gives a different result")
            root = next_state_root(root, scratch.writes)
            if root != block["state_root"]:
                result["errors"].append("State root mismatch after re-executing the block")
            memory.update(scratch.writes)
        for result in results:
            result["ok"] = not result["errors"]
    bad = [r for r in results if not r["ok"]]
    return {
        "valid": not bad,
        "deep": deep,
        "blocks": len(blocks),
        "transactions": sum(len(b["transactions"]) for b in blocks),
        "invalid_blocks": bad,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }
