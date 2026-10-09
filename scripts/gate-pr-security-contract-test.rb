#!/usr/bin/env ruby
# Fail closed when a future workflow change weakens the producer trust boundary.
require "yaml"

def document(path)
  YAML.safe_load(File.read(path), aliases: false)
end

def ensure_contract(condition, message)
  raise "Gate PR Security contract: #{message}" unless condition
end

workflow = document(".github/workflows/reusable-gate-pr-security.yml")
action = document(".github/actions/gate-pr-security/action.yml")
caller = document("templates/workflows/caller-gate-pr-security.yml")
pin = /@[a-f0-9]{40}\z/
permissions = { "contents" => "read", "pull-requests" => "read", "actions" => "read", "packages" => "read", "id-token" => "write" }
trigger = workflow["on"] || workflow[true]
ensure_contract(trigger.keys == ["workflow_call"], "only reusable invocation is supported")
ensure_contract(trigger["workflow_call"].nil? || trigger["workflow_call"].empty?, "no caller commands, policy inputs or secrets")
ensure_contract(workflow.fetch("jobs").keys == ["producer"], "one authenticated producer")
producer = workflow.fetch("jobs").fetch("producer")
ensure_contract(producer["runs-on"] == "ubuntu-24.04", "untrusted PR data stays on the isolated hosted runner")
ensure_contract(producer["timeout-minutes"] == 20, "bounded job duration")
ensure_contract(producer["permissions"] == permissions, "exact least-privilege job permissions")
ensure_contract(!producer.key?("if") && !producer.key?("continue-on-error"), "no silently skipped or ignored producer failure")
steps = producer.fetch("steps")
ensure_contract(steps.length == 1 && steps[0].fetch("uses").match?(pin), "helper action is immutable")
ensure_contract(steps[0]["with"].nil?, "caller cannot configure helper execution")
ensure_contract(action["inputs"].nil?, "helper has no free commands or policy overrides")
action_steps = action.fetch("runs").fetch("steps")
ensure_contract(action_steps.none? { |step| step["continue-on-error"] }, "no ignored step failures")
pull = action_steps.find { |step| step["name"] == "Pull fixed scanner image" }.fetch("run")
ensure_contract(pull.include?('source.py" image --work-dir "$GATE_WORK_DIR"') && pull.include?('docker pull --platform linux/amd64 "$gate_image"'), "pull uses the prepared repository profile on its native platform")
scan_index = action_steps.index { |step| step["run"].to_s.include?("source.py\" scan") }
upload_index = action_steps.index { |step| step["uses"].to_s.start_with?("actions/upload-artifact@") }
submit_index = action_steps.index { |step| step["run"].to_s.include?("evidence.py\" submit") }
ensure_contract(scan_index && upload_index && submit_index && scan_index < upload_index && upload_index < submit_index, "scan then immutable upload then fresh OIDC receipt")
upload = action_steps.fetch(upload_index)
ensure_contract(upload["uses"].match?(pin), "upload action is immutable")
ensure_contract(upload.fetch("with").values_at("compression-level", "overwrite", "if-no-files-found", "retention-days") == [0, false, "error", 14], "bounded immutable artifact upload")
ensure_contract(!action_steps.fetch(scan_index).fetch("env", {}).key?("GITHUB_TOKEN"), "scan step does not receive the repository token")
ensure_contract(action_steps.last["if"] == "always()" && action_steps.last["run"].include?(" cleanup "), "temporary source cleanup is unconditional")
ensure_contract(action_steps.none? { |step| step["uses"].to_s.include?("checkout") }, "never execute checked-out PR files")
caller_events = caller["on"] || caller[true]
ensure_contract(caller_events.key?("pull_request_target"), "forks and bot PRs follow target-event path")
ensure_contract((caller_events.fetch("pull_request_target").keys - ["types"]).empty?, "no branch or path filters")
ensure_contract(caller_events.fetch("pull_request_target").fetch("types").include?("synchronize"), "new head triggers re-evaluation")
consumer = caller.fetch("jobs").fetch("security")
ensure_contract(consumer.fetch("uses").match?(pin), "consumer pins the reusable workflow")
ensure_contract(consumer["permissions"] == permissions, "consumer permissions match the fixed contract")
ensure_contract(consumer["secrets"].nil? && consumer["with"].nil? && consumer["if"].nil?, "no token inheritance, overrides or actor filters")
puts "Gate PR Security workflow contracts passed"
