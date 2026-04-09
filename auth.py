import os

import jwt

CF_ACCESS_TEAM_DOMAIN = os.environ["CF_ACCESS_TEAM_DOMAIN"]
CF_ACCESS_AUD = os.environ["CF_ACCESS_AUD"]

CERTS_URL = f"https://{CF_ACCESS_TEAM_DOMAIN}.cloudflareaccess.com/cdn-cgi/access/certs"

_jwk_client = jwt.PyJWKClient(CERTS_URL, cache_keys=True)


def verify_cf_access_token(token: str) -> str | None:
    """Verify a Cloudflare Access JWT and return the user's email."""
    try:
        signing_key = _jwk_client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=CF_ACCESS_AUD,
        )
        return payload.get("email")
    except Exception:
        return None
