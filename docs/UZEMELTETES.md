# Üzemeltetés, megfigyelés és hibakeresés

## Napi állapotellenőrzés (csak olvasás)

```bash
for svc in bela-channels bela-webchat bela-webchat-worker bela-webchat-broker bela-webchat-mirror; do
  printf '%s: ' "$svc"
  systemctl --user show "$svc.service" -p ActiveState -p MainPID --value
done

tmux list-panes -t bela-channels -F '#{pane_id} #{pane_pid}'
python3 -B install_p598.py --verify
curl --noproxy '*' --fail --silent --show-error --max-time 5 \
  -o /dev/null -w 'Webchat HTTP: %{http_code}\n' http://127.0.0.1:3421/
```

A `--verify` parancsot a kibontott, változatlan kiadási könyvtárból futtasd.

## A HTTP készenléti időzítése

A `systemctl --user restart bela-webchat.service` után előfordulhat, hogy a service már `active`, de a Node HTTP még nem hallgat a `127.0.0.1:3421` porton. Ez önmagában nem hiba. A próbán a naplóban az indulás után két másodperccel szerepelt `HTTP ready`. Portteszthez használj korlátos visszapróbálást, ne egy egyszeri `sleep 2`-t.

## Szelektív naplók

```bash
journalctl --user -u bela-webchat.service --since '30 minutes ago' --no-pager -n 60
journalctl --user -u bela-webchat-worker.service --since '30 minutes ago' --no-pager -n 60
journalctl --user -u bela-webchat-broker.service --since '30 minutes ago' --no-pager -n 60
journalctl --user -u bela-webchat-mirror.service --since '30 minutes ago' --no-pager -n 60
```

**Ezeket a naplókat publikálás előtt szerkeszd meg**, mert tartalmazhatnak üzenet-, fájl-, elérésiút- vagy tokenadatot. A titokállományt soha ne olvasd ki hibaelhárítási célból.

## Hibakeresési döntési fa

| Jelenség | Biztonságos első vizsgálat | Kerülendő művelet |
|---|---|---|
| Webchat HTTP nem válaszol | Service állapot, `journalctl`, `ss -ltnp` | Telegram újraindítása |
| Webchat válasz várakozik | Helyi job/bridge állapot és pontos `remote_id` | Ugyanazon kérés újbóli beküldése |
| Broker nem elérhető | Broker szolgáltatás és AF_UNIX socket jogosultság | Titok továbbítása workernek |
| Worker leállt | Csak worker systemd naplója és távoli ID-k ellenőrzése | Éles feladat közbeni ad hoc stresszteszt |
| `uncertain` átadás | Manuális Marveen-egyeztetés, broker-fence és napló | Automatikus újrapróbálás |
| Markdownban hiányzó `_` | Frontend renderelési vizsgálat | Backend válasz módosítása találomra |

## Systemd indulás

A telepítés WSL2 `systemd --user` szolgáltatásokat használ. A vizsgált környezetben mind az öt szolgáltatás `enabled`; a Telegram `Restart=always`, a webchat komponensek `Restart=on-failure`; a worker `After/Wants=bela-webchat-broker.service`. A `Wants` **nem** készenléti garancia. A WSL-disztribúció indulása és a Windows automatikus WSL-indítása külön kérdés.

## Mentés

A telepítő a saját változtatásairól mentést készít; ez nem helyettesíti a Marveen-konfiguráció, a privát SQLite-adatbázis és a titokanyag megfelelő, titkosított, hozzáférésvédett üzleti mentését. A repóban soha ne legyen valós beszélgetés vagy felhasználói csatolmány.
