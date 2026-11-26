# For a layer an admin applies, whose plan mustn't reach CI's public logs: CI prints only how much
# would change and which checks fail, and keeps no plan. A subtree imports it

script "plan" {
  description = "Plans the stack, printing only how much would change and which checks fail"
  job {
    commands = [
      ["tofu", "init", "-input=false", "-no-color"],
      ["bash", "-c", "tofu plan -input=false -no-color -lock-timeout=5m -out=tfplan >/dev/null"],
      ["bash", "-c", <<-EOT
        set -eo pipefail
        json=$(tofu show -json tfplan)
        rm tfplan
        jq -r '.checks[]? | .instances[]? | select(.status == "fail") | .problems[]?.message' <<< "$json" |
          while IFS= read -r problem; do echo "::warning title=${terramate.stack.name}::$problem"; done
        changed=$(jq '[.resource_changes[]? | select(.change.actions != ["no-op"])] | length' <<< "$json")
        if ((changed)); then
          echo "### ${terramate.stack.name}: $changed to change, applied by hand" >> "$${GITHUB_STEP_SUMMARY:-/dev/stdout}"
        fi
      EOT
      ],
    ]
  }
}

script "apply" {
  description = "Applied by hand"
  job {
    commands = [["echo", "${terramate.stack.name} is applied by hand, with az login"]]
  }
}
