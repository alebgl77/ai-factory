# Contributing to Virtual AI Factory

Thanks for helping! This project is a **generic, fictional-data** reference: please never add real
people, customers, credentials or proprietary data.

## Ways to contribute

- **Try it and report**: open an issue with what you expected, what happened and your setup
  (live demo, Codespaces or local Docker).
- **Bring your factory**: share an anonymised `config/` (use cases, cost centres, catalogue) that
  others can learn from. Everything in `config/` is plain YAML.
- **Improve governance content**: corrections to the EU AI Act, NIST AI RMF or ISO/IEC 42001
  mappings are very welcome. Please cite a primary source (Official Journal, NIST publication).
- **Code**: API engines (`api/app/engines/`), connectors, the 3D dashboard, the Docker stack.

## Development

```bash
cp .env.example .env
docker compose --profile sim up -d --build        # full stack, no API key needed
python -m venv .venv && . .venv/bin/activate && pip install -r api/requirements-dev.txt
(cd api && python -m pytest -q)                   # API engines and endpoints
python scripts/validate_config.py                 # config consistency
python scripts/build_snapshot.py --check          # embedded offline snapshot is up to date
npm i --no-save playwright three@0.186.1 && THREE_VENDOR_DIR=node_modules/three node tests/e2e/run.mjs
python scripts/check_licenses.py && scripts/scan_secrets.sh .
```

## Rules of the road

- **Licence**: contributions are accepted under the MIT licence of the repository. Only
  permissive dependencies (MIT, BSD, Apache-2.0, ISC, PSF…). AGPL software (Grafana, Loki) is only
  used as separate, unmodified services.
- **No fine-tuning, no retraining**: the data loop acts through re-indexing, evaluation-set updates
  and routing changes only. This is a design principle of the project.
- **No secret in the browser**: the dashboard only talks to the aggregation API.
- **Bilingual**: English first, French second (see `docs/CONTRACT.md`, section 9).
- Keep the interface contract (`docs/CONTRACT.md`) and the tests in sync with your change.

## Pull requests

Small, focused PRs with a clear description and passing CI. For larger changes, open an issue
first to discuss the design.
