# Commercial licensing

TraceWire is **dual-licensed**.

| Who you are | Which license applies | What it costs |
|---|---|---|
| Using TraceWire on your own code, at work or at home | [AGPL-3.0-or-later](LICENSE) | Free |
| Teaching, research, internal tooling | [AGPL-3.0-or-later](LICENSE) | Free |
| Shipping TraceWire inside a product you sell | **Commercial license** | Paid |
| Running TraceWire as a hosted service for third parties | **Commercial license** | Paid |

---

## Why a commercial license exists

AGPL-3.0 lets anybody use, modify and self-host TraceWire for free — including
inside a company. The term that matters commercially is **section 13**: if you
let users interact with a modified version over a network, you must offer those
users the complete corresponding source.

That is fine for a team debugging its own code. It is not workable for:

- an **IDE or editor vendor** that bundles the tracer or the viewer,
- an **APM / observability platform** that ingests `.trace` files as a feature,
- a **CI provider** that offers "record and inspect the failure" as part of a
  hosted pipeline,
- an **appliance or embedded device** that ships TraceWire,
- anyone who must **keep their integration and modifications private**.

Those need a commercial license.

> **Using TraceWire is free. Reselling it, or hiding your changes to it inside
> something you sell, is what you pay for.**

---

## What a commercial license includes

| | Community (AGPL-3.0) | Commercial |
|---|---|---|
| Recorder, trace format, CLI | ✔ | ✔ |
| HTML timeline viewer, marker sets, predicates | ✔ | ✔ |
| Modify it, keep a private fork for your own use | ✔ | ✔ |
| **Relief from AGPL section 13 (no source disclosure)** | — | ✔ |
| **Right to embed in a closed-source product** | — | ✔ |
| **Right to offer a hosted tracing service** | — | ✔ |
| Trace-format stability commitment (no breaking change without a major) | — | ✔ |
| Priority issue handling, named contact | — | ✔ |
| Security advisory pre-notification | — | ✔ |
| Trademark license for "powered by TraceWire" branding | — | ✔ |

Nothing is crippled, time-limited or phone-home gated. The commercial license
buys **legal certainty and support** — you can audit the entire implementation
before buying.

---

## How to buy

1. **Evaluate** — run it as long as you like under AGPL-3.0.
2. **Quote** — email **3599905870@qq.com** with the legal entity, the intended
   use (embedded / hosted / internal), and the support level you need.
3. **Agreement** — you receive a commercial license naming the entity, the
   permitted deployment, the term and the fee.
4. **Payment and invoice** on signature.
5. **Delivery** — signed agreement, license reference, support channel, and
   written confirmation that the AGPL source-disclosure obligation does not
   apply to your deployment.

Renewal is annual. Perpetual licences for embedded deployments are available.

---

## Frequently asked

**Do I need a license to try it?**
No. Clone it, run it, modify it.

**We are a company debugging our own code. Do we pay?**
No. Internal use is permitted under AGPL-3.0. You pay when TraceWire becomes
part of something you sell, or when you cannot accept section 13.

**Can we keep our fork private?**
Yes, as long as you do not let third parties interact with it over a network.
Otherwise you need the commercial license.

**Do you accept contributions?**
Yes, under the terms in [CONTRIBUTING.md](CONTRIBUTING.md) — contributions are
licensed to the project under both the AGPL and the commercial terms, which is
what makes dual licensing possible.

---

Copyright © TraceWire Contributors. Contact: **3599905870@qq.com**
