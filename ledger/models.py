from django.conf import settings
from django.db import models


class Authority(models.Model):
    """A Proof-of-Authority node. Sealers take turns signing blocks; the root
    authority is the genesis admin account that bootstraps on-chain roles."""

    SEALER, ROOT = "SEALER", "ROOT"
    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=10, choices=[(SEALER, "Sealer node"), (ROOT, "Root authority")])
    address = models.CharField(max_length=42, unique=True)
    public_key = models.CharField(max_length=128)
    encrypted_private_key = models.TextField()
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order"]
        verbose_name_plural = "authorities"

    def __str__(self):
        return self.name


class Block(models.Model):
    number = models.PositiveBigIntegerField(unique=True)
    timestamp = models.BigIntegerField(help_text="Unix time in milliseconds")
    parent_hash = models.CharField(max_length=66)
    tx_root = models.CharField(max_length=66)
    receipt_root = models.CharField(max_length=66)
    state_root = models.CharField(max_length=66)
    extra_hash = models.CharField(max_length=66)
    extra = models.JSONField(default=dict)
    validator = models.CharField(max_length=42)
    gas_used = models.PositiveIntegerField(default=0)
    hash = models.CharField(max_length=66, unique=True)
    signature = models.CharField(max_length=130, blank=True)

    class Meta:
        ordering = ["-number"]

    def __str__(self):
        return f"Block #{self.number}"

    def to_dict(self, with_transactions=True):
        data = {
            "number": self.number,
            "timestamp": self.timestamp,
            "parent_hash": self.parent_hash,
            "tx_root": self.tx_root,
            "receipt_root": self.receipt_root,
            "state_root": self.state_root,
            "extra_hash": self.extra_hash,
            "extra": self.extra,
            "validator": self.validator,
            "gas_used": self.gas_used,
            "hash": self.hash,
            "signature": self.signature,
        }
        if with_transactions:
            data["transactions"] = [tx.to_dict() for tx in self.transactions.all()]
        return data


class Transaction(models.Model):
    tx_hash = models.CharField(max_length=66, unique=True)
    block = models.ForeignKey(Block, on_delete=models.PROTECT, related_name="transactions")
    index = models.PositiveIntegerField(default=0)
    sender = models.CharField(max_length=42, db_index=True)
    public_key = models.CharField(max_length=128)
    to = models.CharField(max_length=42)
    contract = models.CharField(max_length=40)
    method = models.CharField(max_length=40)
    args = models.JSONField(default=dict)
    nonce = models.PositiveBigIntegerField()
    timestamp = models.BigIntegerField()
    signature = models.CharField(max_length=130)
    status = models.BooleanField(default=True)
    error = models.TextField(blank=True)
    events = models.JSONField(default=list)
    gas_used = models.PositiveIntegerField(default=0)
    # Off-chain conveniences for querying; not part of any hash.
    case_ref = models.CharField(max_length=40, blank=True, db_index=True)
    confirm_ms = models.FloatField(default=0)

    class Meta:
        ordering = ["block__number", "index"]

    def __str__(self):
        return f"{self.contract}.{self.method} {self.tx_hash[:12]}"

    def to_dict(self):
        return {
            "chain_id": settings.BCFMS_CHAIN_ID,
            "tx_hash": self.tx_hash,
            "sender": self.sender,
            "public_key": self.public_key,
            "to": self.to,
            "contract": self.contract,
            "method": self.method,
            "args": self.args,
            "nonce": self.nonce,
            "timestamp": self.timestamp,
            "signature": self.signature,
            "status": self.status,
            "error": self.error,
            "events": self.events,
            "gas_used": self.gas_used,
        }


class WorldState(models.Model):
    """Current contract storage (the result of executing every block)."""

    key = models.CharField(max_length=200, unique=True)
    value = models.JSONField(null=True)

    def __str__(self):
        return self.key
