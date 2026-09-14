# Productieback-ups naar Sanctuary

Dit draaiboek installeert een uitgaande pullketen voor productieback-ups. Sanctuary
maakt geen inkomende poort open. Een geplande cyclus triggert via een aparte
forced-command SSH-key een logische export op de productiehost, haalt het
versleutelde artifact op, controleert checksum en omvang, schrijft het naar een
eigen versleutelde Restic-repository en bevestigt ontvangst pas na `restic check`.

De eerste actieve adapter is `tuinstra-prod-01/umami`. Na de geverifieerde
Tracker 1.4.0-cutover wordt ook `tuinstra-prod-02/tracker` actief met een eigen
credential, repository en policy. De Sanctuary-installer activeert uitsluitend
de backup-policy en systemd-timers; hij start of wijzigt geen Tracker-container
of proxyroute. Sanctuary is alleen bestemming; zijn eigen workloads zijn geen
bron in deze keten.

## Vaste indeling en geheimen

De volgende bestanden worden buiten Git aangeleverd:

| Host | Bestand | Eigenaar/mode | Functie |
|---|---|---|---|
| Sanctuary | `/etc/tuinstra-backup/age-identity.txt` | `root:root 0600` | Ontsleuteling en onafhankelijk herstel |
| Sanctuary | `/etc/tuinstra-backup/restic-passwords/tuinstra-prod-01/umami.password` | `root:root 0600` | Losse Umami-repository |
| Sanctuary | `/etc/tuinstra-backup/restic-passwords/tuinstra-prod-02/tracker.password` | `root:root 0600` | Losse Tracker-repository |
| Sanctuary | `/etc/tuinstra-backup/ssh/prod01` en `prod02` | `root:root 0600` | Hostgebonden pull-keys voor alleen de vaste backupcyclus |
| Sanctuary | `/etc/tuinstra-backup/ssh/prod01-restore` | `root:root 0600` | Afzonderlijke identiteit voor de vaste productieherstel-RPC naar prod-01 |
| Sanctuary | `/etc/tuinstra-backup/ssh/known_hosts` | `root:root 0644` | Handmatig geverifieerde hostkeys |
| Productie | `/etc/tuinstra-backup/age-recipient.txt` | `root:root 0600` | Alleen de publieke, afgeleide age-recipient |

Bewaar de age-identity, het Restic-wachtwoord en alle drie SSH-identiteiten daarnaast in 1Password. De
acceptatietest gebruikt een vanuit 1Password opnieuw aangeleverde kopie. Laat
waarden nooit via command-line-argumenten, terminaloutput, Git of Tracker lopen.

De Sanctuary-installatie maakt de bestaande Umami-repository en policy
idempotent opnieuw vast en activeert daarnaast de allowlisted
`tuinstra-prod-02/tracker`-policy. Het onafhankelijke Restic-wachtwoord onder
`/etc/tuinstra-backup/restic-passwords/tuinstra-prod-02/tracker.password` wordt
eenmalig root-owned gegenereerd en nooit stilzwijgend vervangen. De eerste
Tracker-export valideert de productie-Composeconfiguratie, alle runtime
image-digests en de actieve release voordat hij een quiescence-window opent.

Voor de onafhankelijke credentialtest zet de 1Password-helper exact één tijdelijk JSON-bestand op
`/home/mtuinstra/.local/share/tuinstra-backup-recovery.json` (`mtuinstra:0600`). Voer daarna uit:

```bash
sudo /usr/local/sbin/tuinstra-backup-admin escrow-recovery-test
```

De vaste actie accepteert voor bestaande installaties het oude vier-veld-schema en voor nieuwe installaties
het vijf-veld-schema met de afzonderlijke productieherstel-identiteit. Hij kopieert de waarden naar een root-private tijdelijke map,
bewijst met beide teruggehaalde SSH-keys de beperkte `list`-toegang en voert de volledige geïsoleerde
Umami-restore uit met de teruggehaalde age- en Restic-credentials. Pas nadat het herstelbewijs duurzaam is
opgeslagen, verwijdert de actie de exacte user-readable stagingkopie. Bij een fout blijft deze stagingkopie
beschikbaar voor een gecontroleerde retry; tijdelijke root-kopieën worden altijd verwijderd. De JSON-uitvoer
bevat alleen host-, applicatie-, snapshot- en bewijsidentiteit.

Plaats de beoordeelde bundle exact onder
`/home/mtuinstra/tuinstra-backup-install`. Voeg de via de bestaande vertrouwde
SSH-verbinding gecontroleerde Ed25519-hostkeys toe als
`install-input/hostkeys/vps01.pub` en `vps02.pub`. Voer daarna één keer uit:

```bash
cd /home/mtuinstra/tuinstra-backup-install
sudo ./scripts/install-sanctuary-backups
```

De installer controleert mount en vrije ruimte voordat hij iets wijzigt. Daarna
installeert hij dependencies, maakt credentials idempotent, plaatst de vaste
engine en profielen, valideert sudoers vóór installatie en activeert alleen de
vaste timers. Private age-, Restic- en Ed25519-sleutels blijven root-owned mode
0600. De root-owned systemd-cyclus gebruikt deze sleutels; de niet-loginworker heeft geen secrettoegang.

De eerste installatie maakt één handoffbestand
`/home/mtuinstra/.local/share/tuinstra-backup-escrow.json` als `mtuinstra:0600`.
Een upgrade met een bestaande v1-marker maakt eenmalig een nieuwe vijf-veld-handoff en vervangt daarna
de marker door v2. Voer dit bestand op de Mac rechtstreeks via stdin aan
`backup/onepassword-escrow.py escrow --op <absoluut-op-pad> --vault <vault-id>`.
De helper maakt één beheerde Secure Note en vergelijkt de readback. Een bestaand beheerd item met vier
velden wordt uitsluitend uitgebreid met `prod01-restore`; het volledige JSON-item gaat via stdin en
de helper vergelijkt daarna alle vijf waarden. Verwijder het
handoffbestand op Sanctuary pas na die geslaagde readback. Een root-owned marker
voorkomt dat een herhaalde installatie ongemerkt een nieuwe leesbare kopie maakt.

De source-only `git archive` bevat bewust niet de twee externe hostkeybestanden.
Het getrackte `install-input/manifest.json` bevat hun SHA-256, eigenaar, mode,
hostnaam en verwachte Ed25519-fingerprint. Stage de bestanden alleen na een
vergelijking met de actuele `/etc/ssh/ssh_host_ed25519_key.pub` op beide
productiehosts via de bestaande strict-SSH-verbinding én met de lokale
`known_hosts`-entry. De bundle-installer voert daarna
`scripts/verify-install-inputs.py` uit vóór `apt-get`, accountcreatie of andere
mutaties. Een ontbrekende, symlinked, verkeerd geownerde, verkeerd gemodeerde of
afwijkende key stopt de installatie fail-closed.

Verifieer de productiehostkeys via de bestaande StrictHostKeyChecking-verbinding:
lees op iedere host `/etc/ssh/ssh_host_ed25519_key.pub` met sudo, vergelijk de
fingerprint met de al vertrouwde lokale `known_hosts`-entry en bouw daarna pas
`/etc/tuinstra-backup/ssh/known_hosts` op Sanctuary. Gebruik geen `ssh-keyscan`
als eerste vertrouwensbron.

## Installatie

1. Draai de eenmalige Sanctuary-installer en rond de 1Password-readback af.
2. Kopieer alleen `/var/lib/tuinstra-backup/public/{age-recipient.txt,prod01.pub,prod02.pub,prod01-restore.pub}` naar
   een lokale, niet-getrackte werkmap. Draai `infra/ansible/production-backup.yml` met
   `infra/ansible/production/backup-inventory.yml`. De inventory koppelt iedere
   host aan zijn vaste profiel; geef de werkmap mee als
   `production_backup_public_key_root`. Gebruik `mtuinstra` met sudo; root-SSH
   blijft uitgeschakeld.
3. Controleer `systemctl list-timers 'tuinstra-backup-*'`. Umami start dagelijks
   om 02:00 en Tracker om 03:00 Europe/Amsterdam, beide met maximaal tien
   minuten willekeurige spreiding.

De Ansible-rol `infra/ansible/sanctuary-backup.yml` beschrijft dezelfde toestand
voor latere herhaalbare profieluitvoering. De eerste installatie gebruikt het
enkele interactieve sudo-commando omdat Sanctuary nog geen beperkte beheerroute
heeft.

Het producerprofiel staat root-owned onder `/etc/tuinstra-backup/producer.json`.
De productie-spool heeft een harde limiet van 10 GiB. Alleen `.age`-payloads staan
in de spool; ontvangstbewijzen worden afzonderlijk bewaard. Bij quota, volle
schijf, offline Sanctuary of onderbroken overdracht stopt de cyclus zonder ACK en
blijft het artifact beschikbaar voor een volgende run.

## Dagelijks gebruik

Een volledige, duurzame cyclus:

```bash
sudo /usr/local/sbin/tuinstra-backup-admin run
sudo /usr/local/sbin/tuinstra-backup-admin run tracker
```

Alleen een resultaat met `snapshot_id`, `stored_at`, `integrity_checked_at` en
`integrity_coverage=full-repository-data` telt als geslaagde externe back-up. `export` betekent
alleen dat lokaal versleuteld bronmateriaal klaarstaat.

Een mislukte bronexport wordt in de Sanctuary-catalogus als `source_export_failed` vastgelegd met
een beperkte `stage_code`, bijvoorbeeld `application-contract`, `compose-contract`, `runtime-evidence`, `object-inventory`,
`quiescence`, `database-export` of `config-metadata`. De stagecode bevat geen remote fouttekst,
command-output of geheimen.

Inspecteer secretvrije status:

```bash
sudo /usr/local/sbin/tuinstra-backup-admin status
```

Voer voor Tracker een volledige extra integriteitscontrole uit met
`sudo /usr/local/sbin/tuinstra-backup-admin check tracker`. Retentie draait apart als root, maakt eerst een
dry-runbewijs, weigert een plan zonder bewaard herstelpunt en past daarna de
actieve policy toe. De standaard is 7 dagelijkse, 4 wekelijkse en 12 maandelijkse
herstelpunten. Restic krijgt expliciet `--group-by ''`, zodat de unieke
artifactpaden samen één retentiegroep vormen. De synthetische integratietest in
`scripts/restic-retention-integration-test.sh` bewijst dit met 26 verschillende
bronpaden over dag-, week- en maandgrenzen. Een punt met tag
`tuinstra:production-restore-safety` blijft altijd bewaard totdat de expliciete
hersteloperatie die tag verwijdert.

De primaire timer gebruikt de goedgekeurde lokale starttijd. Een tweede timer
loopt 75 minuten later en gebruikt `ensure-daily`: als die lokale kalenderdag al
een duurzaam en volledig gecontroleerd punt van maximaal twaalf uur oud heeft,
doet hij niets. Daardoor
vangt de fallback ook de niet-bestaande 02:00 tijdens de zomertijdwissel op,
zonder normale dagen dubbel te back-uppen.

Lees het secretvrije cataloguscontract met:

```bash
sudo /usr/local/sbin/tuinstra-backup-admin catalog
```

Het catalogusbestand bewaart maximaal 500 punten met volledige Restic-ID,
artifact-ID, bron, bestemming, trigger, policy, checksum, omvang en controle- en
opslagtijden. Retentieverwijderingen blijven als expliciete tombstones zichtbaar.
`latest_attempt` staat los van eerder geslaagde punten en meldt een lopende,
geslaagde, mislukte of na vier uur onzekere enginepoging met alleen een begrensde
foutcode.

## Begrensde policywijziging

Console levert later alleen host/app, versie, planhash en begrensde velden aan.
Frequentie blijft dagelijks en tijdzone blijft Europe/Amsterdam. Uur is 0–23,
minuut 0–59, daily 1–31, weekly 1–52 en monthly 1–24. Een versie kan niet met
andere inhoud worden hergebruikt. De helper schrijft policy en systemd-units
atomisch; vrije cronregels, paden en commando's zijn geen invoer.

Gebruik `policy-reconcile` uitsluitend via de root-owned operation adapter. Een
voorbeeld van de standaardwaarden staat als Ansible-taak in de Sanctuary-rol.

## Geïsoleerde hersteltest

```bash
sudo /usr/local/sbin/tuinstra-backup-admin restore-test latest
```

De helper controleert Restic, de buitenste SHA-256, de versleutelde interne
manifestchecksums en de vaste app-identiteit. Daarna herstelt hij PostgreSQL 15
en Umami 3.3.1 in één gedeelde netwerknamespace met alleen loopback: PostgreSQL
draait met `--network none` en Umami deelt uitsluitend die namespace. Er is geen
bridge, hostgateway, hostpoort of publieke Caddy-route. Hij vergelijkt een tijdens export vastgelegde
checksum van de admin- en tweefactorstatus met dezelfde query na herstel, bindt de afzonderlijk opgeslagen
encryptiesleutel aan de herstelde applicatieconfiguratie, doorloopt adminlogin plus TOTP en eist een gezonde
`/api/heartbeat`. Daarna ruimt hij alleen zijn eigen tijdelijke containers op en bewaart
secretvrij herstelbewijs onder `/var/lib/tuinstra-backup/restore-evidence`.
Het bewijs noemt het volledige snapshot-ID, de werkelijk gecontroleerde sleutel,
checksum, compatibiliteit en minimumcapaciteit, de database- en applicatiecontroles,
de gebruikte image-digests, netwerkisolatie, opruiming en totale duur. Een geslaagde
status wordt pas atomair geschreven nadat containers en tijdelijke werkruimte
aantoonbaar verwijderd zijn. Een opruimfout maakt de hele hersteltest mislukt.
De datacontrole leest het versleutelde 2FA-seed en het beheerderswachtwoord via
een anonieme pipe, ontsleutelt het seed uitsluitend in de geïsoleerde Umami-container,
maakt daar een verse TOTP en doorloopt login, 2FA en identiteitscontrole via
loopback. Seed, wachtwoord, TOTP, tokens en HTTP-responses worden niet geschreven
of gelogd.

Voor Tracker gebruikt de geïnstalleerde `restore-tracker`-adapter PostgreSQL
17 met de vastgelegde digest, herstelt hij de Doctrine-migratieledger en
reconcilieert hij elk object uit het tijdens de quiescence-window vastgelegde
S3-objectmanifest op sleutel, omvang en SHA-256. Het getarbalde volledige MinIO-
dataroot is transportmateriaal; MinIO's interne `xl.meta`- en part-bestanden
worden niet als objectinhoud geïnterpreteerd. De test start geen Tracker-webapp, workers of
externe endpoints; de applicatie-healthstatus blijft daarom expliciet
`not-run-external-effects-blocked`. De productie-export stopt de geconfigureerde
Tracker-services alleen gedurende de quiescence-window (maximaal 120 seconden)
en start daarna uitsluitend services die vóór de export actief waren. Een
herstelbewijs is geen vervanging voor een gecontroleerde restore naar de echte
productiedoelen.

De native Tracker-gate maakt daarvoor een volledig synthetische PG17-dump en
een MinIO-object met de vastgelegde images en voert daarna dezelfde adapter uit:

```bash
sudo ./scripts/test_tracker_restore_native.sh
```

Deze gate gebruikt alleen een tijdelijke map onder `/run/tuinstra-backup` en
verwijdert haar containers en fixturedata bij afsluiten.

Een volledige acceptatie-oefening gebruikt een lege geïsoleerde doelomgeving en
de recoverycredentials uit 1Password. Noteer begin/eindtijd; het doel is herstel
binnen vier uur en maximaal circa 24 uur dataverlies bij een gezonde keten.

## Console-operation contract

De publieke operation adapters zijn `backup.run`, `backup.check`,
`backup.restore_test` en `backup.retention_reconcile`. Ze accepteren alleen vaste
host/app-ID's en getypeerde velden. `backup.run` is de volledige
export→pull→snapshot→check→ACK-keten; de UI mag een losse export nooit als veilig
op Sanctuary presenteren. Publieke artifactmetadata bevat alleen schema-,
artifact-, host-, app-, adapter-, tijd-, SHA-256- en omvangvelden.

Back-up, controle, retentie, herstel en profieluitvoering gebruiken dezelfde
autoritatieve hostlock: `/run/lock/tuinstra/operations.host.<host>.lock`,
`root:tuinstra-ops 0660`. De hostlock wordt altijd vóór applicatie- of
Restic-locks genomen en blijft gedurende de hele operatie vast. Geplande
back-ups blijven daarmee ook zonder Console veilig coördineren.

## Vaste productieherstel-adapters

De productieherstel-orchestrator gebruikt twee extra root-only engineacties. Ze
accepteren alleen allowlisted host/app-ID's, een begrensd operation-ID en vaste
doelen; er is geen pad- of commando-invoer.

`safety-pull --host HOST --app APP --artifact UUID --operation ID --run-id UUID`
haalt exact één voorbereid artifact via de bestaande beveiligde overdracht op, maakt een
volledig gecontroleerd Restic-snapshot en zet bij de eerste opslag de tags
`tuinstra:production-restore-safety` en `operation:<ID>`. Een bestaand artifact
zonder exact die pin-identiteit wordt geweigerd.

`materialize --host HOST --app APP --snapshot FULL64 --operation ID --purpose restore|rollback`
haalt exact één beschikbaar cataloguspunt op, controleert catalogus, buitenste
checksum, versleuteld manifest en alle inputchecksums, en publiceert de ontsleutelde
payload atomair onder de root-owned materialisatieroot uit het vaste profiel. De
JSON-respons bevat geen pad of secret. Herhaling is alleen toegestaan voor exact
dezelfde operation/snapshot/purpose-binding. Het materiaal blijft root-only staan
tot de productiehersteloperatie expliciet wordt afgerond, zodat een crash kan
worden gereconcilieerd en rollback mogelijk blijft.

De dagelijkse cyclus draait als root-owned, vast geconfigureerde systemd-service. De engine accepteert
geen publieke `ingest`- of `attempt-finish`-primitives en de installer verwijdert het oude
wildcardvormige sudoersbestand `93-tuinstra-backup-ingest`. Een pull-account kan daardoor geen geslaagde
status schrijven zonder een exact, volledig gecontroleerd Restic-snapshot voor dezelfde run.

Het versleutelde interne manifest bevat de werkelijk draaiende image-digests, de PostgreSQL-serverversie
en de gebruikte `pg_dump`-versie. Export stopt wanneer een draaiende container niet overeenkomt met de
root-owned allowlist van goedgekeurde digests.
