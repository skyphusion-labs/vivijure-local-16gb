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

## Wiring this door into your studio

How you wire the door in depends on how the studio itself was deployed.

### If you deploy the studio with `deploy.sh` (the standard path)

Wiring the local-GPU door is one opt-in flag, not a manual checklist:

1. Bring this door up first (`docs/HOMELABBER.md`) and copy its **Backend URL** and **token** from the
   `ready` banner. For a binding that survives door restarts, use a named-tunnel (stable) URL; the
   default quick-tunnel URL changes on every restart.
2. In the studio's `deploy.env`, set:

   ```sh
   INSTALL_LOCAL_GPU=1
   LOCAL_BACKEND_URL=https://your-door-tunnel-host   # no trailing slash
   LOCAL_BACKEND_TOKEN=the-token-from-the-banner
   ```

3. Run `./deploy.sh`.

`deploy.sh` seeds both secrets into your account's Cloudflare Secrets Store, deploys the `local-gpu`
module worker, keeps its `MODULE_LOCAL_GPU` `[[services]]` binding in the core, and redeploys the core.
There is no CI to edit and no `wrangler` command to run by hand. The studio's `docs/DEPLOYMENT.md`
"local-GPU door" section is the authoritative reference.

Then verify: open the planner, pick this door in the `motion.backend` selector, and run one render end
to end.

### If you wire a studio by hand (no `deploy.sh`)

Same end state as `INSTALL_LOCAL_GPU=1`, done manually. Order matters: a `[[services]]` binding must
point at a module that is already DEPLOYED, and `local-gpu`'s `wrangler deploy` fails if its
Secrets-Store secrets are missing.

1. **Seed the two secrets first** into the account Cloudflare Secrets Store:

   ```sh
   wrangler secrets-store secret create <STORE_ID> --name LOCAL_BACKEND_URL   --value "https://your-door-tunnel-host"
   wrangler secrets-store secret create <STORE_ID> --name LOCAL_BACKEND_TOKEN --value "the token from the banner"
   ```

2. **Deploy the module worker.** In the studio's `modules/local-gpu/wrangler.toml`, replace the
   store-id placeholder with your Secrets-Store id, then `wrangler deploy` it; it comes up as
   `vivijure-module-local-gpu`.
3. **Bind it to the core.** Add the `[[services]]` binding to the core config so the registry discovers
   it (the registry scans env for `MODULE_*` bindings):

   ```toml
   # Local consumer GPU (CogVideoX-5B-I2V on a 16GB card). The local door.
   [[services]]
   binding = "MODULE_LOCAL_GPU"
   service = "vivijure-module-local-gpu"
   ```

4. **Redeploy the core.**

Either way, the backend must be running and reachable at `LOCAL_BACKEND_URL` before the door becomes
user-visible; a picked door pointing at nothing fails every render. When it is wired the door appears
in the planner's `motion.backend` selector (rendered from the manifest's `ui.locality="local"`) and
renders end to end.

## Trust boundary (do not break)

The `local-gpu` module has NO public surface (`workers_dev=false`, no route): the studio service
binding IS its auth. The backend itself sits behind a Cloudflare tunnel; keep its published port on
`127.0.0.1` (the compose default) so it is reachable only through the tunnel, and set
`LOCAL_BACKEND_TOKEN` for defense in depth. A public render endpoint is an unauthenticated GPU-spend /
DoS trigger against the homelab box.

## Clip duration and cadence (plan beat-sync around it)

CogVideoX-5B-I2V is a fixed 8 fps model trained at 49 frames, so this door does NOT render to a
requested duration. Every tier renders the native 49-frame clip and exports it at 8 fps:

| Tier | Frames | Realized length @ 8 fps |
|---|---|---|
| `draft` | 49 | ~6.1s |
| `standard` | 49 | ~6.1s |
| `final` | 49 | ~6.1s |

Two consequences the studio side must plan for (properties of the model, not knobs this door can honor):

1. **A requested duration does not change generation length.** Every shot comes back as ~6.1s.
   `num_frames` is ignored: live diagnostics showed that off-grid 25/41-frame jobs can report success
   while producing only latent tile noise.
2. **The cadence is 8 fps, not 24.** A shared `local-gpu` module may default `fps=24` (the LTX door's
   cadence); this door ignores it and pins export to 8 fps, because CogVideoX's frames ARE 8 fps frames
   (exporting them at 24 would play about 3x too fast).

For **beat-sync and audio alignment**, treat a local-16gb clip as a fixed ~6.1s beat at 8 fps,
and lay audio / music cuts against the REALIZED length (`output.seconds` in the job result), never
against the requested seconds. The datacenter door and the LTX door do not share this ceiling, so a film
that mixes doors will have per-shot lengths that differ by backend -- plan the cut list accordingly.

**The door advertises this grid on `/health`.** `GET /health` returns an additive `duration_grid`
block (`{"fps": 8, "tiers": {"draft": {"max_frames": 49}, "standard": {"max_frames": 49}, "final":
{"max_frames": 49}}}`), derived from the same tier config the per-clip clamps use, so the control plane
can preflight a storyboard against this card's real ceiling instead of guessing. The sibling LTX (12gb)
door omits the block by design (absence = no declared constraint), so a studio treats a door with no
`duration_grid` as unconstrained.
