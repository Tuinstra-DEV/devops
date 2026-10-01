#!/usr/bin/env ruby
# frozen_string_literal: true

expected = %w[agent-lab console devops gate marcel-site notify openairco openairco-site status tracker tuinstra-site wodiq-app wodiq-platform wodiq-site]
abort "usage: dependency-update-fleet-test.rb repo=repository-root ... (all 14 Tuinstra-DEV repositories)" unless ARGV.size == expected.size
seen = []
ARGV.each do |argument|
  repository, directory = argument.split("=", 2)
  abort "Invalid or duplicate repository" unless expected.include?(repository) && !seen.include?(repository) && directory
  abort "Missing repository directory: #{directory}" unless Dir.exist?(directory)
  seen << repository
  %w[.github/dependabot.yml .github/dependabot.yaml].each do |path|
    abort "#{repository}: automatic Dependabot updates must remain disabled" if File.exist?(File.join(directory, path))
  end
end
puts "dependency update fleet test passed (#{seen.size} repositories, no version-update configuration)"
