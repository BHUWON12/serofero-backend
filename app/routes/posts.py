from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from typing import Optional, List
from .. import models, schemas
from ..db import get_db
from ..security import get_current_user
from ..deps import get_current_active_user, get_unblocked_users_query
from ..utils import validate_and_save_file, sanitize_text

router = APIRouter()

@router.post("/", response_model=schemas.PostResponse)
async def create_post(
    content: str = Form(...),
    file: Optional[UploadFile] = File(None),
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Create a new post"""
    if len(content.strip()) == 0:
        raise HTTPException(status_code=400, detail="Post content cannot be empty")
    
    media_url = None
    media_type = None
    
    if file and file.filename:
        try:
            media_url, media_type = await validate_and_save_file(file)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))
    
    post = models.Post(
        content=sanitize_text(content),
        media_url=media_url,
        media_type=media_type,
        author_id=current_user.id
    )
    
    db.add(post)
    db.commit()
    db.refresh(post)
    
    return format_post_response(post, current_user.id, db)

@router.get("/{post_id}", response_model=schemas.PostResponse)
async def get_post(
    post_id: int,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Get a specific post"""
    post = db.query(models.Post).filter(models.Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    
    # Check if user is blocked by post author or has blocked post author
    blocked_by_author = db.query(models.Block).filter(
        models.Block.blocker_id == post.author_id,
        models.Block.blocked_id == current_user.id
    ).first()
    
    blocked_author = db.query(models.Block).filter(
        models.Block.blocker_id == current_user.id,
        models.Block.blocked_id == post.author_id
    ).first()
    
    if blocked_by_author or blocked_author:
        raise HTTPException(status_code=404, detail="Post not found")
    
    return format_post_response(post, current_user.id, db)

@router.post("/{post_id}/like")
async def toggle_like(
    post_id: int,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Toggle like on a post"""
    post = db.query(models.Post).filter(models.Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    
    # Check if post is from blocked user
    blocked = db.query(models.Block).filter(
        ((models.Block.blocker_id == current_user.id) & (models.Block.blocked_id == post.author_id)) |
        ((models.Block.blocker_id == post.author_id) & (models.Block.blocked_id == current_user.id))
    ).first()
    
    if blocked:
        raise HTTPException(status_code=404, detail="Post not found")
    
    existing_like = db.query(models.Like).filter(
        models.Like.user_id == current_user.id,
        models.Like.post_id == post_id
    ).first()
    
    if existing_like:
        db.delete(existing_like)
        action = "unliked"
    else:
        like = models.Like(user_id=current_user.id, post_id=post_id)
        db.add(like)
        action = "liked"
    
    db.commit()
    
    return {"message": f"Post {action} successfully"}

@router.post("/{post_id}/comment", response_model=schemas.CommentResponse)
async def add_comment(
    post_id: int,
    comment: schemas.CommentCreate,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Add a comment to a post"""
    post = db.query(models.Post).filter(models.Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    
    # Check if post is from blocked user
    blocked = db.query(models.Block).filter(
        ((models.Block.blocker_id == current_user.id) & (models.Block.blocked_id == post.author_id)) |
        ((models.Block.blocker_id == post.author_id) & (models.Block.blocked_id == current_user.id))
    ).first()
    
    if blocked:
        raise HTTPException(status_code=404, detail="Post not found")
    
    if len(comment.content.strip()) == 0:
        raise HTTPException(status_code=400, detail="Comment cannot be empty")
    
    db_comment = models.Comment(
        content=sanitize_text(comment.content),
        author_id=current_user.id,
        post_id=post_id
    )
    
    db.add(db_comment)
    db.commit()
    db.refresh(db_comment)
    
    return db_comment

@router.get("/{post_id}/comments", response_model=List[schemas.CommentResponse])
async def get_post_comments(
    post_id: int,
    skip: int = 0,
    limit: int = 50,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Get comments for a post"""
    post = db.query(models.Post).filter(models.Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    
    # Get unblocked users query
    unblocked_users = get_unblocked_users_query(current_user, db)
    unblocked_user_ids = [user.id for user in unblocked_users.all()]
    unblocked_user_ids.append(current_user.id)  # Include current user
    
    comments = db.query(models.Comment).filter(
        models.Comment.post_id == post_id,
        models.Comment.author_id.in_(unblocked_user_ids)
    ).order_by(desc(models.Comment.created_at)).offset(skip).limit(limit).all()
    
    return comments

@router.delete("/{post_id}")
async def delete_post(
    post_id: int,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Delete a post (only by author)"""
    post = db.query(models.Post).filter(models.Post.id == post_id).first()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    if post.author_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this post")

    db.delete(post)
    db.commit()

    return {"message": "Post deleted successfully"}

@router.get("/my-posts/", response_model=List[schemas.PostResponse])
async def get_my_posts(
    skip: int = 0,
    limit: int = 50,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Get current user's posts"""
    posts = db.query(models.Post).filter(
        models.Post.author_id == current_user.id
    ).order_by(desc(models.Post.created_at)).offset(skip).limit(limit).all()

    return [format_post_response(post, current_user.id, db) for post in posts]

@router.delete("/comments/{comment_id}")
async def delete_comment(
    comment_id: int,
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Delete a comment (only by author or post owner)"""
    comment = db.query(models.Comment).filter(models.Comment.id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    post = db.query(models.Post).filter(models.Post.id == comment.post_id).first()

    if comment.author_id != current_user.id and post.author_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this comment")

    db.delete(comment)
    db.commit()
    return {"message": "Comment deleted successfully"}


def format_post_response(post: models.Post, user_id: int, db: Session) -> schemas.PostResponse:
    """Format post for response with like and comment counts"""
    likes_count = db.query(func.count(models.Like.id)).filter(models.Like.post_id == post.id).scalar()
    comments_count = db.query(func.count(models.Comment.id)).filter(models.Comment.post_id == post.id).scalar()
    is_liked = db.query(models.Like).filter(
        models.Like.post_id == post.id,
        models.Like.user_id == user_id
    ).first() is not None
    
    return schemas.PostResponse(
        id=post.id,
        content=post.content,
        media_url=post.media_url,
        media_type=post.media_type,
        author=post.author,
        created_at=post.created_at,
        likes_count=likes_count,
        comments_count=comments_count,
        is_liked=is_liked
    )
