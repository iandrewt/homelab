# What CI runs in a stack. A subtree that runs differently redefines them
script "plan" {
  description = "Plans the stack into tfplan"
  job {
    commands = [
      ["tofu", "init", "-input=false", "-no-color"],
      ["tofu", "plan", "-input=false", "-no-color", "-lock-timeout=5m", "-out=tfplan"],
    ]
  }
}

script "apply" {
  description = "Applies the stack's tfplan"
  job {
    commands = [
      ["tofu", "init", "-input=false", "-no-color"],
      ["tofu", "apply", "-input=false", "-no-color", "tfplan"],
    ]
  }
}
