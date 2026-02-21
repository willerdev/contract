import os
import json
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text, inspect as sql_inspect
import bcrypt as bcrypt_lib
from jose import jwt
from datetime import datetime, timedelta
import traceback

DAILY_RATE = 0.02  # 2% per day

from database import get_db, engine, User, Contract, ContractPlan, Withdrawal, TrustedWallet, RunSession, RunEarnings, PermissionCode
import cryptomus

SECRET_KEY = "secret123"

app = FastAPI()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")


@app.get("/", response_class=HTMLResponse)
def root():
    """Cryptomus domain verification: meta tag must be on the page at your project URL."""
    return """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="cryptomus" content="9094fc19" /></head>
<body><p>OK</p></body></html>"""


@app.exception_handler(Exception)
def unhandled_exception_handler(request, exc):
    """Ensure every error returns JSON so the CLI can parse it."""
    traceback.print_exc()
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error", "error": str(exc)},
    )


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
def register(data: dict, db: Session = Depends(get_db)):
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
def login(data: dict, db: Session = Depends(get_db)):
    email = (data.get("email") or "").strip()
    pin = _normalize_pin(data.get("pin") or data.get("password") or "")
    user = db.query(User).filter(User.email == email).first()
    if not user or not bcrypt_lib.checkpw(pin.encode("utf-8"), (user.password or "").encode("utf-8")):
        raise HTTPException(status_code=401, detail="Invalid email or PIN")
    if getattr(user, "is_banned", False):
        raise HTTPException(status_code=403, detail="Account is banned")
    token = create_token(user.id)
    return {"token": token}


# ================= BUY CONTRACT =================
PAYMENT_ADDRESS_ERC20 = "0xD1D0B76F029Af8Bb5aEA1d0D77D061eDdeDfc6ff"
DURATION_OPTIONS_DAYS = [30, 60, 90]

@app.get("/contracts/options")
def contract_options(db: Session = Depends(get_db)):
    """List available contract plans, payment methods, and payment address (ERC20)."""
    plans = db.query(ContractPlan).order_by(ContractPlan.id).all()
    return {
        "plans": [{"id": p.id, "amount": p.amount, "label": p.label or f"${int(p.amount)}"} for p in plans],
        "payment_address_erc20": PAYMENT_ADDRESS_ERC20,
        "duration_options_days": DURATION_OPTIONS_DAYS,
        "cryptomus_available": cryptomus.is_configured(),
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
                raise HTTPException(status_code=502, detail=f"Cryptomus: {err}")
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
    # Save earnings every 10 min: catch up any missed chunks
    chunks_done = int(elapsed / RUN_EARNINGS_INTERVAL_SEC)
    existing = db.query(RunEarnings).filter(RunEarnings.run_id == session.id).count()
    db.refresh(user)
    for _ in range(existing, chunks_done):
        amt = _run_random_earnings_chunk(contract_amount)
        user.available_for_withdraw = (getattr(user, "available_for_withdraw", None) or 0) + amt
        db.add(RunEarnings(run_id=session.id, amount=amt))
        session.earnings_added = (session.earnings_added or 0) + amt
    if chunks_done > existing:
        session.last_earnings_saved_at = now
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
    chunks_done = int(elapsed / RUN_EARNINGS_INTERVAL_SEC)
    existing = db.query(RunEarnings).filter(RunEarnings.run_id == session.id).count()
    db.refresh(user)
    for _ in range(existing, chunks_done):
        amt = _run_random_earnings_chunk(contract_amount)
        user.available_for_withdraw = (getattr(user, "available_for_withdraw", None) or 0) + amt
        db.add(RunEarnings(run_id=session.id, amount=amt))
        session.earnings_added = (session.earnings_added or 0) + amt
    # One final chunk for partial period
    amt = _run_random_earnings_chunk(contract_amount)
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
    from datetime import datetime as dt
    t = dt.now().time()
    return t.hour >= 23 or t.hour < 1


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