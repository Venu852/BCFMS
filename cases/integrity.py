"""Authenticity checks: recompute every record's hash and compare with the chain."""
import hashlib

from ledger import chain

from . import vault


def _check(label, kind, expected, actual, note=""):
    ok = bool(expected) and expected == actual
    if not expected:
        note = "No hash is anchored on-chain for this record"
    return {"label": label, "kind": kind, "ok": ok, "onchain": expected or "", "computed": actual or "", "note": note}


def verify_case(case):
    """Returns {"ok", "checks", "onchain"} for a case and everything attached to it."""
    onchain = chain.read_state(f"case/{case.case_id}", {})
    checks = [_check(f"FIR {case.fir_number}", "FIR record", onchain.get("record_hash"), case.compute_hash())]
    status_ok = onchain.get("status") == case.status
    checks.append(
        {
            "label": "Case status",
            "kind": "Workflow state",
            "ok": status_ok,
            "onchain": onchain.get("status", ""),
            "computed": case.status,
            "note": "" if status_ok else "Database status differs from the contract's state",
        }
    )

    def record(kind, obj, label):
        state = chain.read_state(f"rec/{case.case_id}/{kind}/{obj.uid}", {})
        checks.append(_check(label, kind.title(), state.get("hash"), obj.compute_hash()))

    for note in case.notes.filter(removed=False):
        record("NOTE", note, f"Note: {note.title}")
    if hasattr(case, "chargesheet"):
        record("CHARGESHEET", case.chargesheet, "Chargesheet")
    for hearing in case.hearings.filter(cancelled=False):
        record("HEARING", hearing, f"Hearing: {hearing.purpose}")
    if hasattr(case, "verdict"):
        record("VERDICT", case.verdict, "Verdict")

    for evidence in case.evidence.all():
        state = chain.read_state(f"ev/{evidence.uid}", {})
        try:
            actual = "0x" + hashlib.sha256(vault.load(evidence)).hexdigest()
            note = ""
        except Exception:  # missing file or failed AES-GCM authentication
            actual, note = "", "Stored file is missing or was modified (decryption failed)"
        checks.append(_check(f"{evidence.uid} file: {evidence.original_name}", "Evidence file", state.get("file_hash"), actual, note))
        checks.append(_check(f"{evidence.uid} metadata", "Evidence metadata", state.get("meta_hash"), evidence.compute_meta_hash()))
        custodian_ok = state.get("custodian") == evidence.custodian.wallet_address
        checks.append(
            {
                "label": f"{evidence.uid} custodian",
                "kind": "Chain of custody",
                "ok": custodian_ok,
                "onchain": state.get("custodian", ""),
                "computed": evidence.custodian.wallet_address,
                "note": "" if custodian_ok else "Custodian differs from the on-chain record",
            }
        )
        for report in evidence.reports.all():
            record("FORENSIC", report, f"Forensic report: {report.title}")

    return {"ok": all(c["ok"] for c in checks), "checks": checks, "onchain": onchain}
