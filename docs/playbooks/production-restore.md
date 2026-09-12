# Productieherstel

DEV-34 voegt een begrensde herstelketen voor `tuinstra-prod-01/umami` toe. De
keten is niet bedoeld voor handmatige shellcommando's en is niet actief totdat
de Console-worker, geslaagde geïsoleerde herstelproef en afzonderlijk
geprovisioneerde hersteltransportkey zijn gecontroleerd.

## Vaste binding

Elke opdracht bindt aan één host, applicatie, volledige Restic snapshot-ID,
operatie-ID en planhash. `latest`, korte snapshot-ID's, paden en vrije
commando's worden geweigerd. Alleen een beschikbaar herstelpunt met een
geslaagde, exact gebonden oefenrestore kan de preflight passeren.

Sanctuary gebruikt een aparte key voor de forced-command account
`tuinstra-restore`. De bestaande `tuinstra-backup-pull` key blijft export-only.
Console bezit geen SSH-key en geen Docker socket.

## Herstelvoorwaarde op een lege server

Richt de lege host eerst in met het exact beoordeelde productieprofiel
`2026.09.12.1` uit broncommit
`37954b00b5a83a0194a067a2760211a9b3a230bc`. Dat profiel maakt de Caddy
edge-route en het netwerk, de Compose-hulpbestanden en de vaste mappen opnieuw
aan. Controleer daarna de profielafwijkingen en start pas vervolgens de
applicatierestore. De applicatieback-up dupliceert deze deterministische
profielbestanden niet; een ontbrekende root-owned Caddy-route laat de
restorepreflight gesloten falen.

Productieactivatie blijft geblokkeerd totdat de profielworker deze vaste
volgorde daadwerkelijk uitvoert: hostbaseline, het gebonden `production_umami`
app-profiel, profielcontrole en pas daarna DEV-34. Alleen de hostbaseline is
niet voldoende voor herstel op een lege server.

Bij de eerste installatie of een lege doelhost is de herhaalbare volgorde:

1. Gebruik broncommit `37954b00b5a83a0194a067a2760211a9b3a230bc` en voer op `prod01`
   `configure`, `configure-umami-restore`, `verify` en `verify-umami-restore` uit. Draai de twee
   configure-stappen eerst met `--check`.
2. Gebruik daarna de beoordeelde DEV-34-releasebron en voer `configure` en `verify` opnieuw uit.
   Deze tweede baseline-installatie is nodig omdat DEV-34 de gedeelde
   `/usr/local/sbin/tuinstra-compose-deploy` uitbreidt met dezelfde hostlock en maintenance-weigering.
3. Voer met diezelfde DEV-34-releasebron `infra/ansible/production-backup.yml` eerst met
   `--check --diff` en daarna zonder checkmodus uit. Geef de vaste lokale public-key-map mee als
   `production_backup_public_key_root`; die map bevat ook `prod01-restore.pub`.
4. Controleer opnieuw beide profielverificaties en verifieer dat back-up, deploy en restore dezelfde
   `/run/lock/tuinstra/operations.host.tuinstra-prod-01.lock` gebruiken. Activeer de Console-actie pas
   na enrollment van het beperkte worker-token en de afzonderlijke forced-command restore-account.

Met de wrapper uit commit `37954b00b5a83a0194a067a2760211a9b3a230bc` zijn stap 1 en de
baseline-herhaling uit stap 2 concreet:

```bash
./scripts/production-host-baseline --as-admin --check configure
./scripts/production-host-baseline --as-admin configure
./scripts/production-host-baseline --as-admin --check configure-umami-restore
./scripts/production-host-baseline --as-admin configure-umami-restore
./scripts/production-host-baseline --as-admin verify
./scripts/production-host-baseline --as-admin verify-umami-restore
```

## Herstelvolgorde

1. `preflight` materialiseert de exacte snapshot in een root-owned stagingroot,
   controleert manifest, checksums, image-digests en oefenrestorebewijs en
   verstuurt een vaste bundle naar het doel.
2. `prepare` schrijft een root-owned maintenance marker, activeert de Caddy
   onderhoudsroute, stopt de Umami-writes en maakt bij een bestaand doel een
   verse applicatie-export.
3. Sanctuary slaat die export op met de tags
   `tuinstra:production-restore-safety` en `operation:<operation-id>`, voert een
   volledige Restic integriteitscontrole uit en legt de door Restic teruggegeven
   volledige snapshot-ID in het journal vast.
4. Pas daarna stopt `apply` PostgreSQL. De adapter herstelt eerst naar een nieuw
   datadoel, vervangt Compose en de vaste secretbestanden atomair en start de
   stack. Verkeer wordt pas hervat nadat de Umami heartbeat en een
   databasecontrole slagen.
5. Bij een fout blijft maintenance actief. Een onzekere transportuitkomst wordt
   niet opnieuw uitgevoerd; `status` moet de doeljournal eerst reconciliëren.
6. `rollback` vereist een nieuwe, exacte planhash en gebruikt uitsluitend de
   safety snapshot-ID uit hetzelfde operation journal. De safety tag blijft
   staan totdat de operatie expliciet is opgelost.

De buitenste hostlock is op Sanctuary en op het productiedoel
`/run/lock/tuinstra/operations.host.<host>.lock`. De file is root-owned, mode
`0660`, wordt zonder symlink-follow geopend en krijgt een niet-blokkerende
exclusieve `flock`. Deploys gebruiken dezelfde lock en weigeren zolang de
operationele maintenance marker bestaat.

## Herhaalbare controle

Voer lokaal eerst de vaste controles uit:

```bash
make lint
make test
./scripts/production-restore-empty-target-integration-test.sh
```

De Docker-rehearsal gebruikt per run unieke container- en Compose-namen. Hij
herstelt een echte Umami 3.3.1/PostgreSQL 15 dataset naar een leeg tijdelijk
doel en bewijst dat een corrupte dump wordt afgewezen voordat het doel wordt
vervangen. Hij raakt geen productiehost, bestaande lokale Compose-projecten of
publieke route.

De live bediening blijft uit totdat de volledige Console-authenticatie,
operation-bound approval, workerbinding en deze rehearsal samen zijn
geaccepteerd. Een echte productierestore wordt nooit als installatiecontrole
uitgevoerd.

Wanneer Console niet beschikbaar is, biedt het root-geïnstalleerde commando
`tuinstra-production-restore` dezelfde vaste preflight-, status-, apply- en
rollbackhandelingen aan de `mtuinstra`-operator. Apply en rollback vereisen het
exact getypte doel `umami / tuinstra-prod-01 / production`; sudo vraagt bij
iedere aanroep opnieuw om het besturingssysteemwachtwoord. De wrapper zet
configuratie, host en applicatie vast en accepteert alleen volledige snapshot-
en planhashes met een gevalideerde operation-ID.
