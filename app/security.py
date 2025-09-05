from datetime import datetime, timedelta
from typing import Optional
import os
from jose import JWTError, jwt
from passlib.hash import argon2
from fastapi import HTTPException, status, Depends, WebSocket
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from . import models, schemas
from .db import get_db
import secrets
import string

# Security settings
JWT_SECRET = os.getenv("JWT_SECRET", "your-super-secure-jwt-secret")
JWT_ALGORITHM = "HS256"
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "15"))
JWT_REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("JWT_REFRESH_TOKEN_EXPIRE_DAYS", "7"))

security = HTTPBearer()

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash using Argon2"""
    return argon2.verify(plain_password, hashed_password)

def hash_password(password: str) -> str:
    """Hash a password using Argon2"""
    return argon2.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Create a JWT access token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({"exp": expire, "type": "access"})
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return encoded_jwt

def create_refresh_token() -> str:
    """Create a random refresh token"""
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(64))

def store_refresh_token(db: Session, user_id: int, token: str) -> models.RefreshToken:
    """Store refresh token in database"""
    expires_at = datetime.utcnow() + timedelta(days=JWT_REFRESH_TOKEN_EXPIRE_DAYS)
    
    refresh_token = models.RefreshToken(
        token=token,
        user_id=user_id,
        expires_at=expires_at
    )
    
    db.add(refresh_token)
    db.commit()
    db.refresh(refresh_token)
    return refresh_token

def verify_token(token: str):
    """Verify JWT token and return payload"""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id: int = payload.get("sub")
        token_type: str = payload.get("type")
        
        if user_id is None or token_type != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return user_id
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )

def authenticate_user(db: Session, email: str, password: str):
    """Authenticate user with email and password"""
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user:
        return False
    if not verify_password(password, user.hashed_password):
        return False
    return user

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
):
    """Get current authenticated user"""
    user_id = verify_token(credentials.credentials)
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user

async def get_current_user_from_token(token: str, db: Session) -> Optional[models.User]:
    """Get user from a JWT token string. Used for WebSockets."""
    try:
        user_id = verify_token(token)
        user = db.query(models.User).filter(models.User.id == user_id).first()
        return user
    except HTTPException:
        # This will happen if the token is invalid, expired, or the user
        # doesn't exist.
        return None

async def get_current_user_ws(websocket: WebSocket, db: Session) -> models.User:
    """Get current user for WebSocket connections"""
    # Extract token from query parameters
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=1008)  # Policy violation
        raise HTTPException(status_code=401, detail="No token provided")

    user = await get_current_user_from_token(token, db)
    if not user:
        await websocket.close(code=1008)  # Policy violation
        raise HTTPException(status_code=401, detail="Invalid token")

    return user


def revoke_refresh_token(db: Session, token: str):
    """Revoke a refresh token"""
    refresh_token = db.query(models.RefreshToken).filter(
        models.RefreshToken.token == token,
        models.RefreshToken.is_revoked == False
    ).first()
    
    if refresh_token:
        refresh_token.is_revoked = True
        db.commit()

def validate_refresh_token(db: Session, token: str) -> Optional[models.RefreshToken]:
    """Validate refresh token and return if valid"""
    refresh_token = db.query(models.RefreshToken).filter(
        models.RefreshToken.token == token,
        models.RefreshToken.is_revoked == False,
        models.RefreshToken.expires_at > datetime.utcnow()
    ).first()
    
    return refresh_token
