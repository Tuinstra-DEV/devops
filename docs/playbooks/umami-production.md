# Umami op tuinstra-prod-01

Dit draaiboek installeert Umami 3.3.1 met PostgreSQL 15 op
`tuinstra-prod-01`. Het publiceert alleen Caddy op poorten 80 en 443. De
applicatie en database hebben geen hostpoorten.

## Beheerd contract

- Compose: `/var/www/umami/compose.yml`, project `umami`.
- Secrets: `/etc/tuinstra/umami`, alleen leesbaar door root.
- PostgreSQL-data: `/var/lib/tuinstra/umami/postgres`.
- Domein: `umami.tuinstra.dev`.
- Edge-netwerk: `tuinstra-edge`; alleen Umami en Caddy delen dit netwerk.
- Database: service `db`, database en gebruiker `umami`.
- Healthcheck: `/api/heartbeat`.
- Actieve analyticsretentie: 90 dagen, dagelijks afgedwongen door
  `umami-retention.timer`.

De image-referenties staan met immutable OCI-digests in de defaults van de
Ansible-rol. De Umami-digest hoort bij tag `3.3.1`; de PostgreSQL-digest bij
`15-alpine` (PostgreSQL 15.19 op het moment van vastleggen).

## Retentie van actieve analyticsdata

De root-only job `/usr/local/sbin/tuinstra-umami-retention` verwijdert actieve
analyticsdata die strikt ouder is dan 90 dagen. Records exact op de vaste cutoff
blijven staan. Een `NULL`-datum is niet aantoonbaar binnen de termijn en wordt
daarom verwijderd uit de gedateerde analyticstabellen. De job omvat saved
replays, replay-chunks, heatmap-events, revenue, event- en session-data,
session-links, website-events en daarna alleen oude sessions waarnaar geen van
die tabellen nog verwijst. Account-, website-, team-, rapport-, segment-, board-,
share-, link-, pixel- en applicatieconfiguratie worden nooit door deze job
verwijderd.

De cutoff wordt eenmaal per run vastgelegd. Deletes lopen in transacties van
maximaal 5.000 records. De job controleert vóór de eerste delete en vóór iedere
batch het exacte Umami 3.3.1-/PostgreSQL 15-schema en stopt bij afwijking. Een
hostlock en een PostgreSQL advisory lock voorkomen overlappende runs. De timer
start dagelijks om 04:15 `Europe/Amsterdam`, na het back-upvenster, met maximaal
15 minuten willekeurige vertraging en `Persistent=true` voor een gemiste run.

Een read-only controle valideert schema en aantallen zonder data te wijzigen:

```bash
sudo /usr/local/sbin/tuinstra-umami-retention --check
```

Een succesvolle echte run schrijft alleen cutoff, voltooiingstijd en aantallen
naar het root-only bestand
`/var/lib/tuinstra/umami/retention/last-success.json`. Dezelfde aggregate
metadata staat in de systemd-journal; URL's, sessie-ID's en eventdata worden niet
gelogd. Dit successbestand en de journal zijn inspectiebewijs, geen actieve
monitoring of alarmering.

## Installeren en controleren

Controleer eerst dat `umami.tuinstra.dev` naar `88.198.156.175` wijst. Voer
vervolgens vanuit deze repository uit:

```bash
./scripts/production-umami deploy
./scripts/production-umami verify
```

De deploy is herhaalbaar. Ontbrekende secrets worden op de server gegenereerd
en bestaande secrets worden behouden. Vóór de Caddy-route wordt geplaatst,
vervangt de bootstrap de standaardlogin `admin`/`umami` door een willekeurig
wachtwoord en stelt hij de globale 2FA-verplichting in.

## Login en 2FA naar 1Password overdragen

Gebruik bij voorkeur de begrensde handoff nadat de deploy met de laatste
DEV-24-versie opnieuw is uitgevoerd. Zoek in 1Password de ID van het gekozen
vault en voer uit:

```bash
./scripts/umami-onepassword-handoff.py \
  --op /absoluut/pad/naar/op \
  --vault '<26-teken-vault-id>'
```

De tool beheert uitsluitend het exacte Login-item `Umami — prod01`. Het leest
alleen metadata om een dubbele titel te voorkomen en daarna alleen dat eigen
item. Het wachtwoord, de TOTP-seed, tokens en herstelcodes reizen via stdin en
procesgeheugen; succesvolle uitvoer bevat alleen het item-ID en booleans. De
desktopintegratie van 1Password verzorgt de gebruikersauthenticatie.

De handoff slaat eerst de seed in het Login-item op en controleert dat 1Password
een OTP kan genereren. Pas daarna bevestigt hij 2FA bij Umami. Vervolgens worden
de tien herstelcodes direct als verborgen veld opgeslagen en wordt een verse
2FA-login getest. Bij een onzekere vaultmutatie maakt hij geen tweede item. Als
alleen het opslaan van reeds uitgegeven herstelcodes mislukt, bewaart hij die in
een expliciet gemeld tijdelijk bestand met modus `0600`; verwerk en verwijder
dat bestand daarna handmatig.

Een bestaande, niet door deze tool beheerde 2FA-configuratie wordt nooit gereset.
Handmatige enrollment via de Umami-interface en een eigen authenticator blijft
mogelijk, maar gebruik dan niet hetzelfde beheerde item zonder de staat eerst te
controleren.

## Back-upkoppeling

Een back-upadapter gebruikt een consistente `pg_dump` via service `db` en neemt
de Compose-configuratie, image-digests en bestanden onder
`/etc/tuinstra/umami` mee. Het live pad
`/var/lib/tuinstra/umami/postgres` wordt nooit als bestandenkopie geback-upt.
De algemene export-, transport-, retentie- en herstelimplementatie valt buiten
DEV-24 en wordt door de afzonderlijke back-upstory geleverd.

De 90 dagen hierboven gelden voor de actieve Umami-database. Versleutelde
back-ups hebben een afzonderlijke retentie en kunnen daardoor al verwijderde
analyticsdata blijven bevatten totdat die back-ups volgens hun eigen beleid
vervallen. Een restore van zo'n back-up kan oudere data opnieuw introduceren.
Houd daarom maintenance actief, start nog geen publiek verkeer en voer direct na
het terugzetten eerst de schema-check en daarna de echte purge uit:

```bash
sudo /usr/local/sbin/tuinstra-umami-retention --check
sudo /usr/local/sbin/tuinstra-umami-retention
sudo systemctl start umami-retention.timer
```

Controleer de aggregate successstatus voordat de maintenance-route wordt
opgeheven. Bij schema-afwijking blijft maintenance actief totdat de retention job
voor de herstelde Umami-versie is beoordeeld; omzeil de fail-closed controle niet.

## Herstel of terugrol

Bij een mislukte eerste installatie blijft de Caddy-route afwezig zolang de
veilige admin-bootstrap niet is afgerond. Inspecteer dan zonder secrets te
tonen:

```bash
ssh mtuinstra@vps01.tuinstra.dev \
  'sudo docker compose --project-directory /var/www/umami \
    --file /var/www/umami/compose.yml ps'
```

Voor een applicatierollback wordt alleen de pinned Umami-image aangepast en de
deploy opnieuw uitgevoerd. Een databaseversie met migraties wordt pas
teruggedraaid nadat een geteste logische restore beschikbaar is.

Bij een rollback van de retentieautomatisering kan de timer zonder dataverlies
worden uitgezet met `sudo systemctl disable --now umami-retention.timer`. Dit
herstelt reeds conform beleid verwijderde data niet; daarvoor is een beoordeelde
restore nodig, gevolgd door een onmiddellijke purge vóór heropening van verkeer.
