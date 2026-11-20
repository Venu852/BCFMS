from django.contrib.auth.models import AbstractUser
from django.db import models

from ledger.crypto import address_from_public_key, encrypt_secret, generate_keypair


class Department(models.Model):
    POLICE, FORENSIC, COURT, ADMIN = "POLICE", "FORENSIC", "COURT", "ADMIN"
    KINDS = [(POLICE, "Police station"), (FORENSIC, "Forensic laboratory"), (COURT, "Court"), (ADMIN, "Administration")]

    name = models.CharField(max_length=120)
    code = models.CharField(max_length=12, unique=True, help_text="Short code used in FIR numbers, e.g. KOR")
    kind = models.CharField(max_length=10, choices=KINDS, default=POLICE)
    district = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Role(models.TextChoices):
    ADMIN = "ADMIN", "Admin"
    POLICE = "POLICE", "Police Officer"
    INVESTIGATOR = "INVESTIGATOR", "Investigating Officer"
    FORENSIC = "FORENSIC", "Forensic Expert"
    JUDICIARY = "JUDICIARY", "Judiciary"


class User(AbstractUser):
    role = models.CharField(max_length=14, choices=Role.choices, default=Role.POLICE)
    department = models.ForeignKey(Department, null=True, blank=True, on_delete=models.SET_NULL, related_name="members")
    badge_number = models.CharField(max_length=30, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    is_approved = models.BooleanField(default=False, help_text="Set by an admin after verifying the officer's identity")

    # Digital identity: a custodial secp256k1 wallet. The private key is encrypted at rest.
    wallet_address = models.CharField(max_length=42, blank=True, db_index=True)
    public_key = models.CharField(max_length=128, blank=True)
    encrypted_private_key = models.TextField(blank=True)

    mfa_enabled = models.BooleanField(default=False)
    encrypted_totp_secret = models.TextField(blank=True)

    # Accounts are issued by the admin office with a generated password, which the
    # officer must replace at first sign-in.
    must_change_password = models.BooleanField(default=False)
    created_by = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="issued_accounts")

    def save(self, *args, **kwargs):
        if self.is_superuser:
            self.role, self.is_approved = Role.ADMIN, True
        if not self.wallet_address:
            private, public = generate_keypair()
            self.public_key = public
            self.wallet_address = address_from_public_key(public)
            self.encrypted_private_key = encrypt_secret(private)
        super().save(*args, **kwargs)

    @property
    def display_name(self):
        return self.get_full_name() or self.username

    @property
    def initials(self):
        parts = self.display_name.split()
        return "".join(p[0] for p in parts[:2]).upper()

    def has_role(self, *roles):
        return self.role in roles

    @property
    def is_admin_role(self):
        return self.role == Role.ADMIN
