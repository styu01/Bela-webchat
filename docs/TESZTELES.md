# Tesztstratégia, bizonyítékok, fennmaradó kockázatok

## Módszer és különválasztás

A kiadás tesztjei izolált ideiglenes mappákban, saját SQLite-állományokkal, korlátozott Unix-sockettel és mock localhost Marveen API-val működnek. A `test_fault_recovery.py` valódi Python alfolyamatokat indít és `SIGKILL`-el állít le, **de nem az éles Telegramot vagy WSL szolgáltatásokat**.

## A kiadási tesztmátrix (2026-11-20)

| Csomag | Tesztek | Eredmény |
|---|---:|---|
| `test_p597.py` | 23 | PASS |
| `test_p597_install.py` | 11 | PASS |
| `test_unified_release.py` | 6 | PASS |
| `test_fault_recovery.py` | 4 | PASS, 3 külön teljes ismétlésben is |
| `node --check app.js` | – | PASS |
| `node test_ui.mjs` | – | PASS |
| ZIP és manifest | 29 ellenőrzött fájl | PASS |

Összesen **44 Python-teszt**, valamint JavaScript regressziók. Egy korai összefűzött tesztfuttatás időkorlátba ütközött; azt nem számoltuk sikernek, az érintett teszteket később megismételtük.

## Hibakísérletek

1. **Worker SIGKILL, hosszú már átadott munka:** a `remote_id` megmarad, 1 POST és 1 válasz.
2. **Broker SIGKILL, hosszú már átadott munka:** a worker a visszaállt brokerrel folytatja, 1 POST és 1 válasz.
3. **Broker SIGKILL POST után, ACK előtt:** `uncertain`, nincs második POST.
4. **Worker SIGKILL POST közben:** `uncertain`, nincs duplikált beküldés.

## Az éles funkcionális próbák

A 2026. november 20-i WSL integrációs próbában Webchat és Telegram együtt használta az eredeti Bélát. A `BROKER_RESTART_OK` teszt Webchat `seq=91`, Marveen `id=5663` azonosítóval `answered/done` állapotba került, és mindkét felületen kézbesítést kapott. A külön HTTP-, worker- és brokerújraindítási próbákban a Telegram PID-je és tmux pane-je változatlan maradt.

Az egységes P5.9.8 átvétel ezután sikeresen lefutott: `origin=adopted-p5972-hotfix`, Webchat `enabled=1`, nyitott Marveen-feladat 0, szolgáltatás-PID változás nem történt, a kiadás `--verify` ellenőrzése PASS. A #76–#82 régi feladatok zároltak maradtak.

## Fennmaradó kockázatok

- A közös Claude-session és Marveen üzenetirányító miatt nincs bizonyított teljes Telegram–Webchat erőforrásizoláció.
- Nincs kontrollált éles, **folyamatban levő hosszú** Claude-feladat alatti SIGKILL teszt, mert az eredeti session veszélyeztetése elfogadhatatlan lett volna.
- A `posting/uncertain` kézi egyeztetést igényel, automatizált pontosan-egyszer távoli hatás nem garantálható minden hibánál.
- UI: a régi `P5.9.7 PILOT` címke és aláhúzásos Markdown renderelési hiba megmaradt.
- Nem készült többnapos terheléses vagy 24/7 rendelkezésre állási mérés.

A repo tesztjeinek PASS kimenete fontos bizonyíték, de nem SLA.
