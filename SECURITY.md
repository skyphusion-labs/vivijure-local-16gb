# Security policy

## Supported versions

This is a rolling, single-`main`-branch project: run the latest `main`. Only the latest revision
receives security fixes; if you are on an older checkout, pull `main` to pick them up.

## Reporting a vulnerability

Please do not file a public GitHub issue for security problems. Report it privately through GitHub's
private vulnerability reporting: open the repository's **Security** tab and click **"Report a
vulnerability"**, which creates a private advisory visible only to you and the maintainers. If you
prefer email, **security@skyphusion.org** is a fallback. (If neither is available, open a minimal
public issue asking for a private channel, without disclosing details.)

Please include:

- A description of the issue
- Steps to reproduce, with a minimal example if possible
- The affected revision (commit SHA if known)
- Any suggestions for remediation

Reports will be acknowledged within a reasonable window (target: 5 business days). Time-sensitive
issues should say so. Please allow up to 90 days for a coordinated fix before public disclosure.

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

## Acceptable use

Vivijure is self-hosted software: you run this backend on your own hardware, in your own jurisdiction, and you are the operator responsible for using it lawfully and for whatever is generated on your machine. Skyphusion Labs ships the software and operates no instance of it, so it does not and architecturally cannot monitor what you generate. Misuse is therefore an acceptable-use matter, not a security vulnerability -- do not report generated content through the security channel.

One line is absolute no matter where or how you run it: no child sexual abuse material (CSAM), real or synthetic, and no non-consensual intimate imagery or non-consensual deepfakes of real, identifiable people. Synthetic or "AI-generated" depictions count exactly the same as real ones; there is no fictional, artistic, or "age-play" exception. The Vivijure project unequivocally condemns CSAM, and on any touchpoint the project itself operates it will preserve and report to the National Center for Missing & Exploited Children (NCMEC, report.cybertip.org) and law enforcement.

Full policy: the canonical Vivijure Acceptable Use Policy at https://github.com/skyphusion-labs/vivijure/blob/main/docs/legal/ACCEPTABLE-USE.md (and, if present in this repo, [`ACCEPTABLE-USE.md`](ACCEPTABLE-USE.md)).

## Scope of reports

Security reports should concern this code and its runtime. Please do not send code, diffs, or excerpts
you do not have the rights to share.
