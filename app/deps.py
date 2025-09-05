from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session
from . import models
from .db import get_db
from .security import get_current_user

def get_current_active_user(
    current_user: models.User = Depends(get_current_user)
):
    """Get current active user"""
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user

def check_user_not_blocked(
    target_user_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Check if current user is blocked by target user or has blocked target user"""
    # Check if current user is blocked by target user
    blocked_by_target = db.query(models.Block).filter(
        models.Block.blocker_id == target_user_id,
        models.Block.blocked_id == current_user.id
    ).first()
    
    if blocked_by_target:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are blocked by this user"
        )
    
    # Check if current user has blocked target user
    blocked_target = db.query(models.Block).filter(
        models.Block.blocker_id == current_user.id,
        models.Block.blocked_id == target_user_id
    ).first()
    
    if blocked_target:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You have blocked this user"
        )
    
    return True

def get_unblocked_users_query(current_user: models.User, db: Session):
    """Get SQLAlchemy query for users that are not blocked"""
    # Get list of users blocked by current user
    blocked_user_ids = db.query(models.Block.blocked_id).filter(
        models.Block.blocker_id == current_user.id
    ).subquery()
    
    # Get list of users who blocked current user
    blocking_user_ids = db.query(models.Block.blocker_id).filter(
        models.Block.blocked_id == current_user.id
    ).subquery()
    
    # Query users excluding blocked ones
    return db.query(models.User).filter(
        ~models.User.id.in_(blocked_user_ids),
        ~models.User.id.in_(blocking_user_ids),
        models.User.id != current_user.id
    )