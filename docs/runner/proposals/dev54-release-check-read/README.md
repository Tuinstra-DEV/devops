# DEV-54: minimaal leesrecht voor onafhankelijke releasechecks

Status: reviewvoorstel, geen live activatie of toestemming voor de nieuwe scope. De bestaande
Gate PR #460 blijft open op 9f9ae82d7c359f68f4bf049e448eb83fad908af4;
die head heeft groene normale checks. De volgende gebundelde Gate-correcties bevatten
17cbd071637d2f5b648d930eb37a514ce50af472 en
afe3e5fa3c403dd2c95706db43a2052e1e432afe en de daaropvolgende assurance-pin.

## Bewijs en besluit

Release 45003479956/1 stopte eerder bij de browserfixture. De effectieve
GITHUB_TOKEN-permissions in de joblog zijn Actions/Contents/PullRequests read;
Checks ontbreekt. De private Gate-repository vereist Checks read om de
onafhankelijke check-runs, waaronder de externe Gate PR Security-binding, te
lezen. Dit is een gevolgtrekking uit effectieve scopes en
[GitHub REST-documentatie](https://docs.github.com/en/rest/checks/runs#list-check-runs-for-a-git-reference),
geen gemeten HTTP 403: die stap is nog niet bereikt.

Vraag uitsluitend toestemming voor `checks: read` op de tijdelijke GITHUB_TOKEN
van `release / Verifieer kandidaat` in Gate. De callerjob moet die scope mogen
doorgeven; de provider kent haar alleen aan Verify toe. Build houdt de huidige
drie reads. Er komt geen nieuw App-installatierecht, operatorcredential, write-
of packages-recht. Gewone PR-CI en de bestaande trustcontroles blijven gelijk.
[Job-scopes](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idpermissions)
gelden voor alle stappen binnen Verify, ook checkout en de bestaande actions.

## Exacte instellingen na akkoord en groene provider-CI

Providerimplementatie, lokale commit:
`716ce046a09da7a01b647e74f966ba0c89a0a7ad`.

- In provider `.github/workflows/reusable-gate-release-ci.yml`, uitsluitend
  `jobs.verify.permissions.checks: read` toevoegen.
- Gate `.github/workflows/release-ci.yml`: `jobs.release.permissions` wordt
  `{contents: read, actions: read, pull-requests: read, checks: read}`; root blijft
  drie reads. Caller gebruikt de bovenstaande volledige providerpin.
- Groep 4: vervang uitsluitend
  `Tuinstra-DEV/devops/.github/workflows/reusable-gate-release-ci.yml@a083c789196ee8cd89b4ab3253c458d8d1de6dd6`
  door
  `Tuinstra-DEV/devops/.github/workflows/reusable-gate-release-ci.yml@716ce046a09da7a01b647e74f966ba0c89a0a7ad`.
  Acht refs blijven acht; de elf repositories en alle overige groepsvelden
  blijven identiek. `proposal.json` bindt de verse read-only gh-snapshots.
- In `/etc/ci-runner/manager.toml` uitsluitend `workflow_ref` onder
  `[org_execution_roles.gate-release-build]` en
  `[org_execution_roles.gate-release-verify]` naar dezelfde nieuwe ref zetten.
  Repositories/events/actors blijven exact de bestaande Gate/PR/owner-policy.
- `gate-backend`, gewone CI-provider A083, managercode 82d9…, image 2faa…,
  selector, helper, resources en credentials blijven gelijk. De effectieve
  manager laadt config eenmaal bij start: herstart is na akkoord nodig.
- `releaseProviderCommit` maakt de nieuwe releasepin expliciet; context,
  preflight, promotierouting en retirement controleren dezelfde pin. Afwezige
  override ondersteunt het bestaande contract; een ongeldige override faalt.
  De oude selectieve/schedule/fallbackbewijzen blijven op de gewone CI-pin.

## Veilige volgorde en resterende controle

1. Publiceer provider en bijbehorende assurance-enrollment samen als review-PR;
   volg normale Linux-CI. Merge pas na het specifieke scope-akkoord, zonder bypass. Pin blijft de exacte
   implementatiecommit, niet een bewegende branch of nieuwe main-tip.
2. Voor live delta: verse groep/repos én effectieve rootconfig via de bestaande
   operatorroute lezen; effectieve manager laten valideren. `plan.py` is puur:
   geen HTTP, geen writes, geen credentiallezer. Het levert een omkeerbare delta;
   alleen de operator leest echte config. De actuele configdelta is nog niet
   als root uitgevoerd of live geverifieerd (`effective_config_delta_verified=false`).
3. Wacht op leeg venster zonder andere jobs te annuleren. Bewaar vóór iedere
   mutatie in een NIEUW root-only journal: originele configbytes + uid/gid/mode,
   huidige managerhash + metadata, selector + imagehash, helperhash, volledige
   groep + repositorysnapshot, servicestatus en hashes van alle herstelbestanden.
   Lees dit alles vanuit het herstelproces terug. Laat de oorspronkelijke
   permanente backup onaangeroerd. Onderbreek bij drift of onleesbare metadata.
4. Gebruik een hervatbare transactie met fasen prepared/service-stopped/
   group-updated/config-updated/verified. Hercontroleer na het wachten alle
   baselines en het lege venster, stop de service, wijzig de ene groepsref en
   twee configrefs atomair met dezelfde rechten, start de service en laat
   effectieve config en groep opnieuw vergelijken. Een gedeeltelijke staat
   mag alleen aantoonbaar naar de oude of nieuwe volledige toestand herstellen.
   Deze nieuwe uitvoeringstransactie moet vóór gebruik nog worden gerepeteerd;
   de bestaande rollbacktests bewijzen niet dat een nog ongeschreven repin-
   transactie veilig is.
5. Publiceer de Gate-correcties gebundeld, volg alle normale checks. Merge pas
   na groen; daardoor ontstaat één nieuwe volledige releaseproef op PR #454.
   Geen extra blinde rerun. Volg Build/Verify op exact dezelfde bron/run/poging;
   verifieer alle ZIP-, OCI-, artifact- en rapportdigests, resourcepieken,
   native browserdekking, onafhankelijke checks en promotie-dry-run.
6. Productie, PRIMARY en verwijdering van oude routes blijven geblokkeerd tot
   het runbook hun afzonderlijke bewijzen heeft. Geen merge van #454 naar main.

## Rollback naar de huidige permanente toestand

Bij storing geen nieuw werk starten. Herstel vanuit het NIEUWE journal de
volledige oorspronkelijke groepssnapshot en originele configuratiebytes met
uid/gid/mode, na drain/lege-windowcontrole. Herstart de bestaande manager en
verifieer code 82d9…, selector/image 2faa…, helper, acht oude refs, elf repos en
alle configvelden. Lees het journal bij hervatten opnieuw en accepteer alleen
hashgebonden oude of nieuwe staten; onbekende drift blokkeert.

Herstel Gate via een gewone revert van alleen de nieuwe scope/pin en pinresolver
naar A083; behoud de afzonderlijke browser- en CLI-correcties. Geen force push,
geen audit-bypass. De oude overlaproute blijft beschikbaar. De oudere permanente
rollback naar pre-DEV-54 is een ander herstelpad en mag niet per ongeluk worden
gebruikt om deze config-only update terug te draaien.

## Lokale verificatie

- Scope-regressie eerst rood op ontbrekend Checks read, daarna groen. Provider
  lint en actionlint groen. 25 tests: workflow, exacte config/groepdelta en de
  bestaande permanente preflight/onderbreekbaar herstel.
- Nieuwe delta: drie tests controleren slechts twee configrefs, bestaande trust,
  onveranderde backendpin, afwijzing van drift en byte-exacte inverse; echte verse
  groep eveneens round-trip gelijk. Geen claim van live configvalidatie.
- Gate: 56 gerichte Node 24-regressies groen; preflight frontend/docker/release
  en caller actionlint groen. Oude gh 2.45 --slurp reproduceert lokaal; ondersteunde
  paginatie bewaart alle pagina's en ge-escapete checksummary's.
- Volledige lokale `make test` bereikt de bestaande ontbrekende
  `ansible-playbook`-voorwaarde (exit 69). Complete Linux provider-CI blijft vereist.
- De browserfix is apart met het echte R5-artifact getest: 3 smoke-captures en
  115 bestaande authenticated cases groen, geen rebuild. AMD64-emulatie en Mac
  Chrome zijn geen native Sanctuary-eindbewijs; bestaande mobile-capturebeperking
  is vastgelegd. Er is nog geen volledig groene twee-job Release CI.
