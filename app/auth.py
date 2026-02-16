import os
import httpx
from fastapi import HTTPException
from models.user_profile import UserProfile

# Azure Configuration
CLIENT_ID = os.getenv("OFFICE_365_CLIENT_ID")
CLIENT_SECRET = os.getenv("OFFICE_365_CLIENT_SECRET")
TENANT_ID = os.getenv("OFFICE_365_TENANT_ID")
REDIRECT_URI = "http://localhost:8000/auth/callback"  # Update for production

AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
GRAPH_ENDPOINT = "https://graph.microsoft.com/v1.0/me?$select=id,displayName,mail,department,jobTitle"


async def exchange_code_for_token(code: str):
    """Exchanges the auth code for an access token."""
    async with httpx.AsyncClient() as client:
        data = {
            "client_id": CLIENT_ID,
            "scope": "User.Read offline_access",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
            "client_secret": CLIENT_SECRET,
        }
        resp = await client.post(f"{AUTHORITY}/oauth2/v2.0/token", data=data)
        if resp.status_code != 200:
            raise HTTPException(
                status_code=400, detail="Failed to retrieve token from Microsoft"
            )
        return resp.json()


async def get_user_profile(access_token: str) -> UserProfile:
    """Fetches user details and DEPARTMENT from MS Graph."""
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient() as client:
        # We explicitly select 'department' in the query
        resp = await client.get(GRAPH_ENDPOINT, headers=headers)
        if resp.status_code != 200:
            raise HTTPException(status_code=401, detail="Failed to fetch user profile")

        print("User profile: ", resp.json())
        data = resp.json()

        return UserProfile(
            id=data.get("id"),
            name=data.get("displayName"),
            email=data.get("mail") or data.get("userPrincipalName"),
            # Ensure your SharePoint/AD actually has this field populated
            department=data.get("department"),
        )
