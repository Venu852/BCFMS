"""The kind of access a user has to one case.

Visibility (may the user open the case at all?) is decided by Case.objects.visible_to.
This module answers the next question: in what capacity? The answer drives the
access label shown on every record, what the user may change, and whether personal
details of the complainant are shown.
"""
from dataclasses import dataclass

from accounts.models import Role


@dataclass(frozen=True)
class Access:
    code: str
    label: str        # shown on the record
    icon: str
    tone: str         # full / edit / limited / read
    summary: str      # one line: what this access allows
    can_edit: bool    # may change the case file (subject to role and case stage)
    personal: bool    # may see the complainant's contact details and address


LEAD = Access(
    "LEAD", "Lead Investigator · Full access", "search", "full",
    "You are the Investigating Officer: you can add notes and evidence, amend the FIR and file the chargesheet.", True, True,
)
OWNER = Access(
    "OWNER", "Own Station · Edit access", "folder", "edit",
    "This case belongs to your station: you can update it within the duties of your role.", True, True,
)
COURT = Access(
    "COURT", "Court · Judicial access", "shield", "edit",
    "You can read the full case file, verify its authenticity, schedule hearings and record the verdict.", False, True,
)
CUSTODIAN = Access(
    "CUSTODIAN", "Evidence Custodian · Analysis only", "lock", "limited",
    "You hold evidence in this case: you can examine it and file forensic reports. Personal details are hidden.", False, False,
)
SHARED_FORENSIC = Access(
    "SHARED", "Shared · Analysis only", "link", "limited",
    "Shared with your laboratory: you can read the case, register evidence and file forensic reports. Personal details are hidden.", False, False,
)
SHARED = Access(
    "SHARED", "Shared · Read only", "link", "read",
    "Shared with your department for reference: you can read the case but not change it. Personal details are hidden.", False, False,
)
OVERSIGHT = Access(
    "OVERSIGHT", "Admin Oversight · Read and audit", "users", "read",
    "You can read the case, assign an Investigating Officer, manage sharing and see its full audit trail. Case content is changed only by officers.", False, True,
)


def access_for(user, case):
    """Most specific capacity in which `user` holds `case`. Assumes the case is visible to them."""
    role = user.role
    if role == Role.ADMIN:
        return OVERSIGHT
    if role == Role.JUDICIARY:
        return COURT
    if case.investigating_officer_id == user.pk:
        return LEAD
    if role in (Role.POLICE, Role.INVESTIGATOR) and (
        (user.department_id and case.department_id == user.department_id) or case.filed_by_id == user.pk
    ):
        return OWNER
    if role == Role.FORENSIC:
        return CUSTODIAN if case.evidence.filter(custodian=user).exists() else SHARED_FORENSIC
    return SHARED
