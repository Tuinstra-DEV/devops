# Tessera two-job Ruby-only CI route

The Tessera caller keeps its existing `backend-security`, `flutter-verification`
and `quality` check names. A pinned DevOps composite action runs inside the
existing hosted `backend-security` job. Its policy code comes from
`github.action_path`; it does not check out the private DevOps repository or
use a caller-provided selector.

Only an open PR to `develop` whose exact two-parent merge diff contains only
these paths selects the Ruby fixture:

- `app/Gemfile`
- `app/Gemfile.lock`
- `app/fastlane/Fastfile`

The action validates the current PR and Actions run through the read-only
`GITHUB_TOKEN`, proves the merge parents, then classifies the base-to-head Git
diff. Ruby 4 runs the existing fixture with the locked DRb, Prism and Minitest
versions. CI, application, workflow, mixed and unknown changes use the old
trusted reusable directly. PRs to `main` always use the full Flutter route.

For the Ruby-only route, `backend-security` runs all existing Node/Deno checks
and the Ruby fixture; `flutter-verification` is skipped; `quality` verifies the
backend result and the hosted-only Ruby proof. For the full route, the security
job runs unchanged and `quality` verifies the old trusted/hosted proof. No
Sanctuary runner group or manager admission changes.
