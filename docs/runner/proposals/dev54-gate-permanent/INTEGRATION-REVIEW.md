# DEV-54 integratiereview — 19 november 2026

De gebruiker heeft integratiereview, correcties, groene validatie, mergecommit van
DevOps #220, Gate #444 als overlap, permanente image/toelating en live CI-bewijs
geautoriseerd. Productieactivatie blijft uitgesloten. Primary-promotie blijft false.

## Bevindingen en correcties

1. Grote OCI-archieven werden in hun geheel in Node-geheugen gelezen bij receiptcreatie
   en validatie. Dit is vervangen door dezelfde SHA-256 met een vaste buffer van 1 MiB.
   Regressie controleert meerdere blokken en gewijzigde bytes; bestaand OCI/tamperbewijs
   blijft gelijk. Geen image- of runtimeversie gewijzigd.
2. De oorspronkelijke activatiescript was specifiek voor de zes→zeven-ref-proef.
   De permanente entrypoint rendert exact drie Gate-rollen en twee nieuwe workflowrefs,
   controleert gemergde reviewheads en huidige protected develop, en hergebruikt de
   onveranderde herstelmotor met hash
   `918bdbed1676eceb72ca4d6fdb629c9c86b4dfff49d7a5adf294e543d944218b`.
   De bestaande real-byte/symlink/journal-onderbrekingstests zijn opnieuw uitgevoerd met
   zes→acht refs. Daarnaast test de activatievolgorde dat volledig opgeslagen en
   teruggelezen herstelmetadata vóór service-stop bestaat en een gewon­nen lease-race
   eerst de oorspronkelijke manager herstart, zonder mutatie.
3. Een gewone productiemain-merge is buiten scope. Release Verify verzamelt daarom een
   expliciete **promotie-dry-run**, met exact dezelfde receipts, transfer-artifact-ID's,
   GitHub ZIP/OCI-digests en gecompileerde symbolen. Deze route heeft geen registry-login,
   registry-copy of deploypad. Zij kan geen daadwerkelijke main-merge, registrypublicatie
   of productie-readiness bewijzen. Ontbrekend Sentry/enrollmentbewijs blijft expliciet.
4. De echte Build-kandidaatopdracht krijgt een sampler voor VM-breed geheugen en de
   geverifieerd gedeelde kandidaat/Docker-filesystem. De interval is 200 ms: kortere
   pieken kunnen worden gemist. Geheugenpiek/minimum beschikbaar, swap, absoluut
   schijfgebruik, groeipiek en minimum vrije ruimte plus exitcode worden gebonden aan
   bron/run/poging. Het rapport reist mee in het gehashte runtimeartifact en wordt
   opnieuw gebonden in het verificatiebewijs. Ook falende builds bewaren het rapport.

De bestaande selector/verdicttests blijven vereist: nul/één/twee geselecteerde taken,
geen opvulling, backend→frontend-typen, exacte current-attempt jobs, bron, dubbele/
ontbrekende/skipped/falende taken, bot/fork/actor/head/merge/pin-rejecties. MariaDB blijft
binnen Backend; audit en alle bestaande tests blijven verplicht.

## Operatorroute

Read-only controle op de bestaande sudo-route:

```bash
bash /tmp/dev54-integration-preflight.sh
```

Alleen de laatste JSON-regel teruggeven. Geen credentials, manager.toml of ruwe logs.
De controle leest effectieve policy/manager, bestaande systemd-credentialrechten,
imagehash/PDO en geldig geïsoleerd guest-Dockerbewijs, resources en huidig runnervenster.
Een host-Dockercontrole wordt niet als guest-bewijs gebruikt.

Pas nadat beide reviewheads groen en gemergd zijn:

```bash
bash docs/runner/proposals/dev54-gate-permanent/run-permanent-activation.sh activate
```

De wrapper haalt verse groep/repos op, verifieert de effectieve managerbaseline en de
exacte minimale patch en stageert gehashte inputs. Root leest alleen de reeds geladen
systemd-credential via de bestaande lezer. Geen nieuwe toegang of sudoerswijziging.
De transactie wacht op een leeg venster, maakt root-only backups/journal met volledige
UID/GID/mode en readback, stopt/recheckt leegte en activeert uitsluitend de gereviewde
image, managerdelta en Gate-rollen/twee refs. Daarna readback en gedeelde dispatch.
De Gate-caller wordt door deze roottransactie niet gewijzigd.

Hervatbare rollback op dezelfde operatorroute:

```bash
bash docs/runner/proposals/dev54-gate-permanent/run-permanent-activation.sh rollback
```

Zet vooraf de backend/release-opt-ins uit via de bestaande gh-rechten; primary blijft
false. Laat actieve jobs uitdraaien. De ongewijzigde herstelmotor herstelt manager,
configbytes én metadata, selector, oorspronkelijke groep/repos en kandidaatlocatie,
ook na een afgebroken herstel of reeds herstelde deelstaat. Externe wijzigingen
worden niet overschreven. De oorspronkelijke caller is nog als hosted overlap bruikbaar.

## Hercontrole na wachten

Controleer selector, bron- en kandidaat-image en helper opnieuw na het wachten,
vóór de backup en vóór iedere service-stop. Herhaal dit met de service gestopt,
vóór de eerste inhoudelijke mutatie. Drift mag geen nieuwe herstelbaseline worden.
De regressietest reproduceert dit en controleert de volgorde bij een nieuwe job
tijdens service-stop. Een oude wachtende activatie eerst afbreken; hervat alleen
met de gepubliceerde groene correctie en de geverifieerde oorspronkelijke invoer.

## Live bewijs en resterende productiegrens

Hergebruik geldig bewijs op dezelfde pin/source/attempt. Verzamel echte docs/frontend/
backend/full PR-routes, oorspronkelijke actors en hosted fallback zonder trust te
simuleren; dispatch is geen echte schedule. Default branch is develop, zodat de echte
schedule daar kan draaien. De release-PR develop→main blijft ongemergd: beide CI-fasen,
resourcepiek, artifact- en symbolendry-run zijn premerge te bewijzen.

Vernieuw alleen aantoonbaar geldig observabilitybewijs. De oude proofhash mag de nieuwe
pipeline niet goedkeuren. De nieuwe policy moet de volledige releaseclosure dekken en
krijgt na geldig bewijs een nieuw evidencebestand, approvedProofSha256 en immutable
observability-actionpin. Bestaande backend/privacy/SDK-bewijzen mogen volgens de
bestaande fingerprintregels worden hergebruikt; nieuwe deployment-symbols-fingerprints
zijn geen vervanging voor nieuwe Sentry-service-evidence.

Het huidige retirementcontract verlangt daadwerkelijke promotie-/assurancecontinuïteit.
Een dry-run mag daarvoor niet als gepubliceerde image of productie-readiness worden
opgegeven. Als dit zonder productie niet bewijsbaar is: laat oude workflows/vereisten
staan en rapporteer precies het resterende productiebesluit. Geen productiecommit,
deploy-dispatch, primary=true of versoepelde verifier om het werk af te ronden.
