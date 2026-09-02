# Preview v3 corpus audit

Audited on 2026-09-02 from the owner-provided archive
`poc_dataset/v8_js_pocs_preview_v3.zip`.

## Package and index integrity

- Archive SHA-256: `f89960a11adcd0dfae78e99c2047ea9787b7609902f8f85c32a36c54a15d455f`.
- 10,078 archive entries and 216,968,027 uncompressed bytes.
- 10,070 `.js` files, one SQLite index, and no absolute paths, traversal paths,
  or symbolic links.
- SQLite `PRAGMA integrity_check`: `ok`; dataset schema version: 2.
- 10,070 accepted artifacts, 10,070 FTS source records, 22,767 provenance
  instances, 60,925 tag relations, 13,264 required-flag relations, 13,760 issue
  relations, 2,181 CVE relations, and 12,548 explicitly excluded candidates.
- The dataset builder states that no acquired JavaScript was executed.

## Full accepted inventory

| Dimension | Counts |
| --- | --- |
| Engine | V8 7,491; WebAssembly/V8 2,192; unclassified JS engine 219; Chromium/JS 118; SpiderMonkey 32; JavaScriptCore 18 |
| Syntax | ECMAScript 5,696; V8/d8 intrinsics 4,374 |
| Primary category | regression 8,321; fuzzer corpus/harness 420; extracted inline JS 358; security artifact 287; issue attachment 226; benchmark reproducer 173; issue-inline reproducer 93; PoC/reproducer 83; exploit 82; search candidate 25; support harness 2 |
| Size | ≤4 KiB 9,146; 4–16 KiB 669; 16–64 KiB 156; 64–128 KiB 78; >128 KiB 21 |
| Intrinsics | none 5,595; 1–3 3,193; 4–10 975; >10 307 |
| Markers | Wasm only 1,894; exploit only 187; both 301; neither 7,688 |
| Origin | preserved original 9,590; derived extraction 476; both available 4 |

Prominent tags include native intrinsics, WebAssembly, Maglev, TurboFan,
Turboshaft, Liftoff, sandbox markers, exploit primitives, module syntax, and
historically deleted tests. The most common recorded runtime requirements are
`--allow-natives-syntax`, TurboFan, Maglev, `--expose-gc`, heap verification,
Sparkplug, sandbox testing, JIT fuzzing, and Wasm tiering flags. These fields are
descriptive dataset metadata; Fuzzynth does not automatically enable every
historical flag.

## Fuzzynth eligibility policy

The preview is context data, never an automatically executed test suite. The
loader applies only deterministic technical filters:

1. require an index row accepted by the dataset builder;
2. cap each sample at 48 KiB so two examples plus conversation history fit the
   128 KiB local context envelope;
3. omit explicit SpiderMonkey and JavaScriptCore samples because engine-specific
   shell APIs would reduce d8-validity;
4. omit `support_harness` as non-PoC context;
5. retain one deterministic representative for each normalized-source hash;
6. verify safe filename, exact byte size, SHA-256, and strict UTF-8 at every
   campaign startup; fail closed if fewer than 1,000 samples remain.

This yields 9,830 byte-distinct, normalized-deduplicated samples totaling
15,406,909 bytes:

| Dimension | Eligible counts |
| --- | --- |
| Engine | V8 7,409; WebAssembly/V8 2,101; unclassified JS engine 211; Chromium/JS 109 |
| Syntax | ECMAScript 5,499; V8/d8 intrinsics 4,331 |
| Intrinsics | none 5,406; 1–3 3,161; 4–10 965; >10 298 |
| Markers | Wasm only 1,871; exploit only 178; both 230; neither 7,551 |
| Origin | preserved original 9,411; derived extraction 417; both available 2 |

The filter removes 112 oversized sources, 47 engine-specific sources that pass
the size limit, and 81 additional normalized duplicates. It does not rank or
hand-select supposedly promising vulnerabilities.

## Paired prompt experiment

Each model/effort configuration has exactly two workers: `rich` uses the prior
system prompt and `lean` uses the minimal creativity prompt. Both variants derive
their session plan and corpus window from the same pair ID and ordinal, giving
them identical examples, temperature, reasoning effort, and turn limit. The
selection is a deterministic pseudorandom uniform sample of two from the 9,830
eligible files; the seed is retained for reproducibility.

Every generation archives the exact request containing the system prompt and
also records `prompt_variant`, `prompt_sha256`, `corpus_pair_id`, corpus-window
SHA-256, and every selected source filename and SHA-256. This supports later
paired analysis without relying on mutable configuration files.
