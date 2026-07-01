# Contributing

Thanks for your interest. A few things to know before you open an issue or PR.

## Project posture

This is a labor of love, maintained as time allows. Response times on issues and PRs may
vary. If you find it useful and want to make it better, you are welcome here.

This is the **local** render backend for Vivijure: the image-to-video engine a homelabber runs
on their own GPU, on their own machine. There is no hosted service and no central server; you
run it, you own it, and your data stays on your box. Contributions are very welcome, especially
ones that lower the floor for a first-time homelabber getting a clip out.

## Contributing code

This backend is an independent, built-from-scratch implementation, written against the studio's
job-API contract and the underlying models' own public documentation. It is a clean-sheet
codebase, which is exactly what makes it pleasant to extend. A couple of standard hygiene points
keep it that way:

- By submitting a contribution you affirm it is **your own original work** (or appropriately
  licensed), and that you have the right to contribute it. Please do not paste code or diffs you
  do not have the rights to.
- **Sign your commits off** (`git commit -s`, a [DCO](https://developercertificate.org/)
  affirmation; see below).

## Sign your work (Developer Certificate of Origin)

We use the [Developer Certificate of Origin](https://developercertificate.org/) (DCO) instead of
a CLA: no paperwork, no copyright assignment, just a per-commit affirmation that you wrote the
patch or otherwise have the right to submit it under the project's license.

Sign off every commit:

```bash
git commit -s
```

That appends a line to your commit message:

```
Signed-off-by: Your Name <your.email@example.com>
```

The name and email must be real and must match the commit author. By signing off you certify the
DCO (reproduced in full at the link above). Unsigned commits may be asked to amend with
`git commit --amend -s` (or `git rebase --signoff` for a series) before merge.

## Licensing of contributions (inbound = outbound)

Contributions are accepted under the project's **AGPL-3.0-only** license (see [`LICENSE`](LICENSE)
and [`NOTICE`](NOTICE)). By submitting a contribution you agree it is licensed under that same
license. This keeps the backend a commons: self-host it freely, and if you run a modified version
as a network service, the AGPL asks you to share your changes back.

## Where contributions fit best

Most welcome, lowest friction:

- **Issues and bug reports** with a clear repro (a minimal job input is gold), plus your GPU /
  driver / OS so a hardware-specific issue is reproducible.
- **Documentation** fixes and clarifications, especially anything that lowers the floor for a
  newcomer setting this up on their own machine (see [docs/HOMELABBER.md](docs/HOMELABBER.md)).
- **Tests** that pin existing behavior (CPU-testable; see below).
- Small, self-contained fixes (a crash, an off-by-one, a config edge case) described from
  observed behavior.

Larger feature work is best discussed in an issue first, so we can agree on the shape before you
invest time. [docs/architecture.md](docs/architecture.md) maps how the pieces fit.

## Testing

The pure logic (config, VRAM math, frame math, job lifecycle, server routing) is CPU-testable and
runs in CI with no GPU and no model weights:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

The torch/diffusers generation body is deferred-imported and validated on a real card; it is not
exercised in CI. Keep new logic CPU-testable where you can, and call out clearly in the PR
anything that needs a GPU-validation pass so it can be scheduled rather than assumed. A producer
stage must never fake output: if the GPU runtime is absent it should raise a clear error, not ship
a placeholder clip.

## House rules

- **No em-dashes (U+2014) or en-dashes (U+2013) anywhere** in source, comments, docs, or commit
  messages. Use commas, semicolons, parentheses, or a double hyphen (`--`).
- **Conventional Commits**: `fix(scope): ...`, `feat(scope): ...`, `docs: ...`, `ci: ...`. The
  body explains the *why*.
- Versioning is SemVer-style (PATCH for fixes, MINOR for features while pre-1.0). Release
  mechanics follow this repo's docs.

## Acceptable use (the one hard line)

This backend is a generative engine, so the project's content red lines apply to what you do with
it: above all, **no child sexual abuse material (CSAM), real or synthetic, ever**, and no
non-consensual intimate imagery or non-consensual deepfakes. See [`ACCEPTABLE-USE.md`](ACCEPTABLE-USE.md)
for the bright line and a link to the full canonical policy. Because this runs locally on your
hardware, you are the operator and you carry that responsibility; the project hosts nothing and
cannot see what you generate, with that one CSAM bright line being the exception the project will
always stand behind (condemn and report).

## Pull requests

- Branch from `main`; CI (the CPU test suite) must pass.
- `main` is protected and changes land by review. Open the PR, keep it focused, and tag the
  maintainer.

## Security

Please do **not** open a public issue for a security vulnerability. See [`SECURITY.md`](SECURITY.md)
for private reporting.
