# IN-29: exact observed OIDC host and bounded denial diagnostics

Marcel explicitly approved `run-actions-2-azure-eastus.actions.githubusercontent.com` on 2 October 2026 after metadata-only GitHub runs 37034147683 and 37034783534 observed it. This adds one exact origin to the original pipelines host and the separately approved eastus hosts 1 and 3. No wildcard, subdomain, port, credential, fragment, preexisting audience, westus host or permission expansion is accepted.

Terminal receipt denials expose only a fixed whitelist of public machine codes. Arbitrary codes, details, HTML and oversized bodies remain generic. Transient HTTPError streams are closed without reading their bodies before the bounded retry. The publisher signature, claim, workflow, evidence, repository and permission boundaries are unchanged.

TDD: the new host test and transient no-read regression both failed before repair. After repair the 74 producer tests passed; full `make lint` and `make test` passed using the existing local Ansible environment. The independent security reviewer found the transient-body issue and the root repaired it. Existing untracked pycache was preserved.

This is compatibility and diagnostic evidence, not a successful live publisher proof. No UI change applies.
