# DEV-54: tijdelijke activatie op Sanctuary

Voer dit pas uit nadat de betrokken CVE-PR's gemerged zijn en Gate, Tracker,
Notify en Status geen queued/in-progress runs hebben. De scriptguard controleert
Tracker #375, Notify #33 en Status #21 expliciet en zoekt daarnaast open
CVE/PHP-securityhotfixes in alle vier repositories, inclusief Gate. Er wordt geen
Gate-PR-nummer verzonnen. Een onvolledige API-response of fout blokkeert de activatie.

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
  `112666ba3e1ee0fe1a5e6c33aed95d8fae0fa823`.
- Nieuwe manager SHA-256:
  `2ca8a6936811049e20b7c5084e10f98cba90486a8fa754e00acd55ea0a3fc863`.
- Verwachte oude manager/rollback SHA-256:
  `232d37364c0754bd59aefbf44fee9ba5704def038bbda94514975f857dd61e9f`.
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
- Deze commit bevat ook de Tessera Node 24-fix zonder npm-cache/lockfilevereiste.
  Gebruik de eerdere `5f35529…` niet als definitieve nieuwe consumentenpin.

## Root: installatie en tijdelijke sudoers-regel

De agent heeft niets privileged uitgevoerd. Gebruik de exacte download-URL en
scriptchecksum uit de DEV-54-comment bij de gemergde activatie-PR. Download als
root naar een private map onder `/root`, controleer de checksum en installeer:

```sh
install -o root -g root -m 0755 /root/dev54-stage/dev54-activate.sh /usr/local/sbin/dev54-activate.sh
visudo -f /etc/sudoers.d/dev54-activate
```

Plaats uitsluitend de door Marcel gekozen regel:

```sudoers
mtuinstra ALL=(root) NOPASSWD: /usr/local/sbin/dev54-activate.sh, /usr/local/sbin/dev54-activate.sh --rollback
```

Controleer met `visudo -cf /etc/sudoers.d/dev54-activate` en
`visudo -c`. De gebruikelijke sudoers-specificatie zonder argumenten is op zichzelf
geen argument-whitelist; het root-owned script weigert daarom alle argumenten
behalve exact geen argumenten of exact `--rollback`. Het wist de calleromgeving.
Geen aanvullende sudo-rechten, geen algemene shell, geen Full Access.

## Als mtuinstra: één activatie

```sh
sudo -n /usr/local/sbin/dev54-activate.sh
```

Het script controleert de bronhash, hostbestanden, unit/credentialbindings, bestaande
groep/policies, CVE-merges en rustige runs **vóór** de eerste servicewijziging.
Het maakt een tijdgestempelde root-only backup onder `/var/backups/dev54-activation`
van binary, config, unit/drop-in en afgeleide rol-/admissionpolicies. Credentials
worden niet geback-upt of gelogd. De nieuwe manager wordt voor rollback ook lokaal
hashgebonden opgeslagen; rollback downloadt geen executable code.

De manager wordt reconcile-only gestart en laat alle VMs natuurlijk uitlopen;
geen destroy/kill of TTL-overschrijding. Bij leases nabij hun TTL stopt de procedure.
Na de drain blijft admission gepauzeerd. Het script toont dan alleen de bedoelde rolpinwissels en Tessera-wijziging, geen
config- of repositorytabellen. De exacte policyselecties staan alleen in de
beschermde backup. Pas GitHub handmatig aan en typ alleen `GEREED` als dat klaar is.

## GitHub: handmatig tijdens de gepauzeerde fase

Organisatie **Tuinstra-DEV → Settings → Actions → Runner groups →
`sanctuary-trusted-verification`**:

1. Behoud selected/private repositories, public repositories uit en restrictie
   tot geselecteerde workflows aan. Voeg de private repo `sudoku-tessera`
   (ID `1179209966`) toe; behoud de overige geselecteerde repositories.
2. Behoud alle vier oude trusted-verification-refs. Voeg toe:

```text
Tuinstra-DEV/devops/.github/workflows/reusable-trusted-verification.yml@112666ba3e1ee0fe1a5e6c33aed95d8fae0fa823
Tuinstra-DEV/devops/.github/workflows/reusable-gate-pr-security.yml@112666ba3e1ee0fe1a5e6c33aed95d8fae0fa823
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

Een afwijkende API-permissie geeft een blokkade; geen token in de terminal of chat
invoeren en geen credentialscopes verruimen om deze voorbereiding af te ronden.

## Rollback en opruimen

```sh
sudo -n /usr/local/sbin/dev54-activate.sh --rollback
```

Rollback gebruikt uitsluitend de laatste beschermde backup van deze exacte versie.
Het weigert gewijzigde backup-, binary-, config- of unitinhoud. Het draint opnieuw
natuurlijk en toont de oorspronkelijke rolpin-doelen en de terug te draaien selectiewijziging. Herstel die handmatig
(inclusief oude producer-rolref en oorspronkelijke repo-selectie) en typ `GEREED`.
Daarna herstelt het de oorspronkelijke binary én exacte configbytes, dus ook beide
oude rolpins, en controleert de status. Queued oude rol-jobs blokkeren rollback
niet: rollback moet hun oorspronkelijke admission juist kunnen herstellen.
Bestaande VMs worden ook tijdens rollback nooit geforceerd gestopt.

Bij een mislukte of onderbroken apply/rollback blijft admission gepauzeerd; backup
blijft bewaard. Voer geen ad-hoc start of algemene sudo-opdracht uit om dat te omzeilen.

Na succesvolle activatie en controle, of na een afgeronde rollback, verwijdert
Marcel **als root** eerst de tijdelijke sudoers-regel en daarna het script:

```sh
rm /etc/sudoers.d/dev54-activate
visudo -c
rm /usr/local/sbin/dev54-activate.sh
```

Bewaar de beschermde backup voor incidentherstel. DEV-54 blijft Started totdat de
consumerrollout en echte runner-/kostenverificatie afgerond zijn.
