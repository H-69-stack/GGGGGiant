# Development Quick Start

This project’s local backend is **Flask** (`backend/run_dev.py`, port **5050**), not FastAPI. The frontend is **Vue 3 + Vite** (`npm run dev`, port **3000**, proxies `/api` to 5050).

## First time

```powershell
.\dev.ps1
```

Opens two windows: backend with auto-reload, frontend with Vite HMR. Then open **http://localhost:3000** (do not expect this script to open a browser).

Do **not** open port **5050** in the browser. That is the Flask API. In DEV it no longer serves `frontend/dist` (the copy in this repo is the August production build and does not contain Profile QA / DEV panel). There is **no port 3030**.

New QA UI lives on **上传分析 → 模型文件上传** (`/upload/model`), not on 工艺优化.

Optional: `$env:GTAPP_PYTHON = 'D:\python\envs\occ\python.exe'` if PATH python cannot import flask.

## After changing QA code

Save → wait for backend reload → in **DEV · Profile QA** click **重新运行 QA**.

This reuses the cached raw profile (no STP re-parse).

## After changing the STP reader

Save → wait for backend reload → click **重新读取 STP + QA**.

## Fixture shortcut

DEV panel lists files from `backend/tests/fixtures/stp/` only (no arbitrary disk paths).

## Quick automated tests

```powershell
.\test-profile-qa.ps1
```

Runs Profile QA unit/API/real-STP tests plus DEV shortcut tests. Not the whole repo.

## Production

`GTAPP_DEV_TOOLS` is ignored on the **release** channel. `run_dev.py` is not shipped as the production entry. DEV routes are not registered unless tools are explicitly enabled in a non-release build.
