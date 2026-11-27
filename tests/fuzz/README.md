# Security boundary fuzzing

These Atheris targets run the production functions directly, with coverage
instrumentation on their imports and callbacks. They never start the API, make
network requests or read credentials.

- `fuzz_metadata.py`: compares the LiteLLM metadata parser against a separate
  JSON duplicate-key and semantic oracle. It exercises nested/escaped duplicates,
  nonobjects, malformed UTF-8 represented as Python text, recursion, the exact
  65,536-character limit, and dictionaries/`None`. JSON constants and escaped
  surrogates retain the existing Python JSON parser semantics.
- `fuzz_peers.py`: compares proxy IP normalization and trusted-network membership
  against standard-library address constructors and a numerical IPv4-mapped IPv6
  oracle. An independent character allowlist checks logging values, including
  control characters, trailing newlines and the 64-character limit.
- `test_harnesses.py`: replays boundary cases and proves the assertions fail when
  duplicate-key acceptance, mapped-IP identity or log framing is deliberately
  broken using temporary in-process mocks. Each mock is restored by its context.

Use Linux x86_64 and Python 3.12 (the hash lock selects its official PyPI wheel).
From the repository root, the same commands as CI are:

```sh
python -m venv .venv-fuzz
. .venv-fuzz/bin/activate
python -m pip install --disable-pip-version-check --require-hashes --only-binary :all: -r api/requirements.txt -r .github/tools/fuzzing/requirements.txt
export PYTHONPATH=api
export PYTHONDONTWRITEBYTECODE=1
python -m unittest discover -s tests/fuzz -p 'test_*.py' -v
export RUNNER_TEMP="$(mktemp -d)"
mkdir -p "$RUNNER_TEMP/fuzz/metadata" "$RUNNER_TEMP/fuzz/peers" "$RUNNER_TEMP/fuzz/crashes"
python tests/fuzz/fuzz_metadata.py "$RUNNER_TEMP/fuzz/metadata" tests/fuzz/corpus/metadata \
  -runs=25000 -max_total_time=90 -timeout=5 -max_len=65537 -rss_limit_mb=1024 \
  -artifact_prefix="$RUNNER_TEMP/fuzz/crashes/"
python tests/fuzz/fuzz_peers.py "$RUNNER_TEMP/fuzz/peers" tests/fuzz/corpus/peers \
  -runs=25000 -max_total_time=90 -timeout=5 -max_len=1024 -rss_limit_mb=1024 \
  -artifact_prefix="$RUNNER_TEMP/fuzz/crashes/"
```

The checked-in small corpus is read as seeds; generated inputs go into the first,
temporary corpus directory. Each target stops at 25,000 executions or 90 seconds,
whichever comes first. CI also limits each input to five seconds, each process to
1 GiB, and the whole job to ten minutes. It runs for every pull request, main
push, weekly schedule and manual dispatch. Failures keep their crash reproducer
as an artifact for seven days. To replay one, pass its path as the only input
argument to the corresponding target; report security findings privately using
`SECURITY.md`.
