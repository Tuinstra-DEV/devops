# Prod-01 — bewijs van serverbasis (DEV-26)

11 september 2026. Host `tuinstra-prod-01`, DNS `vps01.tuinstra.dev`, CX23.
Ubuntu 26.04.1 LTS. Dit betreft uitsluitend de gevraagde voorbereiding; geen
site, applicatiedata of DNS gemigreerd. Firewall en back-ups zijn op uitdrukkelijk
verzoek uitgesteld en blijven open vóór migratieacceptatie.

## Uitgevoerde controles

| Controle | Resultaat |
|---|---|
| Bootstrap | 19 taken, 10 wijzigingen, geen fouten |
| Eigen SSH-login `mtuinstra` en sudo | Onafhankelijk geslaagd vóór hardening |
| Docker/Compose | 29.8.0 / 5.5.1, vastgelegde apt-versies |
| Caddy | 2.11.4, image op manifestdigest vastgelegd, lege HTTP-route geeft 404 |
| SSH-hardening | Root/wachtwoord/keyboard-interactive uit, publickey aan |
| Negatieve root-login | Expliciet geweigerd na hardening en reboot |
| Deploy-key | Alleen forced command; `id` geweigerd met status 64 |
| Ontbrekende app | `status site-tuinstra` weigert ontbrekende root-owned Compose met status 66 |
| Herhaalrun configure | 40 taken, 0 wijzigingen, geen fouten |
| Gecontroleerde reboot | Geslaagd, herstel na 119 seconden |
| Verify na reboot | 14 taken, 0 wijzigingen, geen fouten |
| Containerherstel | Caddy, Console-agent en Docker-proxy automatisch terug |
| Schijfruimte na inrichting | circa 30 GiB beschikbaar op rootdisk |
| Console Docker-inventaris | Door hub geaccepteerd; hostcredential wordt gebruikt |
| Docker-proxy mutatiegrens | POST naar synthetisch, niet-bestaand doel geeft HTTP 405 |

Appconfiguratie komt onder `/var/www/<app>`; platform onder `/var/www/_platform`.
Secrets staan in root-only mappen onder `/etc/tuinstra`; duurzame data onder
`/var/lib/tuinstra`. Er is geen algemene Docker-/sudo-toegang voor deploy.
Alleen Caddy publiceert poort 80; er zijn nog geen domeinroutes of certificaten.

## Console: expliciete resterende beperking

De agent gebruikt de huidige Console-release uit Sanctuary, met gecontroleerde
imageconfiguratie, filesystemlagen en platform. Het verschillende image-ID tussen
de klassieke Docker-store en containerd is gecontroleerd; lege/defaultvelden zijn
genormaliseerd, inhoudelijke wijzigingen worden geweigerd.

De beperkte mounts omvatten slechts drie host-metriekbestanden en een lege
capaciteitsmap op hetzelfde filesystem. Geen host-/etc, volledige /proc, back-ups
of Docker-volumedata zijn aan de agent gemount. Back-upcollectie staat uit.

Capaciteitsrapportage wordt door een bestaande Console-contractbug afgewezen:
agent `mount.used_bytes` tegenover hub `mount.bytes_used`. Docker-inventaris werkt,
maar volledige CPU/RAM/schijfbewaking is dus **niet geaccepteerd**. De fout blijft
zichtbaar en is met bronbewijs aan DEV-18 toegevoegd. Er is geen ondersteunde
configuratieworkaround die alle capaciteit behoudt. Geen Console-productcode is
als onderdeel van deze bootstrap aangepast.

## Lokale verificatie en levering

`make lint` en `make test` geslaagd, inclusief 93 runner-tests, 16 bestaande
classificatietests, productiecontract en 9 enrollment/integriteitstests. Alle
Ansible-playbooks syntaxgecontroleerd; alle templates daadwerkelijk lokaal
gerenderd en Bash/shebang gecontroleerd. Packer ontbreekt: bestaande formattercheck
overgeslagen. Cross-repository Dependabot-fleetcheck vereist aparte context en is
niet uitgevoerd; deze wijziging raakt die routing niet.

De eerste apply vond een Jinja/Bash-array-templatefout; die is gecorrigeerd en als
lokale renderregressie afgedekt vóór de geslaagde herhaalrun. Geen appdata aanwezig
tijdens deze correctie. De grote agentimage is gecomprimeerd over SSH overgebracht;
geen registrycredential is naar de hosts gekopieerd en geen token/private key
staat in Git of uitvoer.

DEV-26 blijft Started: migratie van de vier sites, hun DNS/TLS, Umami-handoff,
netwerkbeleid, back-up/restore en volledige monitoringacceptatie blijven open.
