from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer
from pathlib import Path
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Absolute imports
from . import models
from .db import engine, get_db
from .routes import auth, posts, connections, feed, messages, realtime, block
from .security import get_current_user

# Create database tables
models.Base.metadata.create_all(bind=engine)

# Initialize FastAPI app
app = FastAPI(
    title="serofero API",
    description="A secure social platform with messaging and calls",
    version="1.0.0"
)

# Security
security = HTTPBearer()

# Detect environment
ENV = os.getenv("ENV", "development")  # read from .env, default to dev

# Define a base set of allowed origins for development and production Vercel deployments
base_origins = [
    "https://serofero-frontend.vercel.app",
    "https://serofero.vercel.app",
]

# For development, add localhost origins
if ENV == "development":
    base_origins.extend([
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ])

# Read additional origins from the .env file and combine with the base list
additional_origins_str = os.getenv("CORS_ORIGINS", "")
additional_origins = [origin.strip() for origin in additional_origins_str.split(",") if origin.strip()]

# Combine all origins, ensuring no duplicates
origins = list(set(base_origins + additional_origins))

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Static files — skip folder creation, just mount if needed
# Uncomment if you actually serve media files
# app.mount("/media", StaticFiles(directory="media"), name="media")

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

# Run app directly (for dev only)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
