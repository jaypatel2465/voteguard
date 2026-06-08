"""
Security utilities for Aadhaar handling and masking
"""
import hmac
import hashlib
from config import Config

def hash_aadhar(aadhar_id):
    """Deterministically hash Aadhaar using HMAC-SHA256."""
    if aadhar_id is None:
        return None
    data = str(aadhar_id).encode('utf-8')
    secret = Config.AADHAR_HASH_SECRET.encode('utf-8')
    return hmac.new(secret, data, hashlib.sha256).hexdigest()

def mask_aadhar(aadhar_or_last4):
    """
    Mask Aadhaar for display. Accepts full Aadhaar or last4.
    Returns XXXX XXXX 1234 format.
    """
    if not aadhar_or_last4:
        return "XXXX XXXX XXXX"
    value = str(aadhar_or_last4)
    last4 = value[-4:] if len(value) >= 4 else value
    return f"XXXX XXXX {last4}"

def generate_eid_hash(user_id, aadhar_hash):
    """Generate a stable E-ID hash for a voter"""
    if user_id is None or not aadhar_hash:
        return None
    payload = f"{user_id}:{aadhar_hash}"
    secret = Config.AADHAR_HASH_SECRET.encode('utf-8')
    return hmac.new(secret, payload.encode('utf-8'), hashlib.sha256).hexdigest()[:20].upper()
