"""
File database (SQLite) or Neon Postgres for the app. Use get_db() in FastAPI Depends, or the
save/retrieve helpers with a session to store and load data.

Set DATABASE_URL (or NEON_DATABASE_URL) to a Neon Postgres connection string to use Neon.
Loads from .env if python-dotenv is installed.
"""
import os
from datetime import datetime

# Load .env so DATABASE_URL is set (optional; requires: pip install python-dotenv)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from sqlalchemy.exc import IntegrityError

# Prefer Neon/Postgres if DATABASE_URL or NEON_DATABASE_URL is set
_raw_url = os.environ.get("DATABASE_URL") or os.environ.get("NEON_DATABASE_URL")
if _raw_url and _raw_url.strip().startswith("postgresql"):
    # Ensure SQLAlchemy uses psycopg2 driver
    DATABASE_URL = _raw_url.strip()
    if "postgresql://" in DATABASE_URL and "postgresql+psycopg2" not in DATABASE_URL:
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)
    engine = create_engine(DATABASE_URL)
else:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    DATABASE_PATH = os.path.join(SCRIPT_DIR, "database.db")
    DATABASE_URL = f"sqlite:///{DATABASE_PATH}"
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
_is_sqlite = "sqlite" in DATABASE_URL


# ================= MODELS =================


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True)
    password = Column(String)
    available_for_withdraw = Column(Float, default=0.0)  # set by system; withdraw limit
    is_banned = Column(Boolean, default=False)  # banned users cannot log in or use API


class PermissionCode(Base):
    """Valid one-time codes you send to users for sign-up. Checked on register."""
    __tablename__ = "permission_codes"
    id = Column(Integer, primary_key=True)
    code = Column(String(64), unique=True, nullable=False)
    used_at = Column(DateTime, nullable=True)  # when a user used this code
    used_by_user_id = Column(Integer, nullable=True)  # which user used it
    created_at = Column(DateTime, default=datetime.utcnow)


class Contract(Base):
    __tablename__ = "contracts"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    status = Column(String, default="pending")  # "pending" until system verifies payment, then "active", "refunded"
    start_date = Column(DateTime)
    end_date = Column(DateTime)
    duration_days = Column(Integer, nullable=True)  # user-chosen duration (30, 60, 90); refund after this
    wallet = Column(String, nullable=True)  # withdrawal/destination wallet
    amount = Column(Float)  # contract plan amount
    payment_wallet = Column(String, nullable=True)  # wallet address used to pay
    payment_tx_id = Column(String, nullable=True)  # transaction ID of the payment
    refunded_at = Column(DateTime, nullable=True)  # when amount was refunded to user


class Withdrawal(Base):
    __tablename__ = "withdrawals"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    amount = Column(Float)
    wallet = Column(String)
    status = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=True)


class TrustedWallet(Base):
    __tablename__ = "trusted_wallets"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    wallet = Column(String)
    label = Column(String, nullable=True)
    is_default = Column(Boolean, default=False)


class ContractPlan(Base):
    __tablename__ = "contract_plans"
    id = Column(Integer, primary_key=True)
    amount = Column(Float)
    label = Column(String, nullable=True)


class RunSession(Base):
    """Tracks a contract run. Earnings saved every 10 min to run_earnings and user.available_for_withdraw."""
    __tablename__ = "run_sessions"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer)
    contract_id = Column(Integer)
    started_at = Column(DateTime)
    ended_at = Column(DateTime, nullable=True)
    last_heartbeat_at = Column(DateTime, nullable=True)
    last_earnings_saved_at = Column(DateTime, nullable=True)  # last 10-min chunk we credited
    earnings_added = Column(Float, default=0.0)  # total added so far (sum of run_earnings)


class RunEarnings(Base):
    """Earnings saved every 10 minutes during a run (random amount proportional to contract)."""
    __tablename__ = "run_earnings"
    id = Column(Integer, primary_key=True)
    run_id = Column(Integer)
    amount = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)


# Create tables if they don't exist
Base.metadata.create_all(engine)


def seed_contract_plans():
    """Insert default contract plans if the table is empty."""
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    session = Session(engine)
    try:
        count = session.query(ContractPlan).count()
        if count == 0:
            for plan_id, amount in [(1, 1989.0), (2, 2900.0), (3, 4190.0)]:
                session.add(ContractPlan(id=plan_id, amount=amount, label=f"${int(amount)}"))
            session.commit()
    finally:
        session.close()


seed_contract_plans()

# Add new columns to existing tables (for both SQLite and PostgreSQL/Neon)
from sqlalchemy import text, inspect

def _column_exists(table_name, column_name):
    """Check if a column exists in a table."""
    try:
        inspector = inspect(engine)
        columns = [col['name'] for col in inspector.get_columns(table_name)]
        return column_name in columns
    except Exception:
        # If inspection fails, assume column doesn't exist and try to add it
        return False

# SQLite migrations
if _is_sqlite:
    try:
        if not _column_exists("withdrawals", "created_at"):
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE withdrawals ADD COLUMN created_at DATETIME"))
                conn.commit()
    except Exception:
        pass
    try:
        if not _column_exists("contracts", "amount"):
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE contracts ADD COLUMN amount REAL"))
                conn.commit()
    except Exception:
        pass
    for col, typ in [("payment_wallet", "VARCHAR(255)"), ("payment_tx_id", "VARCHAR(255)")]:
        try:
            if not _column_exists("contracts", col):
                with engine.connect() as conn:
                    conn.execute(text(f"ALTER TABLE contracts ADD COLUMN {col} {typ}"))
                    conn.commit()
        except Exception:
            pass
    try:
        if not _column_exists("users", "available_for_withdraw"):
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE users ADD COLUMN available_for_withdraw REAL DEFAULT 0"))
                conn.commit()
    except Exception:
        pass
    for col, typ in [("duration_days", "INTEGER"), ("refunded_at", "DATETIME")]:
        try:
            if not _column_exists("contracts", col):
                with engine.connect() as conn:
                    conn.execute(text(f"ALTER TABLE contracts ADD COLUMN {col} {typ}"))
                    conn.commit()
        except Exception:
            pass
    try:
        if not _column_exists("run_sessions", "last_earnings_saved_at"):
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE run_sessions ADD COLUMN last_earnings_saved_at DATETIME"))
                conn.commit()
    except Exception:
        pass
    try:
        if not _column_exists("users", "is_banned"):
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0"))
                conn.commit()
    except Exception:
        pass
else:
    # PostgreSQL/Neon migrations - auto-add missing columns
    try:
        if not _column_exists("withdrawals", "created_at"):
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE withdrawals ADD COLUMN created_at TIMESTAMP"))
                conn.commit()
    except Exception:
        pass
    try:
        if not _column_exists("contracts", "amount"):
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE contracts ADD COLUMN amount DOUBLE PRECISION"))
                conn.commit()
    except Exception:
        pass
    for col, typ in [("payment_wallet", "VARCHAR(255)"), ("payment_tx_id", "VARCHAR(255)")]:
        try:
            if not _column_exists("contracts", col):
                with engine.connect() as conn:
                    conn.execute(text(f"ALTER TABLE contracts ADD COLUMN {col} {typ}"))
                    conn.commit()
        except Exception as e:
            # Column might already exist or table might not exist yet
            pass
    try:
        if not _column_exists("users", "available_for_withdraw"):
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE users ADD COLUMN available_for_withdraw DOUBLE PRECISION DEFAULT 0"))
                conn.commit()
    except Exception:
        pass
    for col, typ in [("duration_days", "INTEGER"), ("refunded_at", "TIMESTAMP")]:
        try:
            if not _column_exists("contracts", col):
                with engine.connect() as conn:
                    conn.execute(text(f"ALTER TABLE contracts ADD COLUMN {col} {typ}"))
                    conn.commit()
        except Exception:
            pass
    try:
        if not _column_exists("run_sessions", "last_earnings_saved_at"):
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE run_sessions ADD COLUMN last_earnings_saved_at TIMESTAMP"))
                conn.commit()
    except Exception:
        pass
    try:
        if not _column_exists("users", "is_banned"):
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE users ADD COLUMN is_banned BOOLEAN DEFAULT false"))
                conn.commit()
    except Exception:
        pass
    # permission_codes table created by Base.metadata.create_all; ensure it exists
    try:
        inspector = inspect(engine)
        if "permission_codes" not in inspector.get_table_names():
            with engine.connect() as conn:
                conn.execute(text("""
                    CREATE TABLE permission_codes (
                        id SERIAL PRIMARY KEY,
                        code VARCHAR(64) UNIQUE NOT NULL,
                        used_at TIMESTAMP,
                        used_by_user_id INTEGER,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """))
                conn.commit()
    except Exception:
        pass


# ================= SESSION =================


def get_db():
    """Yield a DB session (for FastAPI Depends). Caller must not hold session across async boundaries."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ================= SAVE / RETRIEVE HELPERS =================


def get_user_by_email(db: Session, email: str):
    return db.query(User).filter(User.email == email).first()


def get_user_by_id(db: Session, user_id: int):
    return db.query(User).filter(User.id == user_id).first()


def save_user(db: Session, email: str, password_hash: str):
    """Create a new user. Raises IntegrityError if email already exists."""
    user = User(email=email, password=password_hash)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_contract_by_id(db: Session, contract_id: int, user_id: int = None):
    q = db.query(Contract).filter(Contract.id == contract_id)
    if user_id is not None:
        q = q.filter(Contract.user_id == user_id)
    return q.first()


def get_contracts_by_user(db: Session, user_id: int):
    return db.query(Contract).filter(Contract.user_id == user_id).all()


def save_contract(db: Session, user_id: int, status: str, start_date, end_date, amount: float, wallet: str = None):
    contract = Contract(
        user_id=user_id,
        status=status,
        start_date=start_date,
        end_date=end_date,
        wallet=wallet,
        amount=amount,
    )
    db.add(contract)
    db.commit()
    db.refresh(contract)
    return contract


def update_contract_status(db: Session, contract_id: int, user_id: int, status: str):
    contract = get_contract_by_id(db, contract_id, user_id)
    if not contract:
        return None
    contract.status = status
    db.commit()
    db.refresh(contract)
    return contract


def save_withdrawal(db: Session, user_id: int, amount: float, wallet: str, status: str = "pending"):
    w = Withdrawal(user_id=user_id, amount=amount, wallet=wallet, status=status)
    db.add(w)
    db.commit()
    db.refresh(w)
    return w


def get_withdrawals_by_user(db: Session, user_id: int):
    return db.query(Withdrawal).filter(Withdrawal.user_id == user_id).order_by(Withdrawal.id.desc()).all()


def get_trusted_wallets_by_user(db: Session, user_id: int):
    return db.query(TrustedWallet).filter(TrustedWallet.user_id == user_id).all()


def get_default_wallet(db: Session, user_id: int):
    return db.query(TrustedWallet).filter(TrustedWallet.user_id == user_id, TrustedWallet.is_default == True).first()


def save_trusted_wallet(db: Session, user_id: int, wallet: str, label: str = None, is_default: bool = False):
    if is_default:
        db.query(TrustedWallet).filter(TrustedWallet.user_id == user_id).update({TrustedWallet.is_default: False})
    w = TrustedWallet(user_id=user_id, wallet=wallet.strip(), label=label, is_default=is_default)
    db.add(w)
    db.commit()
    db.refresh(w)
    return w


def set_default_trusted_wallet(db: Session, user_id: int, wallet_id: int):
    db.query(TrustedWallet).filter(TrustedWallet.user_id == user_id).update({TrustedWallet.is_default: False})
    w = db.query(TrustedWallet).filter(TrustedWallet.id == wallet_id, TrustedWallet.user_id == user_id).first()
    if not w:
        return None
    w.is_default = True
    db.commit()
    db.refresh(w)
    return w


def delete_trusted_wallet(db: Session, wallet_id: int, user_id: int):
    w = db.query(TrustedWallet).filter(TrustedWallet.id == wallet_id, TrustedWallet.user_id == user_id).first()
    if not w:
        return False
    db.delete(w)
    db.commit()
    return True
