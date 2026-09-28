from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from config import DATABASE_URL

Base = declarative_base()
engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

def utcnow():
    return datetime.now(timezone.utc)

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    strava_athlete_id = Column(Integer, unique=True, index=True, nullable=False)
    firstname = Column(String, nullable=True)
    lastname = Column(String, nullable=True)
    profile_picture = Column(String, nullable=True)
    access_token = Column(String, nullable=False)
    refresh_token = Column(String, nullable=False)
    expires_at = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    bikes = relationship("Bike", back_populates="user", cascade="all, delete-orphan")

class Bike(Base):
    __tablename__ = "bikes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    strava_gear_id = Column(String, unique=True, index=True, nullable=True)
    name = Column(String, nullable=False)
    brand_name = Column(String, nullable=True)
    model_name = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    is_primary = Column(Boolean, default=False)
    # Total recorded distance in km on this bike
    total_distance_km = Column(Float, default=0.0)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user = relationship("User", back_populates="bikes")
    components = relationship("Component", back_populates="bike", cascade="all, delete-orphan")

class Component(Base):
    __tablename__ = "components"

    id = Column(Integer, primary_key=True, index=True)
    bike_id = Column(Integer, ForeignKey("bikes.id"), nullable=False)
    name = Column(String, nullable=False) # e.g. "Chain", "Front Tire", "Cassette"
    category = Column(String, nullable=False) # e.g. "chain", "tire", "cassette", "brake_pads"
    installed_at = Column(DateTime(timezone=True), default=utcnow)
    
    # Deterministic Wear & Tear Metrics (in km and hours)
    current_distance_km = Column(Float, default=0.0)
    max_distance_km = Column(Float, nullable=False, default=3000.0) # threshold for replacement/check
    current_time_hours = Column(Float, default=0.0)
    max_time_hours = Column(Float, nullable=True) # optional hours threshold (e.g. fork service)
    
    status = Column(String, default="active") # "active", "retired", "needs_service"
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    bike = relationship("Bike", back_populates="components")

# Processed Activity log (Compliant with Strava T&C:
# only aggregates distance & elevation, NO raw telemetry/GPS hoarding, NO LLM piping)
class ProcessedActivity(Base):
    __tablename__ = "processed_activities"

    id = Column(Integer, primary_key=True, index=True)
    strava_activity_id = Column(Integer, unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    bike_id = Column(Integer, ForeignKey("bikes.id"), nullable=True)
    distance_km = Column(Float, nullable=False)
    moving_time_hours = Column(Float, nullable=False)
    elevation_gain_m = Column(Float, default=0.0)
    activity_date = Column(DateTime(timezone=True), nullable=False)
    processed_at = Column(DateTime(timezone=True), default=utcnow)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
