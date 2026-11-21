# Béla Webchat P5.9.8.2 – Egyedi színpanel javítása

## Mi volt a hiba?

1. A P5.9.8.1 a panel CSS-ét `<style>` elemként, JavaScriptből illesztette be, miközben a helyi HTTP-szerver CSP-je `style-src 'self'`. Az ilyen stíluslapot a böngésző blokkolhatja, ezért a panel a dokumentum normál folyamatában jelent meg, és szétverte az elrendezést.
2. A P5.9.8.1 a kiválasztott 11 sablon színeit kézzel megadott, pontatlan változókkal írta felül. Ez rontotta az eredeti sablonok színhűségét.

## Mi változott?

- Külső CSS: a **meglévő, ellenőrzött** `style.css` elejének teljes, bájtpontos megtartása és a panel elkülönített stílusainak hozzáfűzése. Nincs inline stíluslap, a CSP-t nem lazítjuk.
- A panel **fix jobb oldali drawer** (620 px legfeljebb), saját görgetéssel, alul rögzített Mentés/Mégse/Alaphelyzet gombokkal. A chat a háttérben ugyanott marad.
- Hat csoportban **25 színmező**, desktopon kéthasábos grid, 510 px alatti képernyőn egyhasábos elrendezés.
- Mind a **11 eredeti sablon** CSS-változói megmaradnak; beépített téma választásakor **nincs felülíró inline paletta**.
- A „Saját színek” panel az éppen aktív sablon **valódi kiszámított CSS-értékeiből** indul ki. Különálló fejléc- és oldalsávszín is választható egyedi módban.
- Élő előnézet, mentés, mégse, eredeti sablonra visszaállítás. A mentett saját paletta böngésző-újratöltés és más sablon kiválasztása után is megmarad ugyanabban a böngészőprofilban.
- HEX-ellenőrzés, kontrasztfigyelmeztetés (4,5:1 referencia).
- A P5.9.8.1 korábbi, hibásan kézzel felülírt egyedi színpalettáját a javítás **nem migrálja automatikusan**. A legutóbbi beépített sablont állítja vissza, a felhasználó tiszta palettával indulhat.

## Biztonság

A javítás **kizárólag 3 frontend fájlt** érint:

- `app.js`
- `index.html`
- `style.css`

**Nem módosítja** a Telegram/Marveen rendszert, a workert, a brokert, a háttéradatbázist vagy a systemd unitokat. **Nem indít újra szolgáltatást**.

Az `install_p5982.py` csak akkor enged telepíteni, ha az alapfájlok SHA-256 hash-e megegyezik a jóváhagyott P5.9.8.1 verzióval, valamint a `.bela-theme-p5981.json` marker a megfelelő verziót jelzi. A `style.css` várható eredeti SHA-256 értéke: `35bcaccb33af3857b87528737ce51cc05cfef421c8579decc5c36defccd27596`.

Eltérés esetén **STOP** történik, nincs automatikus javítás vagy erőltetett felülírás.

## Telepítés

Töltsd le a ZIP-et a Windows `Downloads` mappába, csomagold ki egy külön WSL mappába, majd:

```bash
python3 -B install_p5982.py --check
python3 -B install_p5982.py --apply
python3 -B install_p5982.py --verify
```

Kötelező böngésző oldali frissítés: **Ctrl+Shift+R**.

Ha megnyitáskor a panel a teljes oldalra kerül, vagy a sablon színei megváltoznak, ne küldj újabb Webchat-kérést; készíts képernyőképet, és használd a visszaállítást:

```bash
python3 -B install_p5982.py --rollback
```

A visszaállítás a **P5.9.8.1 frontend állapotra** történik; a P5.9.8.1 telepítési marker és eredeti mentés érintetlen marad.

**Ne** futtasd a régebbi `install_p598.py` és `install_theme_panel.py` módosító vagy visszaállító parancsait a P5.9.8.2 fájljaira, mert azok korábbi hash-ekre épülnek.

## Offline tesztelés

A csomag gyökerében:

```bash
node --check app.js
node test_ui.mjs
node test_theme_state.mjs
python3 -B test_css_contract.py
python3 -B test_install_p5982.py
```

A Python-tesztek nem igényelnek külső könyvtárakat. A JavaScript-tesztekhez Node.js szükséges. A telepítéshez Python 3 elegendő.

A Chromium/Playwright tényleges renderelése **hat képernyőméreten sikerült** izolált, helyben összeállított oldal és mockolt Webchat-adatok használatával. A helyi hálózati címre történő böngészős navigáció adminisztratív tiltása miatt a teszt nem a valódi WSL webszerver HTTP/CSP válaszát jelenítette meg. A felhasználó WSL-es böngészőjében külön ellenőrzendő: panel helye, 2 oszlop, saját görgetés, Mentés/Mégse, 11 sablon, 25 szín, átméretezés. Az offline és szintaktikai tesztek önmagukban nem garantálják a pixelpontos megjelenést.
