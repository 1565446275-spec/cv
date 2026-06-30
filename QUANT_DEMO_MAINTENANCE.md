# Quant Demo Maintenance

This document records the repeatable operations for the quant resume demo.

## Public URL

- Main public page: <https://1565446275-spec.github.io/cv/quant-demo.html>
- Project directory: `C:\Users\ASUS\Desktop\cv-vercel`
- Git branch: `gh-pages`
- GitHub remote: `https://github.com/1565446275-spec/cv.git`

GitHub Pages is the primary free public deployment. The page includes frontend-only demo fallback, so it can still show backtest, alerts, ETF prediction, and futures/options demo results even when no backend is online.

## Local Frontend

```powershell
cd C:\Users\ASUS\Desktop\cv-vercel
npx live-server --port=5500
```

Open:

```text
http://127.0.0.1:5500/quant-demo.html
```

If `npx live-server` is unavailable, the public GitHub Pages URL can be used directly.

## Local Backend

Preferred command:

```powershell
cd C:\Users\ASUS\Desktop\cv-vercel
.\backend\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

If the virtual environment does not exist:

```powershell
cd C:\Users\ASUS\Desktop\cv-vercel
py -m venv backend\.venv
.\backend\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\backend\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Health check:

```powershell
curl http://127.0.0.1:8000/health
```

Expected response:

```json
{"status":"ok"}
```

## Updating The Public Page

```powershell
cd C:\Users\ASUS\Desktop\cv-vercel
git status --short
git add quant-demo.html backend requirements.txt Procfile railway.json render.yaml runtime.txt QUANT_DEMO_MAINTENANCE.md
git commit -m "Update quant demo"
git push origin gh-pages
```

After pushing, wait 1-2 minutes and open the public URL with `Ctrl+F5` to bypass browser cache.

## Proxy And GitHub Troubleshooting

The user's proxy software usually listens on:

```text
127.0.0.1:7890
```

Check proxy and GitHub connectivity:

```powershell
Get-NetTCPConnection -LocalPort 7890 -ErrorAction SilentlyContinue
Test-NetConnection github.com -Port 443
```

If GitHub push hangs or times out:

1. Open the proxy software.
2. Confirm Windows system proxy is enabled.
3. Retry:

```powershell
cd C:\Users\ASUS\Desktop\cv-vercel
git push origin gh-pages
```

## API Address Rules

The frontend accepts an API address through either:

- `?api=http://127.0.0.1:8000`
- a saved public API address in the browser

For the public GitHub Pages demo, do not require a backend. If the backend request fails, the page should fall back to generated demo data.

Do not use `file://` as the API address. If the page reports `当前 API 地址：file://`, open it from GitHub Pages or from `http://127.0.0.1:5500/quant-demo.html`.

## Common Errors

### Failed to fetch

Meaning: the browser cannot connect to the configured backend API.

Fix order:

1. Use the GitHub Pages public URL and rely on frontend fallback.
2. If testing local API, start the backend on port `8000`.
3. Open `http://127.0.0.1:8000/health`.
4. If the page uses an old API address, clear it in the page settings or append `?api=http://127.0.0.1:8000`.

### HTTP 502

Meaning: the tunnel or deployed backend is down.

Fix order:

1. Prefer GitHub Pages frontend-only fallback for resume demonstrations.
2. Restart local backend.
3. If using a cloud host, redeploy or restart the service.

### Unable to fetch stock data

Meaning: external quote providers failed or the symbol/date range is invalid.

The project should try Tencent/SZSE/yfinance/Stooq and then use demo fallback. For resume demos, the UI should still produce a presentable result.

### Others cannot open the link

Use this link:

```text
https://1565446275-spec.github.io/cv/quant-demo.html
```

Do not share:

- `http://127.0.0.1:5500/...`
- `file:///...`
- temporary Cloudflare tunnel URLs

## Optional Cloud Backend

Render/Railway/VPS can host the FastAPI backend, but they are optional for the resume demo.

Render start command:

```text
uvicorn backend.main:app --host 0.0.0.0 --port $PORT
```

After deploying a backend, open:

```text
https://1565446275-spec.github.io/cv/quant-demo.html?api=https://your-backend-domain
```

For free and stable resume sharing, GitHub Pages is the default.

