# contract-cli — Installable package

This project ships a **CLI** (`contract-cli`) that can be installed via `pip` and distributed by you (private index, Git, or PyPI).

---

## Step 1 — Install build tools (one-time)

```bash
pip install build
```

---

## Step 2 — Build the package

From the project root (where `pyproject.toml` is):

```bash
python -m build
```

This creates:

- `dist/contract_cli-0.1.0.tar.gz` (source distribution)
- `dist/contract_cli-0.1.0-py3-none-any.whl` (wheel)

---

## Step 3 — Install locally (for testing)

**From the built wheel:**

```bash
pip install dist/contract_cli-0.1.0-py3-none-any.whl
```

**Or install in editable mode from source (no build):**

```bash
pip install -e .
```

Then run:

```bash
contract-cli
```

The CLI uses `BASE_URL` from the environment (or default `https://contract-31az.onrender.com`). Optionally use a `.env` in the current directory or set `BASE_URL` before running.

---

## Step 4 — Distribution (you manage it)

### Option A — Install from Git (you push tags/branches)

Users install your repo:

```bash
pip install git+https://github.com/willerdev/contract.git
# or a specific branch/tag:
pip install git+https://github.com/willerdev/contract.git@v0.1.0
```

You manage releases by pushing tags (e.g. `v0.1.0`) or a release branch.

### Option B — Private PyPI index (Gemfury, CodeArtifact, or self-hosted)

1. Upload the built artifacts:

   ```bash
   pip install twine
   twine upload --repository-url https://your-index-url/simple/ dist/*
   ```

2. Users install with your index:

   ```bash
   pip install --extra-index-url https://your-index-url/simple contract-cli
   ```

You manage which versions are available on your index.

### Option C — Public PyPI

1. Create an account on [pypi.org](https://pypi.org).
2. Upload:

   ```bash
   twine upload dist/*
   ```

3. Users install:

   ```bash
   pip install contract-cli
   ```

You manage releases and versions on PyPI.

### Option D — Host the wheel yourself

1. Put `contract_cli-0.1.0-py3-none-any.whl` on a server (e.g. GitHub Releases, S3, or your own HTTPS).
2. Users install:

   ```bash
   pip install https://your-server.com/path/contract_cli-0.1.0-py3-none-any.whl
   ```

You manage the file and the URL.

---

## Step 5 — Bumping the version

Edit `version` in `pyproject.toml` (e.g. `0.1.1`), then run:

```bash
python -m build
```

Use the new files in `dist/` for upload or distribution.

---

## Summary

| Step | Action |
|------|--------|
| 1 | `pip install build` |
| 2 | `python -m build` → creates `dist/*.whl` and `*.tar.gz` |
| 3 | `pip install dist/contract_cli-0.1.0-py3-none-any.whl` then `contract-cli` |
| 4 | Distribute via Git, private index, PyPI, or direct wheel URL (you choose) |
| 5 | Bump version in `pyproject.toml` and rebuild when releasing |

The **backend** (FastAPI in `main2.py`) is separate: run it on your server (e.g. Render); the CLI is only the client users install.
