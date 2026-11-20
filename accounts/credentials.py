"""User IDs and one-time passwords issued by the admin office."""
import re
import secrets

from .models import Role, User

PREFIX = {Role.ADMIN: "ad", Role.POLICE: "po", Role.INVESTIGATOR: "io", Role.FORENSIC: "fx", Role.JUDICIARY: "jd"}
# No look-alike characters (0/O, 1/l/I), so a password can be read out or copied by hand.
LOWER, UPPER, DIGITS, SYMBOLS = "abcdefghijkmnpqrstuvwxyz", "ABCDEFGHJKLMNPQRSTUVWXYZ", "23456789", "@#%&*"


def generate_username(first_name, role):
    """e.g. io.arjun, then io.arjun2 if that is taken."""
    stem = re.sub(r"[^a-z0-9]", "", first_name.lower()) or "officer"
    base = f"{PREFIX.get(role, 'po')}.{stem}"
    candidate, number = base, 1
    while User.objects.filter(username=candidate).exists():
        number += 1
        candidate = f"{base}{number}"
    return candidate


def generate_password(length=12):
    """Random password containing every character class."""
    required = [secrets.choice(group) for group in (LOWER, UPPER, DIGITS, SYMBOLS)]
    rest = [secrets.choice(LOWER + UPPER + DIGITS) for _ in range(length - len(required))]
    characters = required + rest
    secrets.SystemRandom().shuffle(characters)
    return "".join(characters)
