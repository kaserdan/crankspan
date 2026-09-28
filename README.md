# Crankspan — Bike Wear & Tear Tracking

Moderní deterministický tracker opotřebení komponent na kole napojený na Strava API v souladu s licenčními podmínkami Stravy.

## Soulad s podmínkami Stravy (Terms & Conditions)
1. **Žádné LLM / AI zpracování**: Všechny kalkulace opotřebení (km a hodiny provozu) probíhají přes čistě deterministické vzorce a agregace.
2. **Přísně privátní data (Owner-Only)**: Každý uživatel vidí pouze svá vlastní kola a komponenty. Žádné veřejné žebříčky ani sdílená garáž.
3. **Pravidlo 7 dní & žádný archiv raw aktivit**: Z aktivit ze Stravy se ihned extrahují pouze souhrnné metriky (vzdálenost, čas, převýšení), které se přičtou k aktivním komponentám daného kola. Raw GPS záznamy ani celá aktivita se neukládají.
4. **Branding**: Použito standardní „Connect with Strava“ tlačítko a povinná atribuce „Powered by Strava“.

## Spuštění projektu

1. Aktivace venv:
```bash
source venv/bin/activate
```

2. Konfigurace `.env`:
Ujisti se, že soubor `.env` obsahuje:
```env
STRAVA_CLIENT_ID=283045
STRAVA_CLIENT_SECRET=...
APP_BASE_URL=http://localhost:8000
DATABASE_URL=sqlite+aiosqlite:///crankspan.db
SECRET_KEY=...
PORT=8000
```
*Poznámka:* Pokud aplikaci provozuješ na doméně (např. přes Cloudflare tunnel), nastav `APP_BASE_URL=https://tvoje-domena` a na https://www.strava.com/settings/api nastav příslušnou callback doménu. Pro lokální testování stačí nechat callback doménu `localhost`.

3. Spuštění serveru:
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Po spuštění otevři v prohlížeči `http://localhost:8000`.
