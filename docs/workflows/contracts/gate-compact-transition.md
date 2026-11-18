# DEV-54 — Gate-selectie en Release CI voor integratiereview

De geaccepteerde vergelijking blijft bestaand
[44380385946](https://github.com/Tuinstra-DEV/gate/actions/runs/44380385946) tegen
compact [44380385541](https://github.com/Tuinstra-DEV/gate/actions/runs/44380385541):
7 → 4 afgeronde hosted minuten en 8:18 → 5:45, vier jobs, nul skips op die volledige
selectie. De nieuwe selectie/releasecode is afzonderlijk lokaal getest; echte release-
artifact-, Sentry- en promotievalidatie vereist de integratiereview en latere activatie.

## Selectie zonder opvulwerk

Bronselectie maakt uitsluitend geselecteerde matrixleden aan. Docs-only: Bronselectie →
CI-verdict (2 starts); frontend-only: één frontendtaak (3); backend-only: backend én
frontend API-typen (4); volledige PR: beide taken (4). Backend behoudt MariaDB als stap.
Bij lege selectie kan GitHub een skipped matrixsjabloon zonder runner tonen. Dit is
geen opvuljob en geen geselecteerde taak. CI-verdict controleert vaste sleutels, exact
verwacht aggregaat en de echte jobs van dezelfde run/poging/bron via actions:read.
Een geselecteerde skipped/ontbrekende/dubbele/gefaalde taak kan nooit groen opleveren.

`ci / Bronselectie` en `ci / CI-verdict` worden de universele developvereisten.
`ci / Backend CI` en `ci / Frontend CI` zijn conditioneel. De bestaande vier onafhankelijke
security/Heal/Protect-checks inclusief publisher-App-ID's blijven verplicht. De audit
blijft verplicht binnen geselecteerde frontendkwaliteit; geen nieuwe uitzondering.
Schedules/dispatch forceren volledig en blijven hosted (4 starts). Gewone onvertrouwde
PR's behouden hosted met dezelfde vaste adapter, zonder secrets of trusted toelating.

Lokaal reproduceerbare telling:

```bash
python3 -B scripts/measure-gate-selection.py --gate-root /pad/naar/gate
```

Dit draait de echte classifier, MariaDB-selector op een synthetische git-merge,
Gate-bundleselector en provider-matrixcode. Resultaat staat in Gate
`docs/ci/DEV-54-selection-measurement.json`. Geen nieuwe scenario-PR's gestart.

## Volledige releaseketen

`reusable-gate-release-ci.yml` bevat precies **Build kandidaat → Verifieer kandidaat**.
Build gebruikt één Bake-grafiek: vier productie-OCI's, dezelfde dependency-/scan-runtime
voor verificatie, private maps uit dezelfde frontendcompilatie, SBOM en provenance.
Verify controleert source/run/poging/provider/callerblob/tree en artifact-ID/ZIP/OCI-
digests vóór gebruik; vier bestaande Trivy-rapporten en vier failure-policychecks,
preflight/contracttests/Dockerlint, volledige PHP/frontend/API/MariaDB, bestaande paired
Gate-scans en release-check, beide bestaande browserchecks op het exacte frontendimage,
en de onafhankelijke security/Heal-checkbewijzen. Geen rebuild tijdens Verify.

Vier bestaande productie-image-receipts krijgen alleen op deze nieuwe route schema v2
met immutable provider. Legacy v1 blijft uitsluitend geldig voor de oude route. Het
nieuwe verificatieartifact bindt de afzonderlijke rapporthashes aan de kandidaat.
De opt-in promotie selecteert uitsluitend die nieuwe workflow/run/poging, controleert
het verificatieartifact en dezelfde vier image-/maps-artifactdigests en behoudt
`--preserve-digests`. Geen terugval naar een oude groene release als de nieuwe faalt.

De nieuwe observability-policy vereist apart `evidence/gate-release-ci.json`:
**bestaande enrollment bewijst de oude workflow, niet deze nieuwe pipeline**.
Dat ontbrekende integratiebewijs is niet gefingeerd of vervangen door lokale tests.
De nieuwe promotie weigert tot hernieuwde deployment-symbols-assurance bestaat.
Default `GATE_RELEASE_CI_PRIMARY=false` bewaart de oude productieroute.

De nieuwe mainvereisten worden `release / Build kandidaat`, `release / Verifieer kandidaat`
plus de vier onafhankelijke checks. Gate bevat de volledige checkmapping, vervangingslijst,
retirementverifier en generator. De oude workflows blijven tijdens overlap byte-identiek:
`ci.yml`, `lints.yml`, `tests.yml`, `release-gate.yml`, `build.yml`. Daarna samen verwijderen.
De eerdere vijf-job-releasevoorstelroute is verwijderd, geen generiek bundlepad toegevoegd.

Tijdens overlap: volledige gewone CI 9 + 4 = 13 starts; volledige release 9 + 7 + 2 = 18,
beide exclusief gewoonlijk twee onafhankelijke PR-runners. Dubbel draaien PHP/frontend/API,
oude en nieuwe vier images/scans, host/Dockerfrontendbuilds, scan-runtimes/Composer en
paired scans/browserchecks. Na retirement: releasecontract 2 zware starts plus 2 onafhankelijke;
post-merge blijven promotie en deploy 2 eigen jobs. Dit zijn aantallen, geen gemeten minuten.

Exacte workflowpin: `6970cbbf6cef3ae256d61d4805a8db464b9650a0`. Trust/verdict/observability-actions zijn gepind op
`ac4819f414e558da0a469c5755e8ec4a32f5c66c`.

## Permanente runnerwijziging, nog niet geactiveerd

De effectieve bron is exact
`5404bbbf3d4430f4518afa37c19d7be090d1246c3b5d7cc4580ffb601f999ed3`.
Gebruik uitsluitend `docs/runner/proposals/dev54-gate-permanent/manager-effective.patch`
en de hashes uit `manager-manifest.json`, **niet de volledige repo-manager**.
Regressie past de patch zonder fuzz toe op de opgeslagen werkelijke baseline en test
alle nieuwe toelatingspaden. AST-vergelijking houdt pool, disk admission, dispatch,
leases, credentials en helper buiten de wijziging.

Drie rollen: `gate-backend` (medium, `ci / Backend CI`), `gate-release-build` (heavy,
`release / Build kandidaat`), `gate-release-verify` (heavy, `release / Verifieer kandidaat`).
Twee refs in groep 4: `reusable-gate-ci.yml@PIN` en `reusable-gate-release-ci.yml@PIN`.
Beide release-rollen delen één exact ref. Bestaande zes refs en elf repositories blijven.
De rollen vereisen huidige owner/private/same-repo/non-fork/open PR, oorspronkelijke en
triggering actor, protected develop of develop→main, huidige tips, exacte mergeouders,
jobnaam, run/poging/source/execution-nonce. Buiten deze grenzen geen JIT. Runner wordt
binnen de bestaande taakdefinitie gekozen, geen dubbele hosted/trusted job.

Imagecontract: `runner/policy/gate-compact-image.json`; bestaande geïsoleerde kandidaat
`2faaf1bed35a253fd134bb52bcd2f9ce3e93f204a0bcdc4d96a5a2df925971ce`, oude selector
`66eded13ed619c122421566de9b0a55ed49de5eb895caa40a2bf3b77d1b82bc4`.
PHP83 8.3.36, MySQL/SQLite en needrestart-guard getest; andere runtimes behouden.
De nieuwe release-Bake-/browserroute is nog geen bewezen imagecontractrun.

Read-only plan:

```bash
python3 -B scripts/plan-gate-compact-admission.py --group /tmp/group4.json --repositories /tmp/group4-repos.json --workflow-sha PIN
```

## Exacte integratievolgorde

1. Review beide open PR's en normale checks. Vergelijk verse group/repos/rulesets.
2. Merge DevOps via mergecommit zodat de gepinde providercommit bereikbaar blijft;
   Gate houdt de exacte manifestpin. Nieuwe pin vereist nieuwe caller-validatie.
3. Merge Gate als overlap onder huidige vereisten, alle opt-ins nog false.
4. Operator: lege runnerwindow; volledige oorspronkelijke bytes/selector/groep/repos
   én ownership/mode/fasejournal opslaan, onder herstelidentiteit teruglezen. Geen
   activatie als herstelmetadata niet volledig toegankelijk is. Exacte minimale patch,
   image en drie Gate-rollen/twee refs coherent activeren; overige policies gelijk.
   Pas daarna backend/release-opt-ins true. Dit vergt afzonderlijke integratiereview.
5. Bewijs nieuwe pin voor docs/frontend/backend/full/trusted/hosted PR's, volledige hosted
   schedule en tweefasige release. Vernieuw de deployment-symbols-enrollment op deze
   definitieve bestanden/pin en de exacte artifacts. Geen extra proeven bij deze publicatie.
6. Voeg nieuwe branchvereisten toe naast oude, bewijs groen, schakel pas met correcte
   assurance de promotie-opt-in en bewijs digestcontinuïteit. Verwijder dan vijf vervangen
   checkcontexten; onafhankelijke eisen, App-ID's en overige branchregels behouden.
7. Gate-retirementverifier op verse API/artifact/proof-snapshots moet groen zijn. Normale
   vervolg-PR verwijdert vijf oude workflows en oude schedule. Geen opaque patch in Gate.
8. Bewijs gedeelde dispatch en definitieve PR/schedule/release/promotie; daarna WODIQ Site.

## Hervatbare rollback

Primary-flag false, oude promotieroute; beide runner-opt-ins false. Laat actieve leases
uitdraaien. Herstel uit het reeds opgeslagen journal de oorspronkelijke manager/config
bytes én metadata, image-selector en volledige groep/repositoryselectie. Controleer
coherentie en gedeelde dispatch. Hervat fasegewijs bij onderbreking, maak geen nieuwe
baseline over gedeeltelijk herstelde staat. Gebruik de gecorrigeerde rollback uit
91c9cf3 en diens regressie op afgebroken herstel. Na retirement eerst oude workflows
via normale revert en groen bewijs, daarna oude checks herstellen; nooit beide sets weg.
