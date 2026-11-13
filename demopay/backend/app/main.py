import json

import bcrypt
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app import config, pay, seed
from app.db import Base, SessionLocal, add_missing_columns, engine, get_db
from app.deps import current_user, demo_only, device_id, make_token
from app.models import Account, AuthChallenge, Directory, Payment, Profile, User, UserAuthMethod
from app.schemas import LoginIn, MethodIn, PersonaIn, SignupIn
from authn.registry import METHODS, UNIVERSAL_IMPLEMENTED

Base.metadata.create_all(bind=engine)
add_missing_columns()
with SessionLocal() as _db:
    seed.seed_directory(_db)
    seed.seed_customers(_db)

app = FastAPI(title="DemoPay")
app.add_middleware(CORSMiddleware, allow_origins=config.CORS_ORIGINS, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(pay.router)


def _hash(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def _check(pw: str, hashed: str) -> bool:
    return bcrypt.checkpw(pw.encode(), hashed.encode())


@app.post("/auth/signup")
def signup(body: SignupIn, db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(400, "Username already taken")
    user = User(username=body.username, password_hash=_hash(body.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    db.add(Account(user_id=user.id, balance=config.OPENING_BALANCE))
    for m in UNIVERSAL_IMPLEMENTED:
        db.add(UserAuthMethod(user_id=user.id, method=m))
    db.commit()
    return {"token": make_token(user.id), "username": user.username}


@app.post("/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == body.username).first()
    if not user or not _check(body.password, user.password_hash):
        raise HTTPException(401, "Wrong username or password")
    return {"token": make_token(user.id), "username": user.username}


@app.get("/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    acct = db.query(Account).filter(Account.user_id == user.id).first()
    enrolled = {m.method for m in db.query(UserAuthMethod).filter(UserAuthMethod.user_id == user.id, UserAuthMethod.active == True)}  # noqa: E712
    prof = db.query(Profile).filter(Profile.user_id == user.id).first()
    profile = {"full_name": prof.full_name, "age": prof.age, "city": prof.city, "occupation": prof.occupation, "mobile": prof.mobile,
               "account_number": acct.number, "ifsc": acct.ifsc, "account_kind": acct.kind} if prof else None
    return {"username": user.username, "profile": profile, "balance": acct.balance, "demo": config.DEMO, "base_rate": config.BASE_RATE, "hold_seconds": config.HOLD_SECONDS,
            "methods": [{"id": m.id, "name": m.name, "tier": m.tier, "answers": m.answers, "seconds": m.seconds, "implemented": m.implemented,
                         "enrolled": m.id in enrolled} for m in METHODS.values()]}


@app.get("/demo/users", dependencies=[Depends(demo_only)])
def demo_users(db: Session = Depends(get_db)):
    """Public on purpose (sign-in tiles). Demo mode only; the shared password is shown on screen."""
    rows = db.query(User, Profile).join(Profile, Profile.user_id == User.id).order_by(User.id).all()
    return {"password": config.DEMO_PASSWORD, "users": [{"username": u.username, "full_name": p.full_name, "age": p.age, "city": p.city,
                                                        "occupation": p.occupation, "story": p.story} for u, p in rows]}


@app.post("/demo/methods", dependencies=[Depends(demo_only)])
def demo_set_method(body: MethodIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Playground: switch one of this customer's authenticators on or off and watch the plan change."""
    row = db.query(UserAuthMethod).filter(UserAuthMethod.user_id == user.id, UserAuthMethod.method == body.method).first()
    if row:
        row.active = body.enrolled
    elif body.enrolled:
        db.add(UserAuthMethod(user_id=user.id, method=body.method))
    db.commit()
    return {"ok": True}


@app.get("/directory/lookup")
def lookup(vpa: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    e = db.get(Directory, vpa)
    return {"vpa": vpa, "found": bool(e), "registered_name": e.registered_name if e else None}


# ---- demo-only routes (404 unless DEMOPAY_DEMO is on) ----------------------------------------------------------
@app.get("/demo/directory", dependencies=[Depends(demo_only)])
def demo_directory(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [{"vpa": e.vpa, "registered_name": e.registered_name, "label": e.label} for e in db.query(Directory).order_by(Directory.vpa)]


@app.get("/demo/personas", dependencies=[Depends(demo_only)])
def demo_personas(user: User = Depends(current_user)):
    return [{"name": k, "story": v[0], "median": v[1], "payments": v[2], "balance": v[5]} for k, v in seed.PERSONAS.items()]


@app.post("/demo/persona", dependencies=[Depends(demo_only)])
def demo_persona(body: PersonaIn, user: User = Depends(current_user), dev: str = Depends(device_id), db: Session = Depends(get_db)):
    return seed.load_persona(db, user, dev, body.name)


@app.post("/demo/reset", dependencies=[Depends(demo_only)])
def demo_reset(user: User = Depends(current_user), dev: str = Depends(device_id), db: Session = Depends(get_db)):
    if user.username in seed.PERSONAS:
        return seed.load_persona(db, user, dev, user.username)   # back to their original history
    ids = [p.id for p in db.query(Payment).filter(Payment.user_id == user.id)]
    if ids:
        db.query(AuthChallenge).filter(AuthChallenge.payment_id.in_(ids)).delete(synchronize_session=False)
    db.query(Payment).filter(Payment.user_id == user.id).delete()
    db.query(Account).filter(Account.user_id == user.id).update({"balance": config.OPENING_BALANCE})
    db.commit()
    return {"ok": True, "balance": config.OPENING_BALANCE}


@app.get("/demo/xray/{pid}", dependencies=[Depends(demo_only)])
def xray(pid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = db.get(Payment, pid)
    if not p or p.user_id != user.id:
        raise HTTPException(404, "Payment not found")
    return json.loads(p.explain_json or "{}")
