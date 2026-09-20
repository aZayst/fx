# Glossary

| Term | Meaning |
|---|---|
| **Spot** | FX deal settled two business days later (T+2). |
| **Base / quote currency** | In EUR/USD, EUR is base, USD is quote. Price = quote units per 1 base. |
| **Bid / ask / mid** | Dealer buys at bid, sells at ask; mid is the average. A client BUYs at the ask, SELLs at the bid. |
| **Pip** | Smallest usual price step: 0.0001 (0.01 for JPY pairs). **Spread** = ask − bid. |
| **bps** | Basis point, 1/100 of a percent. |
| **Market / limit order** | Fill now at the current price / fill only at that price or better. |
| **Trade date / value date** | The day it was dealt / the day the currencies actually change hands. |
| **Booking** | Recording an executed deal in the system of record with an id, dates and status. |
| **Position** | Net amount held per pair; **mark-to-market P&L** revalues it at the current mid. |
| **Post-trade / STP** | Everything after execution: confirmation, matching, clearing, settlement — ideally straight-through, no manual steps. |
| **Counterparty / CCP** | The other party (here: a clearing house) that must confirm the trade. |
| **TCR** | Trade Capture Report (FIX 35=AE): "here is a trade we did". |
| **Ack** | Trade Capture Report Ack (FIX 35=AR): accept or reject. |
| **Break** | Anything that isn't a clean match: rejected, unacked, or never sent. |
| **Reconciliation** | Comparing what *should* have happened with what did, and listing the gaps. |
| **SLA** | Deadline for an ack (30 s here). A breach is a *stuck* confirmation. |
| **Unacked** | Sent, no answer. A settlement risk: it may fail to settle on time. |
| **Fat finger** | Typing error (bad price/size); caught by the `OFF_MARKET_PRICE` rule. |
| **FIX** | Financial Information eXchange: the industry's `tag=value` messaging protocol. |
| **Idempotent** | Doing it twice has the same effect as once (a duplicate ack changes nothing). |
