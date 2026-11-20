"""Smart contracts executed by the BCFMS chain.

Contracts are deterministic Python classes run by every validating node. They own
the world state, enforce who may do what (role permissions) and in which order
(case workflow). A failed `require` reverts the transaction: it is still recorded
on-chain as failed, but none of its state changes are kept.
"""
from .crypto import contract_address

ADMIN, POLICE, INVESTIGATOR, FORENSIC, JUDICIARY = "ADMIN", "POLICE", "INVESTIGATOR", "FORENSIC", "JUDICIARY"
ROLES = (ADMIN, POLICE, INVESTIGATOR, FORENSIC, JUDICIARY)

FILED, UNDER_INVESTIGATION, CHARGESHEET_FILED, CLOSED, CONVICTED = (
    "FILED",
    "UNDER_INVESTIGATION",
    "CHARGESHEET_FILED",
    "CLOSED",
    "CONVICTED",
)
FINAL_STATUSES = (CLOSED, CONVICTED)
VERDICT_OUTCOMES = ("CONVICTED", "ACQUITTED", "DISMISSED")


class Revert(Exception):
    """Raised by a contract to abort the current transaction."""


class Contract:
    NAME = ""
    ABI = ()

    def __init__(self, state, sender, now):
        self.state = state
        self.sender = sender
        self.now = now
        self.events = []

    def role_of(self, account):
        return self.state.get(f"acl/role/{account}")

    def require(self, condition, message):
        if not condition:
            raise Revert(f"{self.NAME}: {message}")

    def only(self, *roles):
        role = self.role_of(self.sender)
        self.require(
            role in roles,
            f"caller role {role or 'UNREGISTERED'} is not permitted (requires {' / '.join(roles)})",
        )
        return role

    def emit(self, event, **data):
        self.events.append({"event": event, **data})


class AccessControl(Contract):
    """On-chain registry mapping wallet addresses to roles."""

    NAME = "AccessControl"
    ABI = ("grantRole", "revokeRole")

    def grantRole(self, account, role):
        self.only(ADMIN)
        self.require(role in ROLES, f"unknown role {role}")
        self.state.set(f"acl/role/{account}", role)
        self.emit("RoleGranted", account=account, role=role, by=self.sender)

    def revokeRole(self, account):
        self.only(ADMIN)
        self.require(account != self.sender, "an admin cannot revoke their own role")
        self.require(self.role_of(account) is not None, "account has no role")
        self.state.set(f"acl/role/{account}", None)
        self.emit("RoleRevoked", account=account, by=self.sender)


class CaseRegistry(Contract):
    """FIR / case records: anchors record hashes and enforces the case workflow."""

    NAME = "CaseRegistry"
    ABI = (
        "createCase",
        "updateCase",
        "setStatus",
        "assignInvestigator",
        "addRecord",
        "removeRecord",
        "fileChargesheet",
        "recordVerdict",
        "shareCase",
        "revokeShare",
    )
    # Manual status moves. Chargesheet and verdict transitions happen only through
    # fileChargesheet / recordVerdict so they always carry the supporting record.
    MANUAL_TRANSITIONS = {FILED: (UNDER_INVESTIGATION,), UNDER_INVESTIGATION: (CLOSED,)}
    RECORD_RULES = {
        "NOTE": ((INVESTIGATOR,), (UNDER_INVESTIGATION, CHARGESHEET_FILED)),
        "HEARING": ((JUDICIARY,), (CHARGESHEET_FILED,)),
    }

    def _case(self, case_id):
        case = self.state.get(f"case/{case_id}")
        self.require(case is not None, f"case {case_id} does not exist")
        return case

    def _save(self, case_id, case):
        case["updated_at"] = self.now
        self.state.set(f"case/{case_id}", case)

    def createCase(self, case_id, record_hash, department):
        self.only(POLICE, INVESTIGATOR)
        self.require(self.state.get(f"case/{case_id}") is None, f"case {case_id} already exists")
        self.state.set(
            f"case/{case_id}",
            {
                "record_hash": record_hash,
                "status": FILED,
                "department": department,
                "creator": self.sender,
                "investigator": None,
                "created_at": self.now,
                "updated_at": self.now,
                "version": 1,
            },
        )
        self.emit("CaseCreated", case_id=case_id, record_hash=record_hash, status=FILED)

    def updateCase(self, case_id, record_hash):
        self.only(POLICE, INVESTIGATOR)
        case = self._case(case_id)
        self.require(case["status"] not in FINAL_STATUSES, f"case is {case['status']} and can no longer be edited")
        case["record_hash"] = record_hash
        case["version"] += 1
        self._save(case_id, case)
        self.emit("CaseUpdated", case_id=case_id, record_hash=record_hash, version=case["version"])

    def setStatus(self, case_id, status):
        self.only(POLICE, INVESTIGATOR)
        case = self._case(case_id)
        allowed = self.MANUAL_TRANSITIONS.get(case["status"], ())
        self.require(status in allowed, f"illegal status transition {case['status']} -> {status}")
        previous, case["status"] = case["status"], status
        self._save(case_id, case)
        self.emit("StatusChanged", case_id=case_id, previous=previous, status=status)

    def assignInvestigator(self, case_id, officer):
        self.only(POLICE, ADMIN)
        case = self._case(case_id)
        self.require(case["status"] not in FINAL_STATUSES, f"case is {case['status']}")
        self.require(self.role_of(officer) == INVESTIGATOR, "assignee is not a registered Investigating Officer")
        case["investigator"] = officer
        self._save(case_id, case)
        self.emit("InvestigatorAssigned", case_id=case_id, officer=officer)

    def addRecord(self, case_id, kind, record_id, record_hash):
        self.require(kind in self.RECORD_RULES, f"unknown record kind {kind}")
        roles, statuses = self.RECORD_RULES[kind]
        self.only(*roles)
        case = self._case(case_id)
        self.require(case["status"] in statuses, f"{kind} cannot be added while the case is {case['status']}")
        key = f"rec/{case_id}/{kind}/{record_id}"
        self.require(self.state.get(key) is None, "record already exists")
        self.state.set(key, {"hash": record_hash, "author": self.sender, "at": self.now, "removed": False})
        self.emit("RecordAdded", case_id=case_id, kind=kind, record_id=record_id, record_hash=record_hash)

    def removeRecord(self, case_id, kind, record_id):
        self.require(kind in self.RECORD_RULES, f"unknown record kind {kind}")
        self.only(*self.RECORD_RULES[kind][0])
        key = f"rec/{case_id}/{kind}/{record_id}"
        record = self.state.get(key)
        self.require(record is not None and not record["removed"], "record does not exist")
        # A tombstone: the original hash stays on-chain, so the deletion itself is auditable.
        record.update(removed=True, removed_by=self.sender, removed_at=self.now)
        self.state.set(key, record)
        self.emit("RecordRemoved", case_id=case_id, kind=kind, record_id=record_id)

    def fileChargesheet(self, case_id, record_id, record_hash):
        self.only(INVESTIGATOR)
        case = self._case(case_id)
        self.require(
            case["status"] == UNDER_INVESTIGATION,
            f"a chargesheet can only be filed while UNDER_INVESTIGATION (case is {case['status']})",
        )
        self.state.set(
            f"rec/{case_id}/CHARGESHEET/{record_id}",
            {"hash": record_hash, "author": self.sender, "at": self.now, "removed": False},
        )
        case["status"] = CHARGESHEET_FILED
        self._save(case_id, case)
        self.emit("ChargesheetFiled", case_id=case_id, record_id=record_id, record_hash=record_hash)
        self.emit("StatusChanged", case_id=case_id, previous=UNDER_INVESTIGATION, status=CHARGESHEET_FILED)

    def recordVerdict(self, case_id, record_id, record_hash, outcome):
        self.only(JUDICIARY)
        case = self._case(case_id)
        self.require(
            case["status"] == CHARGESHEET_FILED,
            f"a verdict requires a filed chargesheet (case is {case['status']})",
        )
        self.require(outcome in VERDICT_OUTCOMES, f"unknown outcome {outcome}")
        self.state.set(
            f"rec/{case_id}/VERDICT/{record_id}",
            {"hash": record_hash, "author": self.sender, "at": self.now, "removed": False, "outcome": outcome},
        )
        case["status"] = CONVICTED if outcome == "CONVICTED" else CLOSED
        self._save(case_id, case)
        self.emit("VerdictRecorded", case_id=case_id, record_id=record_id, outcome=outcome)
        self.emit("StatusChanged", case_id=case_id, previous=CHARGESHEET_FILED, status=case["status"])

    def shareCase(self, case_id, department):
        self.only(POLICE, INVESTIGATOR, ADMIN)
        self._case(case_id)
        self.state.set(f"share/{case_id}/{department}", {"by": self.sender, "at": self.now, "active": True})
        self.emit("CaseShared", case_id=case_id, department=department, by=self.sender)

    def revokeShare(self, case_id, department):
        self.only(POLICE, INVESTIGATOR, ADMIN)
        share = self.state.get(f"share/{case_id}/{department}")
        self.require(share is not None and share["active"], "case is not shared with that department")
        share.update(active=False, revoked_by=self.sender, revoked_at=self.now)
        self.state.set(f"share/{case_id}/{department}", share)
        self.emit("ShareRevoked", case_id=case_id, department=department, by=self.sender)


class EvidenceRegistry(Contract):
    """Evidence hashes and the chain of custody."""

    NAME = "EvidenceRegistry"
    ABI = ("registerEvidence", "logAccess", "transferCustody", "addForensicReport")

    def _evidence(self, evidence_id):
        evidence = self.state.get(f"ev/{evidence_id}")
        self.require(evidence is not None, f"evidence {evidence_id} is not registered")
        return evidence

    def registerEvidence(self, evidence_id, case_id, file_hash, meta_hash):
        self.only(POLICE, INVESTIGATOR, FORENSIC)
        self.require(self.state.get(f"case/{case_id}") is not None, f"case {case_id} does not exist")
        self.require(self.state.get(f"ev/{evidence_id}") is None, "evidence already registered")
        self.state.set(
            f"ev/{evidence_id}",
            {
                "case_id": case_id,
                "file_hash": file_hash,
                "meta_hash": meta_hash,
                "custodian": self.sender,
                "registered_by": self.sender,
                "registered_at": self.now,
                "custody_events": 1,
            },
        )
        self.emit("EvidenceRegistered", evidence_id=evidence_id, case_id=case_id, file_hash=file_hash)

    def logAccess(self, evidence_id, action):
        self.require(self.role_of(self.sender) is not None, "caller is not a registered user")
        evidence = self._evidence(evidence_id)
        evidence["custody_events"] += 1
        self.state.set(f"ev/{evidence_id}", evidence)
        self.emit("EvidenceAccessed", evidence_id=evidence_id, action=action, by=self.sender)

    def transferCustody(self, evidence_id, to):
        evidence = self._evidence(evidence_id)
        self.require(
            evidence["custodian"] == self.sender or self.role_of(self.sender) == ADMIN,
            "only the current custodian can transfer custody",
        )
        self.require(self.role_of(to) is not None, "recipient is not a registered user")
        self.require(to != evidence["custodian"], "recipient already holds custody")
        previous, evidence["custodian"] = evidence["custodian"], to
        evidence["custody_events"] += 1
        self.state.set(f"ev/{evidence_id}", evidence)
        self.emit("CustodyTransferred", evidence_id=evidence_id, previous=previous, to=to)

    def addForensicReport(self, evidence_id, record_id, record_hash):
        self.only(FORENSIC)
        evidence = self._evidence(evidence_id)
        self.state.set(
            f"rec/{evidence['case_id']}/FORENSIC/{record_id}",
            {"hash": record_hash, "author": self.sender, "at": self.now, "removed": False, "evidence": evidence_id},
        )
        evidence["custody_events"] += 1
        self.state.set(f"ev/{evidence_id}", evidence)
        self.emit("ForensicReportAdded", evidence_id=evidence_id, record_id=record_id, record_hash=record_hash)


CONTRACTS = {contract_address(c.NAME): c for c in (AccessControl, CaseRegistry, EvidenceRegistry)}
ADDRESS = {c.NAME: address for address, c in CONTRACTS.items()}


def call(state, tx):
    """Execute a transaction's contract call against `state`; returns emitted events."""
    contract_cls = CONTRACTS.get(tx["to"])
    if contract_cls is None:
        raise Revert(f"no contract deployed at {tx['to']}")
    if tx["method"] not in contract_cls.ABI:
        raise Revert(f"{contract_cls.NAME}: unknown method {tx['method']}")
    contract = contract_cls(state, tx["sender"], tx["timestamp"])
    try:
        getattr(contract, tx["method"])(**tx["args"])
    except TypeError as exc:
        raise Revert(f"{contract_cls.NAME}.{tx['method']}: bad arguments ({exc})") from exc
    return contract.events
