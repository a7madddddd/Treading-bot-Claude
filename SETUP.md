# Setup Guide — Windows

Full step-by-step instructions to run the paper trading bot on a
local Windows machine. Follow in order; every step is required.

---

## 1. Prerequisites — one-time install

### 1.1 Python 3.11 (exact version required)

The project pins to Python 3.11. Not 3.10, not 3.12 — many libraries
check the version and some syntax won't parse elsewhere.

1. Download Python 3.11 for Windows:
   https://www.python.org/downloads/release/python-3119/
2. Pick the installer: **Windows installer (64-bit)**.
3. During install, **tick "Add python.exe to PATH"** at the first screen.
4. Verify in PowerShell:
   ```powershell
   python --version
   ```
   Must print `Python 3.11.x`. If it says 3.12 or 3.10 you have a
   conflict — use `py -3.11` instead of `python` in later commands.

### 1.2 Git

1. Download: https://git-scm.com/download/win
2. Install with defaults.
3. Verify:
   ```powershell
   git --version
   ```

### 1.3 PowerShell (built-in on Windows 10/11)

Open **Windows PowerShell** (NOT CMD) — all commands below assume it.

---

## 2. Clone the repository

```powershell
cd $HOME
mkdir -p projects
cd projects
git clone https://github.com/a7madddddd/Treading-bot-Claude.git
cd Treading-bot-Claude
git checkout claude/youthful-goodall-4cr0ei
```

The last line switches to the active development branch. If you want
the stable `main`, skip it.

---

## 3. Python dependencies

The project uses only Python's standard library plus `pytest` for
tests. Create an isolated virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell refuses to run the activation script with an execution
policy error, run this ONCE (then retry Activate.ps1):

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

Install test deps:

```powershell
pip install --upgrade pip
pip install pytest
```

Verify Python finds the project modules:

```powershell
$env:PYTHONPATH = "src"
python -c "import engine.engine; print('ok')"
```

Must print `ok`.

---

## 4. Environment variables

The project reads 10+ secrets from env vars. **Never commit them to
Git.** Set them in your PowerShell session OR permanently via Windows
Settings.

### 4.1 For this session only (fast, non-persistent)

Paste into PowerShell:

```powershell
$env:ALPACA_API_KEY_ID       = "<your Alpaca paper key id>"
$env:ALPACA_API_SECRET_KEY   = "<your Alpaca paper secret>"
$env:ALPACA_BASE_URL         = "https://paper-api.alpaca.markets"
$env:TELEGRAM_BOT_TOKEN      = "<your Telegram bot token>"
$env:TELEGRAM_CHAT_ID        = "<your chat id>"
$env:TELEGRAM_ADMIN_USER_IDS = "<your Telegram user id>"
$env:PERPLEXITY_API_KEY      = "<your Perplexity key>"
$env:FINNHUB_API_KEY         = "<your Finnhub key>"
$env:FRED_API_KEY            = "<your FRED key>"
$env:TIINGO_API_KEY          = "<your Tiingo key>"
$env:POLYGON_API_KEY         = "<your Polygon key>"
$env:ALPHA_VANTAGE_API_KEY   = "<your Alpha Vantage key>"
```

Replace `<your ...>` with actual values.

### 4.2 Permanently (survives reboots)

Open: **Windows Settings → System → About → Advanced system
settings → Environment Variables → User variables → New...**

Add each variable one by one. Then **close and reopen PowerShell**
for them to take effect.

### 4.3 Verify

```powershell
echo $env:ALPACA_API_KEY_ID
```

Should print the key (not empty). Repeat for 2-3 others to confirm.

**IMPORTANT:** `ALPACA_BASE_URL` must be exactly
`https://paper-api.alpaca.markets` with NO `/v2` suffix — the code
adds `/v2` itself.

---

## 5. Run the tests (sanity check)

Before any real run, confirm the code works:

```powershell
$env:PYTHONPATH = "src"
python -m pytest -q
```

Expected: **1116 passed** (or a slightly higher number).

If this fails:
- Re-check Python version (step 1.1).
- Re-check PYTHONPATH is set to `src`.
- Read the failing test name — it points to the broken area.

---

## 6. Running the trading engine (paper only)

### 6.1 Confirm market status + account

```powershell
python scripts/run_paper_session.py --symbols TSLA,GOOGL --max-hours 0.02 --skip-confirm
```

This runs for ~1 minute as a smoke test. Watch for:
- `[OK] Alpaca /v2/account status=ACTIVE`
- `[OK] Alpaca /v2/clock is_open=True/False`
- `[OK] Telegram bot reachable`

If any of these fail, fix that integration before continuing.

### 6.2 Daily universe selection (D-0048)

Each trading morning, before the engine, run:

```powershell
python scripts/run_universe_selection.py --whitelist AAPL,MSFT,NVDA,GOOGL,AMZN,META,TSLA,QQQ,SPY
```

This builds today's symbol pool from Alpaca market data. The engine
reads the resulting snapshot via `--universe-mode snapshot`.

### 6.3 Full paper session (watch for 6-7 hours of trading)

```powershell
python scripts/run_paper_session.py `
  --universe-mode snapshot `
  --symbols TSLA,GOOGL,QQQ `
  --max-hours 7 `
  --skip-confirm
```

The backtick ` is PowerShell's line continuation (equivalent to `\`
on Linux). The engine runs until market close or 7h, whichever
first.

To stop early: press `Ctrl+C` once — the engine shuts down cleanly.

---

## 7. Common Windows-specific issues

### 7.1 "python" not found but installed

Windows "App execution aliases" sometimes override Python. Open
**Settings → Apps → Advanced app settings → App execution aliases**
and disable the Python aliases. Then use `py -3.11` instead of
`python`.

### 7.2 SSL / certificate errors on Alpaca or Telegram

Corporate firewalls and some VPNs inject their own TLS certificate
and break Python's SSL verification. Workarounds:

1. Disconnect VPN and retry.
2. If on corporate network, ask IT for a Python-compatible CA bundle
   and set:
   ```powershell
   $env:REQUESTS_CA_BUNDLE = "<path to .pem>"
   $env:SSL_CERT_FILE = "<same path>"
   ```

### 7.3 Encoding errors on logs (`UnicodeEncodeError`)

PowerShell's default encoding chokes on Arabic text in log lines.
Fix once per session:

```powershell
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
chcp 65001
```

### 7.4 `paper_session.sqlite` locked

If the engine crashed and the DB appears locked:
1. Make sure no `python.exe` is running (Task Manager).
2. Delete the `.db-wal` and `.db-shm` sidecars if they exist.
3. Retry.

### 7.5 Git push refused

The branch `claude/youthful-goodall-4cr0ei` requires push access.
If you fork, push to your own fork and open a PR to the original.

---

## 8. Daily workflow — once setup is working

1. Morning (before 09:30 ET market open):
   ```powershell
   cd $HOME\projects\Treading-bot-Claude
   .\.venv\Scripts\Activate.ps1
   $env:PYTHONPATH = "src"
   git pull
   python scripts/run_universe_selection.py --whitelist <your list>
   python scripts/run_paper_session.py --universe-mode snapshot --symbols TSLA,GOOGL,QQQ --max-hours 7 --skip-confirm
   ```

2. During trading hours: Telegram alerts for proposals, approvals
   via ✅/❌ buttons.

3. At 16:00 ET: engine auto-stops (max-hours).

4. End of week: commit any config changes you made, review results.

---

## 9. Where to get the API keys

| Service | Where to sign up | Free tier? |
|---|---|---|
| Alpaca (paper) | https://alpaca.markets | Yes — paper account free |
| Telegram Bot | https://core.telegram.org/bots#how-do-i-create-a-bot | Free |
| Perplexity | https://www.perplexity.ai/settings/api | Paid, has free credit |
| Finnhub | https://finnhub.io/register | Yes — 60 req/min free |
| FRED | https://fredaccount.stlouisfed.org/apikey | Yes — unlimited free |
| Tiingo | https://api.tiingo.com | Yes — 1000 req/day free |
| Polygon | https://polygon.io/dashboard/signup | Yes — 5 req/min free |
| Alpha Vantage | https://www.alphavantage.co/support/#api-key | Yes — 25 req/day free |

For paper trading only (what we do today), **Alpaca + Telegram**
are the only strictly required keys. The others are optional and
only used by the D-0050 enrichers behind their `--enable-*` flags.

---

## 10. Troubleshooting checklist

If a command fails, work through this list:

1. `python --version` — is it 3.11?
2. `.\.venv\Scripts\Activate.ps1` — is the venv active (shell prompt
   should show `(.venv)`)?
3. `$env:PYTHONPATH` — does it equal `src`?
4. `$env:ALPACA_API_KEY_ID` — not empty?
5. `python -m pytest tests/engine/ -q` — do engine tests pass?
6. Error message — copy the full traceback and search for the
   deepest `File "...line X"` — that points at the failing module.

If stuck, paste the full command + full error here and I will
diagnose it.
