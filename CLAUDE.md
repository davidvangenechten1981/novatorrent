# <Projectnaam>

<Eén tot drie zinnen: wat is dit project en voor wie?>

## Commando's
- Installeren: `uv sync`
- Starten: `<...>`
- Testen: `uv run pytest`
- Linter: `uv run ruff check .` en `uv run ruff format .`

## Structuur
- `src/`   broncode
- `tests/` tests
- `docs/`  specs en beslissingen
- `scripts/hooks/` git-hooks (geheimencontrole)

## Werkafspraken
- Antwoord in het Nederlands; code en commentaar in het Engels.
- Maak eerst een kort plan en wacht op akkoord voordat je grote wijzigingen doet.
- Draai `uv run pytest` en `uv run ruff check .` voordat je zegt dat iets klaar is.
- Werk op een eigen branch per taak (`git switch -c feature/<naam>`), nooit rechtstreeks op `main`.
- Commit kleine, duidelijke stappen. Push alleen na mijn akkoord.

## Veiligheid
- Verwijder nooit bestanden. Verplaats ze naar een map `_to_delete/` en meld het.
- Tools die bestanden aanpassen of verplaatsen hebben standaard een dry-run en schrijven een logbestand om terug te draaien.
- Geen geheimen (API-keys, wachtwoorden, `.env`) in code, logs of git.
- Installeer geen nieuwe pakketten zonder het te melden.
