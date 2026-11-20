# P5.9.8 offline tesztjelentés

## Környezet

Tesztfuttatási környezet: elkülönített modell-konténer, ideiglenes mappák és szimulált systemd; az éles WSL-folyamatok nem futnak itt és nem voltak megérintve. A kimenetek és a friss kicsomagolás ellenőrzése a kiadás során történik.

## Esetek

| Tesztcsomag | Tartalom |
|---|---|
| `test_p597.py` | Az átjáró jogosultság-, üzenetazonosító-, egyszeri küldés- és fájlcsatolási regressziója |
| `test_p597_install.py` | Core telepítés, broker-üzemeltetés, hibás indulás, P5.9.6.1 visszaállítás |
| `test_unified_release.py` | P5.9.8 teljes telepítés, élő pilot bájtpontos átvétele, rollback, nyitott munka védelme, drift-védelem |
| `test_fault_recovery.py` | Négy izolált SIGKILL-kísérlet, külön workerrel, brokerrel, SQLite-tal és hamis Marveen HTTP API-val |
| `node test_ui.mjs` | Hat kliens-kérésállapot, zárolt régi feladatok, polling, aktív munka elkülönítése |

## Tényleges eredmények (2026-11-20)

- 23/23 `test_p597.py` – átjáró és feladatsor
- 11/11 `test_p597_install.py` – alaptelepítő és rollback
- 6/6 `test_unified_release.py` – egységes kiadás, átvétel, változatlanság, rollback
- 4/4 `test_fault_recovery.py` – izolált worker/broker kilövések; a frissen kibontott csomagból a teljes hibatesztcsomag **három egymást követő külön futása sikeres**
- `node --check app.js` – sikeres
- `node test_ui.mjs` – az összes UI-regresszió sikeres
- Mind a 29 kiadási forrásfájl SHA-256 értéke megfelelő a friss ZIP-kibontásban.

Az első, összefűzött teljes tesztfuttatás futtatási időkorlátba ütközött a hibatesztek közelében; a hibatesztcsomagot ezért elkülönítve, teljes egészében és ismételten lefuttattuk. A megismételt futások mind sikeresek. Az időkorlátos futást nem számítjuk sikernek.

**Összesen: 44 Python unit/integrációs teszt, továbbá JavaScript UI-regressziók.** A sikeres futások nem helyettesítik a felhasználó WSL-környezetének telepítési ellenőrzését.

## Fail-closed elvek

- `submitted` feladatnál worker/broker újraindítás után csak a **meglévő** Marveen-üzenet azonosítójáról olvasható válasz; új POST nem történhet.
- `posting` utáni megszakadásnál a feladat `uncertain` lesz, és az egyszeri küldést védő SQLite naplója tiltja az azonos UUID újbóli elküldését.
- Visszaállítás tilos, ha `posting`, `submitted`, `uncertain`, vagy éles Webchat futó/bizonytalan állapot létezik.
- A közös session bizonyításához korábbi valós felhasználói WSL-próbák állnak rendelkezésre, azonban **az új kiadást még nem telepítettük** a felhasználó gépére.

## Mi NEM bizonyított

Sem az offline tesztek, sem a rövid éles funkcionális próbák nem garantálják a 24/7 rendelkezésre állást vagy azt, hogy a közös Claude-session hosszú feladat mellett sosem lassítja a Telegramot. Éles, aktív feladat közben szándékosan nem lőttünk le semmilyen szolgáltatást.
