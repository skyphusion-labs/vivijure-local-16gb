"""vivijure_local.core: the byte-identical shared core of the two local-gpu doors.

The LTX door (vivijure-local-12gb) and the CogVideoX door (vivijure-local-16gb) are ~90% the same code.
Everything that does NOT differ per model lives here and is kept BYTE-IDENTICAL across both repos:
R2 object I/O (`r2`), the i2v job contract (`contract`), the in-process job registry (`jobs`), the pure
VRAM-cap math (`vram`), the ready-banner (`announce`), and the RunPod-compatible HTTP server scaffold
(`server`). Each door keeps ONLY its `config.py` tier table, its engine module + `animate()` binding,
and its identity (`vivijure_local.door`: SERVICE, ENGINE, WEIGHTS_NOTE, animate), which the server and
announce read through the stable `..door` / `..config` seam.

STAGED extraction (per docs/architecture.md): the shared surface is a self-contained, diffable package
vendored byte-identical into each repo, proven identical by `diff`. Promoting it to a true single-source
package (its own repo or a git submodule) is now a trivial later lift; until then, a change here MUST be
mirrored to the sibling door in the same change and the two copies kept byte-identical.
"""
