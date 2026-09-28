import time
import httpx
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from models import User, Bike, Component, ProcessedActivity
from config import STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET

STRAVA_API_BASE = "https://www.strava.com/api/v3"
STRAVA_OAUTH_TOKEN_URL = "https://www.strava.com/oauth/token"

# Default components automatically attached when a new bike is discovered
DEFAULT_COMPONENTS = [
    {"name": "Chain", "category": "chain", "max_distance_km": 3000.0},
    {"name": "Front Tire", "category": "tire", "max_distance_km": 4000.0},
    {"name": "Rear Tire", "category": "tire", "max_distance_km": 3000.0},
    {"name": "Cassette", "category": "cassette", "max_distance_km": 8000.0},
    {"name": "Brake Pads (Front)", "category": "brakes", "max_distance_km": 2500.0},
    {"name": "Brake Pads (Rear)", "category": "brakes", "max_distance_km": 2500.0},
]

async def exchange_code_for_token(code: str) -> dict:
    """Exchange temporary OAuth authorization code for permanent refresh & access tokens."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            STRAVA_OAUTH_TOKEN_URL,
            data={
                "client_id": STRAVA_CLIENT_ID,
                "client_secret": STRAVA_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
            },
        )
        resp.raise_for_status()
        return resp.json()

async def get_valid_access_token(user: User, db: AsyncSession) -> str:
    """Check if access token is expired, refresh it if needed, and return valid token."""
    now_ts = int(time.time())
    if user.expires_at > now_ts + 120:
        return user.access_token

    # Token expired or about to expire -> refresh
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            STRAVA_OAUTH_TOKEN_URL,
            data={
                "client_id": STRAVA_CLIENT_ID,
                "client_secret": STRAVA_CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": user.refresh_token,
            },
        )
        resp.raise_for_status()
        data = resp.json()

        user.access_token = data["access_token"]
        user.refresh_token = data["refresh_token"]
        user.expires_at = data["expires_at"]
        await db.commit()
        await db.refresh(user)
        return user.access_token

async def sync_athlete_bikes(user: User, db: AsyncSession) -> list[Bike]:
    """Sync athlete profile and bikes (gear) from Strava."""
    token = await get_valid_access_token(user, db)
    async with httpx.AsyncClient() as client:
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.get(f"{STRAVA_API_BASE}/athlete", headers=headers)
        resp.raise_for_status()
        athlete_data = resp.json()

    # Update basic profile
    user.firstname = athlete_data.get("firstname")
    user.lastname = athlete_data.get("lastname")
    user.profile_picture = athlete_data.get("profile_medium") or athlete_data.get("profile")

    bikes_data = athlete_data.get("bikes", [])
    synced_bikes = []

    for b in bikes_data:
        gear_id = b["id"]
        result = await db.execute(select(Bike).where(Bike.strava_gear_id == gear_id))
        bike = result.scalar_one_or_none()

        dist_km = round(b.get("distance", 0) / 1000.0, 2)
        if not bike:
            bike = Bike(
                user_id=user.id,
                strava_gear_id=gear_id,
                name=b.get("name") or "Unnamed Bike",
                is_primary=b.get("primary", False),
                total_distance_km=dist_km,
            )
            db.add(bike)
            await db.flush()

            # Seed default components for this bike
            for comp_cfg in DEFAULT_COMPONENTS:
                comp = Component(
                    bike_id=bike.id,
                    name=comp_cfg["name"],
                    category=comp_cfg["category"],
                    max_distance_km=comp_cfg["max_distance_km"],
                    current_distance_km=0.0,
                    status="active",
                )
                db.add(comp)
        else:
            bike.name = b.get("name") or bike.name
            bike.is_primary = b.get("primary", bike.is_primary)
            if dist_km > bike.total_distance_km:
                bike.total_distance_km = dist_km

        synced_bikes.append(bike)

    await db.commit()
    return synced_bikes

async def sync_recent_activities(user: User, db: AsyncSession, limit: int = 30) -> int:
    """
    Fetch activities, deterministically update bike and active component wear,
    and PURGE raw payload (complying strictly with Strava 7-day cache & LLM bans).
    """
    token = await get_valid_access_token(user, db)
    async with httpx.AsyncClient() as client:
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.get(
            f"{STRAVA_API_BASE}/athlete/activities",
            headers=headers,
            params={"per_page": limit},
        )
        resp.raise_for_status()
        activities = resp.json()

    processed_count = 0

    for act in activities:
        act_id = act.get("id")
        if not act_id or act.get("type") not in ["Ride", "VirtualRide", "EBikeRide", "GravelRide", "MountainBikeRide"]:
            continue

        # Check if already processed
        res = await db.execute(select(ProcessedActivity).where(ProcessedActivity.strava_activity_id == act_id))
        if res.scalar_one_or_none() is not None:
            continue

        gear_id = act.get("gear_id")
        dist_km = round(act.get("distance", 0) / 1000.0, 2)
        moving_hours = round(act.get("moving_time", 0) / 3600.0, 2)
        elevation_m = round(act.get("total_elevation_gain", 0.0), 1)

        # Parse activity date
        start_date_str = act.get("start_date")
        if start_date_str:
            act_date = datetime.fromisoformat(start_date_str.replace("Z", "+00:00"))
        else:
            act_date = datetime.now(timezone.utc)

        bike = None
        if gear_id:
            res_bike = await db.execute(select(Bike).where(Bike.strava_gear_id == gear_id, Bike.user_id == user.id))
            bike = res_bike.scalar_one_or_none()

        if bike:
            # Deterministic Wear & Tear Calculation:
            # Update active components on this bike
            res_comps = await db.execute(select(Component).where(Component.bike_id == bike.id, Component.status == "active"))
            components = res_comps.scalars().all()

            for comp in components:
                comp.current_distance_km = round(comp.current_distance_km + dist_km, 2)
                comp.current_time_hours = round(comp.current_time_hours + moving_hours, 2)

                # Status check rule
                if comp.current_distance_km >= comp.max_distance_km:
                    comp.status = "needs_service"

        # Record aggregated summary only (NO raw GPS tracks, NO heartrate stream, NO LLM pipes)
        summary = ProcessedActivity(
            strava_activity_id=act_id,
            user_id=user.id,
            bike_id=bike.id if bike else None,
            distance_km=dist_km,
            moving_time_hours=moving_hours,
            elevation_gain_m=elevation_m,
            activity_date=act_date,
        )
        db.add(summary)
        processed_count += 1

    await db.commit()
    return processed_count

async def process_single_activity(user: User, activity_id: int, db: AsyncSession) -> bool:
    """Fetch a single activity by ID and process wear & tear for its bike components."""
    # Check if already processed
    res = await db.execute(select(ProcessedActivity).where(ProcessedActivity.strava_activity_id == activity_id))
    if res.scalar_one_or_none() is not None:
        return False

    token = await get_valid_access_token(user, db)
    async with httpx.AsyncClient() as client:
        headers = {"Authorization": f"Bearer {token}"}
        resp = await client.get(f"{STRAVA_API_BASE}/activities/{activity_id}", headers=headers)
        if resp.status_code == 404:
            return False
        resp.raise_for_status()
        act = resp.json()

    if act.get("type") not in ["Ride", "VirtualRide", "EBikeRide", "GravelRide", "MountainBikeRide"]:
        return False

    gear_id = act.get("gear_id")
    dist_km = round(act.get("distance", 0) / 1000.0, 2)
    moving_hours = round(act.get("moving_time", 0) / 3600.0, 2)
    elevation_m = round(act.get("total_elevation_gain", 0.0), 1)

    start_date_str = act.get("start_date")
    if start_date_str:
        act_date = datetime.fromisoformat(start_date_str.replace("Z", "+00:00"))
    else:
        act_date = datetime.now(timezone.utc)

    bike = None
    if gear_id:
        res_bike = await db.execute(select(Bike).where(Bike.strava_gear_id == gear_id, Bike.user_id == user.id))
        bike = res_bike.scalar_one_or_none()

    if bike:
        # Also ensure bike's total_distance_km stays updated
        bike.total_distance_km = round(bike.total_distance_km + dist_km, 2)

        # Update active components on this bike
        res_comps = await db.execute(select(Component).where(Component.bike_id == bike.id, Component.status == "active"))
        components = res_comps.scalars().all()

        for comp in components:
            comp.current_distance_km = round(comp.current_distance_km + dist_km, 2)
            comp.current_time_hours = round(comp.current_time_hours + moving_hours, 2)
            if comp.current_distance_km >= comp.max_distance_km:
                comp.status = "needs_service"

    summary = ProcessedActivity(
        strava_activity_id=activity_id,
        user_id=user.id,
        bike_id=bike.id if bike else None,
        distance_km=dist_km,
        moving_time_hours=moving_hours,
        elevation_gain_m=elevation_m,
        activity_date=act_date,
    )
    db.add(summary)
    await db.commit()
    return True

