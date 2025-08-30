import os
import logging
from fastapi import FastAPI, Request
import httpx
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()
app = FastAPI()
logging.basicConfig(level=logging.INFO)

# --- Env Vars ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
ORS_API_KEY = os.getenv("ORS_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Store temporary user booking state in memory
user_state = {}
BASE_FEE = 59
PER_KM_RATE = 10  
async def geocode_address(address: str):
    """Convert address text into coordinates using ORS geocoding."""
    url = "https://api.openrouteservice.org/geocode/search"
    params = {"api_key": ORS_API_KEY, "text": address}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, params=params)
        data = resp.json()
        if data.get("features"):
            coords = data["features"][0]["geometry"]["coordinates"]
            return coords[0], coords[1]  # (lon, lat)
    return None

async def get_distance_km(origin: str, destination: str):
    """Compute driving distance (km) between origin & destination addresses."""
    start_coords = await geocode_address(origin)
    end_coords = await geocode_address(destination)

    if not start_coords or not end_coords:
        return None

    url = "https://api.openrouteservice.org/v2/directions/driving-car"
    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}
    body = {
        "coordinates": [
            [start_coords[0], start_coords[1]],  # [lon, lat]
            [end_coords[0], end_coords[1]],
        ]
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, headers=headers, json=body)
        data = resp.json()
        try:
            meters = data["routes"][0]["summary"]["distance"]  # in meters
            return meters / 1000  # convert to km
        except Exception:
            return None

@app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()

    if "message" in data:
        chat_id = data["message"]["chat"]["id"]
        text = (data["message"].get("text") or "").strip()

        # Initialize user state if not present
        if chat_id not in user_state:
            user_state[chat_id] = {"step": None, "data": {}}

        state = user_state[chat_id]
        reply = None  # always set once at the start

        # --- Check for very short input (ignore commands like /start, /book, /mybookings) ---
        if not text.startswith("/") and len(text) <= 2:
            reply = "⚠️ Be specific po about sa sagot"

        # --- Start ---
        elif text == "/start":
            reply = (
                "👋 Welcome to Delivery Bot!\n\n"
                "Commands:\n"
                "📦 /book – Create a new booking\n"
                "📑 /mybookings – View your past bookings"
            )
            state["step"] = None
            state["data"] = {}

        # --- Booking flow ---
        elif text == "/book":
            reply = "📦 Who is the recipient?"
            state["step"] = "recipient"

        elif state["step"] == "recipient":
            state["data"]["recipient_name"] = text
            reply = "👤 Who is booking this delivery?"
            state["step"] = "booker"

        elif state["step"] == "booker":
            state["data"]["booker_name"] = text
            reply = "📍 Where is the drop-off location?"
            state["step"] = "drop_off"

        elif state["step"] == "drop_off":
            state["data"]["drop_off"] = text
            reply = "🚚 Where is the pick-up location?"
            state["step"] = "pick_up"

        elif state["step"] == "pick_up":
            state["data"]["pick_up"] = text
            reply = "📝 Please provide description or package details."
            state["step"] = "description"

        elif state["step"] == "description":
            state["data"]["description"] = text
            origin = state["data"]["pick_up"]
            destination = state["data"]["drop_off"]
            km = await get_distance_km(origin, destination)

            if km is None:
                reply = "⚠️ Couldn't calculate distance automatically. Please type distance in km:"
                state["step"] = "distance"
            else:
                fee = BASE_FEE + (km * PER_KM_RATE)
                state["data"]["distance_km"] = km
                state["data"]["fee"] = fee

                supabase.table("bookings").insert({
                    "chat_id": chat_id,
                    "recipient_name": state["data"]["recipient_name"],
                    "booker_name": state["data"]["booker_name"],
                    "drop_off": state["data"]["drop_off"],
                    "pick_up": state["data"]["pick_up"],
                    "description": state["data"]["description"],
                    "distance_km": km,
                    "fee": fee
                }).execute()

                reply = (
                    f"✅ Booking confirmed!\n\n"
                    f"📦 Recipient: {state['data']['recipient_name']}\n"
                    f"👤 Booker: {state['data']['booker_name']}\n"
                    f"📍 Drop-off: {state['data']['drop_off']}\n"
                    f"🚚 Pick-up: {state['data']['pick_up']}\n"
                    f"📝 Details: {state['data']['description']}\n"
                    f"📏 Distance: {km:.2f} km\n\n"
                    f"💵 Fee: ₱{fee:.2f}"
                )

                user_state[chat_id] = {"step": None, "data": {}}

        elif state["step"] == "distance":
            try:
                km = float(text)
                fee = BASE_FEE + (km * PER_KM_RATE)
                state["data"]["distance_km"] = km
                state["data"]["fee"] = fee

                supabase.table("bookings").insert({
                    "chat_id": chat_id,
                    "recipient_name": state["data"]["recipient_name"],
                    "booker_name": state["data"]["booker_name"],
                    "drop_off": state["data"]["drop_off"],
                    "pick_up": state["data"]["pick_up"],
                    "description": state["data"]["description"],
                    "distance_km": km,
                    "fee": fee
                }).execute()

                reply = (
                    f"✅ Booking confirmed!\n\n"
                    f"📦 Recipient: {state['data']['recipient_name']}\n"
                    f"👤 Booker: {state['data']['booker_name']}\n"
                    f"📍 Drop-off: {state['data']['drop_off']}\n"
                    f"🚚 Pick-up: {state['data']['pick_up']}\n"
                    f"📝 Details: {state['data']['description']}\n"
                    f"📏 Distance: {km:.2f} km\n\n"
                    f"💵 Fee: ₱{fee:.2f}"
                )

                user_state[chat_id] = {"step": None, "data": {}}

            except ValueError:
                reply = "❌ Please type a valid number for distance in km (e.g. 3.5)"

        # --- My bookings ---
        elif text == "/mybookings":
            try:
                res = supabase.table("bookings").select("*").eq("chat_id", chat_id).order("created_at", desc=True).limit(5).execute()
                bookings = res.data

                if not bookings:
                    reply = "📑 You don’t have any bookings yet. Type /book to create one."
                else:
                    reply = "📑 Your recent bookings:\n\n"
                    for b in bookings:
                        reply += (
                            f"📦 Recipient: {b['recipient_name']}\n"
                            f"👤 Booker: {b['booker_name']}\n"
                            f"📍 Drop-off: {b['drop_off']}\n"
                            f"🚚 Pick-up: {b['pick_up']}\n"
                            f"📝 {b['description']}\n"
                            f"💵 Fee: ₱{b['fee']}\n"
                            f"📅 {b['created_at']}\n\n"
                        )

            except Exception as e:
                logging.error(f"❌ Error fetching bookings: {e}")
                reply = "⚠️ Sorry, there was an error fetching your bookings."

        else:
            if reply is None:  # don’t overwrite short-text message
                reply = "🤖 I don’t understand. Type /book to start a booking or /mybookings to see past bookings."

        # Send reply
        if reply:
            async with httpx.AsyncClient() as client:
                await client.post(f"{API_URL}/sendMessage", json={
                    "chat_id": chat_id,
                    "text": reply
                })

    return {"ok": True}



@app.get("/")
async def root():
    return {"message": "Delivery bot is running"}
