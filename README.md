# Friendly Tax Advisor

An advisor-facing Q&A assistant for educational questions about Section 1031 exchanges, Delaware Statutory Trusts (DSTs), and Section 721 contributions to a REIT operating partnership (often described as a “721 exchange”).

> **Status:** This repository currently has no application code. The linked sources below are the planned public reference set; they have not been downloaded, indexed, or used by an assistant.

## Part 1: Advisor Q&A assistant

The planned application is a simple, authenticated chat for financial advisors. It should answer only from its approved source set, cite the passages and source links supporting each answer, and say when the answer is not present in those materials. It should not fill gaps with uncited model knowledge.

### Answer guardrails

- Provide general educational information only. Do not give personalized tax, legal, or investment advice, determine whether a specific transaction qualifies, or recommend an investment.
- When a user asks what they personally should do, decline to make that decision. Offer relevant general information from the approved sources when possible and suggest consulting a qualified tax, legal, or investment professional.
- Include a short disclaimer with every answer: “For general educational purposes only; this is not tax, legal, or investment advice.”
- If the approved sources do not support an answer, say so clearly and do not guess.
- Record each question, answer, cited sources, and timestamp in an admin-only conversation log.

## Public reference set

These ten public references are the planned starting corpus for Part 1. The IRS and statutory materials cover like-kind exchanges, DST treatment, partnership contributions, and basis. SEC materials provide REIT background and one issuer-specific example of an operating-partnership structure.

1. [IRS Publication 544, Sales and Other Dispositions of Assets](https://www.irs.gov/publications/p544) — overview of like-kind exchanges, recognized gain, and basis.
2. [IRS Instructions for Form 8824, Like-Kind Exchanges](https://www.irs.gov/instructions/i8824) — reporting and deferred-exchange requirements.
3. [26 U.S.C. § 1031](https://uscode.house.gov/view.xhtml?req=granuleid:USC-prelim-title26-section1031&num=0&edition=prelim) — statute governing qualifying exchanges of real property.
4. [26 C.F.R. § 1.1031(k)-1](https://www.ecfr.gov/current/title-26/section-1.1031%28k%29-1) — rules for deferred exchanges.
5. [IRS Revenue Ruling 2004-86](https://www.irs.gov/irb/2004-33_IRB#RR-2004-86) — federal tax classification and Section 1031 treatment of the DST arrangement described in the ruling.
6. [26 U.S.C. § 721](https://uscode.house.gov/view.xhtml?req=granuleid:USC-prelim-title26-section721&num=0&edition=prelim) — general nonrecognition rule for property contributed to a partnership for a partnership interest, subject to statutory exceptions.
7. [26 C.F.R. § 1.721-1](https://www.ecfr.gov/current/title-26/section-1.721-1) — regulations on nonrecognition for partnership contributions.
8. [SEC Investor Bulletin: Real Estate Investment Trusts (REITs)](https://www.sec.gov/files/reits.pdf) — REIT structure and investor basics.
9. [ExchangeRight Income Fund 2025 Form 10-K](https://www.sec.gov/Archives/edgar/data/1771514/000119312526075069/none-20251231.htm) — issuer-specific example describing an UPREIT, its operating partnership, and OP-unit holders.
10. [IRS Publication 551, Basis of Assets](https://www.irs.gov/publications/p551) — basis concepts relevant to property exchanges and contributions.

Revenue Ruling 2004-86 applies to the facts it describes, and the SEC filing illustrates only that issuer’s structure. Neither establishes the terms or tax result for another DST, REIT, or investor. Before supporting product-specific answers, add current public transaction documents and ground each claim in the document that supports it.

## Current state and next steps

The app, login, source ingestion and retrieval, passage-level citations, advice-request handling, answer disclaimer, admin log, and deployment are not implemented in this repository. There is no runnable application yet.

Next, build the small chat application, ingest and keep the approved sources current, return citations to the specific supporting passages, and implement the guardrails and admin-only log described above. Then evaluate answers for supported questions, questions outside the source set, and personalized advice requests before deploying it.
