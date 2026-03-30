import os
import httpx
from fastapi import HTTPException
from models.user_profile import UserProfile

# Azure Configuration
CLIENT_ID = os.getenv("OFFICE_365_CLIENT_ID")
CLIENT_SECRET = os.getenv("OFFICE_365_CLIENT_SECRET")
TENANT_ID = os.getenv("OFFICE_365_TENANT_ID")
REDIRECT_URI = "http://localhost:8000/auth/callback" 

AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
GRAPH_ENDPOINT = "https://graph.microsoft.com/v1.0/me?$select=id,displayName,mail,department,jobTitle"

# --- Add your SharePoint Site and Drive IDs here ---
# You can find these using Graph Explorer or your indexing script
SHAREPOINT_SITE_ID = os.getenv("SHAREPOINT_SITE_ID", "your_site_id_here")
SHAREPOINT_DRIVE_ID = os.getenv("SHAREPOINT_DRIVE_ID", "your_drive_id_here")


async def exchange_code_for_token(code: str):
    """Exchanges the auth code for an access token."""
    async with httpx.AsyncClient() as client:
        data = {
            "client_id": CLIENT_ID,
            "scope": "User.Read offline_access Sites.Read.All", # Added Sites scope
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
    """Fetches user details and permissions from MS Graph."""
    headers = {"Authorization": f"Bearer {access_token}"}
    
    async with httpx.AsyncClient() as client:
        # 1. Get basic user info
        resp = await client.get(GRAPH_ENDPOINT, headers=headers)
        if resp.status_code != 200:
            raise HTTPException(status_code=401, detail="Failed to fetch user profile")

        data = resp.json()

        # 2. Check permission for a specific folder (e.g., 'root' or a specific folder ID)
        # Note: Doing this for multiple folders will slow down login significantly.
        has_access = await check_sharepoint_read_permission(
            client, "root", access_token
        )

        # 3. Alternatively (Recommended): Fetch User Groups
        # groups = await get_user_groups(client, access_token)

        return UserProfile(
            id=data.get("id"),
            name=data.get("displayName"),
            email=data.get("mail") or data.get("userPrincipalName"),
            department=data.get("department"),
            # Store the permission boolean or a list of accessible folder IDs
            position=data.get("jobTitle")
        )


async def check_sharepoint_read_permission(client: httpx.AsyncClient, folder_item_id: str, access_token: str) -> bool:
    """
    Checks if a user has read permission to a specific SharePoint folder using their token.
    """
    # CORRECT URL for SharePoint Document Libraries
    url = f"https://graph.microsoft.com/v1.0/sites/{SHAREPOINT_SITE_ID}/drives/{SHAREPOINT_DRIVE_ID}/items/{folder_item_id}"
    
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json'
    }

    try:
        # Instead of listing all permissions (which requires admin scopes), 
        # we simply try to GET the item using the user's token.
        # If it returns 200, the user has at least read access. If 403/404, they do not.
        response = await client.get(url, headers=headers)
        
        if response.status_code == 200:
            print("User has read access to this folder.")
            return True
        elif response.status_code in [401, 403, 404]:
            print("User does not have access to this folder.")
            return False
        else:
            response.raise_for_status()

    except Exception as e:
        print(f"An error occurred checking permissions: {e}")
        return False
