from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash

SECRET_KEY = '6af6c6841b0a9620371190eb5e2044ae98833f3dbd0fd4868c26358b74e161f1'
ALGORITHM = 'HS256'
ACCESS_TOKEN_EXPIRE_MINUTES = 30

password_hash = PasswordHash.recommended()

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against a hashed password."""
    return password_hash.verify(plain_password, hashed_password)

def hash_password(password: str) -> str:
    """Hash a plain password."""
    return password_hash.hash(password)

def create_session_token(user_id: int) -> str:
    """Create a JWT token for the given user ID."""
    token = jwt.encode({
            'sub': str(user_id),
            'exp': datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        },
        SECRET_KEY,
        algorithm=ALGORITHM,
    )
    return token

def verify_session_token(token: str) -> int | None:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get('sub')
        if user_id is None:
            return None
        return int(user_id)
    except:
        return None
