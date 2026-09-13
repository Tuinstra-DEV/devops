# Productiehost-baseline

Dit draaiboek richt een nieuwe productiehost veilig en herhaalbaar in. DEV-26
migreert geen applicaties, wijzigt geen firewall of DNS en configureert nog geen
back-ups. Poort 80 geeft alleen Caddy `404`; HTTPS volgt bij de migratie van de
beoordeelde domeinroutes.

## Wat de baseline beheert

- `mtuinstra` gebruikt `~/.ssh/id_ed25519_marcel.pub` en heeft passwordless sudo.
- `deploy` heeft een vergrendeld wachtwoord, geen algemene shell, sudo of
  Docker-groep en per host een eigen forced-command-sleutel.
- De private deploysleutels zijn
  `~/.ssh/id_ed25519_tuinstra_prod_01_deploy` en
  `~/.ssh/id_ed25519_tuinstra_prod_02_deploy`. Deel of commit deze nooit; de
  inventory verwijst alleen naar de bijbehorende `.pub`-bestanden.
- Docker Engine en Compose gebruiken de exacte apt-versies uit de inventory,
  met live restore en begrensde lokale logs.
- Caddy gebruikt een vastgezette multi-architecture image-digest, een
  loopback-only admin-endpoint en root-owned configuratie onder
  `/var/www/_platform`. Nieuwe applicatieroutes komen in de aanvankelijk lege
  map `/var/www/_platform/caddy/sites`.
- Applicaties staan onder `/var/www/<app>`: alleen `releases` is schrijfbaar
  voor `deploy`; root beheert `shared` en de toekomstige `compose.yml`.
- Secrets staan in `/etc/tuinstra/<app>` met modus `0700`; duurzame data staat
  in `/var/lib/tuinstra/<app>`.

De deploysleutel accepteert alleen `status <app>` en `deploy <app>`. De
root-owned helper weigert onbekende apps, ontbrekende of schrijfbare
Compose-definities en images zonder `@sha256:<digest>`. Verwijs vanuit een
geprivilegieerde Compose-definitie nooit naar bestanden die `deploy` kan wijzigen.

## Voorbereiding

De repository-wrapper detecteert `ansible-playbook` op `PATH` en anders de
optionele repo-installatie `../.tooling/ansible/bin/ansible-playbook`. Hij
installeert zelf niets. Controleer vooraf de persoonlijke admin-public-key en
maak voor elke host een unieke deploy-keypair. CI-credentials zijn nog niet
geconfigureerd en horen niet bij deze baseline.

## Vaste workflow per nieuwe host

Voer bootstrap eerst als `root` uit. Check mode doet alleen de fail-closed
host-, key- en app-preflight, omdat de latere users en apt-bestanden nog niet
bestaan.

Voor `prod01` gebruikt de wrapper standaard
`infra/ansible/production/inventory.yml` en limit `prod01`:

```sh
scripts/production-host-baseline --check bootstrap
scripts/production-host-baseline bootstrap
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes mtuinstra@vps01.tuinstra.dev sudo -n true
PRODUCTION_ADMIN_SSH_VERIFIED=true PRODUCTION_ADMIN_SUDO_VERIFIED=true \
  scripts/production-host-baseline --check harden-ssh
PRODUCTION_ADMIN_SSH_VERIFIED=true PRODUCTION_ADMIN_SUDO_VERIFIED=true \
  scripts/production-host-baseline harden-ssh
scripts/production-host-baseline --as-admin --check configure
scripts/production-host-baseline --as-admin configure
scripts/production-host-baseline --as-admin verify
```

Voor `prod02` is dezelfde workflow pas uitvoerbaar vanuit de afhankelijke
DEV-25-branch, waar `infra/ansible/production/inventory-prod02.yml` wordt
toegevoegd. Voeg aan ieder wrappercommando toe:

```sh
--inventory infra/ansible/production/inventory-prod02.yml --limit prod02
```

Gebruik voor de onafhankelijke controle het `prod02`-adres uit die inventory.
Verifieer vóór hardening in een aparte adminsessie zowel login als `sudo -n
true`. Na hardening moeten `configure`, de herhaalde idempotentiecontrole en
`verify` altijd `--as-admin` gebruiken. Herhaal `configure` en accepteer geen
onverklaarde wijzigingen. Herhaal `verify` na een gecontroleerde reboot en
controleer vooraf of Hetzner Console of rescue bereikbaar is.

## Uitgestelde controles

Firewall- en netwerkfiltering vallen buiten DEV-26. Publiceer tot acceptatie
geen applicatie-, database-, Docker API- of monitoringpoort; alleen Caddy op
poort 80 is bedoeld. Back-ups zijn eveneens uitgesteld. Richt vóór een migratie
versleutelde, applicatieconsistente back-ups in naar Sanctuary
`/mnt/hdd1000-01`, leg retentie vast en bewijs een restore.

Console-enrollment gebeurt afzonderlijk volgens
[Console enrollment](../../infra/console-agent/README.md). De agent mag de
onbeperkte Docker-socket niet mounten. Toekomstige Console-provisioning hoort
niet in DEV-26.

## Lokale regressiecontrole

```sh
ANSIBLE_HOME=/tmp/tuinstra-ansible ANSIBLE_REMOTE_TEMP=/tmp/tuinstra-ansible-local \
  ansible-playbook -i localhost, infra/ansible/production-host-template-test.yml
```

Deze test rendert alleen synthetische configuratie in een tijdelijke map,
controleert shellsyntax en verwijdert de fixtures. Hij benadert geen host.
