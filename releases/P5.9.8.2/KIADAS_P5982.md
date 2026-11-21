# P5.9.8.2 — egyedi színpanel UX hotfix

Kizárólag frontend. Három módosuló fájl: `app.js`, `index.html`, `style.css`.

## Telepítési út

A P5.9.8.1-ről indító `install_p5982.py --check` ellenőrzi a fájlok és a korábbi telepítési marker SHA-256 egyezését. A `--apply` külön mentést készít a három fájlról és atomikus fájlcserével telepít. A `--verify` SHA-256 ellenőrzést futtat a telepített fájlokra és a mentésre. A `--rollback` visszaállítja a P5.9.8.1 frontendfájlokat, ha nem észlel driftet.

Sem a Marveen üzenetsorához, sem a Telegram szolgáltatáshoz, sem a workerhez, sem a brokerhez, sem a HTTP szolgáltatáshoz nem nyúl. A szolgáltatások újraindítása **nem része a telepítésnek**.

## Funkciók

- 11 érintetlen eredeti sablon
- 25 külön állítható szín, hat csoport, kétoszlopos grid
- fix 620 px-es jobb oldali drawer, saját görgetés, mindig látható gombok
- élő előnézet, Mentés, Mégse, Alaphelyzet
- mentett egyedi paletta megőrzése sablonváltás után
- kizárólag HEX-színek, kontrasztfigyelmeztetés
- CSP-biztos, külső `style.css`; nincs inline `<style>` elem

## Vizsgálatok

- 11/11 beépített sablon – nincs inline felülírás, eredeti CSS teljes egyezése
- 25/25 állítható és menthető szín
- 150 ismételt megnyitás/bezárás
- 8/8 telepítési, driftvédelmi és rollback-teszt
- JavaScript regressziós tesztek PASS
- izolált Chromium elrendezési teszt **hat nézetméreten PASS**, köztük desktop, tablet és mobil
- az éles WSL HTTP/CSP-válaszon végzett böngészőpróba a telepítés után szükséges

Teljes részletek: `TELEPITES_P5982_HU.md`, `TESZTJELENTES_P5982_HU.md`.
