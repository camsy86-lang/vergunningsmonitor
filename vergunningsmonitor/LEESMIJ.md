# Vergunningsmonitor Limburg

Deze tool volgt de omgevingsvergunningen voor ingedeelde inrichtingen en activiteiten (IIOA) in Limburg op. Hij haalt elke dag de gegevens op uit de publieke Mercator-dienst van de Vlaamse overheid (`lu:lu_omv_iioa_v2`), legt ze naast de vorige versie en toont het resultaat in een online dashboard.

## Wat zit erin

| Bestand | Wat het doet |
|---|---|
| `monitor.py` | Haalt alle records binnen de zone rond Limburg op en houdt alleen de Limburgse gemeenten over (via postcode, NIS-code of gemeentenaam). Daarna vergelijkt het script met de vorige run en schrijft het de data voor het dashboard weg. Er zijn geen extra Python-pakketten nodig. |
| `docs/index.html` | Het dashboard, met een kaart, een doorzoekbare tabel, recente wijzigingen, een detailvenster, de gemeenten met de meeste dossiers en een CSV-export. |
| `docs/data/feed.xml` | Een RSS-feed met nieuwe en gewijzigde dossiers. Die kun je volgen in een feedlezer, Outlook of Slack. |
| `.github/workflows/monitor.yml` | Laat het script elke ochtend gratis draaien op GitHub. |
| `state/snapshot.json` | Het geheugen van de vorige run. Wordt automatisch aangemaakt. |

## Zo zet je het online (GitHub, gratis, ongeveer 15 minuten)

1. Maak een account aan op github.com, als je er nog geen hebt.
2. Klik op **New repository** en geef het een naam, bijvoorbeeld `vergunningsmonitor`.
   - Kies **Public** als het dashboard voor iedereen zichtbaar mag zijn.
   - Kies **Private** als je het afgeschermd wilt houden. Let op: GitHub Pages werkt bij een privé-repository alleen met een betaald account. Voor een afgeschermd dashboard kun je dus beter samenwerken met de IT- of datadienst van de krant.
3. Upload de volledige inhoud van deze map, inclusief de verborgen map `.github`. Dat kan via **Add file → Upload files**, of je sleept de bestanden erin.
4. Ga naar **Settings → Pages** en kies bij Source: *Deploy from a branch*, branch `main`, map `/docs`. Klik op Save.
5. Ga naar het tabblad **Actions**, kies *Vergunningsmonitor* en klik op **Run workflow**.
6. Na een paar minuten staat het dashboard op `https://<jouw-gebruikersnaam>.github.io/vergunningsmonitor/`.

Daarna draait de monitor elke dag om ongeveer 6.15 uur.

## Eerst lokaal testen (aanbevolen)

```bash
python monitor.py --inspect     # toont de velden en 3 voorbeeldrecords; schrijft niets weg
python monitor.py               # eerste run: legt de basislijn vast
cd docs && python -m http.server 8000
# open daarna http://localhost:8000 in je browser
```

**Belangrijk:** voer `--inspect` eerst uit. Ik kon de Mercator-dienst bij het bouwen niet bereiken en ken de exacte veldnamen dus niet. Het script herkent de velden automatisch aan hun naam (gemeente, postcode, status, klasse, datum…). Klopt dat niet, pas dan `ROLE_PATTERNS` bovenaan in `monitor.py` aan. Je kunt daar ook rechtstreeks de juiste veldnaam invullen, bijvoorbeeld `"gemeente": [r"^gemeentenaam$"]`.

## Hoe je de resultaten leest

- **Eerste run:** alle dossiers worden als basislijn opgeslagen. Er verschijnt nog niets als 'nieuw'.
- **Nieuw:** het dossier stond er bij de vorige run nog niet in.
- **Gewijzigd:** minstens één veld is veranderd, bijvoorbeeld de status of de klasse. Het dashboard toont de oude en de nieuwe waarde.
- **Verdwenen:** het dossier staat niet meer in de bron. Dat kan betekenen dat de vergunning vervallen of ingetrokken is, maar het kan ook een technische aanpassing zijn. Controleer dat altijd.
- **Veiligheidsrem:** verdwijnt in één keer meer dan de helft van de records, dan stopt het script en overschrijft het niets. Meestal is dan de bron tijdelijk defect.

## Kanttekeningen voor de journalistiek

- Mercator is een **afgeleide dataset**. Het officiële dossier staat in het Omgevingsloket. Controleer elk dossier daar of bij de gemeente of provincie voor je publiceert.
- De datum 'eerst gezien' is de dag waarop **de monitor** het dossier voor het eerst zag. Dat is niet de datum van de aanvraag of de beslissing.
- Wijzigingen die tussen twee runs komen en weer verdwijnen, mis je. De monitor draait één keer per dag.
- Wil je het vaker laten draaien? Pas dan de cron-regel aan in `monitor.yml`, bijvoorbeeld `0 */6 * * *` voor elke 6 uur.

## Gebied aanpassen

In `monitor.py` kun je `LIMBURG_BBOX`, `LIMBURG_GEMEENTEN` en `POSTCODE_MIN`/`POSTCODE_MAX` aanpassen. Wil je alleen een paar gemeenten volgen, maak de gemeentelijst dan kleiner.
