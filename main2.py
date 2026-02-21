import os
from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
import bcrypt as bcrypt_lib
from jose import jwt
from datetime import datetime, timedelta
import traceback

DAILY_RATE = 0.02  # 2% per day

from database import get_db, engine, User, Contract, ContractPlan, Withdrawal, TrustedWallet, RunSession

SECRET_KEY = "secret123"

app = FastAPI()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")


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
    pin = _normalize_pin(data.get("pin") or data.get("password") or "")
    hashed = bcrypt_lib.hashpw(pin.encode("utf-8"), bcrypt_lib.gensalt()).decode("utf-8")
    user = User(email=email, password=hashed)
    db.add(user)
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
    token = create_token(user.id)
    return {"token": token}


# ================= BUY CONTRACT =================

@app.get("/contracts/options")
def contract_options(db: Session = Depends(get_db)):
    """List available contract plans (for CLI display)."""
    plans = db.query(ContractPlan).order_by(ContractPlan.id).all()
    return [{"id": p.id, "amount": p.amount, "label": p.label or f"${int(p.amount)}"} for p in plans]


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

    payment_wallet = (data.get("payment_wallet") or data.get("wallet") or "").strip()
    if not payment_wallet:
        raise HTTPException(status_code=400, detail="Payment wallet (wallet used to pay) is required")
    payment_tx_id = (data.get("payment_tx_id") or data.get("transaction_id") or "").strip()
    if not payment_tx_id:
        raise HTTPException(status_code=400, detail="Transaction ID of the payment is required")

    wallet = (data.get("withdrawal_wallet") or "").strip() or None  # optional withdrawal destination

    start = datetime.utcnow()
    end = start + timedelta(days=30)

    # Step 1: Ensure payment columns exist using engine (separate connection, so no session conflict)
    from sqlalchemy import text, inspect as sql_inspect
    columns_exist = False
    try:
        inspector = sql_inspect(engine)
        columns = [col['name'] for col in inspector.get_columns('contracts')]
        has_payment_wallet = 'payment_wallet' in columns
        has_payment_tx_id = 'payment_tx_id' in columns
        if not has_payment_wallet or not has_payment_tx_id:
            with engine.connect() as conn:
                if not has_payment_wallet:
                    conn.execute(text("ALTER TABLE contracts ADD COLUMN payment_wallet VARCHAR(255)"))
                if not has_payment_tx_id:
                    conn.execute(text("ALTER TABLE contracts ADD COLUMN payment_tx_id VARCHAR(255)"))
                conn.commit()
        columns_exist = True
    except Exception:
        columns_exist = False

    try:
        # Step 2: Create contract (ORM)
        contract = Contract(
            user_id=user.id,
            status="pending",
            start_date=start,
            end_date=end,
            wallet=wallet,
            amount=amount,
            payment_wallet=payment_wallet if columns_exist else None,
            payment_tx_id=payment_tx_id if columns_exist else None,
        )
        db.add(contract)
        db.flush()
        contract_id = contract.id

        # Step 3: Always save payment info via direct SQL (same session, before commit)
        if columns_exist:
            try:
                db.execute(
                    text("UPDATE contracts SET payment_wallet = :wallet, payment_tx_id = :tx WHERE id = :id"),
                    {"wallet": payment_wallet, "tx": payment_tx_id, "id": contract_id}
                )
            except Exception:
                pass

        db.commit()
        db.refresh(contract)

        # Step 4: Verify saved by reading back
        saved_payment_wallet = None
        saved_payment_tx_id = None
        try:
            row = db.execute(
                text("SELECT payment_wallet, payment_tx_id FROM contracts WHERE id = :id"),
                {"id": contract_id}
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
        
        # Add warning if payment info wasn't saved
        if not saved_payment_wallet or not saved_payment_tx_id:
            response["warning"] = "Payment info not saved. Database columns may be missing. Run: ALTER TABLE contracts ADD COLUMN payment_wallet VARCHAR(255); ALTER TABLE contracts ADD COLUMN payment_tx_id VARCHAR(255);"
        
        return response
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create contract: {str(e)}")


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


@app.get("/dashboard")
def dashboard(user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    db.refresh(user)
    contracts = db.query(Contract).filter(Contract.user_id == user.id).all()
    withdrawals = db.query(Withdrawal).filter(Withdrawal.user_id == user.id).all()
    total_withdrawn = sum(w.amount for w in withdrawals)
    total_balance = sum(_contract_balance(c) for c in contracts)
    available = getattr(user, "available_for_withdraw", None)
    if available is None:
        available = 0.0
    available = max(0.0, float(available))

    return {
        "contracts": len(contracts),
        "total_balance": round(total_balance, 2),
        "withdrawn": round(total_withdrawn, 2),
        "available": round(available, 2),
        "contract_list": [
            {"id": c.id, "amount": c.amount, "status": c.status or "pending"}
            for c in contracts
        ],
    }


@app.get("/contracts")
def list_contracts(user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    """List all user's contracts (for Run menu)."""
    contracts = db.query(Contract).filter(Contract.user_id == user.id).all()
    return [
        {"id": c.id, "amount": c.amount, "status": c.status or "pending"}
        for c in contracts
    ]


# ================= RUN (22h, earnings to withdrawables) =================
RUN_MAX_HOURS = 22
RUN_EARNINGS_PER_TX = 0.02
RUN_SECONDS_PER_TX = 4  # average seconds between $0.02 transactions


def _run_earnings(seconds_run):
    """Earnings from run: $0.02 per transaction, 1 tx every RUN_SECONDS_PER_TX sec, capped at 22h."""
    cap = RUN_MAX_HOURS * 3600
    s = min(float(seconds_run), cap)
    return round((s / RUN_SECONDS_PER_TX) * RUN_EARNINGS_PER_TX, 2)


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
    """Update last heartbeat. Call every few minutes while run is active. Returns earnings so far."""
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
    now = datetime.utcnow()
    session.last_heartbeat_at = now
    # Auto-end if over 22 hours
    elapsed = (now - session.started_at).total_seconds()
    if elapsed >= RUN_MAX_HOURS * 3600:
        session.ended_at = now
        earnings = _run_earnings(elapsed)
        session.earnings_added = earnings
        db.refresh(user)
        avail = getattr(user, "available_for_withdraw", None) or 0.0
        user.available_for_withdraw = avail + earnings
        db.commit()
        return {"active": False, "ended": True, "earnings_added": earnings, "message": "Run completed (22 hours)."}
    db.commit()
    earnings_so_far = _run_earnings(elapsed)
    return {
        "active": True,
        "run_id": session.id,
        "contract_id": session.contract_id,
        "started_at": session.started_at.isoformat(),
        "elapsed_seconds": int(elapsed),
        "earnings_so_far": earnings_so_far,
        "max_hours": RUN_MAX_HOURS,
    }


@app.post("/run/stop")
def run_stop(data: dict,
             user: User = Depends(get_current_user),
             db: Session = Depends(get_db)):
    """Stop the current run and add earnings to user's available_for_withdraw."""
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
    now = datetime.utcnow()
    session.ended_at = now
    elapsed = (now - session.started_at).total_seconds()
    earnings = _run_earnings(elapsed)
    session.earnings_added = earnings
    db.refresh(user)  # get latest user.available_for_withdraw
    avail = max(0.0, float(getattr(user, "available_for_withdraw", None) or 0.0))
    user.available_for_withdraw = avail + earnings
    db.commit()
    return {
        "active": False,
        "earnings_added": earnings,
        "message": f"Run stopped. ${earnings} added to your withdrawable balance.",
    }


@app.get("/run/status")
def run_status(user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    """Get current run status. If run is over 22h, auto-complete and credit earnings."""
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
        earnings = _run_earnings(elapsed)
        session.earnings_added = earnings
        db.refresh(user)
        avail = getattr(user, "available_for_withdraw", None) or 0.0
        user.available_for_withdraw = avail + earnings
        db.commit()
        return {"active": False, "ended": True, "earnings_added": earnings}
    return {
        "active": True,
        "run_id": session.id,
        "contract_id": session.contract_id,
        "started_at": session.started_at.isoformat(),
        "elapsed_seconds": int(elapsed),
        "earnings_so_far": _run_earnings(elapsed),
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
    user.available_for_withdraw = available - amount
    withdrawal = Withdrawal(
        user_id=user.id,
        amount=amount,
        wallet=wallet,
        status="pending"
    )
    db.add(withdrawal)
    db.commit()
    return {"status": "Withdrawal request submitted"}


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