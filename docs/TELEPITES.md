# Telepítés, átvétel, verziókezelés és visszaállítás

## Előfeltételek

- WSL2 alatt működő `systemd --user`; stabil, már regisztrált `bela-channels` session.
- A meglévő Marveen checkout legyen ellenőrzött (`bfd79f75` commit) és a saját titkai helyben, korlátozott jogosultsággal legyenek elérhetők.
- Python 3, Node.js, `tmux`, `systemctl --user` és `journalctl` rendelkezésre áll.
- **Futó vagy bizonytalan feladat mellett tilos telepítést/rollbacket indítani.** A telepítő ezt ellenőrzi.
- A ZIP-ből kibontott, változatlan kiadási fájlok legyenek ugyanabban a mappában; ne futtasd a telepítőt kézzel egy régebbi vagy részben módosított mappából.

## Biztonságos eljárás

```bash
# A kibontott P5.9.8 kiadás könyvtárában:
python3 -B install_p598.py --check
# Csak PASS esetén:
python3 -B install_p598.py --apply
python3 -B install_p598.py --verify
```

### A ág: működő P5.9.7.2 + UI-hotfix átvétele

A bájtpontos egyezőség igazolása után a P5.9.8 kiadásjelző és privát mentés készül, de a Webchat és Telegram komponensei **nem** indulnak újra, és az `enabled` mező nem változik. A 2026. november 20-i valós WSL-próbán ez az ág sikeresen lefutott, és valamennyi szolgáltatás PID-je változatlan volt.

### B ág: tiszta P5.9.6.1-ről indulás

Települnek az egységes backend+frontend komponensek, elkészül a mentés, a broker és a worker elkülönített szolgáltatásként működik. A feldolgozás **`enabled=0`**, amíg kézi próba után kifejezetten nem engedélyezed:

```bash
python3 -B install_p598.py --enable
```

Bejövő feladatok szüneteltetése:

```bash
python3 -B install_p598.py --pause
```

A pause nem szakítja meg a Marveenhez korábban már átadott fő-session-feladatot.

## Visszaállítás

Csak aktív vagy bizonytalan átadás nélkül:

```bash
python3 -B install_p598.py --verify
python3 -B install_p598.py --rollback
```

- **Átvett pilot:** a P5.9.8 nyilvántartási jelzőt vonja vissza, a P5.9.7.2+UI futva marad.
- **Tiszta telepítés:** az eredeti P5.9.6.1 fájlokat, köztük az `app.js`-t állítja vissza; a Webchat feldolgozás szünetel.
- Ha a fájlok időközben változtak, a rollback biztonsági okból megállhat. Ne használj `--force` kerülő megoldást.

## Amit tilos

- Ne másold át a `~/marveen/.env`, `.dashboard-token`, SQLite-adatbázis vagy `p598-backup-*` fájlokat a nyilvános repóba.
- Ne futtasd a P5.9.7.2 régi `install_p597.py` vagy `patch_ui.py` rollbackjét a P5.9.8 kiadási jelző mellett.
- Ne állítsd le a teljes WSL-t egy webchates hibakísérlet kedvéért; az az eredeti Telegramot is leállítaná.
- Ne ismételd meg automatikusan az `--apply` műveletet hibás kimenet után: először ellenőrizd a naplót és a telepítő állapotát.

## Verzió visszakeresése

A forrás hash-manifestje: `MANIFEST_SHA256.json`. A futó P5.9.8 helyi kiadásjelzője a `~/bela-f0-dev/webchat-p2/.p598-unified-release.json`, a biztonsági mentés helyi `p598-backup-*` könyvtárba kerül. Ezek nem publikus fájlok.
