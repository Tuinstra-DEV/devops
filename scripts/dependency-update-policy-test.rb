#!/usr/bin/env ruby
# frozen_string_literal: true

require "yaml"
require "json"

root = File.expand_path("..", __dir__)
config = YAML.safe_load(File.read(File.join(root, ".github/dependabot.yml")))
abort "Dependabot schema version must be 2" unless config["version"] == 2
abort "DevOps must monitor GitHub Actions" unless config["updates"].any? { |update| update["package-ecosystem"] == "github-actions" }

config["updates"].each do |update|
  abort "Version PR limit must be capped at 2" unless update["open-pull-requests-limit"] == 2
  abort "Updates must be monthly" unless update.dig("schedule", "interval") == "monthly"
  group = update.dig("groups", "routine-updates")
  abort "Routine group is missing" unless group
  abort "Routine group must apply only to version updates" unless group["applies-to"] == "version-updates"
  abort "Majors must remain outside the routine group" unless group["update-types"] == %w[minor patch]
end

matrix = File.read(File.join(root, "docs/workflows/dependency-rollout-matrix.md"))
repositories = %w[
  Tuinstra-DEV/devops
  Tuinstra-DEV/console
  Tuinstra-DEV/gate
  Tuinstra-DEV/marcel-site
  Tuinstra-DEV/notify
  Tuinstra-DEV/openairco-site
  marcel-tuinstra/sudoku-spark-web
  Tuinstra-DEV/tracker
  Tuinstra-DEV/tuinstra-site
  Tuinstra-DEV/WODIQ
  Tuinstra-DEV/wodiq-site
]
repositories.each { |repository| abort "Rollout matrix misses #{repository}" unless matrix.include?(repository) }

evidence = File.read(File.join(root, "docs/workflows/dependency-rollout-evidence-template.md"))
%w[Baseline After Rollback].each do |heading|
  abort "Evidence template misses #{heading}" unless evidence.include?(heading)
end

baseline = File.read(File.join(root, "docs/workflows/dependency-rollout-baseline.md"))
{"Tuinstra-DEV/tracker" => "17", "Tuinstra-DEV/gate" => "10"}.each do |repository, count|
  abort "Baseline misses #{repository} count #{count}" unless baseline.include?("| #{repository} | #{count} |")
end

policy = File.read(File.join(root, "docs/standards/dependency-update-policy.md"))
["at least 20% fewer", "at least 15% fewer", "within 35 days", "within 24 hours"].each do |threshold|
  abort "Policy misses measurable threshold: #{threshold}" unless policy.include?(threshold)
end

bot_baseline = JSON.parse(File.read(File.join(root, "docs/evidence/DEV-13-dependabot-actions-baseline-2026-08-11.json")))
abort "Bot baseline must cover all 11 repositories" unless bot_baseline["repositories"].size == 11
abort "Bot run baseline changed unexpectedly" unless bot_baseline.dig("totals", "workflow_runs") == 201
abort "Bot minute baseline changed unexpectedly" unless bot_baseline.dig("totals", "per_job_rounded_hosted_minutes") == 1029
abort "Bot baseline collector is not reproducible" unless bot_baseline.dig("collector", "invocation")&.include?("collect-dependabot-actions-baseline.rb")
abort "Bot PR baseline is not scoped to the 11 repositories" unless bot_baseline.dig("collector", "pull_request_query")&.include?("11-repository scope")
abort "Merged dependency-update baseline changed unexpectedly" unless bot_baseline.dig("totals", "merged_dependency_updates") == 14
abort "Bot baseline contains unparsed pull requests" unless bot_baseline["unparsed_pull_requests"] == []
abort "Unknown baseline jobs unexpectedly contain rounded duration" unless bot_baseline.dig("totals", "per_job_rounded_unknown_minutes") == 0

security = JSON.parse(File.read(File.join(root, "docs/evidence/DEV-13-security-settings-preflight-2026-08-12.json")))
abort "Security preflight must cover all 11 repositories" unless security["repositories"].size == 11
security["repositories"].each do |repository, state|
  abort "#{repository}: Dependabot security lane not enabled" unless state == {"alerts" => true, "security_updates" => true}
end

abort "Rollback order is not consumer-first" unless matrix.include?("consumer")
abort "Rollback must preserve security PRs" unless matrix.downcase.include?("preserve open security-fix prs")
abort "Wave projection formula is missing" unless matrix.include?("ceil(observed / d * 30)")

puts "dependency update policy test passed"
