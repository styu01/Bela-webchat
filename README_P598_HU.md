# Béla Webchat P5.9.8 – egységes közös-session kiadás (RC)

**Tartalom:** P5.9.7.2 IPC-broker + közös fő Béla-session + beépített, korábban külön telepített UI-állapotjavítás (`app.js`). A csomag a forrásokat, a teljes telepítőt, az offline hibateszteket és a visszaállítást **egy ZIP-ben** tartalmazza. Nincs szükség a korábbi UI-hotfix ZIP-re.

**Állapot:** offline tesztelt *release candidate*. A P5.9.7.2+UI pilotot a felhasználó élő rendszerében már sikeresen használta; az új egységesített telepítő és a folyamatmegszakításos hibatesztek az itt készített izolált tesztkörnyezetben futottak. Az új P5.9.8 kiadás telepítési/átvételi lépése **még nem** futott az élő WSL rendszeren.

## Bizonyított hibakezelési viselkedés

| Offline valós folyamatkilövési próba | Elvárt és mért kimenet |
|---|---|
| Worker `SIGKILL` a már átadott (`submitted`) hosszú feladat közben | Az eredeti `remote_id` megmarad, újraindításkor a válasz figyelése folytatódik; **1 POST / 1 válasz** |
| IPC-broker `SIGKILL`, amíg Béla dolgozik | Worker vár, broker újraindítás után ugyanannak az ID-nak az eredményét átveszi; **1 POST / 1 válasz** |
| Broker `SIGKILL` a távoli POST után, de a válasz előtt | `uncertain`, tartós megőrzés, nincs automatikus ismétlés: **1 POST / 0 újraküldés** |
| Worker `SIGKILL` a POST-visszaigazolás előtt | `uncertain`, az egyszer-küldési broker-fence megmarad: **1 POST / 0 újraküldés** |

A próba valódi Python munkafolyamatokat indít és `SIGKILL`-el állít le, de **kizárólag az ideiglenes tesztkönyvtárban indított saját gyerekfolyamatait**. Külön SQLite-fájlokkal, saját AF_UNIX sockettel, saját localhost mock HTTP API-val dolgozik. Nem kapcsolódik az éles Marveenhez, nem indít Claude-ot, nem hív systemd-t vagy tmuxot. Ebből **nem következik**, hogy minden lehetséges valódi üzemhiba tesztelt.

**Fontos kompromisszum:** az éles Marveen saját üzenetirányítója közvetetten tmuxos ébresztést indíthat. A Webchat és a broker ezt közvetlenül nem végzi, de közös Claude-sessionnél a Telegram teljes terhelésfüggetlensége nem garantálható.

## A két támogatott induló állapot

1. **A jelenleg működő P5.9.7.2 + UI-hotfix:** ha minden telepített fájl bájtpontosan egyezik a kiadással, az új `--apply` csak **átveszi a már telepített** rendszert. Biztonsági mentést és kiadási jelzőfájlt készít, **nem cserél programfájlt, nem állít le és nem indít újra** Webchat- vagy Telegram-szolgáltatást, és a feldolgozás `enabled` állapotát sem módosítja. `--rollback` csak ezt az új kiadási regisztrációt vonja vissza; a korábban működő P5.9.7.2+UI változat változatlanul fut tovább.
2. **Tiszta P5.9.6.1:** a teljes P5.9.8-at a csomag saját `app.js` fájljával együtt telepíti (minden létrehozott fájlról mentés készül), a külön Claude-workert kiváltja, a brokert regisztrálja, és **szüneteltetve** hagyja a feladatfeldolgozást (`enabled=0`). `--rollback` a korábbi P5.9.6.1 fájlokat, az eredeti `app.js`-t és worker-indítást állítja vissza. A korábbi lezárt vagy zárolt feladatokat nem játssza újra.

Más, ismeretlen vagy részben telepített állapotnál a telepítő **megáll**, nem próbál találomra javítani. A Marveen forrására rögzített `bfd79f75` commit és az ismert fájlellenőrzőösszegek kötelezőek. Egy későbbi Marveen-verzióhoz külön kompatibilitásvizsgálat szükséges.

## Tesztelés a kibontott ZIP mappájában

```bash
python3 -B test_p597.py
python3 -B test_p597_install.py
python3 -B test_unified_release.py
python3 -B test_fault_recovery.py
node --check app.js
node test_ui.mjs
python3 -B install_p598.py --check
```

A `test_fault_recovery.py` négy izolált folyamatmegszakítási próbája valós gyerekfolyamatokat indít és kilő, de **nem** a futó WSL-szolgáltatásokat. Nem kell az éles Telegramot leállítani.

## Telepítés / meglévő P5.9.7.2 + UI-hotfix átvétele

A sikeres `--check` után **külön lépésben**:

```bash
python3 -B install_p598.py --apply
python3 -B install_p598.py --verify
```

A jelenlegi P5.9.7.2+UI állapotban az eredmény `ADOPTED`: nincs szolgáltatás-újraindítás, a meglévő `enabled=1` változatlan. Új feladatot nem kell küldeni a validáláshoz; a meglévő éles működés korábban már ellenőrzött. A kiadásjelző fájl: `~/bela-f0-dev/webchat-p2/.p598-unified-release.json`, a mentés privát `p598-backup-*` mappába kerül (fájlkópia és Webchat SQLite-pillanatkép). Soha ne töltsd fel a mentést nyilvánosan.

Tiszta P5.9.6.1 telepítéskor a `--apply` után a feldolgozás szünetel. Csak külön jóváhagyott rövid élő teszt előtt:

```bash
python3 -B install_p598.py --enable
```

Szüneteltetés (a már átadott fő-session feladatot nem szakítja meg):

```bash
python3 -B install_p598.py --pause
```

## Visszaállítás

**Csak ha nincs aktív, függő vagy bizonytalan Webchat→Marveen feladat**:

```bash
python3 -B install_p598.py --rollback
```

A rollback nem kapcsolja ki vagy indítja újra a Telegramot. A tiszta telepítés visszaállításakor a Webchat-szolgáltatások változnak, a Marveen nem. A meglévő P5.9.7.2+UI változat „átvétele” esetén a rollback csak a P5.9.8 jelzőfájlt távolítja el, mert a kiadás regisztrálása a működő kódot **nem** módosította. A backup megmarad.

A P5.9.8 kiadás után **ne futtasd** a korábbi `patch_ui.py --rollback` vagy régi `install_p597.py --rollback` parancsot! A régiek nem ismerik az új kiadási jelzőfájlt, és a régi SHA-ellenőrzések sem egységesek. Mostantól csak az `install_p598.py` az irányadó.

## Nem kezelt / még nyitott pontok

- A `posting` állapotban megszakadt, de a fő Béla által esetleg már felvett feladat **nem** kerül automatikusan újraküldésre vagy lezárásra. Manuális egyeztetés és naplóvizsgálat kell: a bizonytalanságot őrizzük, nehogy duplikált munka induljon.
- A Telegram és a Webchat egyetlen élő Claude-sessionjén dolgoznak, ezért teljes erőforrás- és keretfüggetlenséget nem lehet garantálni.
- Az aláhúzások bizonyos kimenetekben továbbra is eltűnhetnek a Webchat Markdown-formázása miatt; ez a csomag az állapotkijelzési hotfixet tartalmazza, nem új Markdown-renderelőt.
- A fenti folyamat-kilövési tesztek izoláltak. Az éles, hosszú ideig futó valódi munka közbeni megszakítást **szándékosan nem végeztük el**.
- A Webchat fejlécében szereplő régi „P5.9.7 PILOT” felirat változatlan: a most élő frontend bájtpontos, újraindítás nélküli átvételéhez ezt nem változtattuk meg.

## Hiba esetén (titkok kiírása nélkül)

```bash
python3 -B install_p598.py --verify
systemctl --user show bela-channels.service -p ActiveState -p MainPID
systemctl --user show bela-webchat-worker.service -p ActiveState -p MainPID
systemctl --user show bela-webchat-broker.service -p ActiveState -p MainPID
journalctl --user -u bela-webchat-worker.service -u bela-webchat-broker.service --since '15 minutes ago' --no-pager -n 80
```

**Soha ne másold ki a `~/marveen/store/.dashboard-token` tartalmát vagy a `~/marveen/.env` titkait.**
