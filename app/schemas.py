from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List

# Schemas for Authentication
class UserCreate(BaseModel):
    email: str
    username: str
    full_name: str
    password: str

class UserLogin(BaseModel):
    email: str
    password: str

class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str

class TokenRefresh(BaseModel):
    refresh_token: str

class UserResponse(BaseModel):
    id: int
    email: str
    username: str
    full_name: str
    avatar_url: Optional[str] = None
    bio: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

# Base User Schema for nesting
class UserPublic(BaseModel):
    id: int
    username: str
    full_name: str
    avatar_url: Optional[str] = None

    class Config:
        from_attributes = True

# Schemas for Posts
class PostResponse(BaseModel):
    id: int
    content: str
    media_url: Optional[str] = None
    media_type: Optional[str] = None
    author: UserPublic
    created_at: datetime
    likes_count: int
    comments_count: int
    is_liked: bool

    class Config:
        from_attributes = True

# Schema for the feed response
class FeedResponse(BaseModel):
    posts: List[PostResponse]
    has_more: bool
    next_page: Optional[int] = None


# Schemas for Messages
class MessageResponse(BaseModel):
    id: int
    content: str
    sender_id: int
    receiver_id: int
    message_type: str
    media_url: Optional[str] = None
    is_read: bool
    created_at: datetime

    class Config:
        from_attributes = True


# Schemas for Comments
class CommentCreate(BaseModel):
    content: str

class CommentResponse(BaseModel):
    id: int
    content: str
    author_id: int
    post_id: int
    created_at: datetime
    author: UserPublic

    class Config:
        from_attributes = True

# Friend Request Schema for responses
class FriendRequest(BaseModel):
    id: int
    status: str
    created_at: datetime
    sender: UserPublic

    class Config:
        from_attributes = True

# Friendship Schema for response on accept
class Friendship(BaseModel):
    user_id: int
    friend_id: int

    class Config:
        from_attributes = True

# Schemas for Block & Report
class BlockResponse(BaseModel):
    id: int
    blocked: UserPublic
    created_at: datetime

    class Config:
        from_attributes = True

class BlockCreate(BaseModel):
    blocked_id: int

class ReportCreate(BaseModel):
    reported_id: int
    reason: str
    description: Optional[str] = None
