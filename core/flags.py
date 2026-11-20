"""Automatic flagging of officer activity for review by the admin office.

Flags come from fixed, explainable rules. A flag means "worth a look", not proof
of wrongdoing: the admin reviews each one and records the outcome.
"""
import logging
import re
from datetime import timedelta

from django.utils import timezone

from accounts.models import Role

from .notify import notify

logger = logging.getLogger(__name__)

# --- suspicious questions to the assistant ----------------------------------
ACTION = r"(delet\w*|eras\w*|remov\w*|destroy\w*|wip\w*|alter\w*|modif\w*|chang\w*|edit\w*|tamper\w*|falsif\w*|forg\w*|fabricat\w*|backdat\w*|overwrit\w*|manipulat\w*|hid\w*|conceal\w*|suppress\w*|replac\w*)"
RECORD = r"(fir|evidence|record\w*|case\s*files?|case|logs?|audit\w*|hash\w*|blocks?|blockchain|ledger|custody|chargesheet|verdict|accused|reports?|notes?|statements?|entries|entry)"
INTENT = r"(how\s+(can|do|to|could|would|should)\b|can\s+(i|you|we)\b|could\s+(i|you|we)\b|help\s+me\b|i\s+(want|need|wish|would\s+like)\s+to\b|is\s+it\s+possible\s+to\b|is\s+there\s+a\s+way\s+to\b|ways?\s+to\b|please\b|let\s+me\b)"

QUESTION_RULES = [
    (
        "HIGH", "MANIPULATION", "Asked the assistant how to alter or remove records",
        re.compile(rf"{INTENT}[^.?!]{{0,80}}\b{ACTION}\b[^.?!]{{0,60}}\b{RECORD}\b", re.I),
    ),
    (
        "HIGH", "MANIPULATION", "Instructed the assistant to alter or remove records",
        re.compile(rf"^\s*{ACTION}\b[^.?!]{{0,60}}\b{RECORD}\b", re.I),
    ),
    (
        "HIGH", "MANIPULATION", "Asked how to act without being detected or logged",
        re.compile(
            r"without\s+(a\s+trace|being\s+(logged|detected|noticed|caught|flagged|tracked)|anyone\s+(knowing|noticing)|leaving)"
            r"|\b(bypass|circumvent|get\s+around|evade|avoid)\w*\b[^.?!]{0,40}\b(audit|log\w*|detection|flag\w*|blockchain|ledger|verification|security|permission\w*|access)"
            r"|\b(disable|turn\s+off|switch\s+off|stop)\b[^.?!]{0,30}\b(audit|logging|monitoring|flag\w*)"
            r"|\bcover[\s-]?up\b|\bundetect\w*\b|\buntraceabl\w*\b",
            re.I,
        ),
    ),
    (
        "MEDIUM", "QUESTION", "Tried to override the assistant's instructions",
        re.compile(
            r"ignore\s+(all\s+|your\s+|the\s+)?(previous|above|prior|earlier)\s+(instructions|rules|prompts?)"
            r"|\b(system|hidden)\s+prompt\b|\bjailbreak\b|\bdeveloper\s+mode\b"
            r"|\b(reveal|show|print)\b[^.?!]{0,20}\b(your\s+)?(instructions|prompt)\b"
            r"|\bpretend\s+(you|to\s+be)\b|\byou\s+are\s+now\b|\bact\s+as\s+(an?\s+)?(admin|administrator|root)\b",
            re.I,
        ),
    ),
    (
        "MEDIUM", "QUESTION", "Requested personal data in bulk",
        re.compile(
            r"\b(list|give|show|export|dump|extract)\b[^.?!]{0,30}\b(all|every)\b[^.?!]{0,40}\b(complainants?|victims?|witness\w*|phone|mobile|contact|address\w*|accused|names)\b"
            r"|\b(all|every)\b[^.?!]{0,20}\b(phone\s+numbers?|addresses|contact\s+details)\b",
            re.I,
        ),
    ),
]
FIR_PATTERN = re.compile(r"FIR/[A-Z0-9]+/\d{4}/\d+", re.I)

# Flags in these categories raise a tamper alert to both the admin office and the officer.
TAMPER_CATEGORIES = ("MANIPULATION", "MODIFICATION", "ACCESS", "INTEGRITY")

REFUSAL = (
    "The assistant can only answer questions about the contents of case files you are authorised to see. "
    "This request was not processed."
)


def raise_flag(officer, severity, category, title, detail="", case=None, entry=None):
    from .models import SecurityFlag

    flag = SecurityFlag.objects.create(
        officer=officer, severity=severity, category=category, title=title, detail=detail[:2000], case=case, entry=entry
    )
    tamper = category in TAMPER_CATEGORIES
    if tamper or severity == SecurityFlag.HIGH:
        from django.contrib.auth import get_user_model

        admins = get_user_model().objects.filter(role=Role.ADMIN, is_active=True)
        who = officer.display_name if officer else "Unknown user"
        where = f" on {case.fir_number}" if case else ""
        notify(
            admins, f"{'Tamper alert' if tamper else 'Flag raised'}: {who}{where}", f"{title}. {detail[:160]}",
            "flags", exclude=officer, level="ALERT" if tamper else "INFO",
        )
    if tamper and officer is not None and officer.role != Role.ADMIN:
        # The officer is told as well: the change or attempt is on record and the admin office knows.
        if category == "MODIFICATION":
            heading = f"Tamper alert: you changed a blockchain-sealed record{' in ' + case.fir_number if case else ''}"
            body = f"{title}. The change is permanently recorded on the ledger under your user ID and has been reported to the admin office."
        else:
            heading = "Tamper alert: your attempt was blocked and reported"
            body = f"{title}. The attempt is recorded under your user ID and the admin office has been notified."
        notify([officer], heading, body, case.get_absolute_url() if case else "notifications", level="ALERT")
    return flag


def check_question(user, question):
    """Screen a question put to the assistant. Returns True if it must not be answered."""
    from cases.models import Case

    blocked = False
    for severity, category, title, pattern in QUESTION_RULES:
        if pattern.search(question):
            raise_flag(user, severity, category, title, f"Question: {question}")
            blocked = blocked or category == "MANIPULATION" or "override" in title
            break
    named = {m.upper() for m in FIR_PATTERN.findall(question)}
    if named:
        visible = set(Case.objects.visible_to(user).filter(fir_number__in=named).values_list("fir_number", flat=True))
        outside = sorted(set(Case.objects.filter(fir_number__in=named - visible).values_list("fir_number", flat=True)))
        if outside:
            raise_flag(
                user, "MEDIUM", "ACCESS", "Asked the assistant about a case outside their access",
                f"Named {', '.join(outside)}. Question: {question}",
            )
    return blocked


# --- rules on audit entries -------------------------------------------------
def _recent(entry, action, minutes):
    from .models import AuditLog

    since = timezone.now() - timedelta(minutes=minutes)
    return AuditLog.objects.filter(actor=entry.actor, action=action, created_at__gte=since).count()


def _already_flagged(officer, category, minutes):
    from .models import SecurityFlag

    since = timezone.now() - timedelta(minutes=minutes)
    return SecurityFlag.objects.filter(officer=officer, category=category, created_at__gte=since).exists()


def evaluate(entry):
    """Apply the flag rules to a new audit entry."""
    officer, action, case = entry.actor, entry.action, entry.case
    if officer is None:
        return
    if action == "CASE_VERIFIED" and "INTEGRITY FAILURE" in entry.detail:
        raise_flag(
            officer, "HIGH", "INTEGRITY", "Case file does not match the blockchain",
            f"{entry.target}: a record was changed outside the application. Found during verification by {officer.display_name}.",
            case, entry,
        )
        return
    if officer.role == Role.ADMIN:
        return

    if action == "CHAIN_REVERT":
        raise_flag(officer, "HIGH", "MANIPULATION", "Attempted an action the rules do not permit", entry.detail, case, entry)
    elif action == "ACCESS_DENIED":
        raise_flag(officer, "HIGH", "ACCESS", "Tried to open something outside their access", entry.detail, case, entry)
    elif action == "CASE_UPDATED":
        late = case is not None and case.status != "FILED" and case.status != "UNDER_INVESTIGATION"
        raise_flag(
            officer, "MEDIUM" if late else "LOW", "MODIFICATION",
            "FIR amended after the chargesheet" if late else "FIR details amended", entry.detail, case, entry,
        )
    elif action == "ACCUSED_REMOVED":
        raise_flag(officer, "MEDIUM", "MODIFICATION", "Accused removed from an FIR", f"Removed: {entry.target}", case, entry)
    elif action == "NOTE_REMOVED":
        raise_flag(officer, "MEDIUM", "MODIFICATION", "Investigation note withdrawn", f"Note: {entry.target}", case, entry)
    elif action == "HEARING_CANCELLED":
        raise_flag(officer, "LOW", "MODIFICATION", "Hearing cancelled", entry.target, case, entry)
    elif action == "STATUS_CHANGED" and entry.detail.endswith("Closed"):
        raise_flag(officer, "MEDIUM", "MODIFICATION", "Case closed without a chargesheet", entry.detail, case, entry)
    elif action == "EVIDENCE_DOWNLOADED":
        if _recent(entry, action, 10) >= 5 and not _already_flagged(officer, "VOLUME", 10):
            raise_flag(officer, "MEDIUM", "VOLUME", "Many evidence files downloaded in a short time", "5 or more downloads within 10 minutes.", case, entry)
    elif action == "LOGIN_FAILED":
        if _recent(entry, action, 15) >= 3 and not _already_flagged(officer, "SIGN_IN", 15):
            raise_flag(officer, "MEDIUM", "SIGN_IN", "Repeated failed sign-in attempts", "3 or more failed attempts within 15 minutes.", None, entry)


def safe_evaluate(entry):
    try:
        evaluate(entry)
    except Exception:  # flagging must never break the action being logged
        logger.exception("Flag evaluation failed for audit entry %s", entry.pk)
