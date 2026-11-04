# DEV-54: activatie v2 op Sanctuary (na de single-use-reparatie)

De eerdere CVE-precondities zijn op 4 november via GitHub gecontroleerd: Tracker
[#375](https://github.com/Tuinstra-DEV/tracker/pull/375), Notify
[#33](https://github.com/Tuinstra-DEV/notify/pull/33), Status
[#21](https://github.com/Tuinstra-DEV/status/pull/21) en de Gate PHP/IN-46-release
[#435](https://github.com/Tuinstra-DEV/gate/pull/435) zijn gemerged. Tracker draait
PHP 8.4.27 na [deploy 41738272047](https://github.com/Tuinstra-DEV/tracker/actions/runs/41738272047);
Gate's `e94e71fe` heeft een geslaagde
[productiedeploy 41836668207](https://github.com/Tuinstra-DEV/gate/actions/runs/41836668207).

De runtimeguard zoekt huidige open CVE/PHP-securityhotfixes en weigert
queued/in-progress runs in **Gate, Tracker, Notify, Status en Tessera**. Gesloten
historische PR-nummers zijn geen runtimevoorwaarde meer. Ook een onvolledige
API-response of fout blokkeert de activatie. Deze controles worden vóór de drain,
na de drain en na de handmatige groepswijziging herhaald.

De eerste v2-uitvoering strandde volgens de door Marcel uitgevoerde read-only
diagnose op `HTTPError` in de open-PR-inventaris, vóór backup/drain. De precieze
repo/status is niet vastgesteld; ontbrekende Tessera-zichtbaarheid is een
hypothese. De manager bleef actief op `54520f17…` en er was geen activatiebackup.

De herstelde versie vangt HTTP-/transportfouten af. Een 403/404 door de bestaande
repo-credential probeert dezelfde GET één keer met de reeds aanwezige
org-credential, mits dat een andere credential is en de fout geen rate-limit-
headers bevat. Alleen een geslaagd, geldig antwoord bewijst die read access.
**Alle vijf controles blijven verplicht**; niets wordt overgeslagen of vervangen
door een eerdere operatorcheck. Beide credentials geweigerd, rate limits,
transport-/serverfouten of onvolledige antwoorden blokkeren de activatie.
Geen credentialscopes of hostpolicy worden gewijzigd. Bij weigering verschijnen
alleen endpointcategorie, bekende repo en HTTP-status; bij onverwachte fouten
alleen exceptionklasse en vaste stapnaam, zonder exceptionwaarde of config.

**Oude expliciete rol-jobs hebben geen automatische hosted fallback na een rolpinwissel.**
Gate's bestaande lint/test/canary-aanroepen op `a8ace2f…` geven de roloptie expliciet
`true` door. De runnerkeuze is dan al vastgelegd voordat de manager beslist.
Afwijzing door de manager kan de job queued laten; daarom volgt activatie pas na
de CVE-merges en de rustige runstatus. Oude Gate-branches moeten vóór een latere
nieuwe run naar het nieuwe CI-contract bijgewerkt worden. Tests bewijzen deze
negatieve eigenschap; er wordt geen geslaagde live fallback geclaimd.

## Vaste payload en grenzen

- Eén standalone script: `runner/activation/dev54-activate.sh`, zonder runtime
  imports uit een user-writable checkout en zonder caller-paden/refs/commando's.
- Nieuwe gedeelde pin en managerbroncommit:
  `ea86c62eeac615cb292a78bf50995fed81c051ee` (main na PR #201).
- Nieuwe manager SHA-256:
  `5404bbbf3d4430f4518afa37c19d7be090d1246c3b5d7cc4580ffb601f999ed3`.
- Verwachte oude manager/rollback SHA-256:
  `54520f176dc5c07adf9493d27f89e549672c27aa6d7fa05f737b6549f0dcb1f3`.
- Deze beginversie is de op 4 november door Marcel geïnstalleerde reparatie op
  `de87802`, niet de oorspronkelijke `232d3736…`. De v1-activatie op `673e749…`
  weigert deze host terecht. V2 weigert juist `232d3736…` en elke onbekende hash.
  Gebruik de oude `/var/backups/ci-runner-manager-232d37364c0754bd.py` niet voor
  rollback van deze activatie: daarbij zou de single-use-reparatie verdwijnen.
- Precies vier bestaande trusted pins blijven behouden; de vierde is de bestaande
  DEV-14 metadata-pin `a8ace2f171d7d127bbfb3a6d6b13f158f461ba98`. De nieuwe vijfde
  is exact de actieve metadata-rolpin. Een zesde, afwijkende vijfde of onbekende
  bewaarde metadata-pin wordt geweigerd. Andere uitgangstoestanden vragen review.
- Beide bestaande rolpolicies blijven aanwezig. Alleen `workflow_ref` verandert;
  actors, repositories en events blijven gelijk. Het script toont en bewaart per
  rol de **daadwerkelijke oude volledige ref → nieuwe volledige ref** vóór de swap.
  De producer-rolpin is niet door de agent uit de beschermde hostconfig gelezen;
  de operatorpreflight bepaalt die waarde, zonder configinhoud te loggen.
- Tessera `1179209966` wordt aan de bestaande beperkte repositoryselectie toegevoegd.
- De nieuwe bron bevat DEV-54, de Tessera Node 24-fix zonder npm-cache,
  de IN-46-scannerbinding en de single-use/reconciliation-reparatie uit PR #201.
  `112666ba…` bevat die laatste twee wijzigingen niet; gebruik daarom de nieuwe
  volledige pin voor zowel de managerbron, beide rollen als alle consumers.
  De admission-/rolpolicy en de bovengrens van vijf trusted pins blijven gelijk.

## Marcel: interactief ophalen en installeren

De agent voert geen servercommando's uit. Gebruik de exacte download-URL en
scriptchecksum uit de DEV-54-comment bij de gemergde v2-PR. Marcel installeert
via `ssh -t mtuinstra@sanctuary.tuinstra.dev` met interactieve `sudo` in een private
root-owned stagingmap. Controleer de SHA-256 vóór én na installatie.
Er is geen tijdelijke sudoers-regel en geen NOPASSWD-recht nodig.

```sh
ssh -t mtuinstra@sanctuary.tuinstra.dev \
  'sudo install -o root -g root -m 0755 /root/dev54-stage/dev54-activate.sh /usr/local/sbin/dev54-activate.sh'
```

Het root-owned script weigert alle argumenten behalve exact geen argumenten of
exact `--rollback`, vereist een TTY en wist de calleromgeving. Voer geen token of
wachtwoord in GitHub, het script, Tracker of chat in; alleen sudo vraagt het
bestaande operatorwachtwoord in de interactieve terminal.

## Als mtuinstra: één activatie

```sh
ssh -t mtuinstra@sanctuary.tuinstra.dev 'sudo /usr/local/sbin/dev54-activate.sh'
```

Het script controleert de bronhash, hostbestanden, unit/credentialbindings, bestaande
groep/policies, open CVE-hotfixes en rustige runs **vóór** de eerste servicewijziging.
Het maakt een tijdgestempelde root-only backup onder `/var/backups/dev54-activation`
van binary, config, unit/drop-in en afgeleide rol-/admissionpolicies. Credentials
worden niet geback-upt of gelogd. De nieuwe manager wordt voor rollback ook lokaal
hashgebonden opgeslagen; rollback downloadt geen executable code.

De manager wordt reconcile-only gestart en laat alle VMs natuurlijk uitlopen;
geen destroy/kill of TTL-overschrijding. Bij leases nabij hun TTL stopt de procedure.
Na de drain blijft admission gepauzeerd. Het script toont dan alleen de bedoelde rolpinwissels en Tessera-wijziging, geen
config- of repositorytabellen. De exacte policyselecties staan alleen in de
beschermde backup. Claude past GitHub tijdens deze pauze via de API aan; Marcel
typt alleen `GEREED` nadat Claude bevestigt dat dit afgerond is.

## GitHub: handmatig tijdens de gepauzeerde fase

Organisatie **Tuinstra-DEV → Settings → Actions → Runner groups →
`sanctuary-trusted-verification`**:

Claude voert deze wijzigingen tijdens de pauze uit met zijn bestaande
operatorauthenticatie. De activatie zelf doet uitsluitend GET-verificatie.

1. Behoud selected/private repositories, public repositories uit en restrictie
   tot geselecteerde workflows aan. Voeg de private repo `sudoku-tessera`
   (ID `1179209966`) toe; behoud de overige geselecteerde repositories.
2. Behoud alle vier oude trusted-verification-refs. Voeg toe:

```text
Tuinstra-DEV/devops/.github/workflows/reusable-trusted-verification.yml@ea86c62eeac615cb292a78bf50995fed81c051ee
Tuinstra-DEV/devops/.github/workflows/reusable-gate-pr-security.yml@ea86c62eeac615cb292a78bf50995fed81c051ee
```

3. De manager eist **exacte gelijkheid** tussen groep en policy. Verwijder alleen
   een vervallen **role-only producer-ref** uit de getoonde oude → nieuwe
   rolpinwissel; verwijder nooit een oude trusted-verification-ref. Het alleen
   toevoegen van beide refs terwijl een vervallen producer-ref blijft staan is
   onvoldoende. Behoud alle overige selecties; het script verifieert exacte gelijkheid zonder ze te loggen.
4. Typ `GEREED`. Het script doet uitsluitend GET-controles met de bestaande
   beschermde credentials; het wijzigt GitHub niet. Bij een afwijking blijft
   admission gestopt. Binary/config worden atomair per bestand vervangen, met
   hash-readback en herstelbaar journal. Daarna worden manager/socket/status en
   groep gecontroleerd. Pas na `DEV-54 applied` kunnen consumers omschakelen.

De op 4 november read-only bevestigde groep heeft ID **4**. Vervang tijdens de
pauze uitsluitend `selected_workflows` atomair door de volgende volledige lijst
(zes refs: vijf trusted-verification-pins plus één producerrol):

```text
Tuinstra-DEV/devops/.github/workflows/reusable-trusted-verification.yml@23c08cf212dd6411ed5076b2ec53a05c1e2e5f3e
Tuinstra-DEV/devops/.github/workflows/reusable-trusted-verification.yml@36f32aebe315274df8825674b7bd0ced637ab056
Tuinstra-DEV/devops/.github/workflows/reusable-trusted-verification.yml@e1a9f09504b903120e9b5297eff64f58a891c907
Tuinstra-DEV/devops/.github/workflows/reusable-trusted-verification.yml@a8ace2f171d7d127bbfb3a6d6b13f158f461ba98
Tuinstra-DEV/devops/.github/workflows/reusable-trusted-verification.yml@ea86c62eeac615cb292a78bf50995fed81c051ee
Tuinstra-DEV/devops/.github/workflows/reusable-gate-pr-security.yml@ea86c62eeac615cb292a78bf50995fed81c051ee
```

Dit verwijdert alleen de producer-ref op `a8ace2f…`. Voeg repository-ID
`1179209966` toe aan groep 4; behoud alle andere repositories en beveiligingsvelden.
Vergelijk vóór uitvoering opnieuw de live uitgangsgroep met de door het script
gecontroleerde oude policy; wijzig niets voordat admission gepauzeerd is.

Een afwijkende API-permissie geeft een blokkade; geen token in de terminal of chat
invoeren en geen credentialscopes verruimen om deze voorbereiding af te ronden.

## Verificatie, rollback en opruimen

Na `DEV-54 applied` controleert Marcel nogmaals de geïnstalleerde hash en beide
units. Verwacht de nieuwe managerhash hierboven, tweemaal `active`,
`ExecMainStatus=0` en `NRestarts=0`. Het script heeft de exacte GitHub-groep na de
herstart al via GET gecontroleerd. Een actieve unit bewijst nog geen echte
consumer-run; fase 2 begint pas na bevestiging van `applied` aan Claude.

```sh
ssh -t mtuinstra@sanctuary.tuinstra.dev \
  'sudo sha256sum /usr/local/bin/ci-runner-manager && sudo systemctl is-active ci-runner-manager.service ci-runner-host-helper.socket && sudo systemctl show ci-runner-manager.service --property=ExecMainStatus --property=NRestarts'
```

Alleen als rollback nodig is, vóór cleanup:

```sh
ssh -t mtuinstra@sanctuary.tuinstra.dev 'sudo /usr/local/sbin/dev54-activate.sh --rollback'
```

Rollback gebruikt uitsluitend de laatste beschermde backup van deze exacte versie.
Het weigert gewijzigde backup-, binary-, config- of unitinhoud. Het draint opnieuw
natuurlijk en toont de oorspronkelijke rolpin-doelen en de terug te draaien selectiewijziging. Herstel die handmatig
(inclusief oude producer-rolref en oorspronkelijke repo-selectie) en typ `GEREED`.
Daarna herstelt het exact manager `54520f17…` én oorspronkelijke configbytes, dus
ook beide oude rolpins, en controleert de status. Queued oude rol-jobs blokkeren rollback
niet: rollback moet hun oorspronkelijke admission juist kunnen herstellen.
Bestaande VMs worden ook tijdens rollback nooit geforceerd gestopt.

Bij een mislukte of onderbroken apply/rollback blijft admission gepauzeerd; backup
blijft bewaard. Voer geen ad-hoc start of algemene sudo-opdracht uit om dat te omzeilen.

Bewaar het script zolang rollback tijdens de consumerrollout nodig kan zijn.
Na bevestigde succesvolle controle, of na een afgeronde rollback, verwijdert
Marcel interactief uitsluitend het tijdelijke script en de stagingkopie:

```sh
ssh -t mtuinstra@sanctuary.tuinstra.dev \
  'sudo rm /usr/local/sbin/dev54-activate.sh /root/dev54-stage/dev54-activate.sh && sudo rmdir /root/dev54-stage'
```

Bewaar de beschermde backup voor incidentherstel. DEV-54 blijft Started totdat de
consumerrollout en echte runner-/kostenverificatie afgerond zijn.

## Lokale regressiecontrole

De 29 activatie-/rollbacktests werken met tijdelijke nephostbestanden en gemockte
systemd-/GitHub-calls: geen sudo, netwerk of live activatie. Alleen de oude binary
is synthetisch; de targethash wordt tegen de echte ingecheckte manager getoetst.
De hashweigering modelleert expliciet de historische `232d…`-host en een onbekende
host. Alle 47 rolpolicytests blijven verplicht, naast `make lint`, de volledige
`make test` en actionlint. Behoud van rolvelden, vier oude pins, de vijfde exacte
metadata-pin, Tessera-ID en exacte rollbackbytes wordt afzonderlijk gecontroleerd.

### Read-only diagnose van de oorspronkelijke v2-fout

`runner/activation/dev54-diagnose-v2.py` is een versiegebonden diagnose voor het
oorspronkelijke geïnstalleerde script met SHA-256 `a38f5100…`, vóór herinstallatie.
Het controleert die volledige hash, laadt de echte entrypoint-/apply-code zonder
runtimeimports uit de checkout en stopt precies vóór `make_backup`. Bestaande
lockmetadata wordt gelezen; er wordt geen lockbestand/map gemaakt of gelockt.
Ontbrekende lockpaden en werkelijk lock-acquisitiegedrag zijn dus niet getest.
Alleen read-only systemd-/busctl-commando's zijn toegestaan. Python moet met
`-I -B` draaien zodat ook geen import-bytecode wordt geschreven.

De diagnose print alleen exceptionklasse en `bestand:regel:functie`; bij
`HTTPError` ook de vaste endpointcategorie, bekende repo en numerieke status.
Geen exceptionmessage, sourcecode, locals, URL/query, headers of body. Een
`PreflightComplete` betekent dat het read-only voorstuk geslaagd is, geen live
activatie of geslaagde lockcreatie. Nephosttests controleren zowel deze grens als
de afwezigheid van writes/drain/backup en de uitsluiting van synthetische secrets.
Deze extra operatorronde is voor het herstel overgeslagen; de nieuwe activatie
meldt de repo/status zelf als beide bestaande credentials worden geweigerd.
