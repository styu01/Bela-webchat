# Biztonság és felelős használat

## Nyilvános repó — ne tölts fel éles adatot

**A repository nyilvános.** A forrásközzététel nem jogosít fel a tulajdonos privát Telegram-üzeneteinek, agent session adatainak vagy titkainak megosztására.

Nem publikálható:

- `~/marveen/.env`, `~/marveen/store/.dashboard-token`, bármilyen API-kulcs / auth header / cookie;
- `~/marveen/store/claudeclaw.db`, Webchat SQLite és hozzájuk tartozó WAL/SHM fájlok;
- `p598-backup-*`, `p597-backup-*`, `.p598-unified-release.json` és más telepítési mentés;
- felhasználói csatolmányok, Telegram exportok, naplók kitakarás nélkül;
- SSH privát kulcsok, tokenek, helyi DNS/host személyes adatai.

A `.gitignore` védi a tipikus elérési utakat, de **nem helyettesíti az ellenőrzést**: `git status` és `git diff --cached` átvizsgálandó push előtt. Véletlenül publikált tokent azonnal vonj vissza és forgass le; a fájl git historyból történő törlése önmagában nem teszi titkossá.

## Trust boundary

- A Webchat worker ne olvassa a Marveen hitelesítő titkait.
- A broker Unix-sockethez csak a szükséges helyi folyamatok férhetnek hozzá.
- A broker csak szűkített lokális Marveen API-hívásokat engedjen.
- A Marveen üzenetazonosító és feladó ellenőrzése kötelező a válasz kötéséhez.
- A bizonytalan átadás ne legyen automatikusan ismételve.

## Függőségi és kompatibilitási figyelmeztetés

A telepítő konkrét Marveen commitra és ismert SHA-256 hash-ekre rögzített. A biztonsági ellenőrzések megkerülése vagy az ellenőrzőfájlok figyelmen kívül hagyása esetén a feladattükrözés és az üzenetazonosítók nem garantáltan biztonságosak.

## Hibabejelentés

Érzékeny hibát, sérülékenységet vagy kiszivárgott titkot ne nyilvános issue-ba írj. A tulajdonossal privát csatornán egyeztess, és ne mellékelj éles adatbázist vagy titkos naplót.

## Licenc

A projekt nyilvános láthatósága nem jelent automatikus nyílt forráskódú felhasználási engedélyt. A licenc kiválasztása külön tulajdonosi döntés.
