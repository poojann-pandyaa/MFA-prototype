from fastapi import FastAPI, Depends, HTTPException, status
from sqlalchemy.orm import Session
from fastapi.middleware.cors import CORSMiddleware
from database import engine, Base, get_db
import models
import schemas
import config
from services import auth
from services import biometrics

Base.metadata.create_all(bind=engine)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/enroll", response_model=schemas.Token)
def enroll(user_in: schemas.UserCreate, db: Session = Depends(get_db)):
    try:
        db_user = db.query(models.User).filter(models.User.username == user_in.username).first()
        if db_user:
            raise HTTPException(status_code=400, detail="Username already registered")

        # 1. Process biometrics: extract embedding from base64 image
        embedding_str = biometrics.extract_embedding(user_in.face_image_b64)
        if not embedding_str:
            raise HTTPException(status_code=400, detail="Could not extract face embedding. Ensure a face is clearly visible.")

        # 2. Create user
        hashed_password = auth.get_password_hash(user_in.password)
        new_user = models.User(
            username=user_in.username,
            password_hash=hashed_password,
            face_embedding=embedding_str
        )
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
        
        # Return token
        access_token = auth.create_access_token(data={"sub": new_user.username})
        return {"access_token": access_token, "token_type": "bearer"}
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=f"Backend Error: {str(e)}")


@app.post("/login/step1")
def login_step1(login_in: schemas.UserLoginStep1, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.username == login_in.username).first()
    if not user or not auth.verify_password(login_in.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )
    
    # Calculate risk score
    risk_score = auth.calculate_risk_score(db, user, login_in.device_identifier)
    
    if risk_score == "LOW":
        # Record success, register device if not exist
        device = db.query(models.Device).filter(models.Device.user_id == user.id, models.Device.device_identifier == login_in.device_identifier).first()
        if not device:
            db.add(models.Device(user_id=user.id, device_identifier=login_in.device_identifier))
        
        db.add(models.LoginHistory(
            user_id=user.id,
            device_identifier=login_in.device_identifier,
            risk_score=risk_score,
            face_check_triggered=False,
            success=True
        ))
        db.commit()
        access_token = auth.create_access_token(data={"sub": user.username})
        return {"require_step2": False, "access_token": access_token, "token_type": "bearer", "risk_score": risk_score}
    else:
        # Require face check. Issue a single-use, short-lived session_id
        # bound to this user+device: step 2 must present it, which is what
        # proves the password step actually happened and succeeded.
        db.add(models.LoginHistory(
            user_id=user.id,
            device_identifier=login_in.device_identifier,
            risk_score=risk_score,
            face_check_triggered=True,
            success=False # not yet successful
        ))
        db.commit()
        session_id = auth.create_verification_session(db, user, login_in.device_identifier)
        return {"require_step2": True, "risk_score": risk_score, "session_id": session_id}

@app.post("/login/step2")
def login_step2(login_in: schemas.UserLoginStep2, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.username == login_in.username).first()
    if not user:
        raise HTTPException(status_code=400, detail="User not found")

    # 0. Require proof that step 1 (password) was already completed for
    # this exact user+device. Without this check, step 2 would be a full
    # MFA bypass: anyone who can pass the face check for a *known username*
    # would get a token with no password at all.
    auth.consume_verification_session(db, login_in.session_id, user, login_in.device_identifier)

    # 1. Liveness check
    is_live = biometrics.check_liveness(login_in.face_image_b64)
    if not is_live:
        raise HTTPException(status_code=403, detail="Liveness check failed. Spoofing detected.")

    # 2. Face matching
    is_match = biometrics.verify_face(login_in.face_image_b64, user.face_embedding)
    if not is_match:
        raise HTTPException(status_code=401, detail="Face verification failed.")

    # If passes:
    # Update login history to success
    history = db.query(models.LoginHistory).filter(
        models.LoginHistory.user_id == user.id,
        models.LoginHistory.device_identifier == login_in.device_identifier
    ).order_by(models.LoginHistory.timestamp.desc()).first()

    if history:
        history.success = True

    device = db.query(models.Device).filter(models.Device.user_id == user.id, models.Device.device_identifier == login_in.device_identifier).first()
    if not device:
        db.add(models.Device(user_id=user.id, device_identifier=login_in.device_identifier))

    db.commit()
    access_token = auth.create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/history", response_model=list[schemas.LoginHistoryResponse])
def get_history(current_user: models.User = Depends(auth.get_current_user), db: Session = Depends(get_db)):
    # Identity comes from the verified JWT, not a client-supplied path
    # param, so a user can only ever read their own history.
    history = db.query(models.LoginHistory).filter(
        models.LoginHistory.user_id == current_user.id
    ).order_by(models.LoginHistory.timestamp.desc()).all()
    return history
