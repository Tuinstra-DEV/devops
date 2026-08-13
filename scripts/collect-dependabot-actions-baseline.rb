#!/usr/bin/env ruby
# frozen_string_literal: true

require "date"
require "json"
require "open3"
require "thread"
require "time"

REPOSITORIES = %w[
  Tuinstra-DEV/WODIQ
  Tuinstra-DEV/console
  Tuinstra-DEV/devops
  Tuinstra-DEV/gate
  Tuinstra-DEV/marcel-site
  Tuinstra-DEV/notify
  Tuinstra-DEV/openairco-site
  marcel-tuinstra/sudoku-spark-web
  Tuinstra-DEV/tracker
  Tuinstra-DEV/tuinstra-site
  Tuinstra-DEV/wodiq-site
].freeze

OWNERS = REPOSITORIES.map { |repository| repository.split("/", 2).first }.uniq.freeze
ACTOR = "dependabot[bot]"

def run_json(*command)
  stdout, stderr, status = Open3.capture3(*command)
  raise "#{command.take(3).join(' ')} failed: #{stderr.lines.first&.strip}" unless status.success?

  JSON.parse(stdout)
end

def api_pages(*arguments)
  run_json("gh", "api", *arguments, "--paginate", "--slurp").flat_map do |page|
    if page.is_a?(Hash) && page.key?("workflow_runs")
      page["workflow_runs"]
    elsif page.is_a?(Hash) && page.key?("jobs")
      page["jobs"]
    else
      page
    end
  end
end

def job_duration_seconds(job)
  return unless job["started_at"] && job["completed_at"]

  (Time.parse(job["completed_at"]) - Time.parse(job["started_at"])).to_i
rescue ArgumentError
  nil
end

def dependency_rows(body)
  grouped = body.match(/^Bumps the .+ group with (\d+) updates?:/i)
  return grouped[1].to_i if grouped

  body.scan(/^Bumps? /i).size
end

start_date, end_date = ARGV
abort "usage: collect-dependabot-actions-baseline.rb START_DATE END_DATE" unless start_date && end_date && ARGV.size == 2

Date.iso8601(start_date)
Date.iso8601(end_date)
abort "START_DATE must not be after END_DATE" if start_date > end_date

repositories = {}
REPOSITORIES.each do |repository|
  runs = api_pages(
    "-X", "GET", "repos/#{repository}/actions/runs",
    "-f", "actor=#{ACTOR}",
    "-f", "created=#{start_date}..#{end_date}",
    "-f", "per_page=100"
  ).uniq { |run| run["id"] }

  queue = Queue.new
  runs.each { |run| queue << run }
  mutex = Mutex.new
  jobs = []
  errors = []

  [8, [runs.length, 1].max].min.times.map do
    Thread.new do
      loop do
        begin
          run = queue.pop(true)
          run_jobs = api_pages(
            "-X", "GET", "repos/#{repository}/actions/runs/#{run.fetch('id')}/jobs",
            "-f", "filter=all", "-f", "per_page=100"
          )
          mutex.synchronize { jobs.concat(run_jobs) }
        rescue ThreadError
          break
        rescue StandardError => error
          mutex.synchronize { errors << "#{run&.fetch('id', 'unknown')}: #{error.message}" }
        end
      end
    end
  end.each(&:join)

  accepted = jobs.map do |job|
    seconds = job_duration_seconds(job)
    next unless seconds && seconds >= 0

    labels = Array(job["labels"]).map(&:downcase)
    runner = job["runner_name"].to_s.downcase
    self_hosted = labels.include?("self-hosted")
    hosted = !self_hosted && (
      runner.start_with?("github actions") ||
      labels.any? { |label| label.start_with?("ubuntu", "windows", "macos", "mac os") }
    )
    {seconds: seconds, hosted: hosted, self_hosted: self_hosted}
  end.compact

  repositories[repository] = {
    workflow_runs: runs.length,
    completed_jobs: accepted.length,
    hosted_jobs: accepted.count { |job| job[:hosted] },
    self_hosted_jobs: accepted.count { |job| job[:self_hosted] },
    unknown_jobs: accepted.count { |job| !job[:hosted] && !job[:self_hosted] },
    per_job_rounded_hosted_minutes: accepted.sum { |job| job[:hosted] ? (job[:seconds] / 60.0).ceil : 0 },
    per_job_rounded_unknown_minutes: accepted.sum { |job| !job[:hosted] && !job[:self_hosted] ? (job[:seconds] / 60.0).ceil : 0 },
    raw_job_seconds: accepted.sum { |job| job[:seconds] },
    errors: errors
  }
end

pull_requests = OWNERS.flat_map do |owner|
  rows = run_json(
    "gh", "search", "prs",
    "--owner", owner,
    "--app", "dependabot",
    "--merged",
    "--merged-at", "#{start_date}..#{end_date}",
    "--limit", "1000",
    "--json", "repository,number,title,body,closedAt,url"
  )
  abort "Dependabot PR search reached its 1000-result safety limit for #{owner}" if rows.size == 1000
  rows.select { |pull_request| REPOSITORIES.include?(pull_request.dig("repository", "nameWithOwner")) }
end

unparsed_pull_requests = []
merged_updates = Hash.new { |hash, repository| hash[repository] = {merged_prs: 0, dependency_rows: 0} }
pull_requests.each do |pull_request|
  repository = pull_request.dig("repository", "nameWithOwner")
  count = dependency_rows(pull_request.fetch("body", ""))
  unparsed_pull_requests << "#{repository}##{pull_request['number']}" if count.zero?
  merged_updates[repository][:merged_prs] += 1
  merged_updates[repository][:dependency_rows] += count
end

totals = repositories.values.each_with_object(Hash.new(0)) do |row, sum|
  row.each { |key, value| sum[key] += value if value.is_a?(Integer) }
end
totals[:merged_dependabot_prs] = merged_updates.values.sum { |row| row[:merged_prs] }
totals[:merged_dependency_updates] = merged_updates.values.sum { |row| row[:dependency_rows] }

puts JSON.pretty_generate({
  schema: "DEV-13/dependabot-actions-baseline-v2",
  collector: {
    version: 2,
    invocation: "ruby scripts/collect-dependabot-actions-baseline.rb #{start_date} #{end_date}",
    actions_query: "GET /repos/{repository}/actions/runs actor=#{ACTOR} created=#{start_date}..#{end_date}; GET each run /jobs filter=all",
    pull_request_query: "gh search prs --owner {owner} --app dependabot --merged --merged-at #{start_date}..#{end_date}; filter repository.nameWithOwner to the 11-repository scope",
    dependency_row_rule: "Grouped PR count from 'Bumps the ... group with N updates'; otherwise count body lines beginning 'Bump ' or 'Bumps '.",
    runner_attribution: "self-hosted label wins; otherwise GitHub Actions runner name or hosted OS label; remaining jobs are unknown"
  },
  start_date: start_date,
  end_date: end_date,
  actor: ACTOR,
  repositories: repositories,
  merged_updates_by_repository: merged_updates,
  unparsed_pull_requests: unparsed_pull_requests,
  totals: totals
})
