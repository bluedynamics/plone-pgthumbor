# Release process

Checklist for cutting a `plone.pgthumbor` release.
All packaging is automated by
[.github/workflows/release.yaml](https://github.com/bluedynamics/plone-pgthumbor/blob/main/.github/workflows/release.yaml):

- Every push to `main` (after green CI) is published to **test.pypi.org**.
- A published **GitHub Release** is uploaded to **pypi.org**.

The package version itself is derived from the git tag via `hatch-vcs`
(see `[tool.hatch.version]` in `pyproject.toml`), so no version field
needs to be edited in `pyproject.toml`.

## Steps

1. **Make sure `main` is green** and everything you want in the release is
   merged.

2. **Finalize `CHANGES.md`.**
   Replace the current `## X.Y.Z (unreleased)` header with the release
   version and today's date, e.g. `## 0.6.2 (2026-04-10)`.

3. **Bump `docs/sources/conf.py`.**
   Update the `release = "..."` line to the new version so the rendered
   Sphinx site shows the right version in the header.

4. **Commit and push.**

   ```bash
   git add CHANGES.md docs/sources/conf.py
   git commit -m "Release X.Y.Z"
   git push
   ```

   Wait for CI to go green on `main`.

5. **Tag the release.**

   ```bash
   git tag -a vX.Y.Z -m "Release X.Y.Z"
   git push origin vX.Y.Z
   ```

6. **Create a GitHub Release from the tag.**

   ```bash
   gh release create vX.Y.Z --title "X.Y.Z" --notes-from-tag
   ```

   or use the GitHub web UI: *Releases → Draft a new release → choose
   tag `vX.Y.Z`*.
   Publishing the release triggers the `release-pypi` job which uploads
   the package to pypi.org via trusted publishing.

7. **Verify.**
   Check [pypi.org/project/plone.pgthumbor](https://pypi.org/project/plone.pgthumbor/)
   and the GitHub Actions run for the release event.

8. **Open the next development cycle.**
   Add a new `## X.Y.(Z+1) (unreleased)` section at the top of
   `CHANGES.md`, commit, push.

## Notes

- **hotfixes**: same process, just branch off the tag and merge-back via PR.
- **test.pypi**: every `main` push already ships a dev build there, so you
  can sanity-check the build pipeline without cutting a real release.
- **Trusted publishing**: both `release-test-pypi` and `release-pypi`
  environments use OIDC via `pypa/gh-action-pypi-publish` — no API tokens
  to rotate.
