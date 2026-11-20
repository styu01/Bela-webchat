# Architektúra és rendszerhatárok

## Tervezési cél

A Telegramos Béla az eredeti Claude/Marveen sessionben működik; a Webchat csak második kezelőfelület. **Nem indítunk második Claude CLI-t, nem indítunk közvetlenül tmuxot, és nem állítjuk le a Telegramot** a Webchat telepítése vagy újraindítása közben.

## Fő összetevők

1. **`bela-channels.service`** — eredeti Telegramot kiszolgáló Marveen/Béla futtatás, a tényleges session tulajdonosa.
2. **`bela-webchat.service`** — helyi HTTP/Node megjelenítési felület és Python vezérlőszolgáltatás.
3. **`bela-webchat-worker.service`** — SQLite-feladatokat feldolgozó fő session-átjáró (`webchat_main_bridge.py`).
4. **`bela-webchat-broker.service`** — korlátozott jogú AF_UNIX broker (`webchat_bridge_broker.py`); itt történik a Marveen hitelesített üzenet-API elérése.
5. **`bela-webchat-mirror.service`** — Telegram-szinkron/tükrözés, önállóan kezelhető háttérfolyamat.

A worker nem olvassa közvetlenül a Marveen admin titkait: kizárólag a broker felé kommunikál. A broker csak localhost API-hoz férhet hozzá, a Unix socket és a szűkített műveletek korlátozzák az elérhető felületet.

## Üzenetútvonal és azonosítók

1. A felhasználó beküldi a Webchat-kérést; keletkezik egy Webchat `seq` és helyi állapot.
2. A worker egy feladatot igényel; ellenőrzi a korábbi és legacy feladatokat, a feldolgozás engedélyezését és az egyszeri küldést védő nyilvántartást.
3. A broker továbbítja a kérést a Marveen üzenetsorába (`webchat-relay` → `bela`), a Marveen által adott egyedi `remote_id` pedig tartósan kapcsolódik a helyi `seq`-hez.
4. Az eredeti Béla-session a saját eszközeivel, környezetében dolgozik; az üzenetet `done` és `result` adatokkal lezárja.
5. A worker **ugyanazt** a `remote_id`-t olvassa vissza. A Webchat állapota `answered`; a mirror továbbítja az üzenetet és kézbesítési státuszt a Telegramnak.

**Lényeges invariant:** `submitted` után nincs új POST ugyanarra a Webchat-kérésre. A hiányzó vagy bizonytalan visszaigazolás önmagában nem indok újraküldésre.

## Hibahatárok és konzisztencia

- **HTTP újraindítás:** nem állítja le a workert, brokert vagy Telegramot; az HTTP readiness késhet pár másodpercet.
- **Worker újraindítás, `submitted`:** az eredeti távoli üzenetazonosítónál folytatja az ellenőrzést, nem készít második feladatot.
- **Broker újraindítás:** a worker az IPC-kapcsolat helyreállását követően tudja folytatni a lekérdezést.
- **Broker vagy worker összeomlás POST/ACK közben:** a helyi `uncertain` állapot és a tartós fence megakadályozza a duplikált beküldést. Ezt nem szabad automatikusan `done`-nak tekinteni.
- **Párhuzamos Telegram-terhelés:** a két felület ugyanazt a Claude-sessiont használja. A systemd-folyamatfüggetlenség nem jelent session-, kontextus- vagy API-kvótafüggetlenséget.

## Adattárolás

A Webchat helyi SQLite-adatbázisában a feladatok és átadási kapcsolatok szerepelnek. A Marveen saját SQLite-adatbázisában az `agent_messages` sorok találhatók. Ezek az élő adatok **nem részei a GitHubnak**, és nem szabad őket forráskódként beküldeni.

## Verziókompatibilitás

A csomag ellenőrzése ismert P5.9.6.1 vagy P5.9.7.2+UI-hotfix állapotból indul, és a Marveen konkrét, `bfd79f75` commitjára van rögzítve. Új Marveen-commitnál új kompatibilitási teszt kell; nem szabad `--apply`-jal „megpróbálni, hátha működik”.
