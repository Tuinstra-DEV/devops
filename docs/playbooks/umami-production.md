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

De image-referenties staan met immutable OCI-digests in de defaults van de
Ansible-rol. De Umami-digest hoort bij tag `3.3.1`; de PostgreSQL-digest bij
`15-alpine` (PostgreSQL 15.19 op het moment van vastleggen).

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

Kopieer het eenmalige beheerwachtwoord rechtstreeks naar een wachtwoordkluis;
laat het niet in shellgeschiedenis of logs terechtkomen. Op macOS kan dit zonder
terminaluitvoer:

```bash
ssh mtuinstra@vps01.tuinstra.dev \
  'sudo cat /etc/tuinstra/umami/admin-password' | pbcopy
```

Log in als `admin`. Umami blokkeert de rest van de applicatie totdat een
authenticator is gekoppeld. Sla de tien eenmalige herstelcodes buiten de server
op. De controle in dit draaiboek kan de globale verplichting bewijzen; alleen de
beheerder kan het daadwerkelijke TOTP-apparaat en de herstelcodes afronden.

## Back-upkoppeling

Een back-upadapter gebruikt een consistente `pg_dump` via service `db` en neemt
de Compose-configuratie, image-digests en bestanden onder
`/etc/tuinstra/umami` mee. Het live pad
`/var/lib/tuinstra/umami/postgres` wordt nooit als bestandenkopie geback-upt.
De algemene export-, transport-, retentie- en herstelimplementatie valt buiten
DEV-24 en wordt door de afzonderlijke back-upstory geleverd.

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
