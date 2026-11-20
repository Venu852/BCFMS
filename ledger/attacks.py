"""Attack lab: replays well-known attacks against a sandboxed copy of the chain.

Every scenario works on in-memory copies or dry-runs; the real ledger and the
case database are never modified.
"""
import copy

from . import chain, contracts
from .crypto import address_from_public_key, generate_keypair, hash_obj, merkle_root, sign
from .models import Block, Transaction

WINDOW = 8

ATTACKS = {
    "tamper": {
        "title": "Tamper with a recorded transaction",
        "blurb": "An insider with database access edits the data inside an old block.",
        "needs_block": True,
    },
    "rehash": {
        "title": "Tamper, then recompute every hash",
        "blurb": "The attacker rewrites the block and repairs all hashes and links after it.",
        "needs_block": True,
    },
    "forge": {
        "title": "Forge the chain with a rogue validator",
        "blurb": "The attacker re-signs the rewritten blocks with their own key.",
        "needs_block": True,
    },
    "replay": {
        "title": "Replay a signed transaction",
        "blurb": "A captured, validly signed transaction is submitted a second time.",
        "needs_block": False,
    },
    "privilege": {
        "title": "Unauthorised contract call",
        "blurb": "A forensic expert's wallet tries to record a court verdict; an unknown wallet tries to file an FIR.",
        "needs_block": False,
    },
    "offchain": {
        "title": "Edit the case database directly",
        "blurb": "A case record is altered in the off-chain database, bypassing the application.",
        "needs_block": False,
    },
}


def _window():
    rows = list(Block.objects.order_by("-number").prefetch_related("transactions")[:WINDOW])[::-1]
    blocks = [row.to_dict() for row in rows]
    trusted = None if blocks[0]["number"] == 0 else blocks[0]["parent_hash"]
    return blocks, (trusted if trusted else blocks[0]["parent_hash"])


def _present(blocks, results, touched, names):
    out = []
    for block, result in zip(blocks, results):
        tx = block["transactions"][0] if block["transactions"] else None
        out.append(
            {
                "number": block["number"],
                "hash": block["hash"],
                "parent_hash": block["parent_hash"],
                "validator": names.get(block["validator"], "Unknown signer" if block["number"] else "Genesis"),
                "validator_address": block["validator"],
                "action": f"{tx['contract']}.{tx['method']}" if tx else "genesis",
                "tx_ok": tx["status"] if tx else True,
                "ok": result["ok"],
                "errors": result["errors"],
                "tampered": block["number"] in touched,
            }
        )
    return out


def _target(blocks, number):
    candidates = [b for b in blocks if b["transactions"]]
    if not candidates:
        return None
    for block in candidates:
        if block["number"] == number:
            return block
    return candidates[len(candidates) // 2] if len(candidates) > 2 else candidates[0]


def _alter(tx):
    """Change one argument of the transaction; returns a description of the edit."""
    args = tx["args"]
    for field in ("record_hash", "file_hash", "status", "role", "outcome", "department", "action", "to", "officer"):
        if field in args:
            break
    else:
        field = next(iter(args))
    before = args[field]
    after = hash_obj({"forged": before}) if str(before).startswith("0x") and len(str(before)) == 66 else f"FORGED-{before}"
    args[field] = after
    return {"field": field, "before": before, "after": after}


def _rehash_from(blocks, start_index, attacker=None):
    """Recompute hashes and parent links from `start_index` onward, as an attacker would."""
    for i in range(start_index, len(blocks)):
        block = blocks[i]
        if i > start_index:
            block["parent_hash"] = blocks[i - 1]["hash"]
        if attacker:
            block["validator"] = attacker["address"]
        block["tx_root"] = merkle_root([tx["tx_hash"] for tx in block["transactions"]])
        block["receipt_root"] = merkle_root([chain.receipt_hash(tx) for tx in block["transactions"]])
        block["hash"] = chain.compute_block_hash(block)
        if attacker:
            block["signature"] = sign(attacker["private"], block["hash"])


def _block_attack(kind, number):
    keys, order = chain.authority_keys()
    names = {a.address: a.name for a in chain.sealers()}
    blocks, trusted = _window()
    original = copy.deepcopy(blocks)
    target = _target(blocks, number)
    if target is None:
        return {"error": "The chain has no transactions yet. File a case first, then come back."}
    index = blocks.index(target)
    tx = target["transactions"][0]
    edit = _alter(tx)
    touched = {target["number"]}
    steps = [
        f"Attacker opens block #{target['number']} and changes `{edit['field']}` of "
        f"{tx['contract']}.{tx['method']}."
    ]

    if kind == "tamper":
        steps.append("Stored hashes are left untouched, hoping nobody recomputes them.")
    else:
        attacker = None
        if kind == "forge":
            private, public = generate_keypair()
            attacker = {"private": private, "public": public, "address": address_from_public_key(public)}
            # The attacker cannot forge the officer's signature, so they re-issue the
            # transaction from their own wallet.
            tx.update(sender=attacker["address"], public_key=public)
            tx["signature"] = sign(private, chain.signing_hash(tx))
            steps.append("They cannot forge the officer's signature, so they re-sign the transaction with their own wallet.")
        tx["tx_hash"] = chain.compute_tx_hash(tx)
        _rehash_from(blocks, index, attacker)
        touched = {b["number"] for b in blocks[index:]}
        steps.append(
            f"They recompute the transaction hash, Merkle root and block hash, then re-link all "
            f"{len(blocks) - index - 1} later blocks so every parent hash matches again."
        )
        if attacker:
            steps.append("Finally they seal every rewritten block with their own private key.")

    results = chain.validate_blocks(blocks, keys, order, trusted_parent=trusted)
    bad = [r for r in results if not r["ok"]]
    outcome = {
        "tamper": "The altered data no longer hashes to the stored transaction hash, so the block fails verification immediately.",
        "rehash": "Hashes and links are consistent again, but the attacker has neither the officer's key nor the "
        "validators' keys: the transaction signature and every block seal from the tampered block onward are invalid.",
        "forge": "Every signature is now mathematically valid, but the signer is not in the authority set fixed at "
        "genesis. Honest nodes reject the fork; and the attacker's wallet holds no on-chain role, so the "
        "contract call itself would revert on re-execution.",
    }[kind]
    return {
        "steps": steps,
        "edit": edit,
        "blocks": _present(blocks, results, touched, names),
        "before": _present(original, chain.validate_blocks(original, keys, order, trusted_parent=trusted), set(), names),
        "detected": bool(bad),
        "invalid_count": len(bad),
        "outcome": outcome,
    }


def _honest_view():
    keys, order = chain.authority_keys()
    names = {a.address: a.name for a in chain.sealers()}
    blocks, trusted = _window()
    return _present(blocks, chain.validate_blocks(blocks, keys, order, trusted_parent=trusted), set(), names)


def _replay():
    row = Transaction.objects.filter(status=True).order_by("-block__number").first()
    if row is None:
        return {"error": "The chain has no transactions yet. File a case first, then come back."}
    tx = {**row.to_dict()}
    result = chain.simulate(tx)
    return {
        "steps": [
            f"Attacker captures transaction {row.tx_hash[:18]}… ({row.contract}.{row.method}) from block #{row.block.number}.",
            "It carries a genuine signature, so they submit the identical bytes again.",
            f"The node compares the transaction nonce ({row.nonce}) with the sender's current account nonce.",
        ],
        "blocks": _honest_view(),
        "attempts": [
            {
                "label": f"Replay of {row.contract}.{row.method}",
                "sender": row.sender,
                "accepted": result["accepted"],
                "status": result["status"],
                "message": result["error"] or "Transaction accepted",
            }
        ],
        "detected": not result["accepted"],
        "outcome": "Each account nonce can be used exactly once. The replayed transaction is rejected before it "
        "reaches a block, so a signed action can never be applied twice.",
    }


def _privilege():
    chain.ensure_genesis()
    case_key = next(
        (k for k in chain.WorldState.objects.filter(key__startswith="case/").values_list("key", flat=True)), None
    )
    case_id = case_key.split("/", 1)[1] if case_key else "CASE-UNKNOWN"
    attempts = []

    private, public = generate_keypair()
    forensic = address_from_public_key(public)
    tx = chain.build_tx(
        forensic,
        public,
        private,
        contracts.ADDRESS["CaseRegistry"],
        "recordVerdict",
        {"case_id": case_id, "record_id": "VERDICT-FORGED", "record_hash": hash_obj("forged verdict"), "outcome": "ACQUITTED"},
        0,
    )
    result = chain.simulate(tx, {f"acl/role/{forensic}": contracts.FORENSIC})
    attempts.append(
        {
            "label": "Forensic expert calls CaseRegistry.recordVerdict",
            "sender": forensic,
            "accepted": result["accepted"],
            "status": result["status"],
            "message": result["error"] or "Executed",
        }
    )

    private, public = generate_keypair()
    stranger = address_from_public_key(public)
    tx = chain.build_tx(
        stranger,
        public,
        private,
        contracts.ADDRESS["CaseRegistry"],
        "createCase",
        {"case_id": "CASE-FAKE", "record_hash": hash_obj("fake fir"), "department": "NONE"},
        0,
    )
    result = chain.simulate(tx)
    attempts.append(
        {
            "label": "Unregistered wallet calls CaseRegistry.createCase",
            "sender": stranger,
            "accepted": result["accepted"],
            "status": result["status"],
            "message": result["error"] or "Executed",
        }
    )
    return {
        "steps": [
            "A forensic expert, whose role only allows evidence analysis, signs a call to record a court verdict.",
            "Separately, a wallet that was never registered by an admin tries to file an FIR.",
            "Both transactions are correctly signed, so the node executes the contract code.",
        ],
        "blocks": _honest_view(),
        "attempts": attempts,
        "detected": all(not a["status"] for a in attempts),
        "outcome": "Permissions live in the contracts, not in the web interface. The calls revert, no state changes, "
        "and the failed attempts would be recorded on-chain against the caller's address.",
    }


def _offchain():
    from cases.models import Case

    case = Case.objects.exclude(tx_hash="").order_by("-filed_at").first()
    if case is None:
        return {"error": "There are no cases yet. File a case first, then come back."}
    onchain = chain.read_state(f"case/{case.case_id}", {})
    payload = case.payload()
    genuine = hash_obj(payload)
    forged = dict(payload, sections_applied="IPC 323 (bailable)", description=payload["description"] + " [edited]")
    forged_hash = hash_obj(forged)
    return {
        "steps": [
            f"Attacker opens the database and edits case {case.fir_number}: the sections applied are replaced with a "
            "lesser, bailable offence and the description is changed.",
            "No transaction is sent, so the blockchain is untouched.",
            "On the next integrity check the application recomputes the record's SHA-256 hash and compares it "
            "with the hash anchored in the CaseRegistry contract.",
        ],
        "blocks": _honest_view(),
        "compare": {
            "fir_number": case.fir_number,
            "onchain_hash": onchain.get("record_hash", ""),
            "genuine_hash": genuine,
            "forged_hash": forged_hash,
            "before": payload["sections_applied"],
            "after": forged["sections_applied"],
            "genuine_matches": genuine == onchain.get("record_hash"),
        },
        "detected": forged_hash != onchain.get("record_hash"),
        "outcome": "The edited record hashes to a different value than the one on-chain, so the case is flagged as "
        "tampered. The attacker would also have to rewrite the chain, which the previous attacks show is infeasible.",
    }


def run(kind, block_number=None):
    chain.ensure_genesis()
    if kind not in ATTACKS:
        return {"error": "Unknown attack"}
    if kind in ("tamper", "rehash", "forge"):
        result = _block_attack(kind, block_number)
    else:
        result = {"replay": _replay, "privilege": _privilege, "offchain": _offchain}[kind]()
    return {"attack": kind, "title": ATTACKS[kind]["title"], **result}
