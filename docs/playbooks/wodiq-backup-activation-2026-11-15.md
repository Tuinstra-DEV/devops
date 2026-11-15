# WODIQ-backups activeren op Sanctuary

Uitvoeren door Marcel **op de Mac**, vanuit een gewone interactieve terminal.
SSH vraagt op Sanctuary om sudo; 1Password vraagt lokaal om de bestaande desktop-autorisatie.
Geen secrets plakken. De producer op VPS02 is al geïnstalleerd.

De DevOps-bundel komt exact van `5c03e48f1c869ac8c8f3fcdff5d47135a2a4069a`.
De publisher bevat Console `81f2696857c69415e529151034ab9766ee854a1c` plus productie-Composer-dependencies uit de lockfile; `878563dc` wijzigt alleen de containerbase en raakt deze standalone publisher niet.
De helpers en alle staged bestanden vallen onder `SOURCE_SHA256SUMS`.
De runtime wordt uit de bestaande root-owned installatie gekopieerd en op de bestaande pin gecontroleerd; er is geen nieuwe download of pinwijziging.

Dit blok wijzigt alleen het bestaande backup-framework en de bestaande Console-backupworker. Het stopt de worker/timer tijdens tokenrotatie, behoudt alle zes bestaande Umami-scopes en voegt uitsluitend `tuinstra-prod-02/wodiq/backup.catalog` toe. Een retry roteert opnieuw, zodat een eerdere gedeeltelijke fout geen ongeldige token kan verbergen. De catalogusservice is oneshot en leest het env-bestand iedere start opnieuw.

```bash
(
  set -euo pipefail
  set +x

  # 1. Controleer lokaal de bestaande 1Password-kluis; verwacht precies één bestaand beheerd escrow-item.
  wodiq_escrow_vault=$(/opt/homebrew/bin/op item list --format=json | /opt/homebrew/bin/python3.14 -c 'import json,sys; rows=[r for r in json.load(sys.stdin) if r.get("title")=="Tuinstra production backups — Sanctuary"]; assert len(rows)==1, "Verwacht precies één bestaand Sanctuary escrow-item"; print(rows[0]["vault"]["id"])')
  wodiq_escrow_dir=$(mktemp -d "${TMPDIR:-/tmp}/wodiq-escrow-helper.XXXXXX")
  trap 'rm -f -- "$wodiq_escrow_dir/onepassword-escrow.py"; rmdir -- "$wodiq_escrow_dir"' EXIT
  git -C /Users/marceltuinstra/Projects/tuinstra-dev/devops/code show 5c03e48f1c869ac8c8f3fcdff5d47135a2a4069a:backup/onepassword-escrow.py > "$wodiq_escrow_dir/onepassword-escrow.py"

  # 2. Controleer beide staged bundels; verwacht alleen PASS (geen sudo of geheimen).
  ssh -n mtuinstra@sanctuary.tuinstra.dev '
    set -eu
    cd /home/mtuinstra/tuinstra-backup-install
    test "$(cat SOURCE_COMMIT)" = 5c03e48f1c869ac8c8f3fcdff5d47135a2a4069a
    echo "2fe6a7137192fb11fa46499eec06b37287a67dd18f2a912d234709a543d0958a  SHA256SUMS" | sha256sum --check --quiet
    sha256sum --check --quiet SHA256SUMS
    python3 ./scripts/verify-install-inputs.py --root . --manifest ./install-input/manifest.json
    cd /home/mtuinstra/tuinstra-console-worker-install
    test "$(cat console-backup-worker-release.sha)" = 81f2696857c69415e529151034ab9766ee854a1c
    echo "d70830290228c7261075cdee565687a2f046a3dd9aeaf802498873ad76796f16  SOURCE_SHA256SUMS" | sha256sum --check --quiet
    sha256sum --check --quiet SOURCE_SHA256SUMS
    python3 -B ./test-catalog-scope-retry.py
    echo "PASS: beide bundels gecontroleerd"
  '

  # 3–10. Activeer via één interactieve SSH-sessie; een fout stopt de remote reeks.
  # De escrowstap hieronder blijft ook na een remote fout lopen, zodat de plaintext handoff kan worden verwijderd.
  wodiq_activation_rc=0
  ssh -t mtuinstra@sanctuary.tuinstra.dev '
    set -eu
    cd /home/mtuinstra/tuinstra-backup-install

    # 3. Haal exact de herstelhelper op; verwacht digest 18cfe3... en een succesvolle pull.
    sudo docker pull docker.io/library/postgres:17-alpine@sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73

    # 4. Installeer het bestaande framework; verwacht installation complete en behoud van bestaande identiteiten.
    sudo ./scripts/install-sanctuary-backups

    # 5. Controleer timers; verwacht WODIQ cycle rond 05:00 en fallback rond 06:15 (met jitter).
    sudo systemctl list-timers --all "tuinstra-backup-*wodiq*" --no-pager

    # 6. Maak en controleer een echte offsite backup; verwacht succesvolle run/check en catalogus met WODIQ-herstelpunt.
    sudo /usr/local/sbin/tuinstra-backup-admin run wodiq
    sudo /usr/local/sbin/tuinstra-backup-admin check wodiq
    sudo /usr/local/sbin/tuinstra-backup-admin catalog wodiq

    # 7. Herstel geïsoleerd; verwacht PASS pas na ledger/count-vergelijking én opruimen van de tijdelijke DB/container.
    sudo /usr/local/sbin/tuinstra-backup-admin restore-test wodiq latest

    # 8. Stop de backupworker en publisher tijdelijk; dit raakt geen WODIQ-services of Console-hubcontainers.
    sudo systemctl stop console-backup-catalog.timer console-backup-catalog.service console-backup-worker.service
    cd /home/mtuinstra/tuinstra-console-worker-install

    # 9. Hergebruik de vertrouwde runtime (hash 54158c...); verwacht PASS en release 81f26968 installed.
    sudo ./reuse-publisher-runtime.py
    sudo ./install-console-backup-worker

    # 10. Voeg alleen de catalogusscope toe, roteer privé en herstart; verwacht zeven scopes, active/active en publicatiebewijs.
    sudo ./add-wodiq-catalog-scope.py
    sudo systemctl restart console-backup-worker.service
    sudo systemctl enable --now console-backup-catalog.timer
    sudo systemctl start console-backup-catalog.service
    sudo systemctl is-active console-backup-worker.service console-backup-catalog.timer
    sudo systemctl show console-backup-catalog.service --property=Result --property=ExecMainStatus
    wodiq_publish_invocation=$(sudo systemctl show console-backup-catalog.service --property=InvocationID --value)
    test -n "$wodiq_publish_invocation"
    sudo journalctl "_SYSTEMD_INVOCATION_ID=$wodiq_publish_invocation" --output=cat --no-pager | grep -Fx "Published recovery-point catalog for tuinstra-prod-02/wodiq."
    echo "PASS: worker actief; WODIQ-catalogus gepubliceerd"
  ' || wodiq_activation_rc=$?

  # 11. Stream de private handoff direct naar de bestaande 1Password-escrowhelper; geen lokaal secretbestand.
  # Verwacht escrowed:true, verified:true en secretCount:8. Bij fout wordt de handoff NIET verwijderd.
  ssh -n mtuinstra@sanctuary.tuinstra.dev 'cat /home/mtuinstra/.local/share/tuinstra-backup-escrow.json' |
    /opt/homebrew/bin/python3.14 "$wodiq_escrow_dir/onepassword-escrow.py" escrow --op /opt/homebrew/bin/op --vault "$wodiq_escrow_vault"

  # 12. Verwijder direct na succesvolle escrow/readback uitsluitend de plaintext handoff; verwacht PASS.
  ssh -n mtuinstra@sanctuary.tuinstra.dev 'rm -- /home/mtuinstra/.local/share/tuinstra-backup-escrow.json && test ! -e /home/mtuinstra/.local/share/tuinstra-backup-escrow.json && echo "PASS: plaintext escrow-handoff verwijderd"'

  # Alleen alles groen als ook de eerdere activering is geslaagd.
  test "$wodiq_activation_rc" -eq 0
  echo 'PASS: WODIQ backup, hersteltest, publisher en escrow afgerond'
)
```

Bij een fout blijft het blok fail-closed; voer de niet geslaagde stap pas opnieuw uit nadat de oorzaak bekend is. Een fout vóór het maken van de handoff geeft bij stap 11 een ontbrekend bestand; er is dan niets te escroen. Een 1Password-fout laat de root-private credentials en user0600 handoff intact voor herstel; verwijder de handoff uitsluitend na succesvolle escrow/readback. De tokenhelper bewaart root0600 rollbackkopieën van het oude env-bestand; een oud token na een rotatie is niet meer geldig. De helper kan bij een retry met dezelfde zeven scopes een nieuw geldig paar maken.

Rollbacklocaties: framework `/var/lib/tuinstra-backup/install-rollback.*`; vorige gestagede DevOps-bundel `/home/mtuinstra/tuinstra-backup-install.before-wodiq-5c03e48f-f52407ee`; publisher releases onder `/opt/tuinstra-console-worker/releases/`, private env-kopieën naast `/etc/tuinstra-console-worker/worker.env`. De bestaande runtime-pin blijft `54158c17e8bbb03b8426a68dfef027e65a6c6fe604299067a73ef6c1c0512d59`.
