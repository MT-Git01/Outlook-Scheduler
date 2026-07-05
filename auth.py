import os
import msal
from typing import Optional, Tuple
from db_client import DBClient

SCOPES = [
    "User.Read",
    "Calendars.ReadWrite",
    "Place.Read.All",
    "Mail.Send",
    "offline_access"
]

class AuthException(Exception):
    pass

class TokenExpiredException(AuthException):
    pass

def get_msal_app(db_client: DBClient, home_account_id: Optional[str] = None) -> Tuple[msal.ConfidentialClientApplication, msal.SerializableTokenCache]:
    """Helper to initialize the ConfidentialClientApplication with a serialized cache."""
    client_id = os.environ.get("CLIENT_ID")
    client_secret = os.environ.get("CLIENT_SECRET")
    tenant_id = os.environ.get("TENANT_ID", "common")
    authority = f"https://login.microsoftonline.com/{tenant_id}"

    if not client_id or not client_secret:
        raise AuthException("CLIENT_ID and CLIENT_SECRET must be set in the environment.")

    cache = msal.SerializableTokenCache()
    if home_account_id:
        serialized = db_client.get_token_cache(home_account_id)
        if serialized:
            cache.deserialize(serialized)

    app = msal.ConfidentialClientApplication(
        client_id=client_id,
        client_credential=client_secret,
        authority=authority,
        token_cache=cache
    )
    return app, cache

def initiate_auth_flow(db_client: DBClient, redirect_uri: str) -> str:
    """Generates the authorization URL and saves the flow state in the database."""
    app, _ = get_msal_app(db_client)
    flow = app.initiate_auth_code_flow(SCOPES, redirect_uri=redirect_uri)
    
    # Save the flow using state as key to support stateless container restarts
    state = flow.get("state")
    if not state:
        raise AuthException("Failed to generate state for OAuth flow.")
    
    db_client.save_auth_flow(state, flow)
    return flow.get("auth_uri")

def complete_auth_flow(db_client: DBClient, query_params: dict, redirect_uri: str) -> dict:
    """Exchanges the authorization code for a token and stores the token cache."""
    state = query_params.get("state")
    if not state:
        raise AuthException("State parameter is missing from authorization callback.")

    flow = db_client.get_auth_flow(state)
    if not flow:
        raise AuthException("Auth flow state has expired or is invalid. Please login again.")

    app, cache = get_msal_app(db_client)
    try:
        # st.query_params is a StreamlitQueryParamsProxy; MSAL requires a plain dict
        query_params_dict = {k: v for k, v in query_params.items()}
        # Complete the auth code exchange
        result = app.acquire_token_by_auth_code_flow(flow, query_params_dict)
        
        if "error" in result:
            error_desc = result.get("error_description", result.get("error"))
            raise AuthException(f"Authentication failed: {error_desc}")

        account = result.get("account")
        if not account or "home_account_id" not in account:
            raise AuthException("Failed to retrieve account details from token response.")

        home_account_id = account["home_account_id"]
        
        # Save the token cache under the user's home_account_id
        if cache.has_state_changed:
            db_client.save_token_cache(home_account_id, cache.serialize())

        # Clean up the auth flow state
        db_client.delete_auth_flow(state)
        
        return account
    except Exception as e:
        if not isinstance(e, AuthException):
            raise AuthException(f"Token exchange failed: {e}")
        raise e

def get_token_silently(db_client: DBClient, home_account_id: str) -> str:
    """Retrieves access token silently from cache. Refreshes if expired.
    Raises TokenExpiredException if refresh fails or user needs to log in again.
    """
    app, cache = get_msal_app(db_client, home_account_id)
    accounts = app.get_accounts()
    
    # Locate the correct account in the deserialized cache
    target_account = None
    for a in accounts:
        if a.get("home_account_id") == home_account_id:
            target_account = a
            break
            
    if not target_account:
        raise TokenExpiredException("No cached credentials found. Please sign in again.")

    result = app.acquire_token_silent(SCOPES, account=target_account)
    if not result:
        raise TokenExpiredException("Failed to acquire token silently. Please sign in again.")
        
    if "error" in result:
        raise TokenExpiredException(f"Silent token retrieval failed: {result.get('error')}")

    # If the token was refreshed, update the stored cache in database
    if cache.has_state_changed:
        db_client.save_token_cache(home_account_id, cache.serialize())

    return result["access_token"]
