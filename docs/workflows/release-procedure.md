# Reusable Workflow v10 Release Procedure

## Preconditions

- DEV-2 acceptance criteria and repository Definition of Done are met.
- `make lint` and `make test` pass on the exact reviewed commit.
- CODEOWNERS approval is recorded.
- The commit is merged to `main` without rewriting history.
- Canary consumers have validated the exact commit SHA on hosted runners.

No command in this procedure moves an existing tag or uses a force push.

## Create the stable v10 release

Set the reviewed merge commit explicitly and verify it before tagging:

```bash
release_sha=<full-reviewed-main-commit-sha>
git show --no-patch --format=fuller "$release_sha"
git tag -a v10.0.0 "$release_sha" -m "Reusable workflow contracts v10.0.0"
git tag -a v10 "$release_sha" -m "Stable reusable workflow contract v10"
git push origin v10.0.0 v10
```

Both tags are immutable audit markers. If either tag already exists, stop and investigate; do not amend, delete, recreate, or force-push it.

Verify the remote objects and record the resolved commit SHA in release notes:

```bash
git ls-remote --tags origin refs/tags/v10 refs/tags/v10^{} refs/tags/v10.0.0 refs/tags/v10.0.0^{}
```

Update production callers to the full resolved commit SHA, never to the tag name:

```yaml
uses: Tuinstra-DEV/devops/.github/workflows/reusable-release-image.yml@<full-v10-commit-sha>
```

## Patch and minor releases

Create a new immutable semantic tag such as `v10.0.1` or `v10.1.0`. Do not move `v10`. Validate and update consumer SHA pins through normal reviewed pull requests.

## Rollback

1. Identify the consumer's previously verified workflow commit SHA and image digest.
2. Change the caller back to that full workflow SHA in a normal commit.
3. Deploy the previous immutable `name@sha256:digest` image when deployment rollback is also required.
4. Run the consumer's required checks and record the evidence.
5. Correct the shared workflow through a new pull request and publish a new immutable release tag.

Never rewrite Git history or move a release tag during rollback. The existing legacy `make release-tag` target is not part of the v10 process because moving tags breaks auditability and the repository's no-force-push policy.
