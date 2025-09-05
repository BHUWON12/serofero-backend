from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from typing import List
from datetime import timedelta
from .. import models, schemas
from ..db import get_db
from ..security import get_current_user
from ..deps import get_current_active_user
from ..routes.posts import format_post_response

router = APIRouter()

@router.get("/", response_model=schemas.FeedResponse)
async def get_feed(
    page: int = Query(1, ge=1),
    limit: int = Query(10, ge=1, le=50),
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """
    Get user's personalized feed
    Priority: Friends' posts first, then non-friends (excluding blocked users)
    """
    offset = (page - 1) * limit
    
    # Get blocked user IDs (both directions)
    blocked_user_ids = db.query(models.Block.blocked_id).filter(
        models.Block.blocker_id == current_user.id
    ).union(
        db.query(models.Block.blocker_id).filter(
            models.Block.blocked_id == current_user.id
        )
    ).all()
    blocked_ids = [b[0] for b in blocked_user_ids]
    
    # Get friend IDs
    friend_ids = db.query(models.friendship_table.c.friend_id).filter(
        models.friendship_table.c.user_id == current_user.id
    ).all()
    friend_ids = [f[0] for f in friend_ids]
    
    # First, get friends' posts
    friends_posts = []
    if friend_ids:
        friends_posts_query = db.query(models.Post).filter(
            models.Post.author_id.in_(friend_ids),
            ~models.Post.author_id.in_(blocked_ids)
        ).order_by(desc(models.Post.created_at))
        
        friends_posts = friends_posts_query.offset(offset).limit(limit).all()
    
    posts_to_return = friends_posts
    remaining_limit = limit - len(friends_posts)
    
    # If we need more posts, get from non-friends
    if remaining_limit > 0:
        exclude_ids = friend_ids + blocked_ids + [current_user.id]
        
        non_friends_posts = db.query(models.Post).filter(
            ~models.Post.author_id.in_(exclude_ids)
        ).order_by(desc(models.Post.created_at))
        
        # If this is not the first page and we had friends' posts,
        # we need to adjust the offset for non-friends posts
        if page > 1 and friend_ids:
            # Calculate how many friends' posts we've seen in previous pages
            friends_posts_count = friends_posts_query.count()
            friends_posts_per_page = min(limit, friends_posts_count)
            
            if page * limit > friends_posts_count:
                # We've exhausted friends' posts, calculate offset for non-friends
                non_friends_offset = (page * limit) - friends_posts_count - remaining_limit
                non_friends_posts = non_friends_posts.offset(max(0, non_friends_offset))
        
        additional_posts = non_friends_posts.limit(remaining_limit).all()
        posts_to_return.extend(additional_posts)
    
    # Format posts with engagement data
    formatted_posts = []
    for post in posts_to_return:
        formatted_post = format_post_response(post, current_user.id, db)
        formatted_posts.append(formatted_post)
    
    # Check if there are more posts
    total_friends_posts = 0
    if friend_ids:
        total_friends_posts = db.query(func.count(models.Post.id)).filter(
            models.Post.author_id.in_(friend_ids),
            ~models.Post.author_id.in_(blocked_ids)
        ).scalar()
    
    exclude_ids = friend_ids + blocked_ids + [current_user.id]
    total_non_friends_posts = db.query(func.count(models.Post.id)).filter(
        ~models.Post.author_id.in_(exclude_ids)
    ).scalar()
    
    total_posts = total_friends_posts + total_non_friends_posts
    has_more = (page * limit) < total_posts
    
    return schemas.FeedResponse(
        posts=formatted_posts,
        has_more=has_more,
        next_page=page + 1 if has_more else None
    )

@router.get("/trending", response_model=List[schemas.PostResponse])
async def get_trending_posts(
    limit: int = Query(20, ge=1, le=50),
    current_user: models.User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """Get trending posts based on engagement"""
    # Get blocked user IDs
    blocked_user_ids = db.query(models.Block.blocked_id).filter(
        models.Block.blocker_id == current_user.id
    ).union(
        db.query(models.Block.blocker_id).filter(
            models.Block.blocked_id == current_user.id
        )
    ).all()
    blocked_ids = [b[0] for b in blocked_user_ids]
    
    # Get posts with high engagement (likes + comments) from last 7 days
    posts = db.query(models.Post).filter(
        ~models.Post.author_id.in_(blocked_ids),
        models.Post.author_id != current_user.id,
        models.Post.created_at >= (func.now() - timedelta(days=7))
    ).order_by(desc(models.Post.created_at)).limit(limit * 2).all()  # Get more to sort by engagement
    
    # Calculate engagement score for each post
    posts_with_engagement = []
    for post in posts:
        likes_count = db.query(func.count(models.Like.id)).filter(models.Like.post_id == post.id).scalar()
        comments_count = db.query(func.count(models.Comment.id)).filter(models.Comment.post_id == post.id).scalar()
        engagement_score = likes_count * 2 + comments_count * 3  # Comments worth more
        
        posts_with_engagement.append((post, engagement_score))
    
    # Sort by engagement and take top posts
    posts_with_engagement.sort(key=lambda x: x[1], reverse=True)
    top_posts = [post[0] for post in posts_with_engagement[:limit]]
    
    # Format posts
    formatted_posts = []
    for post in top_posts:
        formatted_post = format_post_response(post, current_user.id, db)
        formatted_posts.append(formatted_post)
    
    return formatted_posts