import os
import httpx
from fastapi import HTTPException
from models.user_profile import UserProfile

# Azure Configuration
CLIENT_ID = os.getenv("OFFICE_365_CLIENT_ID")
CLIENT_SECRET = os.getenv("OFFICE_365_CLIENT_SECRET")
TENANT_ID = os.getenv("OFFICE_365_TENANT_ID")
REDIRECT_URI = os.getenv("AUTH_REDIRECT_URI", "http://localhost:8000")

AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
GRAPH_ENDPOINT = "https://graph.microsoft.com/v1.0/me?$select=id,displayName,mail,userPrincipalName,department,jobTitle"

# Login only needs the user's own profile: no SharePoint or offline access
LOGIN_SCOPE = "User.Read"


async def exchange_code_for_token(code: str, code_verifier: str):
    """Exchanges the auth code (and the PKCE verifier) for an access token."""
    async with httpx.AsyncClient() as client:
        data = {
            "client_id": CLIENT_ID,
            "scope": LOGIN_SCOPE,
            "code": code,
            "redirect_uri": f"{REDIRECT_URI}/auth/callback",
            "grant_type": "authorization_code",
            "client_secret": CLIENT_SECRET,
            "code_verifier": code_verifier,
        }
        resp = await client.post(f"{AUTHORITY}/oauth2/v2.0/token", data=data)
        if resp.status_code != 200:
            raise HTTPException(
                status_code=400, detail="Failed to retrieve token from Microsoft"
            )
        return resp.json()


async def get_user_profile(access_token: str) -> UserProfile:
    """Fetches user details and permissions from MS Graph."""
    headers = {"Authorization": f"Bearer {access_token}"}

    async with httpx.AsyncClient() as client:
        resp = await client.get(GRAPH_ENDPOINT, headers=headers)
        if resp.status_code != 200:
            raise HTTPException(status_code=401, detail="Failed to fetch user profile")

        data = resp.json()

        return UserProfile(
            id=data.get("id"),
            name=data.get("displayName"),
            email=data.get("mail") or data.get("userPrincipalName"),
            department=data.get("department"),
            position=data.get("jobTitle"),
        )
