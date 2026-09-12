# Geïsoleerde productiehost-rehearsal

Deze fixture bewijst dat een lege Ubuntu-host vanuit een beoordeeld profiel kan
worden ingericht en opnieuw kan worden gecontroleerd. De host is uitsluitend
`tuinstra-rehearsal-01`; hij gebruikt geen productie-identiteit, DNS, sleutels of
data. Console mag deze target alleen in `APP_ENV=test` registreren.

## Vast contract

| Veld | Waarde |
| --- | --- |
| host slug | `tuinstra-rehearsal-01` |
| inventory ID en limit | `rehearsal01` |
| inventory | `infra/ansible/rehearsal/inventory.yml` |
| credential directory | `tuinstra-rehearsal-01` |
| bronrepository | `git@github.com:Tuinstra-DEV/devops.git` |
| domein | `umami.rehearsal.invalid` |
| SSH | `mtuinstra@127.0.0.1:60022` |

Het Console-profiel gebruikt als `source_sha` exact de commit die deze fixture
bevat. De root-owned sourcebundle heeft die SHA als mapnaam en moet een schone
checkout van dezelfde bronrepository zijn. Een branchnaam, tag, los inventorypad
of een andere limit is geen geldige identiteit. De semantische profielvelden
staan in `infra/ansible/rehearsal/profile-contract.yml`; de SHA wordt pas bij het
publiceren uit de immutable bundle overgenomen om een circulaire Git-hash te
voorkomen.

De inventory bevat opzettelijk niet-bestaande public-keypaden. De worker levert
voor iedere uitvoering een root-owned `vars.yml` dat deze twee waarden vervangt:

```yaml
production_admin_public_key_file: /protected/tuinstra-rehearsal-01/admin.pub
production_deploy_public_key_file: /protected/tuinstra-rehearsal-01/deploy.pub
```

De bijbehorende private SSH-sleutel en `known_hosts` blijven in dezelfde
rehearsal-only credential directory. Gebruik tijdelijke Ed25519-keypairs en
controleer de gescande localhost-hostkey via de console van de eigen Lima-VM.
Gebruik geen persoonlijke, deploy- of productieherstelcredentials.

## VM aanmaken

De VM gebruikt vier CPU's, 8 GiB geheugen, een sparse rootdisk van 64 GiB en een
losse sparse back-updisk van 20 GiB. Hij mount geen hostdirectory, laadt geen
persoonlijke SSH-sleutels of agent en publiceert naast Lima's localhost-SSH geen
gastpoorten. De brede `ignore`-regel zet `guestIPMustBeZero: false` expliciet,
zodat Lima ook loopback-listeners in de gast niet automatisch doorstuurt.
Outbound DNS en HTTPS zijn tijdens de inrichting nodig voor Ubuntu, Docker en de
vastgezette containerimages.

```sh
limactl validate infra/lima/tuinstra-rehearsal-01.yaml
limactl disk create tuinstra-rehearsal-backup-01 --size 20GiB
limactl create --name tuinstra-rehearsal-01 --mount-none --tty=false \
  infra/lima/tuinstra-rehearsal-01.yaml
limactl start --progress --tty=false tuinstra-rehearsal-01
```

Bootstrap buiten de Console-worker één keer gebruiker `mtuinstra` met de
tijdelijke publieke sleutel, passwordless sudo, OpenSSH en Python 3. Pin daarna
de hostkey. Voer vervolgens buiten de worker eenmaal de beoordeelde
`production-host-baseline ... bootstrap` uit met dezelfde vaste inventory,
limit en beschermde vars. Die stap installeert de pakketvoorwaarden, accounts
en de gecontroleerde Docker signing key waarop een betekenisvolle Ansible-check
kan voortbouwen. Check mode maakt ontbrekende gebruikers en apt-bestanden niet
aan; een lege host rechtstreeks aan `ansible.profile_check` aanbieden moet dus
fail-closed eindigen. Dit is expliciet de bootstrapgrens: DEV-36 voert daarna
uitsluitend check en apply van de vaste baseline uit als bestaande
administrator.

## Check, apply en bewijs

De Console-worker gebruikt `ansible.profile_check` en daarna een aan precies dat
bewijs gebonden `ansible.profile_apply`. Het vaste profiel
`baseline_umami_restore` past eerst de hostbaseline toe en legt daarna alleen de
inactieve Umami-herstelvoorwaarden klaar: de lege PostgreSQL-doelmap, Compose,
beide beheerscripts en de Caddy-route. Het maakt geen secrets of
applicatiecontainers en herlaadt Caddy niet. De restore-worker neemt pas na
herstel en validatie verkeer over. De onderliggende vaste aanroepen zijn:

```sh
export ANSIBLE_PRIVATE_KEY_FILE=/protected/tuinstra-rehearsal-01/id_ed25519
export ANSIBLE_SSH_ARGS='-o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/protected/tuinstra-rehearsal-01/known_hosts'
scripts/production-host-baseline \
  --inventory infra/ansible/rehearsal/inventory.yml \
  --limit rehearsal01 --extra-vars /protected/tuinstra-rehearsal-01/vars.yml \
  --as-admin bootstrap
scripts/production-host-baseline \
  --inventory infra/ansible/rehearsal/inventory.yml \
  --limit rehearsal01 --extra-vars /protected/tuinstra-rehearsal-01/vars.yml \
  --as-admin --check configure-umami-restore
scripts/production-host-baseline \
  --inventory infra/ansible/rehearsal/inventory.yml \
  --limit rehearsal01 --extra-vars /protected/tuinstra-rehearsal-01/vars.yml \
  --as-admin configure-umami-restore
scripts/production-host-baseline \
  --inventory infra/ansible/rehearsal/inventory.yml \
  --limit rehearsal01 --extra-vars /protected/tuinstra-rehearsal-01/vars.yml \
  --as-admin verify-umami-restore
```

Controleer daarna dezelfde apply opnieuw: `changed` moet nul zijn. Bewijs verder
de admin/deploy-rechten, Docker-service en logpolicy, Caddy `404` via loopback,
root-only secretmappen en uitsluiting via
`/run/lock/tuinstra/operations.host.tuinstra-rehearsal-01.lock`. Verkeerde SHA,
repository, host, inventory, limit, sleutel of hostkey moet vóór mutatie falen.

Voor de synthetische Umami-oefening bewaart alleen de losse Lima-disk de
versleutelde Restic-repository. Verwijder de VM-rootdisk, maak de host leeg
opnieuw aan, pas hetzelfde profiel toe en herstel uitsluitend synthetische data.
De restore-adapter draait PostgreSQL met `--network none`; Umami deelt alleen
diens loopback-namespace en publiceert geen poort. Dit blokkeert externe effecten.

De huidige geïsoleerde adapter valideert data en health en ruimt zijn containers
weer op. Promotie naar persistente `/var/lib/tuinstra/umami` hoort bij de apart
beoordeelde productie-restore-uitvoering. De lokale extra disk bewijst bovendien
geen Sanctuary- of netwerk-failure-domain.

## Lokale contractcontrole

```sh
scripts/production-host-rehearsal-test.sh
make lint
make test
```

Stop en verwijder na de oefening alleen de eigen rehearsal-VM, extra disk en
tijdelijke credentials. Raak de Colima-VM niet aan.
