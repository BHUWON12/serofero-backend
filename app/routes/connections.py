from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from pydantic import BaseModel
from sqlalchemy import and_, desc, or_
from typing import List

from .. import models, schemas, security
from ..db import get_db

router = APIRouter()

class _FriendRequestCreate(BaseModel):
    receiver_id: int


@router.get("/requests/received", response_model=List[schemas.FriendRequest])
def get_received_requests(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(security.get_current_user),
):
    """
    Get all pending friend requests for the current user.
    """
    requests = (
        db.query(models.FriendRequest)
        .options(joinedload(models.FriendRequest.sender))
        .filter(
            models.FriendRequest.receiver_id == current_user.id,
            models.FriendRequest.status == "pending",
        )
        .all()
    )
    return requests


@router.post("/request", response_model=schemas.FriendRequest, status_code=status.HTTP_201_CREATED)
def send_friend_request(
    request_data: _FriendRequestCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(security.get_current_user),
):
    """
    Send a friend request to another user.
    """
    receiver_id = request_data.receiver_id
    if receiver_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot send a friend request to yourself.",
        )

    receiver = db.query(models.User).filter(models.User.id == receiver_id).first()
    if not receiver:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found."
        )

    # Check if they are already friends
    is_friend = db.query(models.friendship_table).filter(
        or_(
            and_(models.friendship_table.c.user_id == current_user.id, models.friendship_table.c.friend_id == receiver_id),
            and_(models.friendship_table.c.user_id == receiver_id, models.friendship_table.c.friend_id == current_user.id)
        )
    ).first()
    if is_friend:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You are already friends with this user.",
        )

    # Check if a request already exists (either way) and is pending
    existing_request = db.query(models.FriendRequest).filter(
        or_(
            (models.FriendRequest.sender_id == current_user.id) & (models.FriendRequest.receiver_id == receiver_id),
            (models.FriendRequest.sender_id == receiver_id) & (models.FriendRequest.receiver_id == current_user.id)
        ),
        models.FriendRequest.status == 'pending'
    ).first()
    if existing_request:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A friend request already exists.")

    db_request = models.FriendRequest(sender_id=current_user.id, receiver_id=receiver_id)
    db.add(db_request)
    db.commit()
    db.refresh(db_request)
    return db_request

@router.post("/requests/{request_id}/accept", response_model=schemas.UserResponse)
def accept_friend_request(
    request_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(security.get_current_user),
):
    """
    Accept a friend request.
    """
    db_request = (
        db.query(models.FriendRequest)
        .options(joinedload(models.FriendRequest.sender))
        .filter(models.FriendRequest.id == request_id)
        .first()
    )

    if not db_request or db_request.receiver_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Friend request not found.")

    if db_request.status != "pending":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Friend request is not pending.")

    sender = db_request.sender

    # Check if they are already friends to avoid database errors
    is_friend = db.query(models.friendship_table).filter(
        or_(
            and_(models.friendship_table.c.user_id == current_user.id, models.friendship_table.c.friend_id == sender.id),
            and_(models.friendship_table.c.user_id == sender.id, models.friendship_table.c.friend_id == current_user.id)
        )
    ).first()

    # Update request status
    db_request.status = "accepted"

    if not is_friend:
        # Add to friends list. SQLAlchemy will handle the relationship.
        current_user.friends.append(sender)

    db.commit()

    return sender


@router.post("/requests/{request_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
def reject_friend_request(
    request_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(security.get_current_user),
):
    """
    Reject a friend request.
    """
    db_request = db.query(models.FriendRequest).filter(models.FriendRequest.id == request_id).first()

    if not db_request or db_request.receiver_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Friend request not found.")

    if db_request.status != "pending":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Friend request is not pending.")

    db_request.status = "rejected"
    db.commit()
    return None


@router.delete("/friends/{friend_id}", status_code=status.HTTP_204_NO_CONTENT)
def unfriend(
    friend_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(security.get_current_user),
):
    """Remove a friend."""
    friend = db.query(models.User).filter(models.User.id == friend_id).first()
    if not friend:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found."
        )

    # The 'friends' relationship on the User model is a standard
    # many-to-many. SQLAlchemy handles the removal from the association
    # table automatically.
    if friend in current_user.friends:
        current_user.friends.remove(friend)
    elif current_user in friend.friends:
        friend.friends.remove(current_user)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You are not friends with this user.",
        )

    db.commit()
    return None


@router.get("/friends", response_model=List[schemas.UserResponse])
def get_friends(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(security.get_current_user),
):
    """
    Get a list of the current user's friends.
    """
    # Query both sides of the friendship table to ensure symmetry
    friend_ids_1 = db.query(models.friendship_table.c.friend_id).filter(
        models.friendship_table.c.user_id == current_user.id
    )
    friend_ids_2 = db.query(models.friendship_table.c.user_id).filter(
        models.friendship_table.c.friend_id == current_user.id
    )
    friend_ids = {r[0] for r in friend_ids_1.union(friend_ids_2).all()}

    if not friend_ids:
        return []

    return db.query(models.User).filter(models.User.id.in_(friend_ids)).all()


@router.get("/requests/sent", response_model=List[schemas.FriendRequest])
def get_sent_friend_requests(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(security.get_current_user),
):
    """Get a list of friend requests sent by the current user."""
    sent_requests = (
        db.query(models.FriendRequest)
        .options(joinedload(models.FriendRequest.receiver))
        .filter(
            models.FriendRequest.sender_id == current_user.id,
            models.FriendRequest.status == "pending",
        )
        .all()
    )
    return sent_requests


@router.get("/suggestions", response_model=List[schemas.UserResponse])
def get_user_suggestions(
    limit: int = 10,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(security.get_current_user),
):
    """
    Get user suggestions.
    This is a simple implementation that suggests users who are not the current user
    and are not already connected in any way.
    """
    # Get IDs of users the current user has a connection with (friends or pending requests)
    connected_user_ids = {current_user.id}
    
    # Friends
    friends1 = db.query(models.friendship_table.c.friend_id).filter(models.friendship_table.c.user_id == current_user.id)
    friends2 = db.query(models.friendship_table.c.user_id).filter(models.friendship_table.c.friend_id == current_user.id)
    for f in friends1:
        connected_user_ids.add(f[0])
    for f in friends2:
        connected_user_ids.add(f[0])

    # Pending requests (sent and received)
    sent_requests = db.query(models.FriendRequest.receiver_id).filter(models.FriendRequest.sender_id == current_user.id)
    received_requests = db.query(models.FriendRequest.sender_id).filter(models.FriendRequest.receiver_id == current_user.id)
    for r in sent_requests:
        connected_user_ids.add(r[0])
    for r in received_requests:
        connected_user_ids.add(r[0])

    # Query for users not in the connected set
    suggestions = db.query(models.User).filter(
        ~models.User.id.in_(list(connected_user_ids))
    ).order_by(desc(models.User.created_at)).limit(limit).all()
    
    return suggestions