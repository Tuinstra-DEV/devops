# DEV-54: definitieve Gate CI, ter integratiereview

De geaccepteerde vergelijking is bestaand
[44380385946](https://github.com/Tuinstra-DEV/gate/actions/runs/44380385946)
tegen compact [44380385541](https://github.com/Tuinstra-DEV/gate/actions/runs/44380385541):
7 naar 4 afgeronde hosted jobminuten, 8:18 naar 5:45, vier compacte jobs,
geen overgeslagen compacte jobs. Dit is één sample; wachttijd omvat ook VM-boot.

## Vast contract

`reusable-gate-ci.yml`: Bronselectie → parallel Frontend CI en Backend CI →
CI-verdict. De twee matrixleden blijven bestaan bij een lege selectie: een
expliciete groene no-op binnen de job, geen overgeslagen job. Bronselectie doet
classificatie, vaste testselectie, Dockerlint en CI/releasecontracttests.
Frontend doet dezelfde audit, lint/format, typecheck, Vitest, volledige build
wanneer geselecteerd, en API-types. Backend doet dezelfde kwaliteit/PHPUnit,
schema-export en **MariaDB als stap**. CI-verdict weigert falende, ontbrekende,
overgeslagen of geannuleerde geselecteerde uitvoering.

Frontend blijft hosted. Alleen Backend gebruikt de optionele `gate-backend`
medium-rol; runnerkeuze staat binnen dezelfde jobdefinitie. Toelating vereist
de bestaande huidige menselijke owner, private same-repo/non-fork PR,
oorspronkelijke én triggering actor, open actuele PR, protected default/base,
actuele branchtip en de merge met exact head/base als ouders, exacte workflowpin,
run/job/attempt/head/execution-labels en exacte jobnaam. Geen vaste PR of branch.
Onbetrouwbare PR's naar develop blijven hosted met dezelfde adapter/tests en
zonder secrets. Niet-PR-events krijgen geen nieuwe runnerrechten.

Schedules en workflow_dispatch gebruiken dezelfde vier jobs en forceren alle
suites, API/schema, volledige frontendbuild en MariaDB. Backend is daarvoor
bewust hosted: de nieuwe admission blijft PR-only. Dit is een expliciete
wekelijkse uitzondering op het streven zware PHP-checks naar Sanctuary te
brengen, zonder de toelating uit te breiden naar schedules/dispatch.

Release PR's zijn uitsluitend trusted `develop` → protected `main`, met alle
kwaliteit/suites/build/API/MariaDB geforceerd. Een aparte release-caller roept
dezelfde vier jobs aan en daarna `reusable-gate-release-browser.yml`: één
heavy `gate-release-browser`-rol voor de bestaande visuele én authenticated
smoke. Deze rol mag geen gewone PR aan. Beide reusable workflowrefs moeten
dezelfde providercommit hebben; geen willekeurig extra nested workflowref.
De browser controleert vóór artifacttoegang alle vier groene jobs uit dezelfde
run/attempt en gebruikt uitsluitend het frontendoutput-artifact daarvan.
Bij onbetrouwbare release-intentie faalt bronselectie; geen hosted releasebypass.
De bestaande containerbuild/promotion, Gate release-assurance, security, Heal
en Protect-workflows veranderen niet.

De generieke `reusable-trusted-verification` wordt nergens door de nieuwe
core, schedule- of release-route aangeroepen. De bestaande adapters worden als
gepinde composite actions binnen de vaste jobs hergebruikt; dat voegt geen
bundle-jobgrafiek toe. De historische pilotbestanden blijven uitsluitend als
reproduceerbaar bewijs/rollbackbron; geen tijdelijke pilotref wordt opnieuw
toegelaten.

## Permanente image en runnergroep: alleen een voorstel

`runner/policy/gate-compact-image.json` bindt de getestte zelfstandige kandidaat
`2faaf1bed35a253fd134bb52bcd2f9ce3e93f204a0bcdc4d96a5a2df925971ce`
aan de Packer-route, PHP 8.3.36-familiepin en de bestaande MySQL/SQLite/Docker-
en needrestartcontracten. PHP MySQL/SQLite/CLI blijven exact
`8.3.36-1+ubuntu24.04.1+deb.sury.org+1`; overige runtimes gelijk.
Dit is het geteste artifact, geen claim van bit-identieke toekomstige builds.
Een herbouw krijgt een nieuwe hash en moet het hele imagecontract en geïsoleerde
VM-validatie opnieuw doorlopen. Gebruik geen broad Ansible redeploy op de
bestaande custom four-poolconfig: die baseline-template dekt deze live policy niet.

Na review: bewaar manager/configbytes én uid/gid/mode, helperhash, selector en
oude/new imagehash, volledige groep/repos en service/status in een root-only
journal met hashgeverifieerde backups. Bewijs readback **vóór enige mutatie**,
zoals de gecorrigeerde 91c9cf3-rollback. Bewaar herstelstappen idempotent en
hervatbaar; start geen eenmalige pilottransactie opnieuw voor permanente rollen.

`scripts/plan-gate-compact-admission.py` leest twee verse `gh api`-snapshots en
de gereviewde providercommit en rendert uitsluitend een JSON-plan:

```bash
gh api orgs/Tuinstra-DEV/actions/runner-groups/4 > /tmp/dev54-group.json
gh api 'orgs/Tuinstra-DEV/actions/runner-groups/4/repositories?per_page=100' > /tmp/dev54-repositories.json
python3 scripts/plan-gate-compact-admission.py --group /tmp/dev54-group.json --repositories /tmp/dev54-repositories.json --workflow-sha PROVIDER_40_SHA
```

Groep 4 behoudt alle zes bestaande refs en elf repository-ID's; voeg uitsluitend
`Tuinstra-DEV/devops/.github/workflows/reusable-gate-ci.yml@PROVIDER_40_SHA`
en `Tuinstra-DEV/devops/.github/workflows/reusable-gate-release-browser.yml@PROVIDER_40_SHA`
toe. Geen public repositories, geen verandering van visibility/restricties.
Voeg aan de **bestaande** managerconfig uitsluitend twee `org_execution_roles`
toe, met die exacte afzonderlijke refs, repositories `["Tuinstra-DEV/gate"]`,
events `["pull_request"]`, actors `[{login="marcel-tuinstra",type="User"}]`.
Behoud alle huidige pool/queue/credential/repository- en trusted workflowinstellingen.
Installeer de manager uit dezelfde gereviewde DevOps-publicatie.

Image: verifieer hash en `qemu-img check`; installeer exact dit artifact
root-owned/content-addressed/0444 onder `/var/lib/ci-runner/images/`, controleer
de hash opnieuw en wijzig pas in een leeg lease/cleanup/VM-window de selector
naar `ubuntu-24.04-runner-2faaf1bed35a253fd134bb52bcd2f9ce3e93f204a0bcdc4d96a5a2df925971ce.qcow2`.
De selector is gedeeld: het effect op volgende VM's van andere repos moet in
de integratiereview staan. Er is nu geen selector of instelling gewijzigd.

Zet pas na coherente manager/config/groep/image en herstart-readiness de Gate
repositoryvariabele `SANCTUARY_GATE_BACKEND_ENABLED=true`. Tot die tijd is
de gewone PR-route expliciet hosted; publiceren van code activeert geen runner.
Schedules blijven hosted ook met deze variabele. De release-route vereist haar
aparte heavy-admission naast backend-admission.

## Merge- en activatievolgorde

1. Review beide open PR's en hun normale CI; controleer verse heads, groep,
   policies, image en snapshot van de twee Gate-rulesets. Geen live wijziging
   tijdens deze review. De Gate-PR bevat voorlopig oude én nieuwe CI.
2. Merge DevOps #220 na groene provider-CI. Houd de in Gate gereviewde volledige
   provider-SHA beschikbaar; niet vervangen door een bewegend main/tag of een
   nieuwe SHA zonder nieuwe caller-validatie. Actions en manager zijn uit de
   gereviewde commitlijn; de managerrollen zijn opt-in.
3. In een leeg window: herstelmetadata en readback opslaan, kandidaat permanent
   installeren, manager en uitsluitend de twee rollen/groeprefs samen activeren,
   bestaande rechten gebruiken. Service/group/selector en gedeelde dispatch
   controleren. Zet daarna de Gate enable-variabele; oude CI blijft lopen.
4. Bewijs de **nieuwe pin en checknamen**: trusted gewone PR (Backend Sanctuary),
   onbetrouwbare PR (hosted), selectieve/lege selectie (vier groene jobs), volledige
   schedule en trusted release inclusief beide browsers. Geen handmatige runs
   door deze implementatie; deze aanvullende bewijzen horen bij activatie na review.
5. Voeg per ruleset de vier nieuwe Actions-checks toe, behoud eerst de oude
   vereisten en de vier onafhankelijke vereisten. Op main ook
   `browser / Release browser` verplicht. Bewijs groen én dezelfde dekking op
   actuele PR-heads; daarna mogen de vijf oude CI-contexten uit de regels.
   Geen tijdelijk onbeschermde branch, geen admin bypass.
6. Merge Gate #444 als overlapversie onder de geldende checks. Oude automatische
   CI pas in de voorbereide vervolgcommit verwijderen nadat stap 5 aantoonbaar
   voltooid is. Het Gate-runbook bevat checkmapping, bewijsverifier en de
   afzonderlijk toepasbare retirementpatch. Deze patch is nu niet toegepast.
7. Verifieer normale PR, schedule, release en gedeelde dispatch na de cutover.
   WODIQ Site volgt pas daarna; andere repos/productie blijven buiten deze stap.

## Rollback van de definitieve overgang

Voor retirement: enable-variabele terug naar false; releasewijziging terugnemen
of oude releasechecks verplicht houden. Drain bestaande leases en cleanup; houd
de toelating van lopende jobs intact. Stop pas daarna de manager, herstel de
oorspronkelijke config en manager incl. uid/gid/mode, oude selector/hash
`66eded13ed619c122421566de9b0a55ed49de5eb895caa40a2bf3b77d1b82bc4`
en groep/repos uit het volledige journal. Start en verifieer alles, inclusief
echte gedeelde dispatch. Iedere stap moet bij onderbreking hervatbaar zijn.
Na retirement: eerst oude CI via normale revert herstellen en groen bewijzen,
dan oude vereisten terugzetten; pas daarna nieuwe vereisten verwijderen en de
runner/image herstellen. Nooit eerst verplichte checks weghalen zonder vervanging.

De geaccepteerde tijdelijke proef bewijst prestaties en 91c9-herstel, niet de
nog niet geactiveerde permanente toelating of nieuwe release/schedule-checknamen.
