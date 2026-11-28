# DEV-54 permanente Gate-admission

Deze directory is het deploymentvoorstel bij het
[transitierunbook](../../../workflows/contracts/gate-compact-transition.md).
Controleer de actuele status en live readback vóór iedere handeling. Gebruik de volledige repo-manager niet als live
vervanging: die bevat tevens nog niet uitgerolde parallelpool- en disklogica.

`manager-effective.patch` verandert uitsluitend de bestaande Gate-admission
op de exact geverifieerde live bron. De configuratie krijgt drie permanente rollen
(backend, release-build, release-verify), zonder pilottoelating of pool-, queue-, disk- of credentialwijziging.
`manager-manifest.json` bindt bron, patch, target en de twee workflowrefs aan
de gereviewde providerpin `a083c789196ee8cd89b4ab3253c458d8d1de6dd6`.
De compressed testfixture is de niet-geheime managersource, geen configuratie
of credentialbestand.

De lokale test past de patch zonder fuzz toe op de hashgebonden baseline,
controleert de targethash, vergelijkt de AST van alle niet-admissioncode en
voert dezelfde permanente admissiontests op de target uit. De bestaande
generieke en nieuwe admissiontests draaien daarnaast op de repo-manager.

Bij toekomstige activatie: stage uitsluitend een kopie van de actuele bron,
verifieer eerst de bronhash uit de manifest, pas de patch zonder fuzz toe en
verifieer de targethash. Bewaar vóór installatie de originele bytes én
uid/gid/mode in het root-only hersteljournal en bewijs readback. Een gewijzigde
bronhash blokkeert activatie tot de patch opnieuw gereviewd en getest is.
De configuratie krijgt alleen de drie rollen uit het read-only plan; behoud
alle andere instellingen. Groep, image-selector en enable-variabele volgen
uitsluitend de gereviewde volgorde in het runbook. Herstel gebruikt de originele
bron/configmetadata en volledige groepssnapshot, niet de repo-manager.

## Eindcontrole en hervatten na herstel

De eindcontrole gebruikt de GitHub-client van de nieuwe manager voor de nieuwe
rollen, met dezelfde bestaande credential en API-origin. De oude client blijft
voor oude policy en herstel; diens validator wijst de nieuwe rollen terecht af.
De regressie voert de echte hashgebonden oude en gepatchte manager uit met lokale
GET-antwoorden. Policy-, repository- en HTTP-fouten blijven herstel activeren;
de operator ziet uitsluitend een foutcategorie en eventuele HTTP-status.

Na een volledig afgeronde rollback gebruikt de reeds bijgewerkte operatorbundle
`retry`. Die herleest het root-only journal en de backup, verifieert de herstelde
manager/config/metadata, selector/image/helper, kandidaat en groep/repositories
via de bestaande herstelmotor en credentialroute. Alleen een geverifieerde
`rolled_back`-staat wordt atomair naar `completed-activation-journal.json` in
de oorspronkelijke backup verplaatst; directorywijzigingen worden gefsynct.
De backup blijft bewaard. Daarna begint de normale activatie met nieuwe volledige
herstelmetadata en een nieuw leeg venster. Een onderbroken archivering verliest
geen bewijs. Een actieve, onvolledige of gewijzigde herstelstaat blokkeert.

Stage en verifieer eerst de gepubliceerde gecorrigeerde entrypoint in de bestaande
operatorbundle; `retry` stageert zelf geen code en leest geen nieuwe credentials.
