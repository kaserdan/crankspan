from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, Request, Response, Form, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
import os

from models import init_db, async_session, User, Bike, Component
from config import STRAVA_CLIENT_ID, APP_BASE_URL, STRAVA_WEBHOOK_VERIFY_TOKEN
from strava import exchange_code_for_token, sync_athlete_bikes, sync_recent_activities, process_single_activity

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize SQLite tables on startup
    await init_db()
    yield

app = FastAPI(title="Crankspan", lifespan=lifespan)
templates = Jinja2Templates(directory="templates")

# Dependency for DB session
async def get_db():
    async with async_session() as session:
        yield session

# Helper to get current authenticated user from cookie session
async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User | None:
    user_id = request.cookies.get("crankspan_user_id")
    if not user_id:
        return None
    try:
        res = await db.execute(select(User).where(User.id == int(user_id)))
        return res.scalar_one_or_none()
    except Exception:
        return None

# --- Routes ---

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, user: User | None = Depends(get_current_user)):
    if user:
        return RedirectResponse(url="/dashboard", status_code=303)
    return templates.TemplateResponse(request=request, name="index.html", context={"user": None})

# 1. OAuth Initiate: redirect to Strava
@app.get("/auth/strava/login")
async def strava_login():
    redirect_uri = f"{APP_BASE_URL}/auth/strava/callback"
    # Scopes: read (basic), profile:read_all (bikes/gear), activity:read_all (activities)
    scope = "read,profile:read_all,activity:read_all"
    auth_url = (
        f"https://www.strava.com/oauth/authorize?"
        f"client_id={STRAVA_CLIENT_ID}&response_type=code&"
        f"redirect_uri={redirect_uri}&approval_prompt=auto&scope={scope}"
    )
    return RedirectResponse(url=auth_url)

# 2. OAuth Callback: exchange code, create/update user, sync initial gear & activities
@app.get("/auth/strava/callback")
async def strava_callback(request: Request, code: str = None, error: str = None, db: AsyncSession = Depends(get_db)):
    if error or not code:
        raise HTTPException(status_code=400, detail=f"Strava authentication failed: {error}")

    data = await exchange_code_for_token(code)
    athlete_info = data.get("athlete", {})
    athlete_id = athlete_info.get("id")

    if not athlete_id:
        raise HTTPException(status_code=400, detail="Missing athlete ID from Strava response")

    # Find or create user
    res = await db.execute(select(User).where(User.strava_athlete_id == athlete_id))
    user = res.scalar_one_or_none()

    if not user:
        user = User(
            strava_athlete_id=athlete_id,
            firstname=athlete_info.get("firstname"),
            lastname=athlete_info.get("lastname"),
            profile_picture=athlete_info.get("profile_medium") or athlete_info.get("profile"),
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=data["expires_at"],
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
    else:
        user.access_token = data["access_token"]
        user.refresh_token = data["refresh_token"]
        user.expires_at = data["expires_at"]
        user.firstname = athlete_info.get("firstname") or user.firstname
        user.lastname = athlete_info.get("lastname") or user.lastname
        user.profile_picture = athlete_info.get("profile_medium") or athlete_info.get("profile") or user.profile_picture
        await db.commit()

    # Initial sync of bikes and activities
    try:
        await sync_athlete_bikes(user, db)
        await sync_recent_activities(user, db, limit=30)
    except Exception as e:
        print(f"Warning during initial sync: {e}")

    # Set auth cookie & redirect to dashboard
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(
        key="crankspan_user_id",
        value=str(user.id),
        httponly=True,
        max_age=365 * 24 * 3600,
        samesite="lax",
    )
    return response

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("crankspan_user_id")
    return response

# 3. Dashboard (User garage)
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, user: User | None = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if not user:
        return RedirectResponse(url="/", status_code=303)

    # Fetch user's bikes and prefetch components
    res = await db.execute(
        select(Bike)
        .where(Bike.user_id == user.id)
        .options(selectinload(Bike.components))
    )
    bikes = res.scalars().all()

    return templates.TemplateResponse(request=request, name="dashboard.html", context={
        "user": user,
        "bikes": bikes,
    })

# 4. Bike Detail & Components View
@app.get("/bike/{bike_id}", response_class=HTMLResponse)
async def bike_detail(bike_id: int, request: Request, user: User | None = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if not user:
        return RedirectResponse(url="/", status_code=303)

    res = await db.execute(
        select(Bike)
        .where(Bike.id == bike_id, Bike.user_id == user.id)
        .options(selectinload(Bike.components))
    )
    bike = res.scalar_one_or_none()
    if not bike:
        raise HTTPException(status_code=404, detail="Kolo nebylo nalezeno")

    return templates.TemplateResponse(request=request, name="bike.html", context={
        "user": user,
        "bike": bike,
    })

# 5. Manual Sync trigger
@app.post("/sync")
async def sync_data(user: User | None = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if not user:
        return RedirectResponse(url="/", status_code=303)

    await sync_athlete_bikes(user, db)
    await sync_recent_activities(user, db, limit=50)
    return RedirectResponse(url="/dashboard", status_code=303)

# 6. Add component to bike
@app.post("/bike/{bike_id}/component")
async def add_component(
    bike_id: int,
    name: str = Form(...),
    category: str = Form(...),
    max_distance_km: float = Form(3000.0),
    initial_distance_km: float = Form(0.0),
    installed_at: str | None = Form(None),
    notes: str | None = Form(None),
    user: User | None = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    if not user:
        return RedirectResponse(url="/", status_code=303)

    res = await db.execute(select(Bike).where(Bike.id == bike_id, Bike.user_id == user.id))
    bike = res.scalar_one_or_none()
    if not bike:
        raise HTTPException(status_code=404, detail="Kolo nebylo nalezeno")

    install_dt = datetime.now(timezone.utc)
    if installed_at:
        try:
            install_dt = datetime.fromisoformat(installed_at).replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    comp = Component(
        bike_id=bike.id,
        name=name,
        category=category,
        max_distance_km=max_distance_km,
        current_distance_km=initial_distance_km,
        installed_at=install_dt,
        notes=notes if notes else None,
        status="active" if initial_distance_km < max_distance_km else "needs_service"
    )
    db.add(comp)
    await db.commit()
    return RedirectResponse(url=f"/bike/{bike_id}", status_code=303)

# 7. Update component
@app.post("/component/{comp_id}/update")
async def update_component(
    comp_id: int,
    name: str = Form(...),
    category: str = Form(...),
    max_distance_km: float = Form(...),
    current_distance_km: float = Form(...),
    installed_at: str | None = Form(None),
    notes: str | None = Form(None),
    user: User | None = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    if not user:
        return RedirectResponse(url="/", status_code=303)

    res = await db.execute(
        select(Component)
        .join(Bike)
        .where(Component.id == comp_id, Bike.user_id == user.id)
    )
    comp = res.scalar_one_or_none()
    if not comp:
        raise HTTPException(status_code=404, detail="Komponenta nenalezena")

    comp.name = name
    comp.category = category
    comp.max_distance_km = max_distance_km
    comp.current_distance_km = current_distance_km
    comp.notes = notes if notes else None

    if installed_at:
        try:
            comp.installed_at = datetime.fromisoformat(installed_at).replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    if comp.current_distance_km >= comp.max_distance_km:
        comp.status = "needs_service"
    else:
        comp.status = "active"

    await db.commit()
    return RedirectResponse(url=f"/bike/{comp.bike_id}", status_code=303)

# 8. Reset component mileage (e.g. after replacing a chain)
@app.post("/component/{comp_id}/reset")
async def reset_component(
    comp_id: int,
    installed_at: str | None = Form(None),
    user: User | None = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    if not user:
        return RedirectResponse(url="/", status_code=303)

    res = await db.execute(
        select(Component)
        .join(Bike)
        .where(Component.id == comp_id, Bike.user_id == user.id)
    )
    comp = res.scalar_one_or_none()
    if not comp:
        raise HTTPException(status_code=404, detail="Komponenta nenalezena")

    comp.current_distance_km = 0.0
    comp.current_time_hours = 0.0
    comp.status = "active"
    if installed_at:
        try:
            comp.installed_at = datetime.fromisoformat(installed_at).replace(tzinfo=timezone.utc)
        except ValueError:
            comp.installed_at = datetime.now(timezone.utc)
    else:
        comp.installed_at = datetime.now(timezone.utc)

    await db.commit()

    return RedirectResponse(url=f"/bike/{comp.bike_id}", status_code=303)

# 9. Delete component
@app.post("/component/{comp_id}/delete")
async def delete_component(comp_id: int, user: User | None = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if not user:
        return RedirectResponse(url="/", status_code=303)

    res = await db.execute(
        select(Component)
        .join(Bike)
        .where(Component.id == comp_id, Bike.user_id == user.id)
    )
    comp = res.scalar_one_or_none()
    if not comp:
        raise HTTPException(status_code=404, detail="Komponenta nenalezena")

    bike_id = comp.bike_id
    await db.delete(comp)
    await db.commit()

    return RedirectResponse(url=f"/bike/{bike_id}", status_code=303)

# 10. Strava Webhook Verification (GET)
# Strava sends GET request with hub.mode, hub.challenge, hub.verify_token upon subscription registration
@app.get("/webhook/strava")
async def strava_webhook_validate(request: Request):
    mode = request.query_params.get("hub.mode")
    challenge = request.query_params.get("hub.challenge")
    verify_token = request.query_params.get("hub.verify_token")

    if mode == "subscribe" and verify_token == STRAVA_WEBHOOK_VERIFY_TOKEN:
        return {"hub.challenge": challenge}

    raise HTTPException(status_code=403, detail="Invalid verification token or mode")

# 11. Strava Webhook Event Handler (POST)
# Strava pushes event payloads here when an activity is created, updated or deleted
@app.post("/webhook/strava")
async def strava_webhook_event(request: Request, db: AsyncSession = Depends(get_db)):
    payload = await request.json()
    # Strava webhook schema:
    # {
    #   "aspect_type": "create" | "update" | "delete",
    #   "event_time": 1549560669,
    #   "object_id": 1234567890,   (activity_id)
    #   "object_type": "activity" | "athlete",
    #   "owner_id": 9999999,        (athlete_id)
    #   "subscription_id": 1,
    #   "updates": {}
    # }
    object_type = payload.get("object_type")
    aspect_type = payload.get("aspect_type")
    activity_id = payload.get("object_id")
    athlete_id = payload.get("owner_id")

    if object_type == "activity" and aspect_type == "create" and activity_id and athlete_id:
        res = await db.execute(select(User).where(User.strava_id == athlete_id))
        user = res.scalar_one_or_none()
        if user:
            try:
                await process_single_activity(user, activity_id, db)
            except Exception as e:
                print(f"[Webhook] Error processing activity {activity_id}: {e}")

    # Strava expects quick HTTP 200 acknowledge (within 2 seconds)
    return {"status": "ok"}

