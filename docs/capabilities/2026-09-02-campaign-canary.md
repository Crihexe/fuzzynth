# Campaign capability canary

Date: 2026-09-02

This record summarizes provider behavior observed with the real bounded campaign
payload. Exact requests, raw responses, usage, programs, and execution evidence
remain in private content-addressed runtime storage.

| Lane | Campaign result | Consequence |
|---|---|---|
| alternate Spark, none requested, high verbosity | 31 programs across two complete sessions; third session received HTTP 500 routing availability failure | operational; pause on provider availability/quota failure |
| alternate Luna, xhigh, high verbosity | one local 90 s timeout and one HTTP 524 at about 125 s; no program or usage returned | paused; no identical automatic retry |
| official Luna, low reasoning, temperature 1.8 | HTTP 400 unsupported temperature | combination disabled and retained for provenance |
| official Luna, none reasoning, temperature 1.2 | five-turn session completed with usage on every response | supported baseline; manually paused after the bounded run |

The official results refine rather than erase the 2026-09-01 probe: temperature
worked when reasoning was `none`, while the campaign combination using `low` was
rejected. Parameter compatibility must be measured as a tuple.

No candidate V8 crash occurred. Provider failures never produced a program and
therefore never invoked d8. All unknown-usage attempts retain conservative budget
reservations.
