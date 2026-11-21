# P5.9.8.3 – Tesztjelentés

## Izolált tesztek (nem éles Telegram-fiókon)

- 19 mirror/state-machine regressziós és stresszteszt: normál visszaigazolás; 0/timeout/kivétel; 5xx; 429 és retry_after; 400/401/403; eltérő HTTP-válasz; rossz chat ID; sikertelen getMe próba; elveszett visszaigazolás; többrészes üzenet közepén hiba; régi állapot megőrzése; 100 új esemény továbbküldése; valós tesztfolyamat SIGKILL utáni állapothelyreállítás; félbemaradt `sending` védelme.
- 13 telepítőteszt: telepítés–verify–rollback; automatikus forrásfájl-visszaállítás hibás másoláskor; előző engedélyezési állapot megőrzése; sérült backup és idegen fájlverzió elutasítása; symlink-ellenőrzés; aktív feladat és `sending` tiltása; védett PID változásának észlelése; megszakadt quiesce utáni flag-visszaállítás; SQLite mentés integritása.
- 5 meglévő frontend UI-regressziós ellenőrzés: kérésállapotok, régi zárolt feladatok, aktív munka, várakozó sor, polling átmenetek.
- Python szintaxisellenőrzés és Node.js `--check`.

## Biztonsági invariánsok

- A korábban `sending` állapotba lépett rész nem kerül automatikus újra elküldésre.
- `uncertain` esemény marad `uncertain`; az azonos esemény további részei `held` állapotba kerülnek.
- A következő önálló `ready` esemény kizárólag sikeres getMe kapcsolatpróba és várakozás után küldhető.
- 400/401/403: teljes tükrözés továbbra is tiltva.
- Az inbound Telegram szolgáltatás, az IPC-broker, a worker és a Marveen-session módosítása tilos.

## Nem igazolt még

Az éles WSL telepítés és a tényleges Telegram Bot API kézbesítés. A fejlesztőkörnyezet az éles Telegram-szolgáltatáshoz és tokenhez nem fér hozzá. Az elfogadáshoz telepítés után szükséges egy új Webchat-kérdés és Béla-válasz végponttól végpontig történő vizsgálata.
