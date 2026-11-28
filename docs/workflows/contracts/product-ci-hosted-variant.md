# DEV-54 backend runtime probe

## Admission decision

On 16 November 2026, `gh api orgs/Tuinstra-DEV/actions/runner-groups`
confirmed group 4 (`sanctuary-trusted-verification`) restricts jobs to six exact
workflow refs: the Gate security reusable at `ea86c62`, and trusted-verification
at `23c08cf`, `604ad2b`, `a8ace2f`, `e1a9f09`, `ea86c62`. Gate is an allowed
repository. `reusable-product-ci.yml` is absent, including its current full pin.
Therefore choosing Sanctuary inside this new job definition requires a policy
change; repository access alone does not admit it. No group, manager, host,
runner label or admission configuration is changed. The local manager source
also restricts trusted workflow identities to the trusted-verification path;
this source inspection is not a claim about the deployed manager version.

## Measured original run

[Run 43830499195](https://github.com/Tuinstra-DEV/gate/actions/runs/43830499195),
Backend CI job 133174698921, lasted 286 seconds. Timings below are derived from
GitHub step boundaries or timestamped log markers; these intervals do not all
cover the complete job or have the same precision.

| Observed part | Seconds | Measurement boundary |
| --- | ---: | --- |
| Runner Set up job | 6 | API step 08:21:47–08:21:53 UTC |
| Checkout | 1.898 | Composite duration marker |
| PHP/Composer tool setup | 3.553 | Composite duration marker |
| Composer cache lookup | 0.514 | Composite marker; cache miss |
| Composer installation output | 11.332 | First install line 08:22:00.294 to last Composer output 08:22:11.626; output span, not exact command duration |
| GrumPHP quality tasks | 180.498 | First task marker 08:22:12.235 to final result marker 08:25:12.733 |
| PHPUnit | 58.857 | PHPUnit's own reported duration |
| Native MariaDB fixture | 15.655 | Composite duration marker; includes image/container startup and tests |
| Native MariaDB tests only | 0.175 | PHPUnit's own reported duration |

The quality task output is buffered, so individual GrumPHP tasks cannot be
timed independently in the original hosted log. A separate local PHP 8.3.35,
two-CPU container with the same locked dependencies measured PHPStan at 39.551
seconds, PHP CS Fixer at 6.501 seconds and the remaining tasks individually
below two seconds. Those local measurements identify a candidate for study;
they do not establish individual hosted task durations.

## One hosted fallback variant

The unchanged four-job graph remains hosted because admission blocks the
Sanctuary route. Only Backend CI opts into CLI opcache. Timestamp validation
stays enabled with `revalidate_freq=0`, preserving checks of files generated or
modified during tests. There is no JIT, persisted opcode/result cache, vendor
reuse, task exclusion or changed test selection. Frontend receives an empty
runtime override. The MariaDB fixture stays a Backend CI step.

`SHELL_VERBOSITY=3` adds embedded debug timestamps to Backend CI's existing
GrumPHP invocation, allowing individual command intervals to be investigated
even when GitHub receives buffered output. The Gate adapter, PHP quality task
configuration, source files, lockfiles and required workflows remain unchanged.

Local cold-cache quality checks with this setting passed in 40.981 seconds;
the local task-by-task baseline took about 49 seconds including repeated
process startup. These are different local execution shapes and cannot predict
the required hosted saving. One remote sample will be recorded on PR #444;
no additional tuning or repeat is implied if it misses the target.

Accept the variant only if its first comparable run has zero skipped jobs,
unchanged checks/test coverage, fewer than seven diagnostic rounded hosted
job-minutes, and wall time at most 7:23 (the existing CI reference). Record
cache/source differences and runner queue uncertainty. Neither a lower cost
estimate nor one successful run authorizes a merge; PR #220 and #444 stay open.
