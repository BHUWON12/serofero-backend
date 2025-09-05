from fastapi import APIRouter, Depends, HTTPException, status, Form, File, UploadFile
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from .. import models, schemas, security
from ..db import get_db
from ..utils import validate_email, validate_username, sanitize_text, validate_and_save_file
from ..deps import get_current_active_user

router = APIRouter()

@router.post("/register", response_model=schemas.UserResponse)
async def register(user: schemas.UserCreate, db: Session = Depends(get_db)):
    """Register a new user"""
    # Validate input
    if not validate_email(user.email):
        raise HTTPException(status_code=400, detail="Invalid email format")
    
    if not validate_username(user.username):
        raise HTTPException(status_code=400, detail="Invalid username format")
    
    if len(user.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    
    # Check if user exists
    db_user = db.query(models.User).filter(
        (models.User.email == user.email) | (models.User.username == user.username)
    ).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Email or username already registered")
    
    # Create new user
    hashed_password = security.hash_password(user.password)
    db_user = models.User(
        email=user.email,
        username=user.username,
        full_name=sanitize_text(user.full_name),
        hashed_password=hashed_password
    )
    
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    
    return db_user

@router.post("/login", response_model=schemas.Token)
async def login(user_credentials: schemas.UserLogin, db: Session = Depends(get_db)):
    """Login user and return tokens"""
    user = security.authenticate_user(db, user_credentials.email, user_credentials.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Create access token
    access_token_expires = timedelta(minutes=security.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = security.create_access_token(
        data={"sub": str(user.id)}, expires_delta=access_token_expires
    )
    
    # Create and store refresh token
    refresh_token = security.create_refresh_token()
    security.store_refresh_token(db, user.id, refresh_token)
    
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }

@router.post("/refresh", response_model=schemas.Token)
async def refresh_token(token_data: schemas.TokenRefresh, db: Session = Depends(get_db)):
    """Refresh access token using refresh token"""
    refresh_token_obj = security.validate_refresh_token(db, token_data.refresh_token)
    if not refresh_token_obj:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token"
        )
    
    # Revoke old refresh token
    security.revoke_refresh_token(db, token_data.refresh_token)
    
    # Create new tokens
    user = refresh_token_obj.user
    access_token_expires = timedelta(minutes=security.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = security.create_access_token(
        data={"sub": str(user.id)}, expires_delta=access_token_expires
    )
    
    new_refresh_token = security.create_refresh_token()
    security.store_refresh_token(db, user.id, new_refresh_token)
    
    return {
        "access_token": access_token,
        "refresh_token": new_refresh_token,
        "token_type": "bearer"
    }

@router.post("/logout")
async def logout(
    token_data: schemas.TokenRefresh,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(security.get_current_user)
):
    """Logout user and revoke refresh token"""
    security.revoke_refresh_token(db, token_data.refresh_token)
    return {"message": "Successfully logged out"}

@router.get("/me", response_model=schemas.UserResponse)
async def get_current_user_info(current_user: models.User = Depends(security.get_current_user)):
    """Get current user information"""
    return current_user

@router.post("/forgot-password")
async def forgot_password(email: str = Form(...), db: Session = Depends(get_db)):
    """Send password reset email (mock implementation for development)"""
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user:
        # Don't reveal if user exists or not
        return {"message": "If the email exists, a password reset link has been sent"}
    
    # In production, you would:
    # 1. Generate a secure reset token
    # 2. Store it with expiration time
    # 3. Send email with reset link
    
    # For development, just return success
    return {"message": "If the email exists, a password reset link has been sent"}

@router.post("/reset-password")
async def reset_password(
    token: str = Form(...),
    new_password: str = Form(...),
    db: Session = Depends(get_db)
):
    """Reset user password using token (mock implementation)"""
    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    
    # In production, you would validate the reset token here
    # For development, just return success
    return {"message": "Password reset successfully"}


@router.post("/profile/photo")
async def upload_profile_photo(
    file: UploadFile = File(...),
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Upload or update user profile photo"""
    try:
        media_url, _ = await validate_and_save_file(file)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    current_user.avatar_url = media_url
    db.add(current_user)
    db.commit()
    db.refresh(current_user)

    return {"avatar_url": current_user.avatar_url}
