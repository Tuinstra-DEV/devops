#!/usr/bin/env ruby
# frozen_string_literal: true

require "yaml"

WORKFLOW = ".github/workflows/reusable-heavy-ci-v2.yml"
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
required_outputs = %w[contract-version artifact-name artifact-id artifact-digest payload-sha256 effective-execution-class cache-key cache-hit metrics-artifact]
required_stages = %w[bootstrap build unit integration e2e-prepare browser live-smoke]

required_inputs.each { |name| check.call(text.include?("      #{name}:"), "missing input #{name}") }
required_outputs.each { |name| check.call(text.include?("      #{name}:"), "missing output #{name}") }
required_stages.each { |name| check.call(text.include?(name), "missing typed stage #{name}") }

check.call(text.include?("default: hosted"), "hosted must remain the execution default")
check.call(text.include?("hosted|trusted-heavy"), "execution-class enum is not enforced")
check.call(text.include?("off|restore-only|trusted-write"), "cache-policy enum is not enforced")
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
