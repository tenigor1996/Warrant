# Policy

What the agent may change, and who stops it when it tries something else.
There are two layers. They are different things and only one of them is
OpenShell.

| | Layer 1: app-level checks | Layer 2: OpenShell sandbox |
|---|---|---|
| Where | `policy.py` + `remediation_policy.json`, called by `warrant/agent/remediation.py` | `openshell/warrant-sandbox-policy.yaml`, applied by OpenShell to the NemoClaw sandbox |
| Enforced by | Python, inside the Warrant process | the kernel (Landlock) and OpenShell's network proxy |
| Covers | actions requested through Warrant's own tools | every process in the sandbox, whatever it runs |
| Status | implemented, tested, active on every run | **template only: not applied or validated on the GB10 yet** |
| `enforced_by` in results | `warrant-app-policy` | `os-sandbox` |

Layer 1 is a guardrail and the source of the instructive denial text. It is not
a security boundary: code that does not go through Warrant's tools is not
subject to it. Do not describe a layer-1 denial as "OpenShell denied it".
Until layer 2 is applied on the GB10, every denial in the demo is layer 1.

## Layer 1 rules (`remediation_policy.json`)

Deny by default. Deterministic: verdict is a pure function of the action, its
parameters and the rules file.

- `revert_commit_and_push` — always denied (pushing to main).
- `apply_remediation(path, change)` — allowed only when
  - `path` is `checkout_service/config.py`, and
  - every key in `change` is a listed setting, and
  - every value has the listed type and is inside the listed range.
- anything else — denied.

Denial text always names the permitted alternative:

    DENIED: write to checkout_service/payment_client.py is outside the permitted
    remediation scope. Permitted write target: checkout configuration only
    (checkout_service/config.py, via apply_remediation). Choose a different approach.

The ranges are sanity bounds, not the answer: several values pass the policy,
and `verify_recovery` separately checks the settings against git history.

`WARRANT_POLICY_FILE` points at a different rules file.

## Layer 2: wiring OpenShell on the GB10

The tools are already shaped for it:

- The MCP server runs inside the sandbox (OpenClaw spawns it over stdio), so
  its file writes and network calls are subject to the sandbox policy.
- `apply_remediation` writes the config file in place (no temp file + rename),
  so a write grant on that single file is enough.
- If the OS refuses the write, the tool returns the same `DENIED: ...` shape
  with `enforced_by: "os-sandbox"`, so the model replans the same way and the
  timeline shows which layer said no.
- No tool takes a command string, and Warrant has no push capability, so the
  sandbox needs no git remote access.

Steps (commands depend on the installed NemoClaw/OpenShell version; check
`openshell --help` / `nemoclaw --help`):

1. Dump the sandbox's current policy and compare its field names with the template.
2. Fill in the `<PLACEHOLDER>`s and merge the template into it.
3. Apply it to the sandbox.
4. Prove it from inside the sandbox, without Warrant's Python checks in the way:
   - `echo x >> <state>/checkout-service/checkout_service/payment_client.py` must fail
   - `echo >> <state>/checkout-service/checkout_service/config.py` must succeed
   - `curl https://example.com` must fail; `curl <host>:8081/health` must succeed
5. Optional demo of layer 2 on its own: point `WARRANT_POLICY_FILE` at a copy of
   the rules with a second path in `writable_paths`, ask for a write there, and
   show the result carries `enforced_by: "os-sandbox"`.

## Tests

    python -m pytest warrant/policy/tests -q
