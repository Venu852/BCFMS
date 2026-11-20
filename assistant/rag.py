"""Retrieval-augmented question answering over case files.

Retrieval is restricted to the cases the asking officer is allowed to open, so
the model can never be shown, and therefore can never leak, a file outside the
user's access. Answers must cite the numbered excerpts they were built from.
"""
import math
import re
import time
from collections import Counter

import numpy as np
from django.conf import settings
from django.db.models import Q

from cases.access import access_for
from cases.models import Case

from . import embedder, llm
from .indexer import PERSONAL
from .models import AssistantQuery, DocumentChunk

FIR_PATTERN = re.compile(r"FIR/[A-Z0-9]+/\d{4}/\d+", re.I)
CASE_PATTERN = re.compile(r"CASE-[0-9A-F]{16}", re.I)
WORD = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset(
    "a an and are as at be by did do does for from had has have how in is it its of on or that the their there "
    "this to was were what when where which who whom why with about any all case cases me tell show list give".split()
)
MAX_PER_CASE = 3
CONTEXT_CHARS = 9000

SYSTEM_PROMPT = (
    "You are the BCFMS case assistant, helping police officers analyse crime case files.\n"
    "Rules:\n"
    "1. Answer using ONLY the numbered excerpts provided. They are the only facts you know.\n"
    "2. After every fact, cite the excerpt it came from using its tag, for example [S2].\n"
    "3. Always name the FIR number of each case you refer to.\n"
    "4. If the excerpts do not contain the answer, say that it is not in the case files available to you. "
    "Never guess or invent names, dates, places or findings.\n"
    "5. Be concise and factual. Use short bullet points when comparing several cases."
)


def tokens(text):
    return [w for w in WORD.findall(text.lower()) if w not in STOPWORDS and len(w) > 1]


def _bm25(query_terms, documents):
    """Okapi BM25 scores of each tokenised document for the query."""
    n = len(documents)
    if not n or not query_terms:
        return np.zeros(n, dtype=np.float32)
    lengths = np.array([len(d) for d in documents], dtype=np.float32)
    average = float(lengths.mean()) or 1.0
    frequencies = [Counter(d) for d in documents]
    scores = np.zeros(n, dtype=np.float32)
    for term in set(query_terms):
        containing = sum(1 for f in frequencies if term in f)
        if not containing:
            continue
        idf = math.log(1 + (n - containing + 0.5) / (containing + 0.5))
        for i, f in enumerate(frequencies):
            tf = f.get(term, 0)
            if tf:
                scores[i] += idf * tf * 2.5 / (tf + 1.5 * (0.25 + 0.75 * lengths[i] / average))
    return scores


def _normalise(scores):
    top = float(scores.max()) if len(scores) else 0.0
    return scores / top if top > 0 else scores


def retrieve(user, question, case=None, k=None):
    """Top passages for `question` among the cases `user` may access."""
    k = k or settings.BCFMS_RAG_TOP_K
    visible = Case.objects.visible_to(user)
    scoped = case is not None
    if scoped:
        visible = visible.filter(pk=case.pk)
    else:
        # A question that names a case is answered from that case alone.
        mentioned = [m.upper() for m in FIR_PATTERN.findall(question)] + [m.upper() for m in CASE_PATTERN.findall(question)]
        if mentioned:
            named = visible.filter(Q(fir_number__in=mentioned) | Q(case_id__in=mentioned))
            if named.exists():
                visible, scoped = named, True

    chunks = list(DocumentChunk.objects.filter(case__in=visible.values("pk")).select_related("case"))
    # Personal details are withheld where the officer's access to that case excludes them.
    personal = {}
    for chunk in chunks:
        if chunk.source_type == PERSONAL and chunk.case_id not in personal:
            personal[chunk.case_id] = access_for(user, chunk.case).personal
    chunks = [c for c in chunks if c.source_type != PERSONAL or personal[c.case_id]]
    if not chunks:
        return []

    lexical = _normalise(_bm25(tokens(question), [tokens(f"{c.case.fir_number} {c.label} {c.text}") for c in chunks]))
    semantic = np.zeros(len(chunks), dtype=np.float32)
    query_vector = embedder.embed([question])
    have_vectors = query_vector is not None and all(c.embedding for c in chunks)
    if have_vectors:
        matrix = np.vstack([np.frombuffer(c.embedding, dtype=np.float32) for c in chunks])
        semantic = np.clip(matrix @ query_vector[0], 0, None)
    scores = 0.6 * _normalise(semantic) + 0.4 * lexical if have_vectors else lexical

    picked, per_case = [], Counter()
    for index in np.argsort(-scores):
        score = float(scores[index])
        if score <= 0.12 or (have_vectors and semantic[index] < 0.12 and lexical[index] == 0):
            continue
        chunk = chunks[index]
        if not scoped and per_case[chunk.case_id] >= MAX_PER_CASE:
            continue
        per_case[chunk.case_id] += 1
        picked.append((chunk, score))
        if len(picked) == k:
            break
    return picked


def _context(picked):
    blocks, used = [], 0
    for number, (chunk, _) in enumerate(picked, start=1):
        block = f"[S{number}] {chunk.case.fir_number} (case {chunk.case.case_id}) — {chunk.label}\n{chunk.text}"
        if used + len(block) > CONTEXT_CHARS and blocks:
            break
        blocks.append(block)
        used += len(block)
    return "\n\n".join(blocks), len(blocks)


def answer(user, question, case=None):
    """Answer `question`; returns {"answer", "sources", "mode", "latency_ms"} and logs the query."""
    started = time.perf_counter()
    picked = retrieve(user, question, case)
    context, used = _context(picked)
    picked = picked[:used]
    sources = [
        {
            "tag": f"S{number}",
            "fir_number": chunk.case.fir_number,
            "case_id": chunk.case.case_id,
            "label": chunk.label,
            "url": chunk.url,
            "snippet": chunk.text[:320] + ("…" if len(chunk.text) > 320 else ""),
            "score": round(score, 3),
        }
        for number, (chunk, score) in enumerate(picked, start=1)
    ]

    if not picked:
        mode = "none"
        text = (
            "I could not find anything relevant in the case files you have access to. "
            "Try naming the FIR number, a person, a place or the type of crime."
        )
    else:
        reply = llm.chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Case file excerpts:\n\n{context}\n\nQuestion: {question}"},
            ]
        )
        if reply:
            mode, text = "generated", reply
        else:
            mode = "extractive"
            text = "The language model is not available, so here are the most relevant passages from the case files instead."

    # Accept both "[S1]" and grouped forms such as "[S1, S3]".
    cited = {tag for group in re.findall(r"\[([^\]]*)\]", text) for tag in re.findall(r"S\d+", group)}
    for source in sources:
        source["cited"] = source["tag"] in cited
    latency = int((time.perf_counter() - started) * 1000)
    AssistantQuery.objects.create(
        user=user, scope_case=case, question=question, answer=text, sources=sources, mode=mode, latency_ms=latency
    )
    return {"answer": text, "sources": sources, "mode": mode, "latency_ms": latency, "model_error": llm.status()["error"]}
