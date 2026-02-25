import os
import json
import time
import secrets
import threading
import requests as requests_lib
from collections import defaultdict
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text, inspect as sql_inspect
import bcrypt as bcrypt_lib
from jose import jwt
from datetime import datetime, timedelta, time as dtime
import traceback

DAILY_RATE = 0.02  # 2% per day

from database import get_db, engine, User, Contract, ContractPlan, Withdrawal, TrustedWallet, RunSession, RunEarnings, PermissionCode, PinResetCode, TelegramLinkToken, TradingAccount
import cryptomus
import metaapi

SECRET_KEY = "secret123"

app = FastAPI()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")


@app.get("/", response_class=HTMLResponse)
def root():
    """Cryptomus domain verification: meta tag must be on the page at your project URL."""
    return """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="cryptomus" content="9094fc19" /></head>
<body><p>OK</p></body></html>"""


@app.get("/health")
def health(db: Session = Depends(get_db)):
    """For Render and load balancers. Returns 200 if app and DB are reachable."""
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(status_code=503, content={"status": "unhealthy", "database": "error"})
    return {"status": "ok", "database": "ok"}


@app.exception_handler(Exception)
def unhandled_exception_handler(request, exc):
    """Ensure every error returns JSON so the CLI can parse it."""
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error", "error": str(exc)},
    )


# ================= RATE LIMITING (in-memory) =================
_rate_limit_store = defaultdict(list)
_rate_limit_lock = threading.Lock()
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_LOGIN_REGISTER = 10
RATE_LIMIT_CHANGE_PIN = 5


def _rate_limit_key(request: Request, path_tag: str) -> str:
    client = getattr(request, "client", None)
    ip = client.host if client else "unknown"
    return f"{ip}:{path_tag}"


def _rate_limit_allow(key: str, limit: int) -> bool:
    now = time.time()
    with _rate_limit_lock:
        times = _rate_limit_store[key]
        times[:] = [t for t in times if now - t < RATE_LIMIT_WINDOW]
        if len(times) >= limit:
            return False
        times.append(now)
    return True


def rate_limit_login_register(request: Request):
    key = _rate_limit_key(request, "auth")
    if not _rate_limit_allow(key, RATE_LIMIT_LOGIN_REGISTER):
        raise HTTPException(status_code=429, detail="Too many attempts. Try again in a minute.")


def rate_limit_change_pin(request: Request):
    key = _rate_limit_key(request, "change_pin")
    if not _rate_limit_allow(key, RATE_LIMIT_CHANGE_PIN):
        raise HTTPException(status_code=429, detail="Too many attempts. Try again in a minute.")


# ================= UTILS =================


def create_token(user_id: int):
    payload = {"user_id": user_id}
    token = jwt.encode(payload, SECRET_KEY, algorithm="HS256")
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return token


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    if not token or not (token := token.strip()):
        raise HTTPException(status_code=401, detail="Invalid token")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        user = db.query(User).filter(User.id == payload["user_id"]).first()
        if not user:
            raise HTTPException(status_code=401, detail="Invalid token")
        if getattr(user, "is_banned", False):
            raise HTTPException(status_code=403, detail="Account is banned")
        return user
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")


# ================= AUTH =================

# 6-digit PIN only (avoids bcrypt 72-byte issues and simplifies auth)
def _normalize_pin(raw: str) -> str:
    """Accept 'pin' or 'password' key; allow digits with optional spaces/dashes."""
    raw = (raw or "").strip()
    digits = "".join(c for c in raw if c.isdigit())
    if len(digits) != 6:
        raise HTTPException(status_code=400, detail="PIN must be exactly 6 digits")
    return digits


@app.post("/register")
def register(request: Request, data: dict, db: Session = Depends(get_db)):
    rate_limit_login_register(request)
    email = (data.get("email") or "").strip()
    if not email:
        raise HTTPException(status_code=400, detail="Email required")
    permission_code = (data.get("permission_code") or "").strip()
    if not permission_code:
        raise HTTPException(status_code=400, detail="Permission code required")
    row = db.query(PermissionCode).filter(
        PermissionCode.code == permission_code,
        PermissionCode.used_at == None,
    ).first()
    if not row:
        raise HTTPException(status_code=400, detail="Invalid or already used permission code")
    pin = _normalize_pin(data.get("pin") or data.get("password") or "")
    hashed = bcrypt_lib.hashpw(pin.encode("utf-8"), bcrypt_lib.gensalt()).decode("utf-8")
    user = User(email=email, password=hashed)
    db.add(user)
    db.flush()  # get user.id without committing
    row.used_at = datetime.utcnow()
    row.used_by_user_id = user.id
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Email already registered")
    return {"message": "Registered successfully"}


@app.post("/login")
def login(request: Request, data: dict, db: Session = Depends(get_db)):
    rate_limit_login_register(request)
    email = (data.get("email") or "").strip()
    pin = _normalize_pin(data.get("pin") or data.get("password") or "")
    user = db.query(User).filter(User.email == email).first()
    if not user or not bcrypt_lib.checkpw(pin.encode("utf-8"), (user.password or "").encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid email or PIN")
    if getattr(user, "is_banned", False):
        raise HTTPException(status_code=403, detail="Account is banned")
    token = create_token(user.id)
    return {"token": token}


@app.post("/reset-pin")
def reset_pin(request: Request, data: dict, db: Session = Depends(get_db)):
    """Forgot PIN: set new PIN using a one-time code (from admin). Rate-limited."""
    rate_limit_login_register(request)
    email = (data.get("email") or "").strip()
    code = (data.get("reset_code") or data.get("code") or "").strip()
    new_pin = _normalize_pin(data.get("new_pin") or data.get("pin") or "")
    if not email or not code:
        raise HTTPException(status_code=400, detail="email and reset_code required")
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=400, detail="Invalid email or code")
    row = db.query(PinResetCode).filter(
        PinResetCode.user_id == user.id,
        PinResetCode.code == code,
        PinResetCode.used_at == None,
        PinResetCode.expires_at > datetime.utcnow(),
    ).first()
    if not row:
        raise HTTPException(status_code=400, detail="Invalid or expired code")
    hashed = bcrypt_lib.hashpw(new_pin.encode("utf-8"), bcrypt_lib.gensalt()).decode("utf-8")
    user.password = hashed
    row.used_at = datetime.utcnow()
    db.commit()
    return {"message": "PIN reset successfully"}


@app.post("/change-pin")
def change_pin(request: Request, data: dict,
               user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    """Change PIN: requires current PIN and new PIN (6 digits)."""
    rate_limit_change_pin(request)
    current_pin = _normalize_pin(data.get("current_pin") or data.get("pin") or data.get("password") or "")
    new_pin = _normalize_pin(data.get("new_pin") or "")
    if not bcrypt_lib.checkpw(current_pin.encode("utf-8"), (user.password or "").encode("utf-8")):
        raise HTTPException(status_code=401, detail="Current PIN is incorrect")
    hashed = bcrypt_lib.hashpw(new_pin.encode("utf-8"), bcrypt_lib.gensalt()).decode("utf-8")
    user.password = hashed
    db.commit()
    return {"message": "PIN changed successfully"}


# ================= BUY CONTRACT =================
PAYMENT_ADDRESS_ERC20 = "0xD1D0B76F029Af8Bb5aEA1d0D77D061eDdeDfc6ff"
DURATION_OPTIONS_DAYS = [30, 60, 90]

@app.get("/contracts/options")
def contract_options(db: Session = Depends(get_db)):
    """List available contract plans, payment methods, and payment address (ERC20)."""
    plans = db.query(ContractPlan).order_by(ContractPlan.id).all()
    telegram_available = bool((os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip())
    metaapi_available = bool((os.environ.get("METAAPI_TOKEN") or "").strip())
    return {
        "plans": [{"id": p.id, "amount": p.amount, "label": p.label or f"${int(p.amount)}"} for p in plans],
        "payment_address_erc20": PAYMENT_ADDRESS_ERC20,
        "duration_options_days": DURATION_OPTIONS_DAYS,
        "cryptomus_available": cryptomus.is_configured(),
        "telegram_available": telegram_available,
        "metaapi_available": metaapi_available,
    }


@app.get("/contracts/check-columns")
def check_columns(db: Session = Depends(get_db)):
    """Check if payment columns exist in contracts table (diagnostic endpoint)."""
    from sqlalchemy import text, inspect as sql_inspect
    try:
        inspector = sql_inspect(db.bind)
        columns = [col['name'] for col in inspector.get_columns('contracts')]
        return {
            "columns_exist": {
                "payment_wallet": "payment_wallet" in columns,
                "payment_tx_id": "payment_tx_id" in columns,
            },
            "all_columns": columns,
        }
    except Exception as e:
        return {"error": str(e)}


def _ensure_contract_payment_columns():
    """Ensure payment_wallet and payment_tx_id columns exist on contracts."""
    try:
        inspector = sql_inspect(engine)
        columns = [col["name"] for col in inspector.get_columns("contracts")]
        has_wallet = "payment_wallet" in columns
        has_tx = "payment_tx_id" in columns
        if not has_wallet or not has_tx:
            with engine.connect() as conn:
                if not has_wallet:
                    conn.execute(text("ALTER TABLE contracts ADD COLUMN payment_wallet VARCHAR(255)"))
                if not has_tx:
                    conn.execute(text("ALTER TABLE contracts ADD COLUMN payment_tx_id VARCHAR(255)"))
                conn.commit()
        return True
    except Exception:
        return False


@app.post("/buy")
def buy_contract(data: dict,
                 user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    plan_id = data.get("contract_choice") or data.get("plan_id")
    if plan_id is None:
        raise HTTPException(status_code=400, detail="contract_choice or plan_id required")
    plan = db.query(ContractPlan).filter(ContractPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=400, detail="Invalid contract plan")
    amount = plan.amount

    duration_days = data.get("duration_days")
    if duration_days not in (30, 60, 90):
        duration_days = 30
    duration_days = int(duration_days)
    wallet = (data.get("withdrawal_wallet") or "").strip() or None
    payment_method = (data.get("payment_method") or "").strip().lower() or None  # "cryptomus" | "erc20"

    start = datetime.utcnow()
    end = start + timedelta(days=duration_days)

    # Cryptomus flow: only when user chose cryptomus and it is configured
    use_cryptomus = payment_method == "cryptomus" and cryptomus.is_configured()
    if payment_method == "cryptomus" and not cryptomus.is_configured():
        raise HTTPException(status_code=400, detail="Cryptomus payment is not available. Use payment_method=erc20.")

    if use_cryptomus:
        try:
            contract = Contract(
                user_id=user.id,
                status="pending",
                start_date=start,
                end_date=end,
                duration_days=duration_days,
                wallet=wallet,
                amount=amount,
                payment_wallet=None,
                payment_tx_id=None,
            )
            db.add(contract)
            db.flush()
            contract_id = contract.id
            order_id = str(contract_id)
            webhook_base = cryptomus._get_webhook_base()
            url_callback = f"{webhook_base}/webhooks/cryptomus/payment"
            url_success = f"{webhook_base}/payment/success"
            url_return = f"{webhook_base}/payment/return"
            result, err = cryptomus.create_invoice(
                amount=str(int(amount)),
                order_id=order_id,
                url_callback=url_callback,
                currency="USD",
                url_success=url_success,
                url_return=url_return,
            )
            if err:
                db.rollback()
                detail = f"Cryptomus: {err}"
                if "401" in str(err) or "Unauthorized" in str(err):
                    detail += " Check CRYPTOMUS_MERCHANT_ID and CRYPTOMUS_PAYMENT_API_KEY on the server."
                raise HTTPException(status_code=502, detail=detail)
            contract.cryptomus_invoice_uuid = result.get("uuid")
            db.commit()
            db.refresh(contract)
            payment_url = result.get("url") or ""
            return {
                "status": "pending_payment",
                "contract_id": contract.id,
                "amount": amount,
                "payment_url": payment_url,
                "message": "Pay at this link; your contract will activate automatically after payment.",
            }
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Failed to create contract: {str(e)}")

    # Fallback: manual payment (wallet + tx_id required)
    payment_wallet = (data.get("payment_wallet") or data.get("wallet") or "").strip()
    if not payment_wallet:
        raise HTTPException(status_code=400, detail="Payment wallet (wallet used to pay) is required")
    payment_tx_id = (data.get("payment_tx_id") or data.get("transaction_id") or "").strip()
    if not payment_tx_id:
        raise HTTPException(status_code=400, detail="Transaction ID of the payment is required")
    columns_exist = _ensure_contract_payment_columns()

    try:
        contract = Contract(
            user_id=user.id,
            status="pending",
            start_date=start,
            end_date=end,
            duration_days=duration_days,
            wallet=wallet,
            amount=amount,
            payment_wallet=payment_wallet if columns_exist else None,
            payment_tx_id=payment_tx_id if columns_exist else None,
        )
        db.add(contract)
        db.flush()
        contract_id = contract.id
        if columns_exist:
            try:
                db.execute(
                    text("UPDATE contracts SET payment_wallet = :wallet, payment_tx_id = :tx WHERE id = :id"),
                    {"wallet": payment_wallet, "tx": payment_tx_id, "id": contract_id},
                )
            except Exception:
                pass
        db.commit()
        db.refresh(contract)
        saved_payment_wallet = None
        saved_payment_tx_id = None
        try:
            row = db.execute(
                text("SELECT payment_wallet, payment_tx_id FROM contracts WHERE id = :id"),
                {"id": contract_id},
            ).fetchone()
            if row:
                saved_payment_wallet = row[0]
                saved_payment_tx_id = row[1]
        except Exception:
            pass
        response = {
            "status": "Contract submitted for verification",
            "contract_id": contract.id,
            "amount": amount,
            "payment_wallet": saved_payment_wallet,
            "payment_tx_id": saved_payment_tx_id,
            "message": "The system will verify your payment and activate the contract.",
        }
        if not saved_payment_wallet or not saved_payment_tx_id:
            response["warning"] = "Payment info not saved. Database columns may be missing."
        return response
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create contract: {str(e)}")


@app.post("/webhooks/cryptomus/payment")
async def webhook_cryptomus_payment(request: Request, db: Session = Depends(get_db)):
    """Cryptomus payment webhook: verify sign, then on paid/paid_over activate contract."""
    try:
        body = await request.body()
        payload = json.loads(body.decode("utf-8")) if body else {}
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON"})
    api_key = (os.environ.get("CRYPTOMUS_PAYMENT_API_KEY") or "").strip()
    if not cryptomus.verify_webhook_signature(payload, api_key):
        return JSONResponse(status_code=401, content={"detail": "Invalid signature"})
    order_id = payload.get("order_id")
    status = (payload.get("status") or "").strip().lower()
    txid = payload.get("txid")
    from_addr = payload.get("from")
    if not order_id:
        return JSONResponse(status_code=200, content={"ok": True})
    try:
        contract_id = int(order_id)
    except (TypeError, ValueError):
        return JSONResponse(status_code=200, content={"ok": True})
    contract = db.query(Contract).filter(Contract.id == contract_id).first()
    if not contract:
        return JSONResponse(status_code=200, content={"ok": True})
    if status in ("paid", "paid_over"):
        if contract.status == "pending":
            contract.status = "active"
            if txid is not None:
                contract.payment_tx_id = str(txid) if txid else None
            if from_addr is not None:
                contract.payment_wallet = str(from_addr)[:255] if from_addr else None
            db.commit()
    return JSONResponse(status_code=200, content={"ok": True})


# ================= UPGRADE =================

@app.post("/upgrade")
def upgrade(data: dict,
            user: User = Depends(get_current_user),
            db: Session = Depends(get_db)):
    contract_id = data.get("contract_id")
    if contract_id is None:
        raise HTTPException(status_code=400, detail="contract_id required")
    contract = db.query(Contract).filter(
        Contract.id == contract_id,
        Contract.user_id == user.id
    ).first()
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    contract.start_date = datetime.utcnow()
    contract.end_date = datetime.utcnow() + timedelta(days=30)
    contract.status = "active"

    db.commit()

    return {"status": "Contract upgraded"}


# ================= STOP =================

@app.post("/stop")
def stop_contract(data: dict,
                  user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    contract_id = data.get("contract_id")
    pin = _normalize_pin(data.get("pin") or data.get("password") or "")
    if not bcrypt_lib.checkpw(pin.encode("utf-8"), (user.password or "").encode("utf-8")):
        raise HTTPException(status_code=401, detail="PIN incorrect")
    if contract_id is None:
        raise HTTPException(status_code=400, detail="contract_id required")
    contract = db.query(Contract).filter(
        Contract.id == contract_id,
        Contract.user_id == user.id
    ).first()
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    contract.status = "stopped"
    # End any active run for this contract so backend and CLI stay in sync
    now = datetime.utcnow()
    for session in db.query(RunSession).filter(
        RunSession.user_id == user.id,
        RunSession.contract_id == contract_id,
        RunSession.ended_at == None,
    ).all():
        session.ended_at = now
    db.commit()

    return {"status": "Contract stopped"}


# ================= DASHBOARD =================

def _contract_balance(contract, now=None):
    """Current value of a contract: amount + 2% per day since start. Only active contracts earn."""
    if contract.status != "active" or not getattr(contract, "amount", None):
        return contract.amount or 0.0
    now = now or datetime.utcnow()
    start = contract.start_date or now
    days = max(0, (now - start).days)
    return (contract.amount or 0) * (1 + DAILY_RATE * days)


def _process_refunds(user_id: int, db: Session):
    """Refund contract amount to user's available_for_withdraw when end_date has passed."""
    now = datetime.utcnow()
    to_refund = db.query(Contract).filter(
        Contract.user_id == user_id,
        Contract.end_date <= now,
        Contract.refunded_at == None,
        Contract.status == "active",
    ).all()
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return
    for c in to_refund:
        avail = max(0.0, float(getattr(user, "available_for_withdraw", None) or 0.0))
        user.available_for_withdraw = avail + (c.amount or 0)
        c.refunded_at = now
        c.status = "refunded"
    if to_refund:
        db.commit()
        db.refresh(user)


@app.get("/dashboard")
def dashboard(user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    _process_refunds(user.id, db)
    db.refresh(user)
    contracts = db.query(Contract).filter(Contract.user_id == user.id).all()
    withdrawals = db.query(Withdrawal).filter(Withdrawal.user_id == user.id).all()
    total_withdrawn = sum(w.amount for w in withdrawals)
    total_balance = sum(_contract_balance(c) for c in contracts)
    available = getattr(user, "available_for_withdraw", None)
    if available is None:
        available = 0.0
    available = max(0.0, float(available))

    # So CLI can show "(running)" for the contract that has an active run
    active_run = db.query(RunSession).filter(
        RunSession.user_id == user.id,
        RunSession.ended_at == None,
    ).first()
    active_run_contract_id = active_run.contract_id if active_run else None

    telegram_linked = bool(getattr(user, "telegram_chat_id", None))
    trading_accounts_count = db.query(TradingAccount).filter(TradingAccount.user_id == user.id).count()

    return {
        "contracts": len(contracts),
        "total_balance": round(total_balance, 2),
        "withdrawn": round(total_withdrawn, 2),
        "available": round(available, 2),
        "contract_list": [
            {"id": c.id, "amount": c.amount, "status": c.status or "pending"}
            for c in contracts
        ],
        "active_run_contract_id": active_run_contract_id,
        "withdraw_window": _withdraw_window_info(),
        "telegram_linked": telegram_linked,
        "trading_accounts_count": trading_accounts_count,
    }


@app.get("/contracts")
def list_contracts(user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    """List all user's contracts (for Run menu)."""
    contracts = db.query(Contract).filter(Contract.user_id == user.id).all()
    active = db.query(RunSession).filter(
        RunSession.user_id == user.id,
        RunSession.ended_at == None,
    ).first()
    return {
        "contract_list": [
            {"id": c.id, "amount": c.amount, "status": c.status or "pending"}
            for c in contracts
        ],
        "active_run_contract_id": active.contract_id if active else None,
    }


# ================= RUN (22h, earnings every 10 min to withdrawables) =================
import random as _random
RUN_MAX_HOURS = 22
RUN_EARNINGS_INTERVAL_SEC = 600  # save earnings every 10 minutes
# Random base amounts (bigger contract = more earn via scale)
RUN_EARNINGS_BASE_AMOUNTS = [0.012, 0.02, 0.072, 0.08, 0.015, 0.03, 0.05, 0.04]
RUN_EARNINGS_SCALE_BASE = 2000.0  # contract.amount / this = scale factor
SECONDS_PER_DAY = 86400


def _max_run_earnings_for_elapsed(contract_amount: float, elapsed_seconds: float) -> float:
    """Max earnings for this run = 2% per day prorated by elapsed time. Never exceed daily cap."""
    return round((contract_amount or 0) * DAILY_RATE * (elapsed_seconds / SECONDS_PER_DAY), 4)


def _run_random_earnings_chunk(contract_amount: float):
    """One 10-min chunk: random amount proportional to contract size."""
    base = _random.choice(RUN_EARNINGS_BASE_AMOUNTS)
    scale = max(0.5, (contract_amount or 2000) / RUN_EARNINGS_SCALE_BASE)
    return round(base * scale, 4)


@app.post("/run/start")
def run_start(data: dict,
              user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    """Start a 22-hour run for a contract. Only one active run per user."""
    contract_id = data.get("contract_id")
    if contract_id is None:
        raise HTTPException(status_code=400, detail="contract_id required")
    contract = db.query(Contract).filter(
        Contract.id == contract_id,
        Contract.user_id == user.id
    ).first()
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")
    active = db.query(RunSession).filter(
        RunSession.user_id == user.id,
        RunSession.ended_at == None
    ).first()
    if active:
        raise HTTPException(
            status_code=400,
            detail=f"Already running contract #{active.contract_id}. Stop it first or wait for it to finish."
        )
    now = datetime.utcnow()
    session = RunSession(
        user_id=user.id,
        contract_id=contract_id,
        started_at=now,
        last_heartbeat_at=now,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return {
        "run_id": session.id,
        "contract_id": contract_id,
        "started_at": session.started_at.isoformat(),
        "max_hours": RUN_MAX_HOURS,
        "message": "Run started. Earnings will be added to your withdrawable balance when you stop or after 22 hours.",
    }


@app.post("/run/heartbeat")
def run_heartbeat(data: dict,
                  user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    """Update last heartbeat. Every 10 min save random earnings to user. Returns earnings so far."""
    run_id = data.get("run_id")
    if run_id is not None:
        session = db.query(RunSession).filter(
            RunSession.id == run_id,
            RunSession.user_id == user.id,
            RunSession.ended_at == None
        ).first()
    else:
        session = db.query(RunSession).filter(
            RunSession.user_id == user.id,
            RunSession.ended_at == None
        ).first()
    if not session:
        return {"active": False, "message": "No active run."}
    contract = db.query(Contract).filter(
        Contract.id == session.contract_id,
        Contract.user_id == user.id
    ).first()
    contract_amount = (contract.amount or 2000) if contract else 2000
    now = datetime.utcnow()
    session.last_heartbeat_at = now
    elapsed = (now - session.started_at).total_seconds()
    # Auto-end if over 22 hours
    if elapsed >= RUN_MAX_HOURS * 3600:
        session.ended_at = now
        db.commit()
        return {"active": False, "ended": True, "earnings_added": session.earnings_added, "message": "Run completed (22 hours)."}
    cap = _max_run_earnings_for_elapsed(contract_amount, elapsed)
    current_earnings = session.earnings_added or 0
    if current_earnings >= cap:
        session.ended_at = now
        db.commit()
        return {"active": False, "ended": True, "earnings_added": current_earnings, "message": "Run stopped automatically: daily earnings cap (2%) reached."}
    # Save earnings every 10 min: catch up any missed chunks (respect 2% daily cap)
    chunks_done = int(elapsed / RUN_EARNINGS_INTERVAL_SEC)
    existing = db.query(RunEarnings).filter(RunEarnings.run_id == session.id).count()
    db.refresh(user)
    ended_at_cap = False
    for _ in range(existing, chunks_done):
        amt = _run_random_earnings_chunk(contract_amount)
        current_earnings = session.earnings_added or 0
        if current_earnings + amt > cap:
            amt = max(0, round(cap - current_earnings, 4))
            if amt <= 0:
                ended_at_cap = True
                break
        user.available_for_withdraw = (getattr(user, "available_for_withdraw", None) or 0) + amt
        db.add(RunEarnings(run_id=session.id, amount=amt))
        session.earnings_added = (session.earnings_added or 0) + amt
        if (session.earnings_added or 0) >= cap:
            ended_at_cap = True
            break
    if chunks_done > existing and not ended_at_cap:
        session.last_earnings_saved_at = now
    if ended_at_cap:
        session.ended_at = now
        db.commit()
        return {"active": False, "ended": True, "earnings_added": session.earnings_added, "message": "Run stopped automatically: daily earnings cap (2%) reached."}
    db.commit()
    db.refresh(session)
    return {
        "active": True,
        "run_id": session.id,
        "contract_id": session.contract_id,
        "started_at": session.started_at.isoformat(),
        "elapsed_seconds": int(elapsed),
        "earnings_so_far": round(session.earnings_added or 0, 2),
        "max_hours": RUN_MAX_HOURS,
    }


@app.post("/run/stop")
def run_stop(data: dict,
             user: User = Depends(get_current_user),
             db: Session = Depends(get_db)):
    """Stop the current run; credit any remaining 10-min chunks and final partial chunk."""
    run_id = data.get("run_id")
    if run_id is not None:
        session = db.query(RunSession).filter(
            RunSession.id == run_id,
            RunSession.user_id == user.id,
            RunSession.ended_at == None
        ).first()
    else:
        session = db.query(RunSession).filter(
            RunSession.user_id == user.id,
            RunSession.ended_at == None
        ).first()
    if not session:
        return {"active": False, "earnings_added": 0, "message": "No active run to stop."}
    contract = db.query(Contract).filter(
        Contract.id == session.contract_id,
        Contract.user_id == user.id
    ).first()
    contract_amount = (contract.amount or 2000) if contract else 2000
    now = datetime.utcnow()
    session.ended_at = now
    elapsed = (now - session.started_at).total_seconds()
    cap = _max_run_earnings_for_elapsed(contract_amount, elapsed)
    chunks_done = int(elapsed / RUN_EARNINGS_INTERVAL_SEC)
    existing = db.query(RunEarnings).filter(RunEarnings.run_id == session.id).count()
    db.refresh(user)
    for _ in range(existing, chunks_done):
        current = session.earnings_added or 0
        if current >= cap:
            break
        amt = _run_random_earnings_chunk(contract_amount)
        if current + amt > cap:
            amt = max(0, round(cap - current, 4))
        if amt <= 0:
            break
        user.available_for_withdraw = (getattr(user, "available_for_withdraw", None) or 0) + amt
        db.add(RunEarnings(run_id=session.id, amount=amt))
        session.earnings_added = (session.earnings_added or 0) + amt
    # One final chunk for partial period (capped)
    current = session.earnings_added or 0
    if current < cap:
        amt = _run_random_earnings_chunk(contract_amount)
        if current + amt > cap:
            amt = max(0, round(cap - current, 4))
        if amt > 0:
            user.available_for_withdraw = (getattr(user, "available_for_withdraw", None) or 0) + amt
            db.add(RunEarnings(run_id=session.id, amount=amt))
            session.earnings_added = (session.earnings_added or 0) + amt
    db.commit()
    return {
        "active": False,
        "earnings_added": round(session.earnings_added or 0, 2),
        "message": f"Run stopped. ${round(session.earnings_added or 0, 2)} added to your withdrawable balance.",
    }


@app.get("/run/status")
def run_status(user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    """Get current run status. If run is over 22h, auto-complete (earnings already saved every 10 min)."""
    session = db.query(RunSession).filter(
        RunSession.user_id == user.id,
        RunSession.ended_at == None
    ).first()
    if not session:
        return {"active": False}
    now = datetime.utcnow()
    elapsed = (now - session.started_at).total_seconds()
    if elapsed >= RUN_MAX_HOURS * 3600:
        session.ended_at = now
        db.commit()
        return {"active": False, "ended": True, "earnings_added": session.earnings_added}
    db.refresh(session)
    return {
        "active": True,
        "run_id": session.id,
        "contract_id": session.contract_id,
        "started_at": session.started_at.isoformat(),
        "elapsed_seconds": int(elapsed),
        "earnings_so_far": round(session.earnings_added or 0, 2),
        "max_hours": RUN_MAX_HOURS,
    }


# ================= WITHDRAWAL HISTORY =================

@app.get("/withdrawals/history")
def withdrawal_history(user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    rows = db.query(Withdrawal).filter(Withdrawal.user_id == user.id).order_by(Withdrawal.id.desc()).all()
    return [
        {
            "id": w.id,
            "amount": w.amount,
            "wallet": w.wallet,
            "status": w.status,
            "created_at": w.created_at.isoformat() if w.created_at else None,
        }
        for w in rows
    ]


# ================= TRUSTED WALLETS =================

@app.get("/wallets")
def list_wallets(user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    rows = db.query(TrustedWallet).filter(TrustedWallet.user_id == user.id).all()
    return [
        {"id": w.id, "wallet": w.wallet, "label": w.label or "", "is_default": w.is_default}
        for w in rows
    ]


@app.post("/wallets")
def add_wallet(data: dict,
               user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    wallet = (data.get("wallet") or "").strip()
    label = (data.get("label") or "").strip() or None
    is_default = bool(data.get("is_default", False))
    if not wallet:
        raise HTTPException(status_code=400, detail="wallet required")
    from database import save_trusted_wallet
    save_trusted_wallet(db, user.id, wallet, label=label, is_default=is_default)
    return {"status": "Wallet added"}


@app.put("/wallets/default")
def set_default_wallet(data: dict,
                       user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    wallet_id = data.get("wallet_id")
    if wallet_id is None:
        raise HTTPException(status_code=400, detail="wallet_id required")
    from database import set_default_trusted_wallet
    w = set_default_trusted_wallet(db, user.id, wallet_id)
    if not w:
        raise HTTPException(status_code=404, detail="Wallet not found")
    return {"status": "Default wallet updated"}


@app.delete("/wallets/{wallet_id}")
def remove_wallet(wallet_id: int,
                  user: User = Depends(get_current_user),
                  db: Session = Depends(get_db)):
    from database import delete_trusted_wallet
    if not delete_trusted_wallet(db, wallet_id, user.id):
        raise HTTPException(status_code=404, detail="Wallet not found")
    return {"status": "Wallet removed"}


# ================= WITHDRAW =================

def _is_withdraw_window():
    """Withdrawals allowed only between 11pm (23:00) and 1am (01:00) server local time."""
    t = datetime.utcnow().time()
    return t.hour >= 23 or t.hour < 1


def _withdraw_window_info():
    """Return is_open, next_opens_at (iso), next_closes_at (iso), message. Uses UTC."""
    now = datetime.utcnow()
    today = now.date()
    # Window: 23:00–01:00 UTC
    opens_today = datetime.combine(today, dtime(23, 0))
    closes_tomorrow = datetime.combine(today + timedelta(days=1), dtime(1, 0))
    if now.hour < 1:
        # We're in the window that opened yesterday 23:00, closes today 01:00
        closes_today = datetime.combine(today, dtime(1, 0))
        is_open = True
        next_opens_at = opens_today.isoformat() + "Z"
        next_closes_at = closes_today.isoformat() + "Z"
        message = "Withdrawals open until 01:00 UTC."
    elif now.hour >= 23:
        is_open = True
        next_opens_at = now.isoformat() + "Z"
        next_closes_at = closes_tomorrow.isoformat() + "Z"
        message = "Withdrawals open until 01:00 UTC."
    else:
        is_open = False
        next_opens_at = opens_today.isoformat() + "Z"
        next_closes_at = closes_tomorrow.isoformat() + "Z"
        message = "Withdrawals open 23:00–01:00 UTC. Next window at " + next_opens_at
    return {"is_open": is_open, "next_opens_at": next_opens_at, "next_closes_at": next_closes_at, "message": message}


@app.get("/withdraw/window")
def withdraw_window():
    """Return current withdraw window status and next open/close times (UTC). No auth required."""
    return _withdraw_window_info()


# ================= TELEGRAM =================
TELEGRAM_LINK_TOKEN_EXPIRY_SECONDS = 900  # 15 minutes


def _telegram_bot_token():
    return (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()


def _telegram_bot_username():
    return (os.environ.get("TELEGRAM_BOT_USERNAME") or "").strip()


def send_telegram_message(db: Session, user_id: int, text: str) -> bool:
    """Send a Telegram message to the user if they have linked Telegram. Returns True if sent."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user or not getattr(user, "telegram_chat_id", None):
        return False
    token = _telegram_bot_token()
    if not token:
        return False
    try:
        r = requests_lib.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": user.telegram_chat_id, "text": text},
            timeout=10,
        )
        return r.status_code == 200
    except Exception:
        return False


@app.post("/telegram/link-request")
def telegram_link_request(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Create a one-time link token. User opens the deep link in Telegram and sends /start TOKEN to link."""
    if not _telegram_bot_token():
        raise HTTPException(status_code=503, detail="Telegram not configured")
    link_token = secrets.token_urlsafe(24)
    expires_at = datetime.utcnow() + timedelta(seconds=TELEGRAM_LINK_TOKEN_EXPIRY_SECONDS)
    db.add(TelegramLinkToken(user_id=user.id, token=link_token, expires_at=expires_at))
    db.commit()
    bot_username = _telegram_bot_username()
    deep_link = f"https://t.me/{bot_username}?start={link_token}" if bot_username else None
    return {
        "link_token": link_token,
        "deep_link": deep_link,
        "expires_in_seconds": TELEGRAM_LINK_TOKEN_EXPIRY_SECONDS,
        "message": "Open the link in Telegram or send /start " + link_token + " to the bot.",
    }


@app.get("/telegram/status")
def telegram_status(user: User = Depends(get_current_user)):
    """Return whether the user has linked Telegram."""
    linked = bool(getattr(user, "telegram_chat_id", None))
    return {
        "linked": linked,
        "telegram_username": getattr(user, "telegram_username", None) or None,
    }


def _send_telegram_reply(chat_id, text: str) -> None:
    """Send a text message to the given Telegram chat_id. No-op if chat_id missing or send fails."""
    if chat_id is None:
        return
    token = _telegram_bot_token()
    if not token:
        return
    try:
        requests_lib.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except Exception:
        pass


@app.post("/webhooks/telegram")
async def webhook_telegram(request: Request, db: Session = Depends(get_db)):
    """Telegram bot webhook: on /start TOKEN, link chat_id to user and reply.
    Set webhook in Telegram: POST https://api.telegram.org/bot<TOKEN>/setWebhook?url=<BASE_URL>/webhooks/telegram"""
    if not _telegram_bot_token():
        raise HTTPException(status_code=503, detail="Telegram not configured")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    message = body.get("message") or {}
    text = (message.get("text") or "").strip()
    chat_id = message.get("chat", {}).get("id")
    username = message.get("from", {}).get("username")
    if not text.startswith("/start"):
        return {"ok": True}
    parts = text.split(maxsplit=1)
    token = parts[1].strip() if len(parts) > 1 else None
    if not token:
        _send_telegram_reply(
            chat_id,
            "To link your account:\n1. Open the app (CLI or web)\n2. Log in and choose « Connect Telegram »\n3. Open the link you get, or send /start YOUR_TOKEN here",
        )
        return {"ok": True}
    now = datetime.utcnow()
    link_row = db.query(TelegramLinkToken).filter(
        TelegramLinkToken.token == token,
        TelegramLinkToken.expires_at > now,
    ).first()
    if not link_row:
        _send_telegram_reply(chat_id, "Invalid or expired link. In the app, choose Connect Telegram again to get a new link.")
        return {"ok": True}
    user = db.query(User).filter(User.id == link_row.user_id).first()
    if user:
        user.telegram_chat_id = str(chat_id)
        user.telegram_username = (username or "")[:128] if username else None
        db.delete(link_row)
        db.commit()
    reply = "Linked. You'll receive notifications here." if user else "Invalid or expired link."
    _send_telegram_reply(chat_id, reply)
    return {"ok": True}


# ================= TRADING ACCOUNTS (MetaAPI) =================
@app.post("/trading-accounts")
def trading_accounts_add(
    data: dict,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Add a MetaTrader account (login, password, server). MetaAPI stores the connection; we store only id + login + server."""
    if not metaapi.is_configured():
        raise HTTPException(status_code=503, detail="Trading accounts not configured (METAAPI_TOKEN)")
    login = (data.get("login") or "").strip()
    password = data.get("password") or ""
    server = (data.get("server") or "").strip()
    label = (data.get("label") or "").strip() or login
    platform = (data.get("platform") or "mt5").strip().lower()
    if not login or not server:
        raise HTTPException(status_code=400, detail="login and server required")
    if not password:
        raise HTTPException(status_code=400, detail="password required")
    if platform not in ("mt4", "mt5"):
        platform = "mt5"
    account_id, err = metaapi.add_account(login, password, server, label, platform)
    if err:
        raise HTTPException(status_code=502, detail=f"MetaAPI: {err}")
    row = TradingAccount(
        user_id=user.id,
        metaapi_account_id=account_id,
        login=login,
        server=server,
        label=label[:128] if label else None,
        platform=platform,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "metaapi_account_id": account_id, "login": login, "server": server, "label": row.label, "platform": platform}


@app.get("/trading-accounts")
def trading_accounts_list(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """List user's trading accounts with current balance from MetaAPI."""
    accounts = db.query(TradingAccount).filter(TradingAccount.user_id == user.id).all()
    result = []
    for acc in accounts:
        info, err = metaapi.get_account_information(acc.metaapi_account_id)
        balance = equity = currency = None
        if info:
            balance = info.get("balance")
            equity = info.get("equity")
            currency = info.get("currency")
        result.append({
            "id": acc.id,
            "login": acc.login,
            "server": acc.server,
            "label": acc.label,
            "platform": acc.platform,
            "balance": balance,
            "equity": equity,
            "currency": currency,
            "error": err if not info else None,
        })
    return {"trading_accounts": result}


@app.delete("/trading-accounts/{account_id}")
def trading_accounts_delete(
    account_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove a trading account from the user's list (does not remove from MetaAPI)."""
    acc = db.query(TradingAccount).filter(TradingAccount.id == account_id, TradingAccount.user_id == user.id).first()
    if not acc:
        raise HTTPException(status_code=404, detail="Trading account not found")
    db.delete(acc)
    db.commit()
    return {"ok": True}


# ================= CRON (scheduled refunds) =================

def _require_admin(request: Request):
    admin_secret = (os.environ.get("ADMIN_SECRET") or "").strip()
    if not admin_secret:
        raise HTTPException(status_code=503, detail="Admin not configured")
    provided = request.headers.get("x-admin-key") or request.query_params.get("admin_key") or ""
    if provided != admin_secret:
        raise HTTPException(status_code=403, detail="Invalid admin key")


@app.post("/admin/create-pin-reset")
def admin_create_pin_reset(request: Request, data: dict, db: Session = Depends(get_db)):
    """Create a one-time PIN reset code for a user (by email). Requires X-Admin-Key header."""
    _require_admin(request)
    email = (data.get("email") or "").strip()
    if not email:
        raise HTTPException(status_code=400, detail="email required")
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    code = secrets.token_urlsafe(16)
    expires_at = datetime.utcnow() + timedelta(hours=1)
    row = PinResetCode(user_id=user.id, code=code, expires_at=expires_at)
    db.add(row)
    db.commit()
    return {"code": code, "expires_in_minutes": 60, "email": email}


@app.get("/cron/process-refunds")
def cron_process_refunds(request: Request, db: Session = Depends(get_db)):
    """Run refunds for all users with due contracts. Call from Render Cron or external scheduler.
    Requires query param key=CRON_SECRET or header X-Cron-Secret."""
    secret = (os.environ.get("CRON_SECRET") or "").strip()
    if not secret:
        raise HTTPException(status_code=503, detail="Cron not configured")
    provided = request.query_params.get("key") or request.headers.get("x-cron-secret") or ""
    if provided != secret:
        raise HTTPException(status_code=403, detail="Invalid key")
    now = datetime.utcnow()
    due = db.query(Contract).filter(
        Contract.end_date <= now,
        Contract.refunded_at == None,
        Contract.status == "active",
    ).all()
    user_ids = list({c.user_id for c in due})
    for uid in user_ids:
        _process_refunds(uid, db)
    return {"refunded_users": len(user_ids), "contracts_due": len(due)}


def _get_payout_currency_network():
    currency = (os.environ.get("CRYPTOMUS_PAYOUT_CURRENCY") or "USDT").strip().upper()
    network = (os.environ.get("CRYPTOMUS_PAYOUT_NETWORK") or "TRON").strip().upper()
    return currency, network


@app.post("/withdraw")
def withdraw(data: dict,
             user: User = Depends(get_current_user),
             db: Session = Depends(get_db)):
    if not _is_withdraw_window():
        raise HTTPException(
            status_code=403,
            detail="Withdrawals only allowed between 11:00 PM and 1:00 AM (UTC). Try again later.",
        )
    amount = data.get("amount")
    wallet = (data.get("wallet") or "").strip()
    if amount is None:
        raise HTTPException(status_code=400, detail="amount required")
    if not wallet:
        default = db.query(TrustedWallet).filter(
            TrustedWallet.user_id == user.id,
            TrustedWallet.is_default == True
        ).first()
        if default:
            wallet = default.wallet
        else:
            raise HTTPException(status_code=400, detail="wallet required or set a default wallet")
    try:
        amount = float(amount)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="amount must be a number")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be positive")
    db.refresh(user)
    available = getattr(user, "available_for_withdraw", None) or 0.0
    available = max(0.0, float(available))
    if amount > available:
        raise HTTPException(
            status_code=400,
            detail=f"Amount exceeds available for withdrawal ({round(available, 2)}). The system sets your withdrawable amount.",
        )

    if cryptomus.is_payout_configured():
        currency, network = _get_payout_currency_network()
        user.available_for_withdraw = available - amount
        withdrawal = Withdrawal(user_id=user.id, amount=amount, wallet=wallet, status="pending")
        db.add(withdrawal)
        db.flush()
        order_id = str(withdrawal.id)
        webhook_base = cryptomus._get_webhook_base()
        url_callback = f"{webhook_base}/webhooks/cryptomus/payout"
        result, err = cryptomus.create_payout(
            amount=str(amount),
            currency=currency,
            network=network,
            address=wallet,
            order_id=order_id,
            url_callback=url_callback,
            is_subtract=True,
        )
        if err:
            db.rollback()
            raise HTTPException(status_code=502, detail=f"Payout failed: {err}")
        withdrawal.cryptomus_payout_uuid = result.get("uuid")
        db.commit()
        return {
            "status": "Withdrawal submitted",
            "message": "You will receive crypto to your wallet when the network confirms.",
        }

    user.available_for_withdraw = available - amount
    withdrawal = Withdrawal(user_id=user.id, amount=amount, wallet=wallet, status="pending")
    db.add(withdrawal)
    db.commit()
    return {"status": "Withdrawal request submitted"}


@app.post("/webhooks/cryptomus/payout")
async def webhook_cryptomus_payout(request: Request, db: Session = Depends(get_db)):
    """Cryptomus payout webhook: verify sign; on final success mark completed, on final failure refund."""
    try:
        body = await request.body()
        payload = json.loads(body.decode("utf-8")) if body else {}
    except Exception:
        return JSONResponse(status_code=400, content={"detail": "Invalid JSON"})
    api_key = (os.environ.get("CRYPTOMUS_PAYOUT_API_KEY") or "").strip()
    if not cryptomus.verify_webhook_signature(payload, api_key):
        return JSONResponse(status_code=401, content={"detail": "Invalid signature"})
    order_id = payload.get("order_id")
    status = (payload.get("status") or "").strip().lower()
    is_final = payload.get("is_final") is True
    if not order_id or not is_final:
        return JSONResponse(status_code=200, content={"ok": True})
    try:
        withdrawal_id = int(order_id)
    except (TypeError, ValueError):
        return JSONResponse(status_code=200, content={"ok": True})
    withdrawal = db.query(Withdrawal).filter(Withdrawal.id == withdrawal_id).first()
    if not withdrawal:
        return JSONResponse(status_code=200, content={"ok": True})
    if withdrawal.status in ("completed", "paid", "failed"):
        return JSONResponse(status_code=200, content={"ok": True})
    if status in ("paid", "process", "check", "send"):
        withdrawal.status = "completed"
        db.commit()
        return JSONResponse(status_code=200, content={"ok": True})
    withdrawal.status = "failed"
    user = db.query(User).filter(User.id == withdrawal.user_id).first()
    if user:
        avail = max(0.0, float(getattr(user, "available_for_withdraw", None) or 0.0))
        user.available_for_withdraw = avail + (withdrawal.amount or 0)
    db.commit()
    return JSONResponse(status_code=200, content={"ok": True})


if __name__ == "__main__":
    import uvicorn
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    port = int(os.environ.get("PORT", "8000"))
    print(f"Starting server at http://127.0.0.1:{port}")
    print("Press Ctrl+C to stop")
    uvicorn.run(app, host="127.0.0.1", port=port)