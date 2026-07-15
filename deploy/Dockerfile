# vivijure-local-16gb RELEASE image (the CogVideoX consumer door): FROM the pinned runtime base + our
# app code. Deliberately tiny to build: everything heavy (CUDA/torch, diffusers/transformers, the render
# deps) lives in the runtime base (deploy/runtime.Dockerfile, built by runtime-build.yml). So a src-only
# release (the common case) re-pushes ONLY the COPY src layer below; the runtime layers are inherited by
# blob identity and dedup on GHCR. That is the publish-time + runner-cost win.
#
# RELEASE CONTRACT:
#   * src-only change        -> tag vX.Y.Z (this build; fast, ~app layer only).
#   * toolchain/deps/CUDA     -> bump runtime-t<N> + rebuild via runtime-build.yml -> repin RUNTIME_REF
#                                below (its Summary prints the exact line) -> tag vX.Y.Z.
#
# Source hackers: `docker compose up --build` builds THIS file, pulling the public runtime base FROM.
#
# RUNTIME BASE, pinned by TAG (and, once runtime-build.yml has run, by @sha256 digest for reproducible
# builds). runtime-build.yml auto-opens a PR that repins this line to tag@digest.
ARG RUNTIME_REF=ghcr.io/skyphusion-labs/vivijure-local-16gb:runtime-t1
FROM ${RUNTIME_REF}

WORKDIR /app

# Our package. This is the ONLY layer that changes on a src-only release, so it is the only re-push.
COPY src /app/src
ENV PYTHONPATH=/app/src \
    HOST=0.0.0.0 \
    PORT=8000

EXPOSE 8000

# Liveness: GET /health must 200. stdlib-only (no curl in the image). Mirrors the compose healthcheck
# so a bare `docker run` is self-checking too. Generous start-period: first boot pulls CogVideoX weights.
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health',timeout=3).status==200 else 1)"

CMD ["python3", "-m", "vivijure_local.core.server"]
