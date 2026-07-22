# AGENTS.md

## Cursor Cloud specific instructions

Standard test config is in `pytest.ini` (`pythonpath=src`, `testpaths=tests`) and
`CLAUDE.md`. Cloud notes:

- The pure-logic suite is CPU-only by design (torch/diffusers/boto3 are
  deferred-imported and pod-validated on the card). Dev deps are just pytest +
  pytest-cov. Use a per-repo venv (`.venv` is git-ignored; `python3.12-venv` is
  installed by the environment update script, which also creates the venv):
  `python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt`.
- Run the suite: `.venv/bin/python -m pytest`.
- Full CogVideoX-5B-I2V render needs a 16GB-class GPU + `docker-compose.yml` and is
  not runnable on this CPU VM.

Verified in this environment: `pytest` -> 105 passed.
