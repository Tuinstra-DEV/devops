# Productieback-ups naar Sanctuary

Dit draaiboek installeert een uitgaande pullketen voor productieback-ups. Sanctuary
maakt geen inkomende poort open. Een geplande cyclus triggert via een aparte
forced-command SSH-key een logische export op de productiehost, haalt het
versleutelde artifact op, controleert checksum en omvang, schrijft het naar een
eigen versleutelde Restic-repository en bevestigt ontvangst pas na `restic check`.

De eerste actieve adapter is `tuinstra-prod-01/umami`. Prod-02 bevat nog geen
geverifieerde productieapp en rapporteert daarom `not-applicable`. Sanctuary is
alleen bestemming; zijn eigen workloads zijn geen bron in deze keten.

## Vaste indeling en geheimen

De volgende bestanden worden buiten Git aangeleverd:

| Host | Bestand | Eigenaar/mode | Functie |
|---|---|---|---|
| Sanctuary | `/etc/tuinstra-backup/age-identity.txt` | `root:root 0600` | Ontsleuteling en onafhankelijk herstel |
| Sanctuary | `/etc/tuinstra-backup/restic-passwords/tuinstra-prod-01/umami.password` | `root:root 0600` | Losse Umami-repository |
| Sanctuary | `/etc/tuinstra-backup/ssh/prod01` en `prod02` | `tuinstra-backup:tuinstra-backup 0600` | Hostgebonden pull-keys voor alleen de backupworker |
| Sanctuary | `/etc/tuinstra-backup/ssh/known_hosts` | `root:tuinstra-backup 0644` | Handmatig geverifieerde hostkeys |
| Productie | `/etc/tuinstra-backup/age-recipient.txt` | `root:root 0600` | Alleen de publieke, afgeleide age-recipient |

Bewaar de age-identity en het Restic-wachtwoord daarnaast in 1Password. De
acceptatietest gebruikt een vanuit 1Password opnieuw aangeleverde kopie. Laat
waarden nooit via command-line-argumenten, terminaloutput, Git of Tracker lopen.

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
0600. Alleen de niet-loginworker kan de hostgebonden SSH-sleutels lezen.

De eerste installatie maakt één handoffbestand
`/home/mtuinstra/.local/share/tuinstra-backup-escrow.json` als `mtuinstra:0600`.
Voer dit bestand op de Mac rechtstreeks via stdin aan
`backup/onepassword-escrow.py escrow --op <absoluut-op-pad> --vault <vault-id>`.
De helper maakt één beheerde Secure Note en vergelijkt de readback. Verwijder het
handoffbestand op Sanctuary pas na die geslaagde readback. Een root-owned marker
voorkomt dat een herhaalde installatie ongemerkt een nieuwe leesbare kopie maakt.

Verifieer de productiehostkeys via de bestaande StrictHostKeyChecking-verbinding:
lees op iedere host `/etc/ssh/ssh_host_ed25519_key.pub` met sudo, vergelijk de
fingerprint met de al vertrouwde lokale `known_hosts`-entry en bouw daarna pas
`/etc/tuinstra-backup/ssh/known_hosts` op Sanctuary. Gebruik geen `ssh-keyscan`
als eerste vertrouwensbron.

## Installatie

1. Draai de eenmalige Sanctuary-installer en rond de 1Password-readback af.
2. Kopieer alleen `/var/lib/tuinstra-backup/public/{age-recipient.txt,prod01.pub,prod02.pub}` naar
   een lokale, niet-getrackte werkmap. Draai `infra/ansible/production-backup.yml` met
   `infra/ansible/production/backup-inventory.yml`. De inventory koppelt iedere
   host aan zijn vaste profiel; geef de werkmap mee als
   `production_backup_public_key_root`. Gebruik `mtuinstra` met sudo; root-SSH
   blijft uitgeschakeld.
3. Controleer `systemctl list-timers 'tuinstra-backup-*'`. Umami start dagelijks
   om 02:00 Europe/Amsterdam met maximaal tien minuten willekeurige spreiding.

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
```

Alleen een resultaat met `snapshot_id`, `stored_at`, `integrity_checked_at` en
`integrity_coverage=full-repository-data` telt als geslaagde externe back-up. `export` betekent
alleen dat lokaal versleuteld bronmateriaal klaarstaat.

Inspecteer secretvrije status:

```bash
sudo /usr/local/sbin/tuinstra-backup-admin status
```

Voer een volledige extra integriteitscontrole uit met
`sudo /usr/local/sbin/tuinstra-backup-admin check`. Retentie draait apart als root, maakt eerst een
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
bridge, hostgateway, hostpoort of publieke Caddy-route. Hij eist publieke databasetabellen en een gezonde
`/api/heartbeat`, ruimt alleen zijn eigen tijdelijke containers op en bewaart
secretvrij herstelbewijs onder `/var/lib/tuinstra-backup/restore-evidence`.

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
