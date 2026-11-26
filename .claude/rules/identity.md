---
paths:
  - "tofu/identity/**"
  - "tofu/platform/aws/**"
  - "tofu/security/**"
  - "tofu/policy/**"
  - "tofu/modules/tofu-plan/**"
  - "tofu/modules/entra-app/**"
  - ".github/workflows/tofu.yml"
  - "**/stack.tm.hcl"
  - "mise.toml"
---

# Identity

Entra ID Free: tokens carry groups as **object IDs**, and only users can be assigned to an app.
**Groups decide**: `Administrators` and one per team (`Security` is a team), made by
`tofu/identity/entra/directory`. Each consumer looks groups up by display name at plan time and
maps the IDs back itself: Proxmox gets a `<object id>-entra` group per grant, the apiserver a
CEL map to `oidc:<team>`. **Assignments only gate**: each platform instance has its own app (its
client ID the audience), *assignment required*, and `entra/apps` assigns the members of the
groups it grants. Apps live there (`modules/entra-app`), so a rebuild keeps its client ID, and
each is consented for everyone, so no user may consent to anything. App roles are for
permissions an app defines itself, never team membership.

**Tenant policies are set by hand**: the Azure CLI can't be granted `Policy.*` scopes, so
`directory` checks what it can read of them.

AWS is ABAC: a permission set says what an entity can do, never who it is. `entra/aws` writes
the `team-<team>` SAML claims and SCIM's scope; membership is written onto users and groups as
the `aws` directory extension, which the scoping filters read, always before the scope is saved
(saving starts a sync that disables whoever is out of scope).

The organisation and its accounts are Platform's (`platform/aws/org`), made from the account
catalogue, which every layer reads for its accounts; what every account runs under - guardrails,
the trail, detection, the state bucket - is Security's (`security/aws/*`); Identity Center's sets
and assignments and its delegated administration (the Identity account) are Identity's.
**`platform/aws/baseline` is the account factory**: everything every member gets, through
bootstrap - its alias, EBS encryption, no default VPC, GitHub's OIDC provider, `tofu-plan`, and
`tofu-apply` in the team tier. What may federate is the `Identity providers` SCP's, so no team
but Platform's takes part in vending. **A new account is two applies**: `org` makes it,
then `baseline` and `identity-center`, since a provider can't name an account the same plan
creates. Until `baseline` applies there it has no `tofu-plan`, so that first `baseline` apply is
by hand. **OUs group accounts by their controls**,
as AWS's multi-account guidance has it: Security holds only Log Archive and Security Tooling;
Deployments holds release machinery (Tofu State, Artifact Signing, Artifact Distribution), whose SCP
admits no Identity Center session but reading and state, and which Identity Center's Administrator
never reaches, by membership; Infrastructure holds Network and Identity. Only bootstrap makes an identity
provider; a role's trust in one is its own. **The seed (`security/aws/seed`) is what CI can't create**, applied
from a laptop alone: management's GitHub OIDC provider; `deploy-org`, assumable by `tofu.yml` on
`main` but holding nothing until an activation, under a boundary that leaves out IAM, Identity
Center, EventBridge and SNS; `ManagementAdministrator`; and the tripwires, which mail the
management mailbox on any change to the organisation, management's IAM or Identity Center, the
tripwires themselves, or the trail. The trail is under Object Lock in `COMPLIANCE`, which no one
shortens, root included. **An activation opens a tier for one run**: the seed's `activate`
variable, applied by hand, writes an inline policy on `deploy-org` (org) or `images-release`
(release, which signs only through IAM, so only then) for sessions carrying that run's
`run_id` by TIP, issued in the next 15 minutes. Applying without it closes the tier.
**GitHub's settings are `security/github`'s, applied by hand alone** like the seed, since they're
the gates CI runs behind: rulesets on `main` (signed, linear, no force-push or deletion, ci.yml's
checks, only you push) and on release tags (never moved or deleted, only you create), Actions'
permissions, and the environments, each limited to `main`. Its token administers this repository
alone, read ephemerally from `Break-glass`; nothing else holds Administration. A fork's pull
request runs only once approved, whoever opens it: set by hand until
integrations/terraform-provider-github#2108.
**Every layer is a Terramate stack** (`stack.tm.hcl`: what it runs `after`), and CI runs the
changed ones tagged `ci`, in Terramate's order (`terramate run --changed --tags ci`); a layer gets
the tag once CI can reach its providers, and the seed and `security/github` never do. `tofu.yml`
is two jobs: a plan, on a pull request from `main`'s copy (`pull_request_target`), its key and
policy read from the base commit, only if every
commit is signed by the operator's primary key (`.github/signing-key.asc`) or is Dependabot's
signed by GitHub, and on `main` keeping each plan with changes; then an apply, on `main` and
skipped when there are none, of exactly those plans once the run is activated. Each job exchanges its
GitHub token, once, for every profile a layer names, straight to that account's role (bootstrap
alone chains, from `deploy-org`), then runs each stack's `plan` or `apply` script (`terramate
script run`): `tofu/scripts.tm.hcl`'s, or a variant from `tofu/ci/` a subtree imports; the
plan job checks every plan against `main`'s policy, and the apply job waits for the activation
before it touches anything. A later stack's
plan is made before an earlier one applies, so what an upstream apply changes reaches it on the
next run. Plans are printed and checked by conftest (`tofu/policy`: nothing lasting destroyed, no `/org/`
role changed, both applied by hand). Tools are `mise.toml`'s.

**An account's tier is in the catalogue, and decides who applies to it.** `team` (Network,
Artifact Distribution): CI, in its owner's `tofu-<team>` environment, as `tofu-apply`, which
no person may assume. `org` (everything that sets or could loosen the bounds): bootstrap, from
`deploy-org` once activated, or `ManagementAdministrator` by hand. **Every plan, a person's or
CI's, is `tofu-plan` in the account**, reached from the account's Administrator or `ReadOnly`
session, or by the owner's `tofu-plan-<team>` or the org tier's `tofu-plan-org`; management's
is the seed's, for `ManagementAdministrator` and `tofu-plan-org`. Only your runs (`actor_id`)
apply; yours and Dependabot's plan. **People hold standing read, never standing change**: the
owner team and `Administrators` (the platform's operators, across every team, whose passkeys
approve activations) get Administrator outside Deployments until the broker elevates, and
`ReadOnly` inside.
It reads contents too, as drift and ephemeral values need: what a plan sees is scoped by who
may assume it, never by action. A layer picks its profile by `terraform.applying`
(`homelab-<account>-plan`, or `-apply`, or `-bootstrap`), which the backend can't, so a plan
shares its team's state role and the state bucket refuses a state write from a `tofu-plan-*`
session, by trusted identity propagation. `modules/tofu-plan` defines it once, for `baseline` and the seed. **A role the organisation depends on is under `/org/`**, which an
SCP lets only bootstrap change; a team owns
every other path in its account. The Deployments SCP admits every session but Identity Center's
other than `ReadOnly`, `SecurityAudit` and `TofuState`, so it names no account's roles.

The three states apply in order, by blast radius: `directory`, `aws`, `apps`, each reading the
others by name. **Nothing unattended applies them** (assignments need
`AppRoleAssignment.ReadWrite.All`); they run under an admin's `az login`. **CI only plans them**,
by `tofu/ci/private-by-hand.tm.hcl`, as `directory`'s `Homelab Tofu Plan`: federated from
`tofu.yml` as `tofu-plan` is, but for `actor_id`, which Entra can't match, and holding Global
Reader alone, since an app reads a claims policy only with a write permission. Their plans name
people and logs are public, so CI shows only how much would change and which checks fail, and
keeps no plan. `azuread` for what it
types, `microsoft/msgraph` for the gaps - not azapi, which reaches only ARM.
