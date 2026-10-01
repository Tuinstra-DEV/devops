#!/usr/bin/env ruby
# frozen_string_literal: true

require "yaml"
require "fileutils"
require "json"
require "open3"
require "tmpdir"

WORKFLOW = ".github/workflows/reusable-heavy-ci-v2.yml"
CONTRACT_DOC = "docs/workflows/contracts/reusable-heavy-ci-v2.md"
FIXTURES = %w[
  tests/fixtures/heavy-ci-v2/wodiq.yml
  tests/fixtures/heavy-ci-v2/tracker.yml
].freeze

failures = []
check = lambda do |condition, message|
  failures << message unless condition
end

text = File.read(WORKFLOW)
required_inputs = %w[contract-id execution-class cache-policy cache-path cache-schema toolchain lockfile entrypoint artifact-path run-unit run-integration run-browser browser-coverage run-live-smoke artifact-retention-days]
required_outputs = %w[contract-version artifact-name artifact-id artifact-digest payload-sha256 effective-execution-class cache-key cache-hit metrics-artifact preflight-decision preflight-reason-category preflight-evidence planned-expensive-jobs avoided-expensive-jobs]
required_stages = %w[bootstrap build unit integration e2e-prepare browser live-smoke]

required_inputs.each { |name| check.call(text.include?("      #{name}:"), "missing input #{name}") }
required_outputs.each { |name| check.call(text.include?("      #{name}:"), "missing output #{name}") }
required_stages.each { |name| check.call(text.include?(name), "missing typed stage #{name}") }

check.call(text.include?("default: hosted"), "hosted must remain the execution default")
check.call(text.include?("hosted|trusted-heavy"), "execution-class enum is not enforced")
check.call(text.include?("off|restore-only|trusted-write"), "cache-policy enum is not enforced")
check.call(text.include?("decision=blocked") && text.include?("decision=skip") && text.include?("decision=proceed"), "preflight decision enum must include blocked, skip, and proceed")
check.call(text.include?("reason-category"), "preflight must emit a stable reason category")
check.call(text.include?("planned-expensive-jobs") && text.include?("avoided-expensive-jobs"), "preflight must quantify planned and avoided expensive jobs")
check.call(text.include?("job.workflow_ref") && text.include?("job.workflow_sha") && text.include?("github.workflow_ref") && text.include?("github.workflow_sha"), "workflow and caller identity context is incomplete")
check.call(text.include?("github.event.pull_request.head.repo.fork || false"), "fork boundary is missing")
check.call(text.include?("*'[bot]'"), "dependency-bot boundary is missing")
check.call(text.include?("github.event.pull_request.user.type || ''"), "immutable bot-author boundary is missing")
check.call(text.include?("pull_request_target"), "pull_request_target boundary is missing")
check.call(text.include?("BASE_REF") && text.include?("HEAD_REF") && text.include?("HEAD_REPOSITORY"), "develop-to-main release browser context is incomplete")
check.call(text.include?("release-browser-sanctuary") && text.include?("trusted-release-required"), "release browser routing/verdict categories are missing")
check.call(text.include?("matrix.stage == 'browser'") && text.include?("runs-on: ubuntu-24.04"), "routine Heavy CI stages are not hosted independently of browser stages")
check.call(text.include?("BROWSER_COVERAGE") && text.include?("browser-coverage must be required or not-applicable"), "release browser expectation is not independent of run-browser")
check.call(text.include?("EVENT_NAME\" = push") && text.include?("REF_NAME\" = \"$DEFAULT_BRANCH"), "canonical cache write is not restricted to default-branch pushes")
check.call(text.include?("actions/cache/restore@") && text.include?("actions/cache/save@"), "split restore/save cache actions are required")
check.call(!text.include?("restore-keys:"), "broad cache restore prefixes are prohibited")
check.call(text.include?("node_modules|vendor|dist|build|\\.output"), "unsafe dependency/build cache paths are not rejected")
check.call(text.include?("GITHUB_REPOSITORY_ID") && text.include?("CONTRACT_ID") && text.include?("RUNNER_OS") && text.include?("RUNNER_ARCH") && text.include?("ImageOS") && text.include?("LOCKFILE_HASH") && text.include?("CONTENT_HASH"), "cache key dimensions are incomplete")

check.call(text.include?("artifact-ids: ${{ needs.build.outputs.artifact-id }}"), "fan-out does not consume the build artifact by ID")
check.call(text.include?("merge-multiple: true"), "artifact ID download does not restore bundle files directly into the verified directory")
check.call(text.include?("payload checksum mismatch"), "payload checksum verification is missing")
check.call(text.include?("run_attempt") && text.include?("repository_id") && text.include?("source_sha"), "artifact manifest binding is incomplete")
check.call(text.include?("artifact contains an unsafe path"), "archive path traversal check is missing")
check.call(text.include?("artifact-path may not contain symbolic links"), "artifact symlinks are not rejected")
check.call(text.include?("artifact contains a path outside artifact-path"), "artifact root confinement is missing")
check.call(text.include?("compression-level: 0"), "already-compressed bundle should not be recompressed")
check.call(text.include?("duration_seconds") && text.include?("status"), "stage timing/failure evidence is missing")
check.call(text.scan("set +e").length == 2, "build and fan-out stages must capture adapter failures before exiting")
check.call(text.scan("include-hidden-files: true").length == 2, "hidden build and fan-out evidence must be uploaded explicitly")
check.call(text.include?("mkdir -p evidence metrics"), "summary must tolerate a missing evidence download")
%w[bootstrap build artifact-capture e2e-prepare].each { |stage| check.call(text.include?("stage\":\"#{stage}"), "timing evidence missing for #{stage}") }
check.call(!text.match?(/^\s*(packages|deployments|id-token|attestations|security-events):\s*write\s*$/), "heavy CI grants a privileged write permission")
check.call(!text.match?(/^\s*secrets:\s*inherit\s*$/), "secrets: inherit is prohibited")
check.call(text.scan("persist-credentials: false").length >= 3, "checkout credentials must not persist into preflight, build, or fan-out scripts")

text.scan(/^\s*uses:\s*([^\s#]+)(?:\s+#.*)?$/).flatten.each do |reference|
  next if reference.start_with?("./")

  check.call(reference.match?(/@[0-9a-f]{40}$/), "external action is not pinned by full SHA: #{reference}")
end

def safe_path?(path)
  !path.empty? && !path.start_with?("/") && path.split("/").none?("..")
end

def cache_path?(path)
  forbidden = %w[node_modules vendor dist build .output]
  safe_path?(path) && (path.split("/") & forbidden).empty?
end

def untrusted?(event:, fork:, actor:)
  event == "pull_request_target" || fork || actor.end_with?("[bot]")
end

def can_write_cache?(policy:, event:, fork:, actor:, ref:, default_branch:)
  policy == "trusted-write" && !untrusted?(event: event, fork: fork, actor: actor) && event == "push" && ref == default_branch
end

%w[.cache/heavy-ci .npm frontend/.pnpm-store].each { |path| check.call(cache_path?(path), "valid cache path rejected: #{path}") }
%w[/tmp/cache ../cache app/../cache node_modules backend/vendor dist .output build].each { |path| check.call(!cache_path?(path), "unsafe cache path accepted: #{path}") }

negative_contexts = [
  { event: "pull_request", fork: true, actor: "contributor" },
  { event: "pull_request", fork: false, actor: "dependabot[bot]" },
  { event: "pull_request", fork: false, actor: "renovate[bot]" },
  { event: "pull_request_target", fork: false, actor: "maintainer" },
  { event: "pull_request", fork: false, actor: "maintainer" }
]
negative_contexts.each do |context|
  check.call(!can_write_cache?(policy: "trusted-write", ref: "main", default_branch: "main", **context), "non-default-push context can write the canonical cache: #{context}")
end
check.call(can_write_cache?(policy: "trusted-write", event: "push", fork: false, actor: "maintainer", ref: "main", default_branch: "main"), "trusted default-branch push cannot write cache")

workflow = YAML.safe_load(text, aliases: true)
preflight_step = workflow.fetch("jobs").fetch("preflight").fetch("steps").find { |step| step["id"] == "contract" }
check.call(!preflight_step.nil?, "preflight contract step is missing")

if preflight_step
  preflight_script = preflight_step.fetch("run")
  run_preflight = lambda do |overrides = {}, setup = nil|
    Dir.mktmpdir("heavy-ci-v2-preflight") do |workspace|
      FileUtils.mkdir_p(File.join(workspace, ".github", "ci"))
      FileUtils.mkdir_p(File.join(workspace, ".git"))
      File.write(File.join(workspace, ".github", "ci", "heavy-ci"), "#!/usr/bin/env bash\n")
      File.write(File.join(workspace, "package-lock.json"), "{}\n")
      setup&.call(workspace)

      output = File.join(workspace, "github-output")
      summary = File.join(workspace, "step-summary")
      env = {
        "CONTRACT_ID" => "contract-test",
        "EXECUTION_CLASS" => "hosted",
        "CACHE_POLICY" => "restore-only",
        "CACHE_PATH" => ".cache/heavy-ci",
        "CACHE_SCHEMA" => "v1",
        "TOOLCHAIN" => "node24-npm11",
        "LOCKFILE" => "package-lock.json",
        "ENTRYPOINT" => ".github/ci/heavy-ci",
        "ARTIFACT_PATH" => ".heavy-ci/payload",
        "RETENTION_DAYS" => "14",
        "EVENT_NAME" => "push",
        "IS_FORK" => "false",
        "BASE_REF" => "",
        "HEAD_REF" => "",
        "HEAD_REPOSITORY" => "consumer/app",
        "ACTOR" => "maintainer",
        "TRIGGERING_ACTOR" => "maintainer",
        "DEFAULT_BRANCH" => "main",
        "REF_NAME" => "main",
        "RUN_UNIT" => "true",
        "RUN_INTEGRATION" => "false",
        "RUN_BROWSER" => "false",
        "BROWSER_COVERAGE" => "not-applicable",
        "RUN_LIVE_SMOKE" => "false",
        "CALLER_WORKFLOW_REF" => "consumer/app/.github/workflows/ci.yml@refs/heads/main",
        "CALLER_WORKFLOW_SHA" => "1" * 40,
        "REUSABLE_WORKFLOW_REF" => "Tuinstra-DEV/devops/.github/workflows/reusable-heavy-ci-v2.yml@#{"2" * 40}",
        "REUSABLE_WORKFLOW_SHA" => "2" * 40,
        "REUSABLE_WORKFLOW_REPOSITORY" => "Tuinstra-DEV/devops",
        "GITHUB_REPOSITORY" => "consumer/app",
        "GITHUB_REPOSITORY_ID" => "12345",
        "GITHUB_SHA" => "3" * 40,
        "GITHUB_RUN_ID" => "45678",
        "GITHUB_RUN_ATTEMPT" => "1",
        "GITHUB_WORKSPACE" => workspace,
        "GITHUB_OUTPUT" => output,
        "GITHUB_STEP_SUMMARY" => summary,
        "RUNNER_OS" => "Linux",
        "RUNNER_ARCH" => "X64"
      }.merge(overrides)
      stdout, stderr, status = Open3.capture3(env, "bash", "-c", preflight_script, chdir: workspace)
      values = File.exist?(output) ? File.readlines(output, chomp: true).select { |line| line.include?("=") }.map { |line| line.split("=", 2) }.to_h : {}
      evidence = values["preflight-evidence"] ? JSON.parse(values["preflight-evidence"]) : {}
      { status: status, stdout: stdout, stderr: stderr, outputs: values, evidence: evidence, summary: File.exist?(summary) ? File.read(summary) : "" }
    end
  end

  hosted = run_preflight.call
  check.call(hosted[:status].success?, "valid hosted preflight failed: #{hosted[:stderr]}")
  check.call(hosted[:outputs]["decision"] == "proceed", "valid hosted preflight did not proceed")
  check.call(hosted[:outputs]["reason-category"] == "hosted-requested", "valid hosted preflight reason is unstable")
  check.call(hosted[:outputs]["planned-expensive-jobs"] == "2", "hosted preflight did not count build plus unit jobs")
  check.call(hosted[:evidence]["schema_version"] == "heavy-ci/preflight-v1", "preflight evidence schema is missing")

  invalid_execution = run_preflight.call("EXECUTION_CLASS" => "bad\nvalue")
  check.call(invalid_execution[:evidence]["requested_execution_class"] == "invalid", "invalid execution class leaked raw input into preflight evidence")

  trusted = run_preflight.call("EXECUTION_CLASS" => "trusted-heavy")
  check.call(trusted[:status].success? && trusted[:outputs]["effective-execution-class"] == "hosted", "routine Heavy CI escaped the hosted runner policy")
  check.call(trusted[:outputs]["runner"] == '"ubuntu-24.04"', "routine Heavy CI preflight selected a self-hosted runner")

  release_browser = run_preflight.call(
    "EVENT_NAME" => "pull_request",
    "BASE_REF" => "main",
    "HEAD_REF" => "develop",
    "HEAD_REPOSITORY" => "consumer/app",
    "IS_FORK" => "false",
    "PR_AUTHOR_TYPE" => "User",
    "ACTOR" => "maintainer",
    "TRIGGERING_ACTOR" => "maintainer",
    "EXECUTION_CLASS" => "hosted",
    "RUN_BROWSER" => "false",
    "BROWSER_COVERAGE" => "required"
  )
  check.call(release_browser[:status].success?, "trusted develop-to-main release preflight failed: #{release_browser[:stderr]}")
  check.call(release_browser[:outputs]["reason-category"] == "release-browser-sanctuary", "release browser routing reason changed")
  check.call(release_browser[:outputs]["runner"] == '"ubuntu-24.04"', "release browser moved routine build work to Sanctuary")
  check.call(release_browser[:outputs]["matrix"].include?('"stage":"browser"'), "eligible release PR omitted its requested browser stage")

  explicitly_not_applicable = run_preflight.call(
    "EVENT_NAME" => "pull_request",
    "BASE_REF" => "main",
    "HEAD_REF" => "develop",
    "HEAD_REPOSITORY" => "consumer/app",
    "IS_FORK" => "false",
    "PR_AUTHOR_TYPE" => "User",
    "ACTOR" => "maintainer",
    "TRIGGERING_ACTOR" => "maintainer",
    "RUN_BROWSER" => "true",
    "BROWSER_COVERAGE" => "not-applicable"
  )
  check.call(explicitly_not_applicable[:status].success? && !explicitly_not_applicable[:outputs]["matrix"].include?('"stage":"browser"'), "explicitly not-applicable fixture unexpectedly ran browser stage")

  ordinary_pr_browser = run_preflight.call(
    "EVENT_NAME" => "pull_request",
    "BASE_REF" => "develop",
    "HEAD_REF" => "feature/change",
    "HEAD_REPOSITORY" => "consumer/app",
    "IS_FORK" => "false",
    "PR_AUTHOR_TYPE" => "User",
    "ACTOR" => "maintainer",
    "TRIGGERING_ACTOR" => "maintainer",
    "RUN_BROWSER" => "true"
  )
  check.call(ordinary_pr_browser[:status].success?, "ordinary PR preflight failed while skipping browser")
  check.call(!ordinary_pr_browser[:outputs]["matrix"].include?('"stage":"browser"'), "ordinary PR ran the browser stage")

  dispatch_browser = run_preflight.call("EVENT_NAME" => "workflow_dispatch", "RUN_BROWSER" => "true")
  check.call(dispatch_browser[:status].success? && !dispatch_browser[:outputs]["matrix"].include?('"stage":"browser"'), "manual dispatch ran the browser stage")

  bot_release_browser = run_preflight.call(
    "EVENT_NAME" => "pull_request",
    "BASE_REF" => "main",
    "HEAD_REF" => "develop",
    "HEAD_REPOSITORY" => "consumer/app",
    "IS_FORK" => "false",
    "PR_AUTHOR_TYPE" => "Bot",
    "ACTOR" => "release-bot[bot]",
    "TRIGGERING_ACTOR" => "release-bot[bot]",
    "RUN_BROWSER" => "true"
  )
  check.call(!bot_release_browser[:status].success?, "bot-created release PR did not fail closed")
  check.call(bot_release_browser[:outputs]["decision"] == "blocked" && bot_release_browser[:outputs]["reason-category"] == "trusted-release-required", "untrusted release did not emit the stable blocked verdict")

  same_repository = run_preflight.call(
    "GITHUB_REPOSITORY" => "Tuinstra-DEV/devops",
    "CALLER_WORKFLOW_REF" => "Tuinstra-DEV/devops/.github/workflows/heavy-ci-v2-integration.yml@refs/heads/main",
    "REUSABLE_WORKFLOW_REF" => "Tuinstra-DEV/devops/.github/workflows/reusable-heavy-ci-v2.yml@refs/heads/main",
    "REUSABLE_WORKFLOW_SHA" => "1" * 40
  )
  check.call(same_repository[:status].success?, "same-revision local reusable workflow call was rejected")

  [
    { "EVENT_NAME" => "pull_request", "IS_FORK" => "true", "ACTOR" => "contributor", "EXECUTION_CLASS" => "trusted-heavy" },
    { "EVENT_NAME" => "pull_request", "ACTOR" => "dependabot[bot]", "TRIGGERING_ACTOR" => "dependabot[bot]", "EXECUTION_CLASS" => "trusted-heavy" },
    { "EVENT_NAME" => "pull_request_target", "EXECUTION_CLASS" => "trusted-heavy" }
  ].each do |context|
    result = run_preflight.call(context)
    check.call(result[:status].success?, "untrusted context did not select safe hosted fallback: #{context}")
    check.call(result[:outputs]["decision"] == "proceed" && result[:outputs]["effective-execution-class"] == "hosted", "untrusted context escaped hosted routing: #{context}")
    check.call(result[:outputs]["reason-category"] == "untrusted-hosted-fallback", "untrusted fallback reason changed: #{context}")
    check.call(result[:outputs]["can-write-cache"] == "false", "untrusted context can write cache: #{context}")
  end

  blocked_cases = [
    ["invalid execution class", { "EXECUTION_CLASS" => "arbitrary-runner" }, nil, "invalid-contract"],
    ["invalid browser coverage declaration", { "BROWSER_COVERAGE" => "optional" }, nil, "invalid-contract"],
    ["unsupported event", { "EVENT_NAME" => "deployment" }, nil, "unsupported-event"],
    ["missing actor", { "ACTOR" => "" }, nil, "invalid-context"],
    ["short caller SHA", { "CALLER_WORKFLOW_SHA" => "1234567" }, nil, "invalid-context"],
    ["mutable remote workflow", { "REUSABLE_WORKFLOW_REF" => "Tuinstra-DEV/devops/.github/workflows/reusable-heavy-ci-v2.yml@main" }, nil, "immutable-reference-required"],
    ["mismatched remote workflow SHA", { "REUSABLE_WORKFLOW_SHA" => "4" * 40 }, nil, "invalid-context"],
    ["missing entrypoint", { "ENTRYPOINT" => ".github/ci/missing" }, nil, "missing-entrypoint"],
    ["missing lockfile", { "LOCKFILE" => "missing.lock" }, nil, "missing-lockfile"],
    ["cache/artifact overlap", { "CACHE_PATH" => ".heavy-ci", "ARTIFACT_PATH" => ".heavy-ci/payload" }, nil, "input-path-conflict"],
    ["symlinked entrypoint", {}, lambda { |workspace| FileUtils.rm(File.join(workspace, ".github", "ci", "heavy-ci")); File.symlink("../../package-lock.json", File.join(workspace, ".github", "ci", "heavy-ci")) }, "unsafe-input"]
  ]
  blocked_cases.each do |label, env_overrides, setup, reason|
    result = run_preflight.call(env_overrides, setup)
    check.call(!result[:status].success?, "#{label} did not fail closed")
    check.call(result[:outputs]["decision"] == "blocked", "#{label} did not emit a blocked decision")
    check.call(result[:outputs]["reason-category"] == reason, "#{label} emitted #{result[:outputs]["reason-category"].inspect}, expected #{reason}")
    check.call(result[:outputs]["planned-expensive-jobs"] == "0", "#{label} planned an expensive job")
    check.call(result[:outputs]["avoided-expensive-jobs"].to_i >= 1, "#{label} did not quantify avoided expensive jobs")
  end
end

browser_workflow = YAML.safe_load(File.read(".github/workflows/reusable-browser-quality.yml"), aliases: true)
policy_step = browser_workflow.fetch("jobs").fetch("release-policy").fetch("steps").find { |step| step["id"] == "policy" }
check.call(!policy_step.nil?, "browser release policy step is missing")
check.call(browser_workflow.fetch("jobs").fetch("browser-quality").fetch("if").include?("github.event.pull_request.head.ref == 'develop'"), "Sanctuary browser job is missing its direct release-source guard")
check.call(browser_workflow.fetch("jobs").fetch("browser-quality").fetch("if").include?("github.event.pull_request.head.repo.full_name == github.repository"), "Sanctuary browser job is missing its direct same-repository guard")
check.call(browser_workflow.fetch("jobs").fetch("release-verdict").fetch("if") == "${{ always() }}", "independent hosted release verdict must always run")
if policy_step
  browser_policy = policy_step.fetch("run")
  run_browser_policy = lambda do |overrides = {}|
    Dir.mktmpdir("browser-release-policy") do |workspace|
      output = File.join(workspace, "github-output")
      env = {
        "EVENT_NAME" => "pull_request",
        "BASE_REF" => "main",
        "HEAD_REF" => "develop",
        "HEAD_REPOSITORY" => "consumer/app",
        "REPOSITORY" => "consumer/app",
        "IS_FORK" => "false",
        "ACTOR" => "maintainer",
        "TRIGGERING_ACTOR" => "maintainer",
        "PR_AUTHOR_TYPE" => "User",
        "GITHUB_OUTPUT" => output
      }.merge(overrides)
      stdout, stderr, status = Open3.capture3(env, "bash", "-c", browser_policy, chdir: workspace)
      values = File.exist?(output) ? File.readlines(output, chomp: true).to_h { |line| line.split("=", 2) } : {}
      { status: status, stdout: stdout, stderr: stderr, outputs: values }
    end
  end

  valid_release = run_browser_policy.call
  check.call(valid_release[:status].success? && valid_release[:outputs]["run-browser"] == "true", "trusted release PR did not enable browser suite")
  [
    { "EVENT_NAME" => "pull_request", "BASE_REF" => "develop", "HEAD_REF" => "feature/x" },
    { "EVENT_NAME" => "push" }
  ].each do |context|
    skipped = run_browser_policy.call(context)
    check.call(skipped[:status].success? && skipped[:outputs]["run-browser"] == "false", "non-release event did not skip browser suite: #{context}")
  end
  [
    { "IS_FORK" => "true", "HEAD_REPOSITORY" => "contributor/consumer" },
    { "ACTOR" => "dependabot[bot]", "TRIGGERING_ACTOR" => "dependabot[bot]" },
    { "PR_AUTHOR_TYPE" => "Bot" }
  ].each do |context|
    rejected = run_browser_policy.call(context)
    check.call(!rejected[:status].success? && rejected[:outputs]["run-browser"] == "false", "untrusted release candidate did not fail closed: #{context}")
  end
end

verdict_step = browser_workflow.fetch("jobs").fetch("release-verdict").fetch("steps").find { |step| step["name"] == "Require the expected trusted release browser result" }
check.call(!verdict_step.nil?, "independent release browser verdict step is missing")
if verdict_step
  verdict_script = verdict_step.fetch("run")
  run_release_verdict = lambda do |overrides = {}|
    env = {
      "EVENT_NAME" => "pull_request",
      "BASE_REF" => "main",
      "HEAD_REF" => "develop",
      "HEAD_REPOSITORY" => "consumer/app",
      "REPOSITORY" => "consumer/app",
      "IS_FORK" => "false",
      "ACTOR" => "maintainer",
      "TRIGGERING_ACTOR" => "maintainer",
      "PR_AUTHOR_TYPE" => "User",
      "POLICY_RESULT" => "success",
      "POLICY_RUN_BROWSER" => "true",
      "BROWSER_RESULT" => "success"
    }.merge(overrides)
    stdout, stderr, status = Open3.capture3(env, "bash", "-c", verdict_script)
    { stdout: stdout, stderr: stderr, status: status }
  end

  check.call(run_release_verdict.call[:status].success?, "complete trusted release browser verdict failed")
  [
    { "POLICY_RESULT" => "failure" },
    { "POLICY_RUN_BROWSER" => "false" },
    { "BROWSER_RESULT" => "skipped" },
    { "BROWSER_RESULT" => "failure" },
    { "IS_FORK" => "true", "HEAD_REPOSITORY" => "contributor/consumer" },
    { "ACTOR" => "release-bot[bot]", "TRIGGERING_ACTOR" => "release-bot[bot]" }
  ].each do |context|
    result = run_release_verdict.call(context)
    check.call(!result[:status].success?, "release browser verdict incorrectly passed missing/failed/untrusted result: #{context}")
  end
  nonrelease = run_release_verdict.call("HEAD_REF" => "feature/x")
  check.call(nonrelease[:status].success?, "non-release browser verdict should not require browser execution")
end

contract_doc = File.read(CONTRACT_DOC)
%w[blocked skip proceed hosted-requested trusted-heavy-approved untrusted-hosted untrusted-hosted-fallback release-browser-sanctuary trusted-release-required invalid-contract invalid-context unsupported-event immutable-reference-required missing-entrypoint missing-lockfile unsafe-input input-path-conflict].each do |term|
  check.call(contract_doc.include?("`#{term}`"), "#{CONTRACT_DOC} does not document preflight term #{term}")
end
check.call(contract_doc.include?("planned-expensive-jobs") && contract_doc.include?("avoided-expensive-jobs"), "#{CONTRACT_DOC} does not document quantified preflight evidence")

fixture_shapes = FIXTURES.map do |path|
  fixture = YAML.safe_load(File.read(path), aliases: false)
  %w[contract-id toolchain lockfile entrypoint artifact-path stages].each do |key|
    check.call(fixture.key?(key), "#{path} is missing #{key}")
  end
  check.call(fixture.fetch("stages").all? { |stage| required_stages.include?(stage) }, "#{path} declares an unknown stage")
  fixture
end
check.call(fixture_shapes.map { |fixture| fixture.fetch("entrypoint") }.uniq.length == 2, "consumer fixtures must keep repository-specific adapters local")
check.call(fixture_shapes.map { |fixture| fixture.fetch("contract-id") }.uniq.length == 2, "consumer fixtures need unique contract IDs")

if failures.any?
  failures.each { |failure| warn "heavy CI v2 contract test failed: #{failure}" }
  exit 1
end

puts "heavy CI v2 contract test passed (#{required_inputs.length} inputs, #{required_outputs.length} outputs, #{required_stages.length} stages, #{FIXTURES.length} consumer shapes)"
