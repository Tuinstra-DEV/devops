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
required_inputs = %w[contract-id execution-class cache-policy cache-path cache-schema toolchain lockfile entrypoint artifact-path run-unit run-integration run-browser run-live-smoke artifact-retention-days]
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
check.call(text.include?("dependabot[bot]"), "Dependabot boundary is missing")
check.call(text.include?("pull_request_target"), "pull_request_target boundary is missing")
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
  event == "pull_request_target" || fork || actor == "dependabot[bot]"
end

def can_write_cache?(policy:, event:, fork:, actor:, ref:, default_branch:)
  policy == "trusted-write" && !untrusted?(event: event, fork: fork, actor: actor) && event == "push" && ref == default_branch
end

%w[.cache/heavy-ci .npm frontend/.pnpm-store].each { |path| check.call(cache_path?(path), "valid cache path rejected: #{path}") }
%w[/tmp/cache ../cache app/../cache node_modules backend/vendor dist .output build].each { |path| check.call(!cache_path?(path), "unsafe cache path accepted: #{path}") }

negative_contexts = [
  { event: "pull_request", fork: true, actor: "contributor" },
  { event: "pull_request", fork: false, actor: "dependabot[bot]" },
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
        "ACTOR" => "maintainer",
        "TRIGGERING_ACTOR" => "maintainer",
        "DEFAULT_BRANCH" => "main",
        "REF_NAME" => "main",
        "RUN_UNIT" => "true",
        "RUN_INTEGRATION" => "false",
        "RUN_BROWSER" => "false",
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
  check.call(trusted[:status].success? && trusted[:outputs]["effective-execution-class"] == "trusted-heavy", "trusted push cannot select trusted-heavy")

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

contract_doc = File.read(CONTRACT_DOC)
%w[blocked skip proceed hosted-requested trusted-heavy-approved untrusted-hosted untrusted-hosted-fallback invalid-contract invalid-context unsupported-event immutable-reference-required missing-entrypoint missing-lockfile unsafe-input input-path-conflict].each do |term|
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
