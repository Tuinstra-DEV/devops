# DEV-54 permanente Gate-admission — alleen voorbereiding

Deze directory is het deploymentvoorstel bij het
[transitierunbook](../../../workflows/contracts/gate-compact-transition.md).
Niets hier is geactiveerd. Gebruik de volledige repo-manager niet als live
vervanging: die bevat tevens nog niet uitgerolde parallelpool- en disklogica.

`manager-effective.patch` verandert uitsluitend de bestaande Gate-admission
op de exact geverifieerde live bron. De target bevat de twee permanente rollen,
geen pilotrol en geen pool-, queue-, disk- of credentialwijziging.
`manager-manifest.json` bindt bron, patch, target en de twee workflowrefs aan
de gereviewde providerpin `09ca00db3bc8d3f5624b5473f3b2c651dfdf98f5`.
De compressed testfixture is de niet-geheime managersource, geen configuratie
of credentialbestand.

De lokale test past de patch zonder fuzz toe op de hashgebonden baseline,
controleert de targethash, vergelijkt de AST van alle niet-admissioncode en
voert dezelfde zeven permanente admissiontests op de target uit. De bestaande
generieke en nieuwe admissiontests draaien daarnaast op de repo-manager.

Bij toekomstige activatie: stage uitsluitend een kopie van de actuele bron,
verifieer eerst de bronhash uit de manifest, pas de patch zonder fuzz toe en
verifieer de targethash. Bewaar vóór installatie de originele bytes én
uid/gid/mode in het root-only hersteljournal en bewijs readback. Een gewijzigde
bronhash blokkeert activatie tot de patch opnieuw gereviewd en getest is.
De configuratie krijgt alleen de twee rollen uit het read-only plan; behoud
alle andere instellingen. Groep, image-selector en enable-variabele volgen
uitsluitend de gereviewde volgorde in het runbook. Herstel gebruikt de originele
bron/configmetadata en volledige groepssnapshot, niet de repo-manager.
