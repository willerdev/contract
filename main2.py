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

from database import get_db, User, Contract, ContractPlan, Withdrawal, TrustedWallet

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
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        user = db.query(User).filter(User.id == payload["user_id"]).first()
        return user
    except:
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
    wallet = (data.get("wallet") or "").strip() or None

    start = datetime.utcnow()
    end = start + timedelta(days=30)

    contract = Contract(
        user_id=user.id,
        status="active",
        start_date=start,
        end_date=end,
        wallet=wallet,
        amount=amount,
    )
    db.add(contract)
    db.commit()
    db.refresh(contract)
    return {"status": "Contract activated", "contract_id": contract.id, "amount": amount}


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
    contracts = db.query(Contract).filter(Contract.user_id == user.id).all()
    withdrawals = db.query(Withdrawal).filter(Withdrawal.user_id == user.id).all()
    total_withdrawn = sum(w.amount for w in withdrawals)
    total_balance = sum(_contract_balance(c) for c in contracts)
    available = total_balance - total_withdrawn

    return {
        "contracts": len(contracts),
        "total_balance": round(total_balance, 2),
        "withdrawn": round(total_withdrawn, 2),
        "available": round(available, 2),
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
    # Check available balance
    contracts = db.query(Contract).filter(Contract.user_id == user.id).all()
    withdrawals = db.query(Withdrawal).filter(Withdrawal.user_id == user.id).all()
    total_balance = sum(_contract_balance(c) for c in contracts)
    total_withdrawn = sum(w.amount for w in withdrawals)
    available = total_balance - total_withdrawn
    if amount > available:
        raise HTTPException(status_code=400, detail=f"Amount exceeds available balance ({round(available, 2)})")
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