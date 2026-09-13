# Beperkte profieluitvoering op productiehosts

Console gebruikt voor serverprofielen een apart SSH-account:
`console-profile-executor`. Dit account heeft geen beheershell. De enige
geautoriseerde sleutel heeft een vaste forced command en kan uitsluitend een
`tuinstra-profile-v1` verzoek doorgeven aan de root-owned dispatcher. Voeg deze
sleutel nooit toe aan `mtuinstra` of `deploy`; hun bestaande public-keysets
blijven afzonderlijke, complete allowlists.

## Publicatiecontract

Publiceer eerst een schoon, detached checkout van de beoordeelde DevOps-SHA in:

```text
/var/lib/tuinstra-profile-executor/staging/<source-sha>
```

Plaats daarnaast alleen deze root-staged invoer:

```text
/var/lib/tuinstra-profile-executor/staging/profile-worker.pub
/var/lib/tuinstra-profile-executor/staging/<host>/vars.yml
/var/lib/tuinstra-profile-executor/staging/<host>/admin.pub
/var/lib/tuinstra-profile-executor/staging/<host>/deploy.pub
```

`admin.pub` bevat alle bestaande, goedgekeurde persoonlijke beheersleutels.
`deploy.pub` bevat uitsluitend de bestaande deploysleutel. De installer stopt
als de beperkte profielsleutel in een van beide bestanden staat.

Voer de installer als root uit met uitsluitend de beoordeelde waarden uit de
Console-profielversie en de change-approval:

```bash
scripts/install-production-profile-executor \
  tuinstra-prod-01 <source-sha> <profile-version> <profile-content-hash> \
  baseline_umami_restore change-<approval-id>
```

De vaste registry accepteert `tuinstra-prod-01/baseline_umami_restore`,
`tuinstra-prod-02/baseline` en de geïsoleerde
`tuinstra-rehearsal-01/baseline_umami_restore`. Een andere host, procedure,
inventory, pad of command kan niet via het netwerk worden gekozen.

De installatie maakt een locked-password account, forced authorized key, een
exacte no-argument sudo-regel, een root-owned immutable bundle en een
root-owned profielallowlist. Controleer na publicatie `sshd -T`, `visudo -c`,
de bestandseigenaren en de gepinde host key voordat de Console-worker wordt
ingeschakeld. De installer start geen applicatiecontainers en maakt geen
applicatiesecrets.

## Uitvoering en herstel

Een check voert de vaste baseline uit in Ansible check mode. Voor prod-01 en de
rehearsal volgt daarna de inert Umami-herstelvoorbereiding. Apply herhaalt eerst
exact dezelfde check onder de hostlock en vergelijkt de evidence-hash met het
goedgekeurde plan. Alleen bij een exacte match volgen configuratie, verificatie
en een zero-change herhaling.

De autoritatieve lock is
`/run/lock/tuinstra/operations.host.<host>.lock`. De dispatcher houdt deze
non-blocking lock gedurende de volledige check of apply. Back-up-, restore- en
deployhelpers gebruiken dezelfde buitenste lock voordat ze een app- of
Restic-lock nemen.

Resultaten worden atomair en met `fsync` gejournaled op job- en planhash. Een
herhaald verzoek krijgt hetzelfde terminale resultaat. Een afgebroken apply
zonder terminaal resultaat blijft `uncertain` en moet worden gereconcilieerd;
de worker mag hem niet blind opnieuw uitvoeren.

Rollback van de netwerktoegang bestaat uit het uitschakelen van de profielworker
en het verwijderen van uitsluitend de forced key voor
`console-profile-executor`. Laat journal, allowlist en immutable bundle staan
voor onderzoek. Draai de oude profielversie niet terug als dat actuele Caddy-
of applicatieroutes zou verwijderen; publiceer daarvoor een nieuwe beoordeelde
profielversie.
