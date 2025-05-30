# import dependencies
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse
from auth0_server_python.auth_server import ServerClient
from asyncio import sleep
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import uvicorn
import threading
import asyncio
import os
import webbrowser
import requests

from datetime import datetime, timedelta, time
import pytz  # For timezone handling

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
        # Updated scope for Google Calendar free/busy
        "connection_scope": "https://www.googleapis.com/auth/calendar.freebusy",
    }
)

app = FastAPI()


def get_next_working_day_timestamps(timezone_str='America/Los_Angeles'):
    """
    Calculates the start and end timestamps for the next working day (Mon-Fri, 9 AM - 5 PM)
    in the specified timezone.
    Returns (time_min_iso, time_max_iso, target_date_obj, timezone_obj).
    """
    try:
        tz = pytz.timezone(timezone_str)
    except pytz.exceptions.UnknownTimeZoneError:
        # Fallback to UTC if timezone_str is invalid
        print(f"Warning: Unknown timezone '{timezone_str}'. Defaulting to UTC.")
        tz = pytz.utc
        timezone_str = "UTC"

    # Start from tomorrow relative to the current date in the specified timezone
    current_date_in_tz = datetime.now(tz).date()
    target_date = current_date_in_tz + timedelta(days=1)

    # Skip to Monday if tomorrow is a weekend
    while target_date.weekday() >= 5:  # 5 for Saturday, 6 for Sunday
        target_date += timedelta(days=1)

    # Create naive datetime objects for 9 AM and 5 PM on the target date
    dt_min_naive = datetime.combine(target_date, time(9, 0, 0))
    dt_max_naive = datetime.combine(target_date, time(17, 0, 0))

    # Localize the naive datetime objects to the target timezone
    time_min_dt_aware = tz.localize(dt_min_naive)
    time_max_dt_aware = tz.localize(dt_max_naive)

    return time_min_dt_aware.isoformat(), time_max_dt_aware.isoformat(), target_date, tz


async def check_calendar_availability(access_token, timezone_str='America/Los_Angeles'):
    """Get free/busy information for the primary calendar for the next working day."""
    try:
        credentials = Credentials(token=access_token)
        service = build('calendar', 'v3', credentials=credentials)

        time_min_iso, time_max_iso, target_date, tz = get_next_working_day_timestamps(timezone_str)

        freebusy_query_body = {
            "timeMin": time_min_iso,
            "timeMax": time_max_iso,
            "items": [{"id": "primary"}],
            # "timeZone": timezone_str # timeMin/Max are already timezone-aware ISO strings
        }

        results = service.freebusy().query(body=freebusy_query_body).execute()

        primary_calendar_info = results.get('calendars', {}).get('primary', {})
        busy_slots_utc = primary_calendar_info.get('busy', [])

        # Format busy slots for display in the target timezone
        busy_slots_local = []
        for slot in busy_slots_utc:
            start_utc = pytz.utc.localize(datetime.fromisoformat(slot['start'].replace('Z', '')))
            end_utc = pytz.utc.localize(datetime.fromisoformat(slot['end'].replace('Z', '')))
            busy_slots_local.append({
                "start": start_utc.astimezone(tz).strftime('%I:%M %p'),
                "end": end_utc.astimezone(tz).strftime('%I:%M %p')
            })

        return {
            "target_date_str": target_date.strftime('%A, %B %d, %Y'),
            "timezone_str": timezone_str,
            "time_window_str": f"9:00 AM - 5:00 PM",
            "busy_slots": busy_slots_local,
            "raw_busy_slots": busy_slots_utc,  # for debugging or more complex logic
            "time_min_iso": time_min_iso,  # for debugging
            "time_max_iso": time_max_iso,  # for debugging
        }

    except HttpError as error:
        print(f'An error occurred with Google Calendar API: {error}')
        return {"error": f"Google Calendar API error: {error.resp.status} {error._get_reason()}"}
    except Exception as e:
        print(f'Error accessing Google Calendar: {e}')
        return {"error": f"An unexpected error occurred while checking calendar: {str(e)}"}


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
            <h1>Welcome to OpenRange</h1>
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

        calendar_availability_html = ""
        error_message_html = ""

        if idp_access_token:
            # Let's use 'America/Los_Angeles' as the target timezone for calendar check
            target_timezone = 'America/Los_Angeles'
            availability_info = await check_calendar_availability(idp_access_token, target_timezone)

            if availability_info and "error" not in availability_info:
                busy_slots = availability_info["busy_slots"]
                availability_status_html = ""
                if not busy_slots:
                    availability_status_html = f"<p style='color: green;'>🎉 You appear to be <strong>completely free</strong>!</p>"
                else:
                    availability_status_html = "<p style='color: orange;'>🗓️ You have the following busy slots:</p><ul>"
                    for slot in busy_slots:
                        availability_status_html += f"<li>{slot['start']} - {slot['end']}</li>"
                    availability_status_html += "</ul>"

                calendar_availability_html = f"""
                    <div class='availability-info'>
                        <h3>📅 Calendar Availability Check</h3>
                        <p>For: <strong>{availability_info['target_date_str']}</strong></p>
                        <p>Time Window: <strong>{availability_info['time_window_str']} ({availability_info['timezone_str']})</strong></p>
                        {availability_status_html}
                    </div>
                """
            elif availability_info and "error" in availability_info:
                error_message_html = f"""
                    <div class='error'>
                        <p>Could not retrieve calendar availability: {availability_info['error']}</p>
                        <p>Make sure your Auth0 application has the necessary permissions for Google Calendar API (<code>https://www.googleapis.com/auth/calendar.freebusy</code>) and that the API is enabled in your Google Cloud project.</p>
                    </div>"""
            else:
                error_message_html = "<div class='error'><p>An unknown error occurred while fetching calendar availability.</p></div>"
        else:
            error_message_html = """
                <div class='error'>
                    <p>Google access token not available.</p>
                    <p>Please ensure "Token Vault" or "Store IDP Access Token" is enabled in your Auth0 Google connection settings and that the connection requests the <code>https://www.googleapis.com/auth/calendar.freebusy</code> scope.</p>
                </div>"""

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

                {calendar_availability_html}
                {error_message_html}

                <h3>You can now close this window.</h3>
                <button onclick="closeWindow()">Close Window</button>
            </body>
        </html>
        """


if __name__ == "__main__":
    # Ensure you have pytz installed: pip install pytz
    # Also, ensure your .env.local has AUTH0_DOMAIN, AUTH0_CLIENT_ID, AUTH0_CLIENT_SECRET, AUTH0_SECRET, and APP_BASE_URL (e.g., http://127.0.0.1:3000)

    # Make sure Google Calendar API is enabled for your project in Google Cloud Console.
    # And that your Auth0 Google connection is configured to request the calendar.freebusy scope.

    print("Starting server on http://127.0.0.1:3000")
    print(
        "Login by navigating to: http://127.0.0.1:3000/ (or any other route that triggers Auth0 login if you set one up)")
    print("The callback URL used by Auth0 should be: http://127.0.0.1:3000/auth/callback")


    server_thread = threading.Thread(
        target=uvicorn.run,
        args=(app,),
        kwargs={"host": "127.0.0.1", "port": 3000, "log_level": "info"},  # Changed log level for better debugging
        daemon=True,
    )
    server_thread.start()

    # Keep the main thread alive to allow the server thread to run
    # Or use uvicorn.run(app, host="127.0.0.1", port=3000) directly if not needing threading for other tasks
    try:
        while True:
            # sleep(1) # Removed to avoid dependency on asyncio.sleep here
            threading.Event().wait(1)  # More standard way to wait
    except KeyboardInterrupt:
        print("Shutting down server...")