# Security policy

## Supported versions

This is a rolling, single-`main`-branch project: run the latest `main`. Only the latest revision
receives security fixes; if you are on an older checkout, pull `main` to pick them up.

## Reporting a vulnerability

Please do not file a public GitHub issue for a security problem. Report it privately to
**security@skyphusion.org**. If you would rather use GitHub, open the repository's **Security** tab and
click **"Report a vulnerability"** to file a private advisory that only you and the maintainers can
see.

Please include:

- A description of the issue
- Steps to reproduce, with a minimal example if possible
- The affected revision (commit SHA if known)
- Any suggestions for remediation

What to expect:

- **Acknowledgment** within a reasonable window (target: 5 business days).
- A **fix** in the latest revision once we confirm the issue; time-sensitive reports should say so.
- **Credit** for your report when the fix ships, unless you would rather stay anonymous.

Please give us a chance to ship a fix before any public disclosure (target: up to 90 days for a
coordinated fix).

## Scope

This is the **local-consumer render backend**: image-to-video (CogVideoX-5B-I2V) that a self-hoster runs on
**their own GPU**, exposed to their Vivijure studio over a Cloudflare tunnel. It is **self-hosted
software** -- you run it, on your own hardware, under your own jurisdiction; skyphusion ships the
software and operates nothing. The security boundary is:

- The backend is exposed to the public internet through the tunnel, so the image-to-video endpoint
  **hard-rejects any request without the operator's `LOCAL_BACKEND_TOKEN`** (an unset/empty token makes
  the i2v endpoint refuse to serve, never run open). That token is the gate between the public URL and
  the GPU; `/health` and the no-GPU selftest are the only open routes.
- The backend holds one storage credential: an R2 (S3-compatible) token scoped to the shared bucket,
  delivered through the operator's environment (`.env`). It reads the keyframe by key and writes the
  clip by key; it never moves bytes through the studio.
- Render-job input arrives from the studio control plane (trusted, behind Cloudflare Access) via the
  `local-gpu` module; this backend does not authenticate end users itself.
- The studio is **single-operator** (anti-SaaS identity strip, vivijure #292): no submitter identity is
  sent, and this backend stamps **no identity** onto artifacts; a job body that still carries a
  `user_email` is ignored, so a stripped identity cannot resurface as object metadata.

In-scope vulnerabilities include:

- An authentication bypass of the token gate (any path that runs i2v / touches the GPU without a valid
  `LOCAL_BACKEND_TOKEN`), or any regression that lets the endpoint serve open on a public tunnel.
- Unsafe file handling / path traversal in artifact or key handling that reads or writes outside the
  intended bucket prefix or job workspace.
- Server-side request forgery or arbitrary object access via attacker-influenced keys.
- Command or argument injection into a render step / shell-out (ffmpeg, model tooling) driven by job
  input.
- Exploitable dependency pulls, or leakage of the R2 credential or the `LOCAL_BACKEND_TOKEN`, or any
  reintroduction of submitter identity into artifact metadata (the identity strip must hold).

Out-of-scope:

- Operator misconfiguration on the operator's own deployment (e.g. forcing the endpoint open, or
  publishing the backend without the tunnel/token). The defaults are secure (token-gated, tunnel-only);
  a deployment that removes those protections is the operator's responsibility.
- Denial of service from intentionally expensive but well-formed renders (GPU cost is the operator's
  own concern; submit access is gated by the studio + the token).
- The security posture of the upstream model weights or third-party libraries themselves. Model weights
  load as `safetensors` (no code execution on load; pin the model revision); supply-chain risk in the
  weights or libraries is upstream (report it to those projects), beyond how this backend invokes them.

## Known dependency advisories (pinned ML stack -- deferred, with rationale)

The ML stack was RE-PINNED and re-proved on real silicon in the S8 re-pin proof (`docs/proof/REPIN-S8.md`):
`transformers==4.57.6` and `diffusers==0.38.0` on `torch 2.5.1` (cu124), with `accelerate==1.14.0` /
`safetensors==0.8.0`. The earlier deferral (pinned `transformers==4.46.3` / `diffusers==0.32.2` on
`torch 2.4.1`) is now CLOSED. `diffusers` 0.38.0 requires `torch >= 2.5`, so the bump moved the whole
stack together, validated on an RTX 4090 (both the LTX and CogVideoX doors: pipeline load + i2v render
+ R2 round-trip + the token / 404 hardening). `transformers` is held at the latest 4.x (4.57.6), not
5.x: `diffusers` 0.38 is not built against `transformers` 5, which additionally breaks the T5 tokenizer
load without a new `tiktoken` dependency.

Current open advisories (re-verified in S6) and why each is UNREACHABLE in this backend's threat model:

- HIGH -- untrusted-artifact-load: `transformers` remote code execution, the `diffusers`
  `trust_remote_code` / `custom_pipeline` bypass (including a TOCTOU variant), and `transformers`
  deserialization of untrusted data. This backend loads ONLY a PINNED model id (`THUDM/CogVideoX-5b-I2V`), never a
  user-supplied model / pipeline / config, and NEVER sets `trust_remote_code`. There is no path for a
  caller to point it at a malicious artifact, so none of these is reachable.
- MEDIUM / LOW -- `transformers` ReDoS: the reported regex-DoS advisories are in `MarianTokenizer`,
  `DonutProcessor`, `get_imports`, and `get_configuration_file`. None is on this backend's prompt path:
  the only caller-controlled strings are `prompt` / `negative_prompt`, which go through the pinned
  model's T5 / CLIP tokenizer (not Marian / Donut), while `get_imports` / `get_configuration_file` run
  during `from_pretrained` on the PINNED model id, never on attacker-influenced input. So there is no
  caller-reachable ReDoS.
- The endpoint is token-gated and tunnel-only (see Scope above), not a public multi-tenant surface.

The re-pin is now DONE (not merely scheduled): the diffusers `trust_remote_code` / `custom_pipeline`
RCE advisories (CVE-2026-44513 / GHSA-98h9-4798-4q5v, CVE-2026-44827 / GHSA-j7w6-vpvq-j3gm) and the
TOCTOU variant (CVE-2026-45804) are FIXED in `diffusers` 0.38.0 (the gate moved into
`get_cached_module_file`, huggingface/diffusers PR #13448). Reachability was and remains NONE here --
we never set `trust_remote_code` and load only a pinned model id -- so this bump is defense-in-depth
hygiene, now closed out on a real-silicon proof (S8) rather than a live-exposure fix. This note is the
paper trail -- a degrade is never silent. If you find a NEW advisory that IS reachable here (for
example, one triggerable through `prompt` text into the pinned model tokenizer), report it via the
channel above; that flips to an immediate re-pin.

The non-ML runtime deps (`accelerate`, `safetensors`, `sentencepiece`, `imageio`, `imageio-ffmpeg`,
`av`, `boto3`) are pinned to exact versions for reproducible builds, re-confirmed against the S8 re-pin
build on real silicon (`accelerate` moved to 1.14.0 and `safetensors` to 0.8.0 as the resolved set on
torch 2.5.1; `imageio` 2.37.3 / `av` 17.1.0 / `boto3` 1.43.38 unchanged).

## Acceptable use

Vivijure is self-hosted software: you run this backend on your own hardware, in your own jurisdiction, and you are the operator responsible for using it lawfully and for whatever is generated on your machine. Skyphusion Labs ships the software and operates no instance of it, so it does not and architecturally cannot monitor what you generate. Misuse is therefore an acceptable-use matter, not a security vulnerability -- do not report generated content through the security channel.

One line is absolute no matter where or how you run it: no child sexual abuse material (CSAM), real or synthetic, and no non-consensual intimate imagery or non-consensual deepfakes of real, identifiable people. Synthetic or "AI-generated" depictions count exactly the same as real ones; there is no fictional, artistic, or "age-play" exception. The Vivijure project unequivocally condemns CSAM, and on any touchpoint the project itself operates it will preserve and report to the National Center for Missing & Exploited Children (NCMEC, report.cybertip.org) and law enforcement.

Full policy: the canonical Vivijure Acceptable Use Policy at https://github.com/skyphusion-labs/vivijure/blob/main/docs/legal/ACCEPTABLE-USE.md (and, if present in this repo, [`ACCEPTABLE-USE.md`](ACCEPTABLE-USE.md)).

## Scope of reports

Security reports should concern this code and its runtime. Please do not send code, diffs, or excerpts
you do not have the rights to share.
