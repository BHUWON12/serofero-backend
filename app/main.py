from fastapi import FastAPI, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Import modules using absolute imports
import models
from db import engine, get_db
from routes import auth, posts, connections, feed, messages, realtime, block
from security import get_current_user

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
    origins = ["*"]
    allow_credentials = True
else:
    default_origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://10.181.246.74:5173",
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
media_path.mkdir(exist_ok=True)
app.mount("/media", StaticFiles(directory="media"), name="media")

# Temporary uploads
temp_media_path = Path("temp_media")
temp_media_path.mkdir(exist_ok=True)

# Routers
app.include_router(auth.router, prefix="/auth", tags=["Authentication"])
app.include_router(posts.router, prefix="/posts", tags=["Posts"])
app.include_router(connections.router, prefix="/connections", tags=["Connections"])
app.include_router(feed.router, prefix="/feed", tags=["Feed"])
app.include_router(messages.router, prefix="/messages", tags=["Messages"])
app.include_router(block.router, prefix="/block", tags=["Block & Report"])
app.include_router(realtime.router)

# Basic routes
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

# Run app
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
