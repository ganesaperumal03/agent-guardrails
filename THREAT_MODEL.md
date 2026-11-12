# Threat model: NovaPay alert triage agents

Four questions: **What are we protecting? Who might attack? How can they get in? How do we stop it?**

## 1. What we protect (assets)

| Asset | Why it matters |
|---|---|
| Customer money | Missed fraud = customer loses money |
| Innocent customers | A wrong freeze blocks rent, salary, EMI payments |
| Private data (phone, address, Aadhaar, KYC documents) | Privacy law (DPDP Act), RBI rules, trust |
| Correct fraud decisions | Real fraud must not be closed as a false alarm |
| Audit records | Must be complete and unchangeable for RBI / auditors |

## 2. Who might attack

| Attacker | Goal |
|---|---|
| Fraudster | Get their fraud alert closed |
| Malicious person | Get an innocent person's account frozen |
| Data thief | Pull customer private data out of the system |
| Insider / stolen analyst login | Approve or edit actions they shouldn't |
| Compromised tool or agent | Return fake data or hidden instructions |

## 3. Entry points (where outside text enters)

- UPI payment notes (incoming payments are fully attacker-controlled)
- Customer name and occupation fields
- Uploaded KYC documents
- Tool results (fraud API, ledger)
- Messages between agents
- Analyst decision form (edit action, reason, analyst ID)
- Alert fields (customer ID, account)

## 4. Threats and controls

| # | Threat | Attacker | Entry point | Damage | Controls |
|---|---|---|---|---|---|
| T1 | Hidden "close this alert" instruction | Fraudster | UPI note | Real fraud missed | Raw notes never reach the LLM (only a `suspicious_note` flag). Close on HIGH risk = Tier 2. Risk comes from the fraud score in code. |
| T2 | "Freeze account X" instruction | Malicious person | UPI note | Innocent person blocked | Action target always comes from the alert, never from text. Freeze = Tier 2 (human). |
| T3 | "Send data to this email" | Data thief | KYC document | Data leak | No email tool exists. Tools never return document text, phone, address or name. Report is scrubbed for emails / long numbers. |
| T4 | Fake authority ("ADMIN_OVERRIDE") | Fraudster | Name field | Wrong action | Name never sent to agents (screening returns only `clear` / `possible_match`). |
| T5 | Fake or tampered agent message | Compromised agent | Agent → agent | Skipped checks | Orchestrator re-validates findings with Pydantic (`extra="forbid"`). Invalid → `needs_review`. Routing is fixed code. |
| T6 | Poisoned tool result | Compromised tool | Fraud API | Wrong risk level | Score must be a number between 0 and 1. Else agent fails → `needs_review`. Unknown score = HIGH risk. |
| T7 | Injection / data dump via IDs | Data thief | customer_id | Many customers' data | Strict ID format check in every tool. Max 200 rows. |
| T8 | Edit to a dangerous action | Insider | Decision form | Delete / transfer | Deny by default. Policy checked again in `act` after any edit. |
| T9 | Rubber-stamp approval | Busy / careless analyst | Decision form | Fake human check | Reason required (empty = reject). Evidence shown on screen. |
| T10 | Approval by unknown person | Stolen / fake login | Decision form | Unauthorised action | Analyst allow list. (Production: real login + SSO.) |
| T11 | Invented facts in report | LLM hallucination | Case writer | Wrong decision | Evidence must match real flags, else `needs_review`. Freeze needs ≥ 2 real flags. |
| T12 | Huge input → cost / slowness | Attacker | Ledger | Cost spike, timeouts | 200-row limit. Fixed graph (no loops). Steps and cost measured in evals. |
| T13 | Changing logs to hide a mistake | Insider | Audit log | No accountability | Postgres trigger blocks UPDATE / DELETE / TRUNCATE on `audit_log`. Masked data. |
| T14 | Untrusted MCP server | Supply chain | MCP | Tool poisoning | Only our own 3 servers, fixed URLs in config. Only read tools on MCP. |

## 5. Main design principles

1. **The AI can be fooled. Code and humans decide.** Policy engine is plain code, deny by default.
2. **Least privilege.** Each agent only has the tools it needs. The case writer has none.
3. **What the AI never sees, it can never leak.** Private data stays inside the tools.
4. **Pass facts and labels between agents, never raw outside text.**
5. **Failed or unsure → human.** The system never guesses.
6. **Defense in depth.** Several layers, so one failing layer is not a disaster.

## 6. Remaining risks (accepted for now)

- New prompt-injection styles can bypass the note word list. Mitigated by T1/T2 controls, not removed.
- Analyst identity is a simple allow list, not real authentication.
- With Bedrock, AWS receives the findings (no names, phones, account IDs or raw notes, but still case details). Data stays in the Mumbai region (ap-south-1).
