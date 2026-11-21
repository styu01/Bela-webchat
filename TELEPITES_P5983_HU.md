# Béla Webchat P5.9.8.3 – Telegram Mirror Recovery

## Kiadás célja

A 2026-11-20 21:15:32 óta ismert `transport_unconfirmed` után a korábbi sender letiltotta az összes további Telegram-tükrözést. Ez a változat minden bizonytalan Telegram Bot API-küldést kizárólag a saját eseményére korlátoz. A már elküldhetőként jelölt `sending` rész soha nem kerül automatikus újrapróbálásra.

**FONTOS:** Ez a release kizárólag a P5.9.8.2 színpaneles Webchatre telepíthető. A live `webchat_sync_sender.py` és `webchat_http.py` eredeti P5.9.6.1/P5.9.8 hashének pontosan egyeznie kell. Más verzió esetén a telepítő megáll, **nem szabad átírni a hash-ellenőrzést**.

## Mit módosít?

Kizárólag:
- `/home/kisss/bela-f0-dev/webchat-p2/webchat_sync_sender.py`
- `/home/kisss/bela-f0-dev/webchat-p2/webchat_http.py`
- `/home/kisss/bela-f0-dev/webchat-p2/app.js`

A színpanel `index.html` és `style.css` fájljai változatlanok. A többi kód (`webchat_isolation.py`, `webchat_presentation.py`, test-fixtures) **csak tesztfüggőség**, nem kerül éles telepítésre.

## Biztonsági műveleti sorrend

1. ZIP és benne a `SHA256SUMS.json` ellenőrzése.
2. Éles források SHA-256 ellenőrzése; előző P5.9.8.2 frontend megkövetelése.
3. Telegram, worker, broker PID + Telegram tmux pane mentése; aktív Webchat-feladat és `sending` rész elutasítása.
4. A mirror `enabled` jelzőjének **ideiglenes 0-ra állítása** SQLite-tranzakcióban. A már elindított Telegram API-kérések lezárásának megvárása (legfeljebb 72 mp).
5. **Kizárólag** `bela-webchat-mirror.service` leállítása; a védett PID-k ellenőrzése.
6. Régi forrásfájlok és konzisztens SQLite-pillanatkép létrehozása (privát mentési mappa, korlátozott jogosultság).
7. Három fájl atomikus cseréje, hash-ellenőrzése.
8. **Kizárólag** `bela-webchat-mirror.service` indítása és `bela-webchat.service` újraindítása.
9. A tükrözés korábbi engedélyezési állapotának visszaállítása és a védett PID-k ismételt ellenőrzése.
10. Hiba esetén forrásfájlok automatikus visszaállítása, amennyiben ez biztonságos; bizonytalan folyamatban lévő küldést nem szakít meg.

**Nem módosítja** a Telegramos Béla / Marveen sessiont, a Telegram szolgáltatást, a workert, az IPC-brokert, illetve a Telegram saját adatbázisát. Nem állítja vissza automatikusan az SQLite-pillanatképet: ezzel új üzenetek vesznének el.

## WSL: ZIP kicsomagolás és telepítés

A ZIP-et a Windows `C:\Users\kisss\Downloads` mappába töltsd.

```bash
set -Eeuo pipefail
ZIP="/mnt/c/Users/kisss/Downloads/bela-webchat-P5.9.8.3-TELEGRAM-MIRROR-RECOVERY-TELEPITO.zip"
DIR="$HOME/codex-review-marveen/webchat-fix/p5983-mirror-recovery"
test -f "$ZIP" || { echo 'STOP: ZIP nem található'; exit 1; }
unzip -tq "$ZIP"
mkdir -p "$DIR"
unzip -oq "$ZIP" -d "$DIR"
cd "$DIR"
python3 -B -m unittest -q test_mirror_recovery test_installer_p5983
node --check app.js
node test_ui_p5983.mjs
python3 -B install_p5983.py --check
python3 -B install_p5983.py --apply
python3 -B install_p5983.py --verify
```

A szállított ZIP SHA-256 értékét a kiadási üzenet külön tartalmazza. A telepítő saját kiadási manifestet is ellenőriz.

**Ha `--check` STOP-pal kilép, ne futtasd a `--apply` parancsot.** Különösen az eltérő fájlverzió, a `sending` állapot és a futó Webchat-feladat a kockázatos.

## Visszaállítás

A telepítő saját mentését használva:

```bash
cd "$HOME/codex-review-marveen/webchat-fix/p5983-mirror-recovery"
python3 -B install_p5983.py --rollback
```

A visszaállítás **nem írja vissza az élő SQLite-adatbázist**, hanem a korábbi sender/HTTP/frontend kódot állítja vissza; az új üzenetek és kézbesítési állapotok megmaradnak. A visszaállító is ellenőrzi a Telegram/worker/broker PID-ket, és csak a mirror/HTTP egységeket indítja újra.

## Meglévő történeti `uncertain` állapot

A 2026-11-20-i #24 esemény továbbra is `uncertain` marad. A javítás nem tekinti azt `done`-nak, nem küldi újra. Az új kód a később létrejövő bizonytalan eseményeket karanténba helyezi, és nem blokkolja a további, még sosem elküldött eseményeket.

## Ellenőrzés telepítés után

1. Webchat: Ctrl+Shift+R. A színpanel működjön és a téma megmaradjon.
2. Küldj **egyetlen új tesztüzenetet** a Webchatben. Az ehhez tartozó Webchat-kérdés és Béla-válasz jelenjen meg Telegramon.
3. Az események `done` állapotba kerüljenek. A korábbi #24 ne változzon.
4. A Telegramos Béla, worker, broker folyamatai maradjanak változatlanok.
5. A `sync_quarantined` szám tartalmazza a történeti bizonytalan eseményeket; nem egy újabb hiba bizonyítéka önmagában.

Nem futtattunk éles Telegram Bot API-próbát, mert a fejlesztőkörnyezet nem tartalmazza a felhasználó titkos tokenjét és WSL szolgáltatásait.
