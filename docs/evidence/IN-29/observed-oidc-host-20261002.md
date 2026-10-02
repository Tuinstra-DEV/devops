# IN-29 actual GitHub OIDC host compatibility

The three actual bot-contributor scans on 2 October reached immutable evidence upload but failed safely with `oidc_url_rejected`. The publisher receipt count remained zero; no App pass was published.

Credential-free metadata diagnostic [run37031702870](https://github.com/Tuinstra-DEV/gate/actions/runs/37031702870) observed only `run-actions-3-azure-eastus.actions.githubusercontent.com`, HTTPS, no port, userinfo, fragment or preexisting audience. It did not access a private key or request a token. Marcel explicitly approved adding this one exact hostname on 2 October.

The allowed origins now enumerate the original pipelines host and the two specifically observed eastus hosts (run-actions-1 and run-actions-3). No wildcard, suffix acceptance, extra App right or repository was added. Scheme, authority, userinfo, port, fragment and existing-audience rejection are retained. Workflow/token/artifact/source verification is unchanged.

TDD: the additional host case first failed; the smallest explicit allowlist change makes it pass while origin-confusion negatives remain denied. Synthetic tests are not live OIDC evidence. Fresh immutable helper/workflow/caller bindings and actual controlled Gate PR proof follow.
