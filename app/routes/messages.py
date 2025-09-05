from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import desc, or_, and_, func
from typing import List, Optional
from datetime import datetime, timezone
from pathlib import Path
import os
from .. import models
from ..schemas import MessageResponse, UserResponse
from ..db import get_db, SessionLocal
from ..security import get_current_user
from ..deps import get_current_active_user
from ..routes.realtime import manager
from ..utils import sanitize_text, MAX_FILE_SIZE, upload_file_to_cloudinary
from ..utils.encryption import encrypt_message_content, decrypt_message_content

router = APIRouter()

TEMP_MEDIA_DIR = Path("temp_media")
TEMP_MEDIA_DIR.mkdir(exist_ok=True)

async def upload_and_finalize_message(message_id: int, temp_file_path: str, original_filename: str):
    """
    Background task to upload file to Cloudinary, update the message,
    and notify clients via WebSocket.
    """
    db = SessionLocal()
    try:
        media_url, detected_type = await upload_file_to_cloudinary(temp_file_path, original_filename)

        message = db.query(models.Message).filter(models.Message.id == message_id).first()
        if message:
            message.media_url = media_url
            message.message_type = detected_type
            message.status = "sent"
            db.commit()
            db.refresh(message)

            # Create response data with decrypted content for WebSocket
            response_data = MessageResponse(
                id=message.id,
                content=decrypt_message_content(message.content) if message.content else "",
                sender_id=message.sender_id,
                receiver_id=message.receiver_id,
                message_type=message.message_type,
                media_url=message.media_url,
                is_read=message.is_read,
                created_at=message.created_at,
                sender=message.sender,
                receiver=message.receiver
            ).model_dump(mode="json")

            # Send an update to both sender and receiver
            try:
                await manager.send_json_to_user({"type": "message_updated", "data": response_data}, message.sender_id)
                await manager.send_json_to_user({"type": "message_updated", "data": response_data}, message.receiver_id)
            except Exception as e:
                print(f"Failed to send message update for message {message_id}: {e}")
    except Exception as e:
        print(f"Error uploading file for message {message_id}: {e}")
        message = db.query(models.Message).filter(models.Message.id == message_id).first()
        if message:
            message.status = "failed"
            db.commit()
            db.refresh(message)
            response_data = MessageResponse.from_orm(message).model_dump(mode="json")
            try:
                await manager.send_json_to_user({"type": "message_updated", "data": response_data}, message.sender_id)
                await manager.send_json_to_user({"type": "message_updated", "data": response_data}, message.receiver_id)
            except Exception as e:
                print(f"Failed to send message failure update for message {message_id}: {e}")
    finally:
        db.close()
        # Clean up the temporary file
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)

@router.post("/", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
async def send_message(
    background_tasks: BackgroundTasks,
    content: Optional[str] = Form(None),
    receiver_id: int = Form(...),
    file: Optional[UploadFile] = File(None),
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Send a message to another user"""
    if receiver_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot send message to yourself")
    
    receiver = db.query(models.User).filter(models.User.id == receiver_id).first()
    if not receiver:
        raise HTTPException(status_code=404, detail="User not found")
    
    blocked = db.query(models.Block).filter(
        ((models.Block.blocker_id == current_user.id) & (models.Block.blocked_id == receiver_id)) |
        ((models.Block.blocker_id == receiver_id) & (models.Block.blocked_id == current_user.id))
    ).first()
    
    if blocked:
        raise HTTPException(status_code=403, detail="Cannot send message to this user")

    if not content and not (file and file.filename):
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    
    # Encrypt message content before storing
    encrypted_content = encrypt_message_content(sanitize_text(content.strip())) if content else ""
    message = models.Message(
        content=encrypted_content,
        sender_id=current_user.id,
        receiver_id=receiver_id,
    )

    if file and file.filename:
        temp_file_path = TEMP_MEDIA_DIR / f"{datetime.utcnow().timestamp()}_{file.filename}"
        
        file_size = 0
        try:
            with open(temp_file_path, "wb") as buffer:
                while chunk := await file.read(8192):
                    file_size += len(chunk)
                    if file_size > MAX_FILE_SIZE:
                        buffer.close()
                        os.remove(temp_file_path)
                        raise HTTPException(status_code=413, detail=f"File is too large. Max size is {MAX_FILE_SIZE // 1024 // 1024}MB.")
                    buffer.write(chunk)
        finally:
            await file.close()
        
        message.status = "uploading"
        message.message_type = "file" # Placeholder, will be updated by background task
        if not message.content:
            message.content = file.filename
        
        db.add(message)
        db.commit()
        db.refresh(message)

        background_tasks.add_task(upload_and_finalize_message, message.id, str(temp_file_path), file.filename)
        
        # Send a placeholder message to both sender and receiver immediately
        # Create response data with decrypted content for WebSocket
        response_data = MessageResponse(
            id=message.id,
            content=decrypt_message_content(message.content) if message.content else "",
            sender_id=message.sender_id,
            receiver_id=message.receiver_id,
            message_type=message.message_type,
            media_url=message.media_url,
            is_read=message.is_read,
            created_at=message.created_at,
            sender=message.sender,
            receiver=message.receiver
        ).model_dump(mode="json")
        try:
            await manager.send_json_to_user({"type": "new_message", "data": response_data}, current_user.id)
            await manager.send_json_to_user({"type": "new_message", "data": response_data}, receiver_id)
            # Notify both users to update their conversation lists
            await manager.send_json_to_user({"type": "conversation_update"}, current_user.id)
            await manager.send_json_to_user({"type": "conversation_update"}, receiver_id)
        except Exception as e:
            print(f"Failed to send new message notification for message {message.id}: {e}")
    else:
        # Handle text-only messages
        message.status = "sent"
        message.message_type = "text"
        db.add(message)
        db.commit()
        db.refresh(message)

        # Broadcast the text message immediately to both parties
        # Create response data with decrypted content for WebSocket
        response_data = MessageResponse(
            id=message.id,
            content=decrypt_message_content(message.content) if message.content else "",
            sender_id=message.sender_id,
            receiver_id=message.receiver_id,
            message_type=message.message_type,
            media_url=message.media_url,
            is_read=message.is_read,
            created_at=message.created_at,
            sender=message.sender,
            receiver=message.receiver
        ).model_dump(mode="json")
        try:
            await manager.send_json_to_user({"type": "new_message", "data": response_data}, receiver_id)
            await manager.send_json_to_user({"type": "new_message", "data": response_data}, current_user.id)
            # Notify both users to update their conversation lists
            await manager.send_json_to_user({"type": "conversation_update"}, current_user.id)
            await manager.send_json_to_user({"type": "conversation_update"}, receiver_id)
        except Exception as e:
            print(f"Failed to send new message notification for message {message.id}: {e}")
            # Continue execution even if WebSocket fails

    # Return response with decrypted content
    response = MessageResponse(
        id=message.id,
        content=decrypt_message_content(message.content) if message.content else "",
        sender_id=message.sender_id,
        receiver_id=message.receiver_id,
        message_type=message.message_type,
        media_url=message.media_url,
        is_read=message.is_read,
        created_at=message.created_at,
        sender=message.sender,
        receiver=message.receiver
    )
    return response

@router.get("/{user_id}", response_model=List[MessageResponse])
async def get_conversation(
    user_id: int,
    skip: int = 0,
    limit: int = 50,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Get conversation between current user and another user"""
    # Check if other user exists and is not blocked
    other_user = db.query(models.User).filter(models.User.id == user_id).first()
    if not other_user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Check for blocks
    blocked = db.query(models.Block).filter(
        ((models.Block.blocker_id == current_user.id) & (models.Block.blocked_id == user_id)) |
        ((models.Block.blocker_id == user_id) & (models.Block.blocked_id == current_user.id))
    ).first()
    
    if blocked:
        return []  # Return empty conversation if blocked
    
    messages = db.query(models.Message).filter(
        or_(
            and_(models.Message.sender_id == current_user.id, models.Message.receiver_id == user_id),
            and_(models.Message.sender_id == user_id, models.Message.receiver_id == current_user.id)
        )
    ).order_by(desc(models.Message.created_at)).offset(skip).limit(limit).all()

    # Decrypt message content for each message and create new objects
    decrypted_messages = []
    for message in messages:
        if message.content:
            # Create a new MessageResponse with decrypted content
            decrypted_message = MessageResponse(
                id=message.id,
                content=decrypt_message_content(message.content),
                sender_id=message.sender_id,
                receiver_id=message.receiver_id,
                message_type=message.message_type,
                media_url=message.media_url,
                is_read=message.is_read,
                created_at=message.created_at,
                sender=message.sender,
                receiver=message.receiver
            )
            decrypted_messages.append(decrypted_message)
        else:
            decrypted_messages.append(MessageResponse.from_orm(message))

    # Mark messages as read
    db.query(models.Message).filter(
        models.Message.sender_id == user_id,
        models.Message.receiver_id == current_user.id,
        models.Message.is_read == False
    ).update({models.Message.is_read: True})
    db.commit()

    return list(reversed(decrypted_messages))  # Return in chronological order

@router.get("/", response_model=List[dict])
async def get_conversations(
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Get list of conversations with last message.
    
    This will include all friends, even if no messages have been exchanged.
    """
    blocked_user_ids = db.query(models.Block.blocked_id).filter(
        models.Block.blocker_id == current_user.id
    ).union(
        db.query(models.Block.blocker_id).filter(
            models.Block.blocked_id == current_user.id
        )
    ).all()
    blocked_ids = {b[0] for b in blocked_user_ids}

    # Get all potential conversation partners (friends + people you've messaged)
    messaged_user_ids = {r[0] for r in db.query(models.Message.receiver_id).filter(
        models.Message.sender_id == current_user.id
    ).distinct()}
    messaged_user_ids.update({r[0] for r in db.query(models.Message.sender_id).filter(
        models.Message.receiver_id == current_user.id
    ).distinct()})

    friend_ids_1 = db.query(models.friendship_table.c.friend_id).filter(
        models.friendship_table.c.user_id == current_user.id
    )
    friend_ids_2 = db.query(models.friendship_table.c.user_id).filter(
        models.friendship_table.c.friend_id == current_user.id
    )
    friend_ids = {r[0] for r in friend_ids_1.union(friend_ids_2).all()}
    all_user_ids = (messaged_user_ids.union(friend_ids)) - blocked_ids

    if not all_user_ids:
        return []

    # --- Optimized Queries ---
    # 1. Get all user info in one go
    users = db.query(models.User).filter(models.User.id.in_(all_user_ids)).all()
    users_by_id = {user.id: user for user in users}

    # 2. Get all unread counts in one go
    unread_counts_query = db.query(
        models.Message.sender_id, func.count(models.Message.id)
    ).filter(
        models.Message.receiver_id == current_user.id,
        models.Message.sender_id.in_(all_user_ids),
        models.Message.is_read == False
    ).group_by(models.Message.sender_id).all()
    unread_counts = {sender_id: count for sender_id, count in unread_counts_query}

    # 3. Get all last messages in one go using a window function
    last_message_subquery = db.query(
        models.Message,
        func.row_number().over(
            partition_by=func.least(models.Message.sender_id, models.Message.receiver_id),
            order_by=models.Message.created_at.desc()
        ).label('rn')
    ).filter(
        or_(models.Message.sender_id == current_user.id, models.Message.receiver_id == current_user.id),
        or_(models.Message.sender_id.in_(all_user_ids), models.Message.receiver_id.in_(all_user_ids))
    ).subquery()
    last_messages_query = db.query(last_message_subquery).filter(last_message_subquery.c.rn == 1).all()
    last_messages_by_user_id = {
        (msg.sender_id if msg.receiver_id == current_user.id else msg.receiver_id): msg
        for msg in last_messages_query
    }

    # --- Assemble Response ---
    conversations = []
    for user_id in all_user_ids:
        user = users_by_id.get(user_id)
        if not user:
            continue

        last_message = last_messages_by_user_id.get(user_id)
        # Decrypt last message content if it exists
        if last_message:
            sender = users_by_id.get(last_message.sender_id)
            receiver = users_by_id.get(last_message.receiver_id)
            content = decrypt_message_content(last_message.content) if last_message.content else ""
            last_message_response = MessageResponse(
                id=last_message.id,
                content=content,
                sender_id=last_message.sender_id,
                receiver_id=last_message.receiver_id,
                message_type=last_message.message_type,
                media_url=last_message.media_url,
                is_read=last_message.is_read,
                created_at=last_message.created_at,
                sender=sender,
                receiver=receiver
            )
        else:
            last_message_response = None

        conversations.append({
            "user": UserResponse.from_orm(user),
            "last_message": last_message_response,
            "unread_count": unread_counts.get(user_id, 0)
        })
    
    # Sort by last message time
    conversations.sort(
        key=lambda x: x["last_message"].created_at if x["last_message"] else datetime.min.replace(tzinfo=timezone.utc),
        reverse=True
    )
    
    return conversations

@router.post("/{message_id}/read")
async def mark_message_read(
    message_id: int,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Mark a specific message as read"""
    message = db.query(models.Message).filter(
        models.Message.id == message_id,
        models.Message.receiver_id == current_user.id
    ).first()
    
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    
    message.is_read = True
    db.commit()
    
    return {"message": "Message marked as read"}

@router.delete("/{message_id}")
async def delete_message(
    message_id: int,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Delete a message (only sender can delete)"""
    message = db.query(models.Message).filter(
        models.Message.id == message_id,
        models.Message.sender_id == current_user.id
    ).first()
    
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    
    db.delete(message)
    db.commit()
    
    return {"message": "Message deleted successfully"}