# P5.9.8 változásjegyzék

- Egyetlen kiadási ZIP a P5.9.7.2 közös fő Béla-sessiones átjáróval, korlátozott Unix-socket IPC-brokerrel, a teljes Webchat frontenddel és a hotfixelt `app.js` fájllal.
- Egységes telepítő: `install_p598.py --check|--apply|--verify|--pause|--enable|--rollback`.
- A jelenlegi működő P5.9.7.2+UI változat biztonságos *átvétele* (változatlan futtatási környezet, nulla szolgáltatás-újraindítás).
- Tiszta P5.9.6.1 telepítési útvonal teljes frontend- és backendfájlokkal; rollbackkor az eredeti `app.js` visszaáll.
- Új `test_fault_recovery.py`: valódi izolált broker- és workerfolyamatok SIGKILL-megszakítása négy kritikus ponton.
- Új `test_unified_release.py`: telepítés, átvétel, feladatsor-védelmi kapuk, SHA-drift és visszaállítás tesztjei.
- Korábbi 23 átjáróteszt, 11 telepítőteszt és a JavaScript UI-tesztek megtartva.
- Megtartott követelmény: nincs közvetlen Telegram service vagy tmux vezérlés; nincs külön Claude CLI; nincs automatikus feladat-újraküldés.
- A bizonytalan POST-visszaigazolás nem automatikusan helyreállítható; az állapot megmarad manuális egyeztetéshez.
