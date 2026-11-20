# BCFMS — Blockchain-Enabled Intelligent Crime File Management System

A Django application for managing FIRs, evidence, investigation records and case
histories, with:

- a **Python, Ethereum-style private blockchain** (signed transactions, smart
  contracts, Proof-of-Authority sealing) that makes every record tamper-evident;
- a **privacy-preserving LLM–RAG assistant** (Google Gemma 4 E2B, run locally)
  that answers questions about case files and cites the documents it used;
- **role-based access**, a full **audit trail**, inter-departmental sharing,
  reports with PDF export, notifications and a REST API.

Everything runs on one machine. No case data is sent to any external service.

## Quick start (Windows, PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# GPU build of the Gemma runtime (NVIDIA, CUDA 12). For CPU only, use .../whl/cpu
.\.venv\Scripts\python.exe -m pip install llama-cpp-python --only-binary=:all: `
    --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124

# One-time model download (about 3.2 GB in total) into .\models
.\.venv\Scripts\python.exe -c "from huggingface_hub import hf_hub_download, snapshot_download; hf_hub_download('unsloth/gemma-4-E2B-it-GGUF', 'gemma-4-E2B-it-Q4_K_M.gguf', local_dir='models/gemma-4-E2B-it-GGUF'); snapshot_download('sentence-transformers/all-MiniLM-L6-v2', local_dir='models/all-MiniLM-L6-v2', allow_patterns=['*.json','*.txt','model.safetensors','1_Pooling/*'])"

.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py seed_demo      # optional: fictional demo data
.\.venv\Scripts\python.exe manage.py runserver
```

Open <http://127.0.0.1:8000/> for the public index page (the dashboard is at
`/dashboard/` after sign-in). After `seed_demo`, the sign-in page lists the demo
accounts (shown only while `BCFMS_DEBUG=1`); they all share the password printed
by the command.

| Demo user | Role | Department |
|---|---|---|
| `admin` | Admin | Police Headquarters |
| `officer.ravi`, `officer.meena` | Police Officer | Koramangala / Indiranagar |
| `io.arjun`, `io.fatima` | Investigating Officer | Koramangala / Cyber Crime |
| `forensic.nisha` | Forensic Expert | State Forensic Science Laboratory |
| `judge.rao` | Judiciary | City Civil and Sessions Court |

Without demo data, run `manage.py createsuperuser`, sign in, add departments and
create officer logins under **Officers**.

## The admin office

- **Officer logins are issued by the admin.** *Officers → Create officer login*
  generates a user ID and a one-time password, shown once. The officer must set
  their own password at first sign-in. There is no self-registration.
- **Everything officers do is logged**: sign-ins, pages opened, records viewed,
  additions, changes, exports and every question put to the assistant. Open an
  officer under *Officers → Activity* to see their full history.
- **Flags** are raised automatically (rules in `core/flags.py`) for: actions rejected
  by the smart contracts, attempts to open pages or cases outside the officer's
  access, FIR amendments, removal of accused or notes, closing a case without a
  chargesheet, bursts of evidence downloads, repeated failed sign-ins, failed
  integrity checks, and suspicious assistant questions (asking how to alter or
  remove records, how to avoid detection, attempts to override the assistant, bulk
  requests for personal data, questions about cases outside the officer's access).
  Questions about altering records or evading monitoring are not answered.
  The admin reviews each flag and records the outcome. Flags come from fixed
  keyword rules: they prompt a human review and can miss or over-report.
- **Tamper alerts.** Changing a sealed record (amending an FIR, removing an accused,
  withdrawing a note, closing without a chargesheet), a refused action, or an
  attempt to reach something outside the officer's access raises a tamper alert to
  **both** the officer and the admin office. It stays pinned at the top of every
  page until acknowledged. The edit form warns the officer before they save.
- **Access labels.** Every case shows the capacity in which the signed-in user holds
  it (`cases/access.py`): Lead Investigator, Own Station, Court, Evidence Custodian,
  Shared (read only or analysis only) or Admin Oversight. A case shared with another
  department is read-only there, and complainant contact details and addresses are
  withheld from shared and custodian access, including in the assistant and the API.
  These capacity rules are enforced by the application; the smart contracts enforce
  the role and workflow rules.
- **The blockchain is visible only to the admin**: the explorer, transactions,
  attack lab and ledger API. Officers see plain confirmations instead of hashes.

Notes:

- The CUDA wheel of `llama-cpp-python` needs the CUDA runtime DLLs. The app finds
  them automatically inside an installed CUDA build of PyTorch (or the `nvidia-*`
  pip packages). With no GPU, install the CPU wheel and set `BCFMS_LLM_GPU_LAYERS=0`.
- If the model cannot be loaded, the assistant still works in a reduced mode: it
  returns the most relevant passages without a written answer.
- Settings are read from `.env` (see `.env.example`).

## What is where

| App | Responsibility |
|---|---|
| `accounts` | Users, roles, departments, admin-issued officer logins, TOTP two-factor authentication, custodial wallets |
| `ledger` | The blockchain: `crypto.py`, `contracts.py` (smart contracts), `chain.py` (engine and validation), `attacks.py` (attack lab), block explorer |
| `cases` | FIRs, accused, notes, chargesheets, hearings, verdicts, evidence vault, chain of custody, forensic reports, sharing, integrity checks |
| `core` | Public index page, admin office, officer monitoring and flags, activity log, notifications, reports and PDF export, public FIR status lookup, demo seed |
| `assistant` | LLM–RAG: text extraction, embeddings, indexing, retrieval, Gemma generation |
| `api` | Read-only REST API |

## How the blockchain works

- **Accounts.** Every user gets a secp256k1 key pair at registration. The address is
  the last 20 bytes of the Keccak-256 hash of the public key, as in Ethereum.
- **Transactions.** Each action is a contract call signed by the acting user's key,
  with a per-account nonce (replay protection). Records themselves stay off-chain;
  the transaction carries their **SHA-256 hash**.
- **Smart contracts** (`ledger/contracts.py`), executed by the chain, not by the web layer:
  - `AccessControl` — wallet → role registry; only an on-chain admin can grant roles.
  - `CaseRegistry` — anchors FIR and document hashes and enforces the workflow
    Filed → Under Investigation → Chargesheet Filed → Closed / Convicted, including
    who may make each move.
  - `EvidenceRegistry` — evidence file hashes, custody transfers and access log.
  A call that breaks a rule **reverts**: it is recorded on-chain as failed and
  changes nothing.
- **Blocks** commit to their transactions (Merkle root), receipts and state, link to
  the parent block by hash, and are signed by the in-turn authority node
  (**Proof of Authority**, three sealers fixed at genesis, round-robin).
- **Verification.** *Verify chain* rechecks every hash, link, Merkle root and
  signature. *Deep verify* additionally re-executes every transaction from genesis
  and compares the results and state roots. *Verify authenticity* on a case
  re-hashes each record and evidence file and compares with the contract state.
- **Attack lab.** Six attacks (edit a block, edit and re-hash, forge with a rogue
  validator, replay, unauthorised contract call, direct database edit) are run
  against a copy of the chain and shown visually. The real ledger is never modified.

Evidence files are encrypted with AES-256-GCM and stored content-addressed (named
by their SHA-256, the same idea as an IPFS CID) under `media/vault/`.

## How the assistant works

1. Every case document (FIR, notes, evidence descriptions and the text of uploaded
   PDF/DOCX/TXT files, forensic reports, chargesheet, hearings, verdict) is split into
   passages and embedded with `all-MiniLM-L6-v2`.
2. A question is matched against passages **only from cases the asking officer may
   open**, using a hybrid of embedding similarity and BM25 keyword scoring.
3. The top passages are given to **Gemma 4 E2B** (`unsloth/gemma-4-E2B-it-GGUF`,
   Q4_K_M, run in-process by llama.cpp) with instructions to answer only from them
   and cite each fact as `[S1]`, `[S2]`…
4. The answer is shown with its sources (FIR number, document reference, link).
   Every question is written to the audit trail.

Available to Admin, Police Officer and Investigating Officer roles.
Rebuild the index at any time with `manage.py reindex_rag`.

## REST API

Authenticate with `Authorization: Token <key>` (generate one on the Profile page, or
`POST /api/v1/auth/token/` with `username` and `password`).

| Endpoint | Purpose |
|---|---|
| `GET /api/v1/cases/?q=&status=&location=&date_from=&date_to=` | Search cases |
| `GET /api/v1/cases/<case_id>/` | Full case file |
| `GET /api/v1/cases/<case_id>/audit/` | Audit entries and on-chain transactions |
| `GET /api/v1/cases/<case_id>/verify/` | Integrity check against the chain |
| `GET /api/v1/chain/blocks/`, `/chain/blocks/<n>/`, `/chain/tx/<hash>/` | Ledger data |
| `GET /api/v1/chain/verify/?deep=1` | Chain verification |
| `GET /api/v1/public/fir-status/?fir_number=` | Public status check (no login, rate-limited) |

## Tests

```powershell
.\.venv\Scripts\python.exe manage.py test core ledger
```

Covers the full case workflow, contract enforcement, block and database tamper
detection, replay rejection, the attack scenarios, case visibility, admin-issued
credentials, admin-only pages, activity logging and the flag rules.

## Scope and limits

This is a single-server system built to demonstrate the design; it is not hardened
for production use as it stands.

- **One node.** The three authority nodes are separate signing keys held by the same
  server, and blocks are stored in the application database. The validation logic is
  what real nodes would run, but there is no peer-to-peer network, so the server
  operator is trusted. A deployment would run the sealers on separate machines
  owned by the police, the forensic laboratory and the judiciary.
- **Custodial wallets.** Users' private keys are held by the server, encrypted with
  `BCFMS_VAULT_KEY`. Losing or changing that key makes wallets, TOTP secrets and
  evidence files unreadable.
- **Encryption** covers data at rest (evidence, keys). Use HTTPS in front of the
  server for data in transit.
- **Two-factor authentication** is implemented but only enforced for sensitive roles
  when `BCFMS_ENFORCE_MFA=1` (off by default so the demo accounts can sign in).
- **The assistant can be wrong.** It is a small model; treat answers as pointers and
  check the cited document. Retrieval loads all accessible passages into memory,
  which suits thousands of passages; use a vector index (FAISS, pgvector) beyond that.
- **SQLite** is the default database. Use PostgreSQL for concurrent users.
