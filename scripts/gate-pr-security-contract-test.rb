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
call = trigger.fetch("workflow_call")
request = { "description" => "Request isolated scheduling under the fixed role policy; does not grant trust", "required" => false, "default" => false, "type" => "boolean" }
ensure_contract(call.is_a?(Hash) && call.keys == ["inputs"] && call["inputs"] == { "request-isolated-role-runner" => request }, "only one default-disabled scheduling request; no caller policy, commands or secrets")
ensure_contract(workflow.fetch("jobs").keys == ["producer"], "one authenticated producer")
producer = workflow.fetch("jobs").fetch("producer")
selector = producer.fetch("runs-on")
ensure_contract(selector.is_a?(String) && selector.start_with?("${{ inputs['request-isolated-role-runner'] == true &&") && selector.end_with?("|| 'ubuntu-24.04' }}"), "fixed role scheduling request retains original hosted fallback")
ensure_contract(selector.include?('"group":"sanctuary-trusted-verification"') && selector.include?('"ci-pr-security-producer"'), "producer role uses the exact reviewed group")
ensure_contract(selector.include?("github.run_id") && selector.include?("github.run_attempt") && selector.include?("github.event.pull_request.head.sha"), "role is bound to exact run, attempt and event source")
ensure_contract(selector.include?("toJSON(format('ci-execution-sha-{0}', github.sha))"), "target controller execution SHA is derived only from GitHub context")
ensure_contract(selector.gsub(/\s+/, " ").split(" && fromJSON(format(", 2).first == "${{ inputs['request-isolated-role-runner'] == true && contains(fromJSON('[\"Tuinstra-DEV/gate\",\"Tuinstra-DEV/tracker\"]'), github.repository) && github.event.repository.full_name == github.repository && toJSON(github.event.repository.private) == 'true' && toJSON(github.event.repository.fork) == 'false' && github.event_name == 'pull_request_target' && github.actor == 'marcel-tuinstra' && github.triggering_actor == 'marcel-tuinstra' && github.event.sender.login == 'marcel-tuinstra' && github.event.sender.type == 'User' && github.event.pull_request.user.login == 'marcel-tuinstra' && github.event.pull_request.user.type == 'User' && github.event.pull_request.state == 'open' && toJSON(github.event.pull_request.merged) == 'false' && github.event.pull_request.number > 0 && github.event.pull_request.head.sha != '' && github.event.pull_request.head.ref != '' && github.event.pull_request.base.ref != '' && github.event.pull_request.head.repo.full_name == github.repository && toJSON(github.event.pull_request.head.repo.private) == 'true' && toJSON(github.event.pull_request.head.repo.fork) == 'false' && github.event.pull_request.base.repo.full_name == github.repository && toJSON(github.event.pull_request.base.repo.private) == 'true' && toJSON(github.event.pull_request.base.repo.fork) == 'false' && github.event.repository.default_branch != '' && github.ref == format('refs/heads/{0}', github.event.repository.default_branch) && github.sha != ''", "every fixed human-only event/source predicate is mandatory before runner selection")
ensure_contract(!selector.include?("trusted-heavy") && !selector.include?("trusted-medium") && !selector.include?("vars.") && !selector.include?("secrets."), "caller cannot choose role labels or assert source trust")
ensure_contract(producer["timeout-minutes"] == 20, "bounded job duration")
ensure_contract(producer["permissions"] == permissions, "exact least-privilege job permissions")
ensure_contract(!producer.key?("if") && !producer.key?("continue-on-error"), "no silently skipped or ignored producer failure")
steps = producer.fetch("steps")
ensure_contract(steps.length == 1 && steps[0].fetch("uses").match?(pin), "helper action is immutable")
ensure_contract(steps[0]["with"].nil?, "caller cannot configure helper execution")
ensure_contract(steps[0]["env"] == { "GATE_TRACKER_SCANNER_BINDING" => '${{ vars.GATE_TRACKER_SCANNER_BINDING }}' }, "only trusted workflow resolves the Tracker scanner binding")
ensure_contract(action["inputs"].nil?, "helper has no free commands or policy overrides")
action_steps = action.fetch("runs").fetch("steps")
ensure_contract(action_steps.none? { |step| step.fetch("env", {}).values.any? { |value| value.to_s.include?("${{ vars.") } }, "composite action cannot resolve repository variables")
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
