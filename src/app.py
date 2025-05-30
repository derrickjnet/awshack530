# import dependencies
import os

from auth0_server_python.auth_server import ServerClient
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

load_dotenv(dotenv_path=".env.local")
print()


class MemoryTransactionStore:
    def __init__(self):
        self.store = {}

    async def set(self, key, value, options=None):
        self.store[key] = value

    async def get(self, key, options=None):
        return self.store.get(key)

    async def delete(self, key, options=None):
        if key in self.store:
            del self.store[key]


auth0 = ServerClient(
    domain=os.getenv("AUTH0_DOMAIN"),
    client_id=os.getenv("AUTH0_CLIENT_ID"),
    client_secret=os.getenv("AUTH0_CLIENT_SECRET"),
    secret=os.getenv("AUTH0_SECRET"),
    redirect_uri=os.getenv("APP_BASE_URL") + "/auth/callback",
    transaction_store=MemoryTransactionStore(),
    state_store=MemoryTransactionStore(),
    authorization_params={
        "scope": "openid profile email offline_access",  # Keep base OIDC scopes
        "connection": "google-oauth2",
    }
)

app = FastAPI()

    # A simple root route to initiate login easily for testing
@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    # Generate the Auth0 login URL
    login_url = await auth0.start_interactive_login()
    
    # Return HTML with a login link
    return f"""
    <html>
        <head>
            <title>Auth0 Login</title>
            <style>
                body {{ font-family: Arial, sans-serif; padding: 40px; text-align: center; }}
                .login-button {{ 
                    display: inline-block; 
                    padding: 12px 24px; 
                    background-color: #007bff; 
                    color: white; 
                    text-decoration: none; 
                    border-radius: 5px; 
                    font-size: 16px; 
                }}
                .login-button:hover {{ background-color: #0056b3; }}
            </style>
        </head>
        <body>
            <h1>Welcome to Vitae</h1>
            <p>Click below to login with Google via Auth0</p>
            <a href="{login_url}" class="login-button">Login with Google</a>
        </body>
    </html>
    """

@app.get("/auth/callback", response_class=HTMLResponse)
async def callback(request: Request):
    result = await auth0.complete_interactive_login(str(request.url))
    if result.get("error"):

        return f"<html><body><h2>Login Error</h2><p>{result.get('error')}: {result.get('error_description', '')}</p></body></html>"
    else:
        user_info = result.get("state_data", {}).get("user", {})
        user_display = user_info.get("name") or user_info.get("email", "N/A")

        tokens = result.get("tokens", {})
        # idp_access_token is the Google access token
        idp_access_token = None
        if "identities" in user_info and user_info["identities"]:
            identity = user_info["identities"][0]
            idp_access_token = identity.get("access_token")

        return f"""
        <html>
            <head>
                <title>Login Successful</title>
                <script>
                    function closeWindow() {{
                        window.close();
                    }}
                </script>
                <style>
                    body {{ font-family: Arial, sans-serif; padding: 20px; max-width: 700px; margin: 0 auto; line-height: 1.6; }}
                    .user-info, .availability-info, .error {{ background-color: #f0f0f0; padding: 15px; border-radius: 8px; margin: 20px 0; }}
                    .error {{ color: red; background-color: #ffe0e0; border: 1px solid #ffc0c0; }}
                    h2 {{ color: #333; }}
                    h3 {{ color: #555; margin-top: 0; }}
                    ul {{ padding-left: 20px; }}
                    li {{ margin-bottom: 5px; }}
                    button {{ padding: 10px 15px; background-color: #007bff; color: white; border: none; border-radius: 5px; cursor: pointer; font-size: 16px; }}
                    button:hover {{ background-color: #0056b3; }}
                </style>
            </head>
            <body>
                <h2>Login Successful!</h2>
                <div class="user-info">
                    <p>Logged in as: <strong>{user_display}</strong></p>
                </div>


                <h3>You can now close this window.</h3>
                <button onclick="closeWindow()">Close Window</button>
            </body>
        </html>
        """