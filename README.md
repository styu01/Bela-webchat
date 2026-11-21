# Béla Webchat — P5.9.8.3 (stabil WSL kiadás)

**Az eredeti Marveen/Béla-session böngészős felülete, kétirányú Telegram-tükrözéssel.**

Ez a repository a **2026. november 21-én éles WSL-környezetben telepített és ellenőrzött P5.9.8.3** forrásállapotát tartalmazza. A felhasználó ellenőrzése alapján mind az öt `systemd --user` szolgáltatás aktív, a `recovery_state=healthy`, a Telegram API utolsó HTTP-kódja `200`, a legutóbbi tíz tükrözött esemény `done`, és a történeti bizonytalan `#24` esemény `uncertain` maradt. Ez az állapot ellenőrzésének pillanatképe, nem 24/7 garancia.

## A jelenlegi verzió

| Réteg | Kiadás | Lényeg |
|---|---|---|
| Közös Béla-session, broker, worker | P5.9.8 | Stabilizált közös üzenetfolyam, Telegram-védelem |
| Kinézet, egyedi színpanel | P5.9.8.2 | 11 gyári sablon, külön testreszabás és mentés, `style.css` |
| Telegram-tükrözés és állapotjelzés | **P5.9.8.3** | Bizonytalan kézbesítés izolálása, kapcsolat-helyreállítás, karantén és diagnosztika |

A `webchat_sync_sender.py`, `webchat_http.py`, `app.js`, `index.html` és `style.css` a **jelenleg telepített kombinált P5.9.8.3** forrásait adják. A forrásokból kimaradt a privát Marveen checkout, `.env`, bot token, SQLite-adatbázis és felhasználói fájl.

## Kritikus biztonsági invariánsok

- Egy bizonytalanul elküldött Telegram-üzenetet **nem küldünk újra automatikusan**; a karanténba került `uncertain` esemény megmarad.
- Egy új, még nem elküldött esemény ne álljon le tartósan egy másik üzenet bizonytalan kézbesítése miatt.
- Hitelesítési és konfigurációs hiba esetén teljes szinkronzárolás marad érvényben.
- A Telegramos Béla session, a worker és a broker **nem** indítható újra Webchat-hiba javításának mellékhatásaként.

## Telepítés / frissítés

**Ez nem önállóan telepíthető alkalmazás.** A P5.9.8.3 hotfix **egy már telepített P5.9.8.2** állapothoz készül, és szigorú forrásfájl-ellenőrzést végez. A korábbi kiadások telepítői nem használhatók vakon a mostani (felülírt) root forrásfájlokkal. Ha új gépre telepítesz, használd az adott kiadás **verziópontos archívumát** vagy a megfelelő korábbi tagjét, és csak dokumentált sorrendben frissíts.

- [P5.9.8.2 – színpanel és telepítés](docs/TELEPITES_P5982_HU.md)
- [P5.9.8.3 – Telegram Mirror Recovery telepítése](docs/TELEPITES_P5983_HU.md)
- [P5.9.8.3 változásjegyzék](docs/KIADAS_P5983.md)
- [P5.9.8.3 tesztjelentés](docs/TESZTJELENTES_P5983_HU.md)
- [Eredeti P5.9.8 történeti dokumentáció](docs/history/README_P598.md)
- [Architektúra](docs/ARCHITEKTURA.md), [üzemeltetés](docs/UZEMELTETES.md), [biztonság](SECURITY.md)

## Tesztek – forrás checkoutból

```bash
python3 -B test_p597.py
python3 -B test_p597_install.py
python3 -B test_unified_release.py
python3 -B test_fault_recovery.py
cd releases/P5.9.8.2 && python3 -B test_css_contract.py && node test_theme_state.mjs && python3 -B -m unittest -q test_install_p5982 && cd ../..
python3 -B -m unittest -q test_mirror_recovery test_installer_p5983
node --check app.js
node test_ui.mjs
node test_ui_p5983.mjs
```


Az eredeti P5.9.8.2 kiadási források és telepítő a `releases/P5.9.8.2/` könyvtárban, egymással kompatibilis állapotban vannak. A P5.9.8.3 telepítő a jelenlegi gyökérből használható, de kizárólag az előírt éles előfeltételek mellett.

A korábbi P5.9.8-os `MANIFEST_SHA256.json` külön történeti másolatot kapott (`docs/history/MANIFEST_P598_SHA256.json`); a gyökérbeli manifest az aktuális forrásállományok hashét tartalmazza.

## Korlátok

A Telegram Bot API elveszett HTTP-válasza után általánosan nem bizonyítható, hogy az adott üzenetet a szolgáltatás már kézbesítette-e. A rendszer ezért nem tud garantált exactly-once kézbesítést ígérni. A bemutatott éles teszt a normál kézbesítést igazolta; a hálózati hiba utáni automatikus helyreállítás izolált tesztekkel lett ellenőrizve.

**Licenc:** az elérhető forrás nem jelent automatikusan szabad újrafelhasználási engedélyt; külön licenc nincs megadva.
