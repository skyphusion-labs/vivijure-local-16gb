# Wiring the local door into the studio

How `vivijure-local-16gb` (this repo) plugs into the Vivijure studio through the `local-gpu`
motion.backend module. The control plane is UNCHANGED in code; wiring is binding + secrets + the
homelabber's running backend. Stage all of this now; flip it on the instant the benchmark proves out.

## The picture

```
studio control plane --(service binding MODULE_LOCAL_GPU)--> local-gpu module worker
   local-gpu --(POST /run i2v_clip, GET /status, POST /cancel)--> Cloudflare tunnel --> THIS backend (your consumer GPU)
```

The `local-gpu` module (in the `vivijure` repo, `modules/local-gpu/`) is the bridge. It holds the
backend URL + optional token and speaks the same `i2v_clip` wire body as the datacenter door. This
backend shares the studio's R2 bucket, so it reads the keyframe by key and writes the clip by key --
the module moves no bytes.

## The contract this backend exposes (what the module calls)

| Endpoint | Purpose |
|---|---|
| `POST /run` `{ "input": { action:"i2v_clip", project, shot_id, prompt, keyframe_key?, config } }` -> `{ "id" }` | submit a clip job |
| `GET /status/<id>` -> `{ id, status: IN_QUEUE\|IN_PROGRESS\|COMPLETED\|FAILED, output?, error? }` | poll |
| `POST /cancel/<id>` -> `{ ok: true }` | cancel (idempotent) |
| `GET /health` -> `{ ok: true, ... }` | liveness (tunnel + compose healthcheck) |
| `POST /run { "selftest": true }` -> `{ ok:true, selftest:true }` | no-GPU transport probe |

`config` carries `{ quality (draft\|standard\|final), num_frames, fps, seed?, flow_shift?, negative_prompt? }`.
Auth: if `LOCAL_BACKEND_TOKEN` is set, the module sends it as `Authorization: Bearer <token>` and the
server enforces it; unset = open (trusted-LAN tunnel only).

## Progress and status semantics (poll-only)

There is **no sub-step progress channel**. `GET /status/<id>` returns only the RunPod-compatible
`IN_QUEUE | IN_PROGRESS | COMPLETED | FAILED` envelope -- deliberately identical to the datacenter
(own-gpu) door, so the `local-gpu` module's poll loop is unchanged. There is no percentage and no
per-denoise-step event; the module polls until the status is terminal.

One consequence to plan for: **`IN_PROGRESS` covers the first-render weights load, not just the
denoise.** The first job on a freshly started (cold) box loads the model into VRAM before any denoise
step runs, and a box that has never pulled the model downloads several GB of weights first. During that
window `/status` reads `IN_PROGRESS` with no output for **several minutes** -- indistinguishable from a
hang from the poll side alone.

Two things make it legible:

- **Server logs.** The backend prints a one-time heads-up on the first job (`vivijure-local: first i2v
  job on this process -- the model weights load now ...`). An operator tailing the container logs sees
  the cold load is progressing, not stuck.
- **The warm model.** After the first job the model stays loaded for the life of the process, so every
  subsequent clip skips the load and goes straight to denoise.

If the studio wants a richer progress signal later (e.g. an R2 NDJSON event stream the planner tails),
that is an additive, cross-repo change -- it is intentionally NOT built here, because the door's job is
to match the datacenter contract, and the datacenter door is poll-only too.

## The flip checklist (studio side)

STATE (as of studio v0.7.7): `local-gpu` is currently **EXCLUDED** from the studio CI deploy (Strummer's
PR #382 added it to `EXCLUDE` because its `wrangler.toml` binds Secrets-Store secrets that were not yet
seeded -- an unsatisfiable binding aborted the v0.7.6 deploy), and there is **no** core
`MODULE_LOCAL_GPU` binding yet. So the flip is a deliberate, ORDERED sequence. Order matters -- verify
the live studio CI workflow before you run it, and only flip once the backend endpoint is reachable.

1. **Seed the module secrets FIRST** into the account Cloudflare Secrets Store. This must precede the
   deploy: `local-gpu`'s `wrangler.toml` binds them by `secret_name`, and `wrangler deploy` FAILS if the
   store secret does not exist (that is exactly what broke v0.7.6). Same store + flow as the RunPod
   modules (studio `docs/DEPLOYMENT.md` "Module secrets via the Secrets Store"):

   ```sh
   # the tunnel hostname terminating at the reachable backend (no trailing slash)
   wrangler secrets-store secret create <STORE_ID> --name LOCAL_BACKEND_URL   --value "https://render.example"
   # the shared secret the backend checks (optional; match the backend's .env LOCAL_BACKEND_TOKEN)
   wrangler secrets-store secret create <STORE_ID> --name LOCAL_BACKEND_TOKEN --value "<openssl rand -hex 32>"
   ```

2. **Remove `local-gpu` from the studio CI `EXCLUDE`** (`.github/workflows/ci.yml`). With the secrets
   seeded (step 1) its `wrangler deploy` now succeeds, so `vivijure-module-local-gpu` deploys.

3. **Bind it to the core.** Add a `[[services]]` binding to the core `wrangler.toml.example` so the
   registry discovers it (the registry scans env for `MODULE_*` service bindings). A `[[services]]`
   binding must point at a DEPLOYED module (else the core deploy dangles), so do this AFTER step 2:

   ```toml
   # Local consumer GPU (CogVideoX-5B-I2V on the homelabber's own card). The local door.
   [[services]]
   binding = "MODULE_LOCAL_GPU"
   service = "vivijure-module-local-gpu"
   ```

4. **The backend must already be running + reachable** at `LOCAL_BACKEND_URL` (this repo, via a
   Cloudflare tunnel) BEFORE steps 1-3 make the door user-visible -- a picked door pointing at nothing
   fails every render. See the repo README run-story + `docker-compose.yml`.

5. **Verify a live local-door render** end to end (the door appears in the selector and produces a clip).

Once this sequence completes the local door appears in the planner's motion.backend selector (Joan's
#379 selector renders it from the manifest's `ui.locality="local"` framing) and renders end to end.

## Trust boundary (do not break)

The `local-gpu` module has NO public surface (`workers_dev=false`, no route): the studio service
binding IS its auth. The backend itself sits behind a Cloudflare tunnel; keep its published port on
`127.0.0.1` (the compose default) so it is reachable only through the tunnel, and set
`LOCAL_BACKEND_TOKEN` for defense in depth. A public render endpoint is an unauthenticated GPU-spend /
DoS trigger against the homelab box.

## Clip duration and cadence (plan beat-sync around it)

CogVideoX-5B-I2V is a fixed 8 fps model with a hard 49-frame ceiling, so this door does NOT render to a
requested duration -- it renders a FIXED-LENGTH clip per tier and exports it at 8 fps:

| Tier | Frames | Realized length @ 8 fps |
|---|---|---|
| `draft` | 25 | ~3.1s |
| `standard` | 49 | ~6.1s |
| `final` | 49 | ~6.1s |

Two consequences the studio side must plan for (properties of the model, not knobs this door can honor):

1. **A requested duration is quantized, not honored.** A shot asking for ~5s comes back as ~6.1s on
   `standard`/`final` or ~3.1s on `draft`. `num_frames` can only LOWER the count within the tier ceiling
   (snapped to the 4k+1 stride); it can never extend a clip past 49 frames or change the 8 fps cadence.
2. **The cadence is 8 fps, not 24.** A shared `local-gpu` module may default `fps=24` (the LTX door's
   cadence); this door ignores it and pins export to 8 fps, because CogVideoX's frames ARE 8 fps frames
   (exporting them at 24 would play about 3x too fast).

For **beat-sync and audio alignment**, treat a local-16gb clip as a fixed ~3.1s or ~6.1s beat at 8 fps,
and lay audio / music cuts against the REALIZED length (`output.seconds` in the job result), never
against the requested seconds. The datacenter door and the LTX door do not share this ceiling, so a film
that mixes doors will have per-shot lengths that differ by backend -- plan the cut list accordingly.
