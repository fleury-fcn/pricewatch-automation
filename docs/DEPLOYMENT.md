# Déploiement

## 1. Démo en ligne — Streamlit Community Cloud (gratuit)

Le dashboard fonctionne **sans base de données persistante** : au démarrage, il construit
une base SQLite temporaire à partir du jeu de données versionné
`data/demo/price_history.csv.gz` (60 livres réels, 90 jours d'historique). Le stockage de
Streamlit Community Cloud étant éphémère, c'est le seul mode fiable pour une démo publique.

### Pas à pas

1. Aller sur <https://share.streamlit.io> et se connecter avec le compte GitHub
   **fleury-fcn** (autoriser l'accès aux dépôts publics suffit).
2. Cliquer sur **Create app** → **Deploy a public app from GitHub**.
3. Renseigner :

   | Champ | Valeur |
   |---|---|
   | Repository | `fleury-fcn/pricewatch-automation` |
   | Branch | `main` |
   | Main file path | `app/streamlit_app.py` |
   | App URL (optionnel) | `pricewatch-automation` → `https://pricewatch-automation.streamlit.app` |

4. Ouvrir **Advanced settings** :
   - **Python version** : `3.12` ;
   - **Secrets** : rien d'obligatoire. Pour forcer le mode démo (recommandé) :
     ```toml
     PRICEWATCH_MODE = "demo"
     ```
5. Cliquer sur **Deploy**. Le premier build installe `requirements.txt` (2 à 4 minutes).
6. Vérifier : les indicateurs affichent 60 produits ; le bouton **Lancer le pipeline**
   collecte réellement books.toscrape.com (1 page ≈ 25 s) ou, interrupteur
   « Collecte en ligne » désactivé, rejoue le catalogue embarqué sans réseau.
7. Si l'URL choisie diffère de `pricewatch-automation.streamlit.app`, mettre à jour le
   badge « Open in Streamlit » du README.

> L'application s'endort après quelques jours sans visite : le premier visiteur la
> réveille en un clic (≈ 30 s). Les exécutions lancées depuis la démo disparaissent au
> redémarrage du conteneur — c'est voulu.

### Mettre à jour le jeu de données de la démo

```bash
pricewatch seed-demo --days 90 --refresh-catalogue   # recollecte le catalogue réel
pricewatch run                                       # ajoute le point du jour
pricewatch export-demo                               # écrit data/demo/price_history.csv.gz
git add data/demo && git commit -m "chore(data): actualise le jeu de démo" && git push
```

Streamlit Cloud redéploie automatiquement à chaque push sur `main`.

## 2. Rapport quotidien — GitHub Actions + GitHub Pages

Le workflow [`daily-pipeline.yml`](../.github/workflows/daily-pipeline.yml) s'exécute
chaque jour à 06:00 UTC (et à la demande : *Actions → Pipeline quotidien → Run workflow*).

1. Il restaure l'état publié sur la branche `gh-pages` (base SQLite + rapports archivés) ;
2. initialise un historique de démo au tout premier lancement ;
3. exécute `pricewatch run` (collecte réelle, analyse, alertes, rapport) ;
4. publie le site, en français et en anglais :
   - <https://fleury-fcn.github.io/pricewatch-automation/> : dernier rapport en français ;
   - <https://fleury-fcn.github.io/pricewatch-automation/en/> : dernier rapport en anglais ;
   - `/latest_fr.pdf`, `/latest_en.pdf` ; `/archive/` (30 derniers jours, FR + EN) ; `/data/`.

**Activation de GitHub Pages** (une seule fois) : *Settings → Pages → Source : Deploy from
a branch → `gh-pages` / `(root)`*.

**Alertes réelles** : ajouter les secrets voulus dans *Settings → Secrets and variables →
Actions* (`SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `ALERT_EMAIL_TO`,
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `DISCORD_WEBHOOK_URL`) puis ajouter le canal dans
`alerts.channels` de `config/config.yaml`. Un canal sans secret est simplement ignoré.

## 3. Docker (API + dashboard + pipeline)

```bash
cp .env.example .env          # facultatif : secrets des alertes
docker compose up -d api dashboard
docker compose run --rm pipeline   # une exécution du pipeline
```

- API : <http://localhost:8000/docs>
- Dashboard : <http://localhost:8501>

La base et les rapports sont persistés dans les volumes `pricewatch-data` et
`pricewatch-reports`.
