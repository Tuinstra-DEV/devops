# prod02 baseline evidence

Date: 2026-09-11
Story: DEV-25
Host: `prod02` / `vps02.tuinstra.dev` / `tuinstra-prod-02`
Baseline source: DEV-26 commit `90656a699d11cdbc6d15d61213fc934ebed2d70e`

## Applied scope

The shared production-host baseline was applied to Ubuntu 26.04.1 for the
allowlisted future applications `notify`, `tracker`, `wodiq`, and `sudoku`.
No application, application configuration, application secret/data, DNS record,
firewall rule or backup job was migrated by this work. A fresh, host-scoped
Console Agent was enrolled after the initial baseline handoff.

The host now has:

- administrator `mtuinstra` with the externally supplied public key and
  passwordless sudo;
- locked-password `deploy` with its separate forced-command key, no sudo group,
  and no Docker group;
- exact Docker packages from the prod02 inventory, Docker Engine 29.8.0 and
  Compose 5.5.1;
- Docker `live-restore` and bounded local-container logs;
- root-controlled application, secret, and durable-data directories;
- Caddy 2.11.4 pinned to manifest digest
  `sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648`;
- only the empty Caddy HTTP route published on `0.0.0.0:80`; it returns 404.

## Execution evidence

| Gate | Result |
| --- | --- |
| Local production contract test | Passed |
| Baseline, verify, and SSH-hardening syntax checks | Passed with Ansible 2.19.13 |
| Repository `make lint` and `make test` | Passed, including 93 runner tests and 16 classifier tests |
| Bootstrap as initial root connection | Passed: 19 tasks, 10 changed, no failures |
| Independent administrator login | `id -un` returned `mtuinstra` |
| Independent administrator sudo | `sudo -n id -un` returned `root` |
| Full configure through administrator | Passed: 42 tasks, 17 changed, no failures |
| SSH hardening through administrator | Passed effective-config assertions; root key login denied afterward |
| Baseline verification before reboot | Passed: 14 tasks, 0 changed |
| Repeated full configure | Passed: 40 tasks, 0 changed |
| Controlled reboot | Completed; Ansible reported reboot recovery after 17 seconds |
| Baseline verification after reboot | Passed: 14 tasks, 0 changed |
| Post-reboot service check | Docker active; Caddy running; loopback HTTP returned 404 |

The effective SSH configuration reports `PermitRootLogin no`,
`PasswordAuthentication no`, `KbdInteractiveAuthentication no`, and
`PubkeyAuthentication yes`. The administrator remained able to log in and use
sudo after the reload and after reboot.

The deploy forced command rejected an arbitrary `id` command with usage status
64. `status tracker` reached the allowlisted helper but failed closed with
status 66 because no root-owned `/var/www/tracker/compose.yml` exists. This is
the expected pre-migration state.

Secret directories are root-owned mode `0700`. Release directories are
root-owned, group `tuinstra-deploy`, mode `2770`; deploy cannot alter the app
root, future Compose file, secret directory, or Docker daemon.

## Deferred acceptance work

Application migration, database/object/token restoration, workers and scheduled
jobs, Auth0/OAuth integrations, DNS/TLS routes, resource peak testing, backups
to Sanctuary, firewall/network policy, full capacity monitoring and migration rollback remain
open. Console enrollment and Docker inventory are complete; capacity is blocked
by the existing Console contract defect below. DEV-25 must not be marked Delivered or Finished based on
this host baseline alone.

## Console en laatste controle

De actuele agentimage is via gecomprimeerde SSH-overdracht aangeleverd, zonder
registrycredentials naar de host te kopiëren. De configuratie, filesystemlagen en
het platform zijn gecontroleerd ondanks verschillende lokale Docker-store-ID's.
Een eigen hostcredential is uitgegeven en wordt gebruikt; Docker-inventaris is
door Console geaccepteerd. Back-upcollectie staat expliciet uit.

De agent mount slechts drie host-metriekbestanden en een lege capaciteitsmap;
geen host-/etc, volledige /proc, back-ups of Docker-volumedata. De private proxy
weigert een POST naar een niet-bestaand testdoel met HTTP 405.

Een tweede gecontroleerde reboot na agentinstallatie herstelde in 109 seconden.
De persoonlijke beheerlogin en sudo werken; Caddy, agent en proxy kwamen
automatisch terug en het publieke vps02-domein geeft de verwachte HTTP 404.
Rootdisk heeft circa 66 GiB beschikbaar.

Volledige capaciteitsbewaking is niet geaccepteerd: de bestaande Console-agent
emitteert `mount.used_bytes`, maar de hub vereist `mount.bytes_used` en geeft
HTTP 400. Geen ondersteunde configuratieworkaround behoudt alle capaciteit. Dit
is met producer/consumer-bronbewijs aan DEV-18 toegevoegd; geen Console-code of
deliverystatus is tijdens deze bootstrap aangepast. De fout blijft zichtbaar.

De per-host deploy-key staat lokaal buiten Git onder ~/.ssh en is niet naar
GitHub geüpload; workflowintegratie volgt later zoals gevraagd.
