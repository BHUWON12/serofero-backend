from fastapi import FastAPI, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from .db import engine
from . import models
from .routes import auth, posts, connections, feed, messages, realtime, block
from .security import get_current_user

# Create tables
models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="serofero API",
    description="A secure social platform with messaging and calls",
    version="1.0.0"
)

# Security
security = HTTPBearer()

# Detect environment
ENV = os.getenv("ENVIRONMENT", "development")

# CORS middleware
if ENV == "development":
    # Open everything in development
    origins = ["*"]
    allow_credentials = True  # ⚠️ If you use cookies, better to set explicit origins
else:
    # Strict origins in production
    default_origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://10.181.246.74:5173",  # Your machine's LAN IP for frontend
    ]
    origins_str = os.getenv("CORS_ORIGINS", ",".join(default_origins))
    origins = [origin.strip() for origin in origins_str.split(",")]
    allow_credentials = True

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files for media uploads
media_path = Path("media")
try:
    media_path.mkdir(exist_ok=True)
except OSError:
    # Handle read-only file system in deployment
    pass
app.mount("/media", StaticFiles(directory="media"), name="media")

# Create a temporary directory for uploads that will be processed in the background
temp_media_path = Path("temp_media")
try:
    temp_media_path.mkdir(exist_ok=True)
except OSError:
    # Handle read-only file system in deployment
    pass


# Routers
app.include_router(auth.router, prefix="/auth", tags=["Authentication"])
app.include_router(posts.router, prefix="/posts", tags=["Posts"])
app.include_router(connections.router, prefix="/connections", tags=["Connections"])
app.include_router(feed.router, prefix="/feed", tags=["Feed"])
app.include_router(messages.router, prefix="/messages", tags=["Messages"])
app.include_router(block.router, prefix="/block", tags=["Block & Report"])
app.include_router(realtime.router)

@app.get("/")
async def root():
    return {"message": "serofero API - Secure Social Platform"}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "serofero-backend"}

@app.get("/profile")
async def get_profile(current_user: models.User = Depends(get_current_user)):
    """Get current user profile"""
    return {
        "id": current_user.id,
        "email": current_user.email,
        "username": current_user.username,
        "full_name": current_user.full_name,
        "avatar_url": current_user.avatar_url,
        "bio": current_user.bio,
        "created_at": current_user.created_at
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
