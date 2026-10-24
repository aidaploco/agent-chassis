#!/usr/bin/env bash
# Build a release image from the EXACT current commit — never from
# whatever happened to be lying around — so the image is traceable to a
# commit by construction.
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -n "$(git status --porcelain)" ]]; then
  echo "refusing to build: working tree is dirty" >&2
  git status --short >&2
  exit 1
fi

VERSION=$(python3 -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")
SHA=$(git rev-parse HEAD)

docker build \
  --label "org.opencontainers.image.revision=${SHA}" \
  --label "org.opencontainers.image.version=${VERSION}" \
  -t "agent-chassis:${VERSION}" \
  .

echo
echo "built  agent-chassis:${VERSION}"
echo "commit ${SHA}"
echo "verify: docker inspect agent-chassis:${VERSION} --format '{{index .Config.Labels \"org.opencontainers.image.revision\"}}'"
