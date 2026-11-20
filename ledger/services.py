"""Bridge between Django users and the chain."""
from django.contrib.auth import get_user_model

from . import chain, contracts
from .crypto import decrypt_secret
from .models import Authority


def user_send(user, contract, method, case_ref="", **args):
    """Send a contract call signed with the user's custodial wallet key."""
    return chain.send_transaction(
        user.wallet_address,
        user.public_key,
        decrypt_secret(user.encrypted_private_key),
        contract,
        method,
        args,
        case_ref=case_ref,
    )


def root_send(contract, method, **args):
    chain.ensure_genesis()
    root = chain.root_authority()
    return chain.send_transaction(
        root.address, root.public_key, decrypt_secret(root.encrypted_private_key), contract, method, args
    )


def onchain_role(address):
    return chain.read_state(f"acl/role/{address}")


def sync_role(user, by=None):
    """Mirror the user's Django role into the AccessControl contract.

    Signed by `by` when that admin already holds the on-chain ADMIN role,
    otherwise by the root authority. Returns the transaction, or None if in sync."""
    chain.ensure_genesis()
    wanted = user.role if (user.is_active and user.is_approved) else None
    if onchain_role(user.wallet_address) == wanted:
        return None
    signer = by if by is not None and by.pk != user.pk and onchain_role(by.wallet_address) == contracts.ADMIN else None
    send = (lambda *a, **kw: user_send(signer, *a, **kw)) if signer else root_send
    if wanted:
        return send("AccessControl", "grantRole", account=user.wallet_address, role=wanted)
    return send("AccessControl", "revokeRole", account=user.wallet_address)


def address_book():
    """Human-readable labels for every known address."""
    book = {address: f"{name} contract" for name, address in contracts.ADDRESS.items()}
    book.update({a.address: a.name for a in Authority.objects.all()})
    for user in get_user_model().objects.exclude(wallet_address=""):
        book[user.wallet_address] = f"{user.display_name} ({user.get_role_display()})"
    return book
