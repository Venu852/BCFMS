from django.core.paginator import Paginator
from django.db.models import Avg, Count, Max, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from accounts.decorators import role_required
from accounts.models import Role
from core.audit import log as audit

from . import attacks, chain, contracts
from .models import Authority, Block, Transaction
from .services import address_book


def _strip(limit=14):
    """Latest blocks, oldest first, for the chain visualisation."""
    names = {a.address: a.name for a in Authority.objects.all()}
    rows = list(Block.objects.order_by("-number").prefetch_related("transactions")[:limit])[::-1]
    out = []
    for row in rows:
        txs = list(row.transactions.all())
        out.append(
            {
                "number": row.number,
                "hash": row.hash,
                "parent_hash": row.parent_hash,
                "timestamp": row.timestamp,
                "validator": names.get(row.validator, "Genesis"),
                "action": f"{txs[0].contract}.{txs[0].method}" if txs else "genesis",
                "tx_ok": txs[0].status if txs else True,
                "case_ref": txs[0].case_ref if txs else "",
            }
        )
    return out


@role_required(Role.ADMIN)
def explorer(request):
    chain.ensure_genesis()
    query = request.GET.get("q", "").strip()
    blocks = Block.objects.annotate(tx_count=Count("transactions")).order_by("-number")
    txs = Transaction.objects.select_related("block").order_by("-block__number")
    if query:
        if query.isdigit():
            blocks = blocks.filter(number=int(query))
            txs = txs.filter(block__number=int(query))
        else:
            blocks = blocks.filter(hash__istartswith=query)
            txs = txs.filter(
                Q(tx_hash__istartswith=query) | Q(sender__iexact=query) | Q(case_ref__iexact=query) | Q(method__iexact=query)
            )
    stats = Transaction.objects.aggregate(
        total=Count("id"), avg_ms=Avg("confirm_ms"), max_ms=Max("confirm_ms"), reverted=Count("id", filter=Q(status=False))
    )
    return render(
        request,
        "ledger/explorer.html",
        {
            "page": Paginator(blocks, 12).get_page(request.GET.get("page")),
            "transactions": txs[:15],
            "height": Block.objects.aggregate(h=Max("number"))["h"],
            "stats": stats,
            "authorities": Authority.objects.all(),
            "contracts": contracts.ADDRESS,
            "strip": _strip(),
            "book": address_book(),
            "query": query,
        },
    )


@role_required(Role.ADMIN)
def feed(request):
    chain.ensure_genesis()
    return JsonResponse({"blocks": _strip()})


@role_required(Role.ADMIN)
def block_detail(request, number):
    block = get_object_or_404(Block, number=number)
    keys, order = chain.authority_keys()
    parent = Block.objects.filter(number=number - 1).first()
    check = chain.validate_blocks([block.to_dict()], keys, order, trusted_parent=parent.hash if parent else block.parent_hash)[0]
    return render(
        request,
        "ledger/block.html",
        {
            "block": block,
            "transactions": block.transactions.all(),
            "check": check,
            "book": address_book(),
            "has_next": Block.objects.filter(number=number + 1).exists(),
        },
    )


@role_required(Role.ADMIN)
def tx_detail(request, tx_hash):
    tx = get_object_or_404(Transaction.objects.select_related("block"), tx_hash=tx_hash)
    data = tx.to_dict()
    return render(
        request,
        "ledger/transaction.html",
        {
            "tx": tx,
            "book": address_book(),
            "hash_ok": chain.compute_tx_hash(data) == tx.tx_hash,
            "signature_ok": chain.verify(tx.public_key, chain.signing_hash(data), tx.signature),
            "signing_hash": chain.signing_hash(data),
        },
    )


@role_required(Role.ADMIN)
@require_POST
def verify(request):
    deep = request.POST.get("deep") == "1"
    report = chain.verify_chain(deep=deep)
    audit(request, "CHAIN_VERIFIED", detail=f"{'Deep' if deep else 'Standard'} verification: {'valid' if report['valid'] else 'INVALID'}")
    return JsonResponse(report)


@role_required(Role.ADMIN)
def attack_lab(request):
    chain.ensure_genesis()
    return render(request, "ledger/attack_lab.html", {"attacks": attacks.ATTACKS, "strip": _strip(attacks.WINDOW)})


@role_required(Role.ADMIN)
def attack_run(request):
    block = request.GET.get("block", "")
    result = attacks.run(request.GET.get("attack", ""), int(block) if block.isdigit() else None)
    return JsonResponse(result)
