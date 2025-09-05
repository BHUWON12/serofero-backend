"""
Call security utilities for secure WebRTC signaling and access control
"""
import hashlib
import hmac
import time
import secrets
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.backends import default_backend
import json
import base64

from sqlalchemy.orm import Session
from .. import models


class CallSecurityManager:
    """Manages security for WebRTC calls including encryption, authentication, and access control"""
    
    def __init__(self):
        self.active_calls: Dict[str, Dict] = {}
        self.call_permissions: Dict[int, Dict] = {}
        self.security_events: List[Dict] = []
        
    def generate_call_id(self) -> str:
        """Generate a secure call ID"""
        return secrets.token_hex(32)
    
    def create_call_session(self, caller_id: int, receiver_id: int, call_type: str = 'audio') -> Dict:
        """Create a new secure call session"""
        call_id = self.generate_call_id()
        session_key = secrets.token_bytes(32)
        
        call_session = {
            'call_id': call_id,
            'caller_id': caller_id,
            'receiver_id': receiver_id,
            'call_type': call_type,
            'session_key': session_key,
            'created_at': datetime.utcnow(),
            'status': 'initiating',
            'security_level': 'high',
            'encryption_enabled': True,
            'heartbeat_count': 0,
            'last_heartbeat': datetime.utcnow()
        }
        
        self.active_calls[call_id] = call_session
        
        self.log_security_event('call_session_created', {
            'call_id': call_id,
            'caller_id': caller_id,
            'receiver_id': receiver_id,
            'call_type': call_type
        })
        
        return call_session
    
    def validate_call_permissions(self, db: Session, caller_id: int, receiver_id: int) -> Tuple[bool, str]:
        """Validate if a call is allowed between two users"""
        
        # Check if users are blocked
        block_query = db.query(models.Block).filter(
            ((models.Block.blocker_id == caller_id) & (models.Block.blocked_id == receiver_id)) |
            ((models.Block.blocker_id == receiver_id) & (models.Block.blocked_id == caller_id))
        ).first()
        
        if block_query:
            self.log_security_event('call_blocked', {
                'caller_id': caller_id,
                'receiver_id': receiver_id,
                'reason': 'user_blocked'
            })
            return False, "Call not allowed - user blocked"
        
        # Check if users are friends (optional - depends on app policy)
        friendship_query = db.query(models.friendship_table).filter(
            ((models.friendship_table.c.user_id == caller_id) & 
             (models.friendship_table.c.friend_id == receiver_id)) |
            ((models.friendship_table.c.user_id == receiver_id) & 
             (models.friendship_table.c.friend_id == caller_id))
        ).first()
        
        if not friendship_query:
            # Allow calls between non-friends but log for monitoring
            self.log_security_event('call_non_friend', {
                'caller_id': caller_id,
                'receiver_id': receiver_id,
                'reason': 'not_friends'
            })
        
        # Check call rate limiting
        if not self.check_call_rate_limit(caller_id):
            self.log_security_event('call_rate_limited', {
                'caller_id': caller_id,
                'receiver_id': receiver_id,
                'reason': 'rate_limit_exceeded'
            })
            return False, "Too many call attempts - please wait"
        
        return True, "Call allowed"
    
    def check_call_rate_limit(self, user_id: int, max_calls: int = 10, window_minutes: int = 5) -> bool:
        """Check if user has exceeded call rate limit"""
        current_time = datetime.utcnow()
        window_start = current_time - timedelta(minutes=window_minutes)
        
        # Count recent calls from this user
        recent_calls = sum(1 for call in self.active_calls.values() 
                          if call['caller_id'] == user_id and call['created_at'] > window_start)
        
        return recent_calls < max_calls
    
    def encrypt_signaling_data(self, call_id: str, data: Dict) -> Optional[Dict]:
        """Encrypt WebRTC signaling data"""
        if call_id not in self.active_calls:
            return None
            
        call_session = self.active_calls[call_id]
        session_key = call_session['session_key']
        
        try:
            # Use AEAD encryption for signaling data
            aesgcm = AESGCM(session_key)
            nonce = secrets.token_bytes(12)
            
            plaintext = json.dumps(data).encode('utf-8')
            ciphertext = aesgcm.encrypt(nonce, plaintext, None)
            
            return {
                'encrypted_data': base64.b64encode(ciphertext).decode('utf-8'),
                'nonce': base64.b64encode(nonce).decode('utf-8'),
                'is_encrypted': True
            }
        except Exception as e:
            self.log_security_event('encryption_failed', {
                'call_id': call_id,
                'error': str(e)
            })
            return None
    
    def decrypt_signaling_data(self, call_id: str, encrypted_data: str, nonce: str) -> Optional[Dict]:
        """Decrypt WebRTC signaling data"""
        if call_id not in self.active_calls:
            return None
            
        call_session = self.active_calls[call_id]
        session_key = call_session['session_key']
        
        try:
            aesgcm = AESGCM(session_key)
            
            ciphertext = base64.b64decode(encrypted_data.encode('utf-8'))
            nonce_bytes = base64.b64decode(nonce.encode('utf-8'))
            
            plaintext = aesgcm.decrypt(nonce_bytes, ciphertext, None)
            return json.loads(plaintext.decode('utf-8'))
        except Exception as e:
            self.log_security_event('decryption_failed', {
                'call_id': call_id,
                'error': str(e)
            })
            return None
    
    def validate_signaling_message(self, message: Dict, user_id: int) -> Tuple[bool, str]:
        """Validate incoming signaling message for security"""
        
        # Check message age to prevent replay attacks
        if 'timestamp' in message:
            message_age = time.time() * 1000 - message['timestamp']
            if message_age > 30000:  # 30 seconds
                self.log_security_event('replay_attack_detected', {
                    'user_id': user_id,
                    'message_age': message_age,
                    'message_type': message.get('type')
                })
                return False, "Message too old - possible replay attack"
        
        # Validate call ID format
        call_id = message.get('call_id')
        if call_id and not self.is_valid_call_id(call_id):
            self.log_security_event('invalid_call_id', {
                'user_id': user_id,
                'call_id': call_id,
                'message_type': message.get('type')
            })
            return False, "Invalid call ID format"
        
        # Check if user is authorized for this call
        if call_id and call_id in self.active_calls:
            call_session = self.active_calls[call_id]
            if user_id not in [call_session['caller_id'], call_session['receiver_id']]:
                self.log_security_event('unauthorized_call_access', {
                    'user_id': user_id,
                    'call_id': call_id,
                    'message_type': message.get('type')
                })
                return False, "Unauthorized access to call"
        
        return True, "Message valid"
    
    def is_valid_call_id(self, call_id: str) -> bool:
        """Validate call ID format"""
        if not call_id or len(call_id) != 64:
            return False
        
        try:
            int(call_id, 16)  # Check if it's a valid hex string
            return True
        except ValueError:
            return False
    
    def update_call_heartbeat(self, call_id: str) -> bool:
        """Update call heartbeat for connection monitoring"""
        if call_id not in self.active_calls:
            return False
        
        call_session = self.active_calls[call_id]
        call_session['last_heartbeat'] = datetime.utcnow()
        call_session['heartbeat_count'] += 1
        
        return True
    
    def check_call_health(self, call_id: str) -> Dict:
        """Check the health status of a call"""
        if call_id not in self.active_calls:
            return {'status': 'not_found'}
        
        call_session = self.active_calls[call_id]
        current_time = datetime.utcnow()
        
        # Check if call is stale (no heartbeat for 30 seconds)
        time_since_heartbeat = (current_time - call_session['last_heartbeat']).total_seconds()
        
        if time_since_heartbeat > 30:
            self.log_security_event('call_stale_detected', {
                'call_id': call_id,
                'time_since_heartbeat': time_since_heartbeat
            })
            return {'status': 'stale', 'time_since_heartbeat': time_since_heartbeat}
        
        # Check call duration (optional limit)
        call_duration = (current_time - call_session['created_at']).total_seconds()
        
        return {
            'status': 'healthy',
            'duration': call_duration,
            'heartbeat_count': call_session['heartbeat_count'],
            'time_since_heartbeat': time_since_heartbeat
        }
    
    def end_call_session(self, call_id: str, reason: str = 'normal') -> bool:
        """End a call session and clean up resources"""
        if call_id not in self.active_calls:
            return False
        
        call_session = self.active_calls[call_id]
        call_duration = (datetime.utcnow() - call_session['created_at']).total_seconds()
        
        self.log_security_event('call_session_ended', {
            'call_id': call_id,
            'caller_id': call_session['caller_id'],
            'receiver_id': call_session['receiver_id'],
            'duration': call_duration,
            'reason': reason,
            'heartbeat_count': call_session['heartbeat_count']
        })
        
        del self.active_calls[call_id]
        return True
    
    def get_active_calls_for_user(self, user_id: int) -> List[Dict]:
        """Get all active calls for a user"""
        user_calls = []
        current_time = datetime.utcnow()
        for call_id, call_session in self.active_calls.items():
            if user_id in [call_session['caller_id'], call_session['receiver_id']]:
                user_calls.append({
                    'call_id': call_id,
                    'caller_id': call_session['caller_id'],
                    'receiver_id': call_session['receiver_id'],
                    'call_type': call_session['call_type'],
                    'status': call_session['status'],
                    'created_at': call_session['created_at'].isoformat(),
                    'duration': (current_time - call_session['created_at']).total_seconds()
                })
        return user_calls
    
    def log_security_event(self, event_type: str, details: Dict):
        """Log security events for monitoring and analysis"""
        event = {
            'type': event_type,
            'timestamp': datetime.utcnow().isoformat(),
            'details': details
        }
        
        self.security_events.append(event)
        
        # Keep only last 1000 events to prevent memory issues
        if len(self.security_events) > 1000:
            self.security_events = self.security_events[-1000:]
        
        # In production, send critical events to monitoring service
        critical_events = [
            'call_blocked', 'call_rate_limited', 'replay_attack_detected',
            'unauthorized_call_access', 'encryption_failed', 'decryption_failed'
        ]
        
        if event_type in critical_events:
            print(f"CRITICAL SECURITY EVENT: {event}")
    
    def get_security_events(self, limit: int = 100) -> List[Dict]:
        """Get recent security events"""
        return self.security_events[-limit:]
    
    def cleanup_stale_calls(self):
        """Clean up stale call sessions"""
        current_time = datetime.utcnow()
        stale_calls = []
        
        for call_id, call_session in self.active_calls.items():
            time_since_heartbeat = (current_time - call_session['last_heartbeat']).total_seconds()
            if time_since_heartbeat > 60:  # 1 minute without heartbeat
                stale_calls.append(call_id)
        
        for call_id in stale_calls:
            self.end_call_session(call_id, 'stale_cleanup')


# Global instance
call_security_manager = CallSecurityManager()
