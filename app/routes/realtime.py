from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, status
from typing import Dict
from sqlalchemy.orm import Session
from jose import jwt, JWTError
from datetime import datetime
from .. import models
from ..db import get_db
from ..security import get_current_user_ws
import os

router = APIRouter()

# Load secret key from env (same as JWT secret)
SECRET_KEY = os.getenv("JWT_SECRET", "your-super-secure-jwt-secret")
ALGORITHM = os.getenv("ALGORITHM", "HS256")

# -------------------------
# Connection Manager
# -------------------------
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[int, WebSocket] = {}

    async def connect(self, websocket: WebSocket, user_id: int):
        self.active_connections[user_id] = websocket
        print(f"✅ User {user_id} connected. Active: {list(self.active_connections.keys())}")

    def disconnect(self, user_id: int):
        if user_id in self.active_connections:
            del self.active_connections[user_id]
            print(f"❌ User {user_id} disconnected. Active: {list(self.active_connections.keys())}")

    async def send_json_to_user(self, payload: dict, user_id: int):
        websocket = self.active_connections.get(user_id)
        if websocket:
            try:
                await websocket.send_json(payload)
                print(f"✅ Sent message to user {user_id}: {payload.get('type', 'unknown')}")
            except Exception as e:
                print(f"❌ Failed to send message to user {user_id}: {e}")
                self.disconnect(user_id)

    async def broadcast_to_friends(self, user_id: int, payload: dict):
        # Iterate over a snapshot of connections to avoid runtime mutation issues.
        for friend_id, websocket in list(self.active_connections.items()):
            if friend_id == user_id:
                continue
            try:
                await websocket.send_json(payload)
            except Exception as e:
                # If sending fails (client disconnected / broken socket), remove the connection
                print(f"❌ Failed to broadcast to user {friend_id}: {e}")
                try:
                    self.disconnect(friend_id)
                except Exception:
                    pass


manager = ConnectionManager()

# -------------------------
# General WS (broadcast to all, no auth)
# -------------------------
@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            for client in list(manager.active_connections.values()):
                await client.send_text(data)
    except WebSocketDisconnect:
        print("Anonymous WS client disconnected")


# -------------------------
# User-Specific WS with Auth
# -------------------------
@router.websocket("/ws/{user_id}")
async def user_websocket_endpoint(websocket: WebSocket, user_id: int, db: Session = Depends(get_db)):
    # Authenticate websocket using shared helper. The helper will close the websocket
    # on failure and return None. This centralizes token verification and DB lookup.
    user = await get_current_user_ws(websocket, db)
    if not user:
        return

    # Ensure token's user id matches the requested path param
    if user.id != user_id:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # Accept and register connection
    await websocket.accept()
    await manager.connect(websocket, user_id)

    # 5b. Notify others that this user is online
    await manager.broadcast_to_friends(user_id, {
        "type": "status",
        "status": "online",
        "user_id": user_id
    })

    # 6. Listen for messages
    try:
        while True:
            data = await websocket.receive_json()

            # Normalize receiver_id (accepts both shapes)
            receiver_id = (data.get("data") or {}).get("receiver_id") or data.get("receiver_id") or data.get("to_user_id")

            if data.get("type") == "typing_start" and receiver_id:
                typing_payload = {"type": "typing_start", "data": {"user_id": user_id}}
                await manager.send_json_to_user(typing_payload, receiver_id)

            elif data.get("type") == "typing_stop" and receiver_id:
                typing_payload = {"type": "typing_stop", "data": {"user_id": user_id}}
                await manager.send_json_to_user(typing_payload, receiver_id)

            elif data.get("type") == "message" and receiver_id and "content" in data:
                message_payload = {
                    "type": "new_message",
                    "data": {
                        "sender_id": user_id,
                        "receiver_id": receiver_id,
                        "content": data["content"],
                        "message_type": "text",
                        "created_at": datetime.utcnow().isoformat()
                    }
                }
                await manager.send_json_to_user(message_payload, receiver_id)
                await manager.send_json_to_user(message_payload, user_id)

            elif data.get("type") == "webrtc-offer" and receiver_id:
                offer_payload = {
                    "type": "webrtc-offer",
                    "offer": data.get("offer"),
                    "from_user_id": user_id,
                    "caller_info": data.get("caller_info")
                }
                await manager.send_json_to_user(offer_payload, receiver_id)

            elif data.get("type") == "webrtc-answer" and receiver_id:
                answer_payload = {
                    "type": "webrtc-answer",
                    "answer": data.get("answer"),
                    "from_user_id": user_id
                }
                await manager.send_json_to_user(answer_payload, receiver_id)

            elif data.get("type") == "webrtc-ice-candidate" and receiver_id:
                candidate_payload = {
                    "type": "webrtc-ice-candidate",
                    "candidate": data.get("candidate"),
                    "from_user_id": user_id
                }
                await manager.send_json_to_user(candidate_payload, receiver_id)

            elif data.get("type") == "call-ended" and receiver_id:
                end_payload = {
                    "type": "call-ended",
                    "from_user_id": user_id
                }
                await manager.send_json_to_user(end_payload, receiver_id)

    except WebSocketDisconnect:
        # Normal disconnect from client
        pass
    except Exception as e:
        # Log unexpected errors to avoid crashing the event loop and causing abnormal closures (1006)
        print(f"WebSocket error for user {user_id}: {e}")
    finally:
        # Ensure we always clean up the connection and notify friends
        manager.disconnect(user_id)
        try:
            await manager.broadcast_to_friends(user_id, {
                "type": "status",
                "status": "offline",
                "user_id": user_id
            })
        except Exception as e:
            print(f"Failed to broadcast offline status for user {user_id}: {e}")
