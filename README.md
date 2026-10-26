# PriceWatch

[![CI](https://github.com/fleury-fcn/pricewatch-automation/actions/workflows/ci.yml/badge.svg)](https://github.com/fleury-fcn/pricewatch-automation/actions/workflows/ci.yml)
[![Couverture](https://img.shields.io/endpoint?url=https://fleury-fcn.github.io/pricewatch-automation/badges/coverage.json)](https://github.com/fleury-fcn/pricewatch-automation/actions/workflows/ci.yml)
[![Pipeline quotidien](https://github.com/fleury-fcn/pricewatch-automation/actions/workflows/daily-pipeline.yml/badge.svg)](https://github.com/fleury-fcn/pricewatch-automation/actions/workflows/daily-pipeline.yml)
[![Open in Streamlit](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://pricewatch-automation.streamlit.app)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](pyproject.toml)
[![Licence MIT](https://img.shields.io/badge/licence-MIT-green)](LICENSE)

> Pipeline automatisé de suivi de prix e-commerce : collecte → nettoyage → stockage →
> analyse → alertes → rapport PDF/HTML, d'une seule commande ou d'un clic.

- **Démo en ligne** : <https://pricewatch-automation.streamlit.app>
- **Dernier rapport (mis à jour chaque jour)** : <https://fleury-fcn.github.io/pricewatch-automation/>

## Démarrage rapide

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pricewatch seed-demo        # 90 jours d'historique de démonstration
pricewatch run              # collecte réelle + analyse + alertes + rapport
streamlit run app/streamlit_app.py
```

Déploiement (Streamlit Cloud, GitHub Pages, Docker) : voir [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

Licence : [MIT](LICENSE)
