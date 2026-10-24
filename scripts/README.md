# `scripts/`

Operational scripts.

- [`release-image.sh`](release-image.sh) — build the release Docker image from
  the **exact current commit**, refusing to build from a dirty working tree. It
  reads the version from `pyproject.toml`, stamps the git SHA and version as
  OCI image labels, and prints the `docker inspect` command to verify the
  revision. This makes the built artifact traceable to a commit by
  construction.

```sh
scripts/release-image.sh
```
