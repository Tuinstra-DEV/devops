#!/usr/bin/env ruby
# frozen_string_literal: true

require "yaml"

abort "usage: dependency-update-fleet-test.rb repo=dependabot.yml ..." unless ARGV.size == 11

expected_update_counts = {
  "console" => 3,
  "devops" => 1,
  "gate" => 4,
  "marcel-site" => 2,
  "notify" => 3,
  "openairco-site" => 2,
  "sudoku-spark-web" => 2,
  "tracker" => 4,
  "tuinstra-site" => 2,
  "wodiq" => 2,
  "wodiq-site" => 2
}
required_docker_directories = {
  "gate" => %w[/backend/docker/php /backend/docker/web /frontend/docker],
  "tracker" => %w[/code /code/infra/php /code/web/docker]
}

times = {}
ARGV.each do |argument|
  repository, path = argument.split("=", 2)
  abort "invalid repository=config argument" unless repository && path

  config = YAML.safe_load(File.read(path))
  abort "#{repository}: schema version must be 2" unless config["version"] == 2
  updates = config.fetch("updates")
  abort "#{repository}: unexpected update-lane count" unless updates.size == expected_update_counts.fetch(repository)
  abort "#{repository}: GitHub Actions coverage missing" unless updates.any? { |update| update["package-ecosystem"] == "github-actions" }

  if required_docker_directories.key?(repository)
    docker = updates.select { |update| update["package-ecosystem"] == "docker" }
    abort "#{repository}: Docker must use one shared concurrency lane" unless docker.size == 1
    actual = Array(docker.first["directories"] || docker.first["directory"]).sort
    abort "#{repository}: Docker coverage is incomplete" unless actual == required_docker_directories.fetch(repository).sort
  end

  updates.each do |update|
    ecosystem = update.fetch("package-ecosystem")
    abort "#{repository}/#{ecosystem}: cadence must be monthly" unless update.dig("schedule", "interval") == "monthly"
    abort "#{repository}/#{ecosystem}: timezone must be Europe/Amsterdam" unless update.dig("schedule", "timezone") == "Europe/Amsterdam"
    abort "#{repository}/#{ecosystem}: PR limit must be 2" unless update["open-pull-requests-limit"] == 2
    group = update.dig("groups", "routine-updates")
    abort "#{repository}/#{ecosystem}: version-only routine group missing" unless group&.fetch("applies-to", nil) == "version-updates"
    abort "#{repository}/#{ecosystem}: majors must remain separate" unless group["update-types"] == %w[minor patch]

    time = update.dig("schedule", "time")
    abort "#{repository}/#{ecosystem}: schedule time missing" unless time
    previous = times[time]
    abort "#{repository}/#{ecosystem}: schedule overlaps #{previous} at #{time}" if previous
    times[time] = "#{repository}/#{ecosystem}"
  end
end

abort "fleet must contain exactly 27 staggered update lanes" unless times.size == 27
puts "dependency update fleet test passed (11 repositories, #{times.size} unique ecosystem schedules)"
