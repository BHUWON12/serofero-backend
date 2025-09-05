import os
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import hashlib

class MessageEncryption:
    def __init__(self):
        # Get encryption key from environment or generate one
        key = os.getenv('MESSAGE_ENCRYPTION_KEY')
        if not key:
            # Generate a key from a password (in production, use a proper key)
            password = os.getenv('ENCRYPTION_PASSWORD', 'default-encryption-password-change-in-production')
            salt = b'static_salt_for_messages'  # In production, use a proper salt
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=100000,
            )
            key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
        else:
            key = key.encode()

        self.fernet = Fernet(key)

    def encrypt_message(self, message: str) -> str:
        """Encrypt a message before storing in database"""
        if not message:
            return ""
        try:
            # Fernet.encrypt already returns a URL-safe base64-encoded token (bytes).
            # Store the token as a UTF-8 string to avoid double-encoding.
            token = self.fernet.encrypt(message.encode())
            return token.decode()
        except Exception as e:
            print(f"Encryption error: {e}")
            return message  # Fallback to plain text if encryption fails

    def decrypt_message(self, encrypted_message: str) -> str:
        """Decrypt a message when retrieving from database"""
        if not encrypted_message:
            return ""
        try:
            # First, try to decrypt assuming the stored value is the Fernet token string.
            try:
                decrypted = self.fernet.decrypt(encrypted_message.encode())
                return decrypted.decode()
            except Exception:
                # Fallback for existing entries that were double-base64-encoded:
                encrypted_bytes = base64.urlsafe_b64decode(encrypted_message.encode())
                decrypted = self.fernet.decrypt(encrypted_bytes)
                return decrypted.decode()
        except Exception as e:
            print(f"Decryption error: {e}")
            return encrypted_message  # Fallback to encrypted if decryption fails

# Global instance
message_encryptor = MessageEncryption()

def encrypt_message_content(content: str) -> str:
    """Convenience function to encrypt message content"""
    return message_encryptor.encrypt_message(content)

def decrypt_message_content(encrypted_content: str) -> str:
    """Convenience function to decrypt message content"""
    return message_encryptor.decrypt_message(encrypted_content)
