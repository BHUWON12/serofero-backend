from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from .. import models, schemas
from ..db import get_db
from ..security import get_current_user
from ..deps import get_current_active_user
from ..utils import sanitize_text

router = APIRouter()

@router.post("/", response_model=schemas.BlockResponse)
async def block_user(
    block_data: schemas.BlockCreate,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Block a user"""
    if block_data.blocked_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot block yourself")
    
    # Check if user exists
    blocked_user = db.query(models.User).filter(models.User.id == block_data.blocked_id).first()
    if not blocked_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Check if already blocked
    existing_block = db.query(models.Block).filter(
        models.Block.blocker_id == current_user.id,
        models.Block.blocked_id == block_data.blocked_id
    ).first()
    
    if existing_block:
        raise HTTPException(status_code=400, detail="User is already blocked")
    
    # Create block record
    block = models.Block(
        blocker_id=current_user.id,
        blocked_id=block_data.blocked_id
    )
    
    db.add(block)
    
    # Remove friendship if exists
    db.execute(
        models.friendship_table.delete().where(
            ((models.friendship_table.c.user_id == current_user.id) & 
             (models.friendship_table.c.friend_id == block_data.blocked_id)) |
            ((models.friendship_table.c.user_id == block_data.blocked_id) & 
             (models.friendship_table.c.friend_id == current_user.id))
        )
    )
    
    # Cancel any pending friend requests
    db.query(models.FriendRequest).filter(
        ((models.FriendRequest.sender_id == current_user.id) & 
         (models.FriendRequest.receiver_id == block_data.blocked_id)) |
        ((models.FriendRequest.sender_id == block_data.blocked_id) & 
         (models.FriendRequest.receiver_id == current_user.id))
    ).delete()
    
    db.commit()
    db.refresh(block)
    
    return block

@router.delete("/{user_id}")
async def unblock_user(
    user_id: int,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Unblock a user"""
    block = db.query(models.Block).filter(
        models.Block.blocker_id == current_user.id,
        models.Block.blocked_id == user_id
    ).first()
    
    if not block:
        raise HTTPException(status_code=404, detail="Block not found")
    
    db.delete(block)
    db.commit()
    
    return {"message": "User unblocked successfully"}

@router.get("/blocked", response_model=List[schemas.BlockResponse])
async def get_blocked_users(
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Get list of blocked users"""
    blocks = db.query(models.Block).filter(
        models.Block.blocker_id == current_user.id
    ).all()
    
    return blocks

@router.post("/report")
async def report_user(
    report_data: schemas.ReportCreate,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Report a user"""
    if report_data.reported_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot report yourself")
    
    # Check if reported user exists
    reported_user = db.query(models.User).filter(models.User.id == report_data.reported_id).first()
    if not reported_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Check if already reported recently (within 24 hours)
    from datetime import datetime, timedelta
    recent_report = db.query(models.Report).filter(
        models.Report.reporter_id == current_user.id,
        models.Report.reported_id == report_data.reported_id,
        models.Report.created_at >= datetime.utcnow() - timedelta(hours=24)
    ).first()
    
    if recent_report:
        raise HTTPException(status_code=400, detail="You have already reported this user recently")
    
    report = models.Report(
        reporter_id=current_user.id,
        reported_id=report_data.reported_id,
        reason=sanitize_text(report_data.reason),
        description=sanitize_text(report_data.description) if report_data.description else None
    )
    
    db.add(report)
    db.commit()
    
    return {"message": "User reported successfully"}

@router.get("/reports")
async def get_reports(
    skip: int = 0,
    limit: int = 50,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Get reports (admin only endpoint - implement proper admin check in production)"""
    # In production, add proper admin role checking
    # For now, this is a placeholder that could be restricted
    
    reports = db.query(models.Report).offset(skip).limit(limit).all()
    
    formatted_reports = []
    for report in reports:
        formatted_reports.append({
            "id": report.id,
            "reporter": {
                "id": report.reporter.id,
                "username": report.reporter.username,
                "email": report.reporter.email
            },
            "reported": {
                "id": report.reported.id,
                "username": report.reported.username,
                "email": report.reported.email
            },
            "reason": report.reason,
            "description": report.description,
            "status": report.status,
            "created_at": report.created_at
        })
    
    return formatted_reports

@router.put("/reports/{report_id}/status")
async def update_report_status(
    report_id: int,
    status: str,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Update report status (admin only)"""
    # In production, add proper admin role checking
    
    if status not in ["pending", "reviewed", "resolved"]:
        raise HTTPException(status_code=400, detail="Invalid status")
    
    report = db.query(models.Report).filter(models.Report.id == report_id).first()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    
    report.status = status
    db.commit()
    
    return {"message": f"Report status updated to {status}"}

@router.get("/check-blocked/{user_id}")
async def check_if_blocked(
    user_id: int,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Check if a user is blocked or has blocked current user"""
    blocked_by_current = db.query(models.Block).filter(
        models.Block.blocker_id == current_user.id,
        models.Block.blocked_id == user_id
    ).first()
    
    blocked_by_other = db.query(models.Block).filter(
        models.Block.blocker_id == user_id,
        models.Block.blocked_id == current_user.id
    ).first()
    
    return {
        "is_blocked_by_current_user": bool(blocked_by_current),
        "is_blocked_by_other_user": bool(blocked_by_other),
        "can_interact": not (blocked_by_current or blocked_by_other)
    }