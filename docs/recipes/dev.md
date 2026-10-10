# just dev

For people changing this code.

| Recipe | What it does |
|---|---|
| `test` | Runs the tests. Anything after it goes to pytest, as in `just dev test -k builder`. |
| `cov` | Runs the tests and reports how much of the code they cover. |
| `lint` | Runs `ruff check`. The project lets it fix what it can. |
| `fmt` | Formats the Python with ruff, then the markdown with markdownlint. |
| `pre-commit` | Runs the pre-commit checks on what you have staged. CI runs them on every file. |
| `secrets` | Looks for private keys in the files git tracks. The fuller secret scan runs in CI, because IPA laptops block its program. |
| `update` | Upgrades the locked dependencies and the pre-commit checks. Ruff is pinned in both places, so move them together. |
| `builder-sync` | Updates the survey builder's copy of the limits and the starter survey. A test fails when they drift apart. |
| `sample` | Rebuilds `sample_template.xlsx`. A test fails when it drifts from the starter. |
| `clean` | Deletes `.venv`. |
