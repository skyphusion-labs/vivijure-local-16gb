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
