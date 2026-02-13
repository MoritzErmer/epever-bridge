# =============================================================================
# Fensterkraftwerk – FastAPI Backend (Hauptmodul)
# =============================================================================
#
# API-Endpunkte:
#   POST /api/login         → Passwort prüfen, Token zurückgeben
#   POST /api/verify-token  → Token serverseitig validieren (für QR-Login)
#   GET  /api/live           → Letzter empfangener Datensatz (Echtzeit)
#   GET  /api/history        → Historische Daten aus Supabase
#   POST /api/toggle-load    → Lastausgang des Ladereglers schalten
#   GET  /api/health         → Health-Check für Monitoring (öffentlich)
#
# Alle Endpunkte außer /api/login und /api/health sind mit
# Token-Authentifizierung geschützt. Das Token kann als Bearer-Token
# im Header oder als ?token= Query-Parameter übergeben werden.
#
# =============================================================================

import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel

from app.auth import verify_token
from app.config import settings
from app.mqtt_service import mqtt_service

# --- Logging konfigurieren ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("fensterkraftwerk.api")

# Server-Startzeit für Uptime-Berechnung
SERVER_START_TIME = time.time()


# =============================================================================
# Lifespan – Start/Stop des MQTT-Services
# =============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI Lifespan Context Manager.
    Startet den MQTT-Service beim Hochfahren und stoppt ihn beim Herunterfahren.
    """
    logger.info("Starte Fensterkraftwerk Backend...")
    mqtt_service.start()
    yield
    logger.info("Stoppe Fensterkraftwerk Backend...")
    mqtt_service.stop()


# =============================================================================
# FastAPI App-Instanz
# =============================================================================
app = FastAPI(
    title="Fensterkraftwerk API",
    description="Backend-API für das Solar-Fensterkraftwerk Monitoring-System",
    version="1.0.0",
    lifespan=lifespan
)

# --- CORS-Middleware für das React-Frontend ---
# Origins werden aus der Umgebungsvariable CORS_ORIGINS geladen
# (kommasepariert, z.B. "http://localhost:5173,https://mein-dashboard.vercel.app")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Trusted Host Middleware (optional, für Produktion) ---
if settings.trusted_hosts:
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[h.strip() for h in settings.trusted_hosts.split(",") if h.strip()],
    )


# --- Security Headers Middleware ---
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


# =============================================================================
# Pydantic Request/Response Modelle
# =============================================================================

class LoginRequest(BaseModel):
    """Request-Body für den Login-Endpoint."""
    password: str


class ToggleLoadRequest(BaseModel):
    """Request-Body für den Load-Toggle Endpoint."""
    state: bool  # True = EIN, False = AUS


class HealthResponse(BaseModel):
    """Response für den Health-Check Endpoint."""
    status: str
    mqtt_connected: bool
    last_data_received: Optional[str] = None
    uptime_seconds: int


# =============================================================================
# API Endpunkte
# =============================================================================

# --- Root-Route für UptimeRobot / Health-Checks ---
@app.get("/")
async def root():
    """Root-Endpoint für Monitoring-Dienste (UptimeRobot, Render Health Check)."""
    return {"status": "ok", "service": "fensterkraftwerk-backend"}


# --- Token-Validierung (öffentlich) ---
@app.post("/api/verify-token")
async def verify_token_endpoint(request: LoginRequest):
    """
    Prüft ob ein Token gültig ist, ohne ein neues auszustellen.
    Wird vom Frontend für Auto-Login per URL-Token verwendet.
    """
    if request.password != settings.api_token:
        raise HTTPException(status_code=401, detail="Ungültiges Token.")
    return {"valid": True}


# --- Login (öffentlich) ---
@app.post("/api/login")
async def login(request: LoginRequest):
    """
    Prüft das Passwort und gibt bei Erfolg das Token zurück.

    Das Token wird im Frontend gespeichert und bei allen
    weiteren API-Aufrufen als Bearer-Token mitgesendet.
    Alternativ kann ein QR-Code mit dem Token generiert werden.

    Request Body:
        { "password": "mein-geheimes-passwort" }

    Returns:
        { "token": "...", "message": "Login erfolgreich" }
    """
    if request.password != settings.api_token:
        raise HTTPException(status_code=401, detail="Falsches Passwort.")

    return {
        "token": settings.api_token,
        "message": "Login erfolgreich",
    }


# --- Live-Daten (geschützt) ---
@app.get("/api/live")
async def get_live_data(_token: str = Depends(verify_token)):
    """
    Gibt den zuletzt empfangenen Datensatz vom ESP32 zurück.

    Dieser Endpunkt liefert die aktuellsten Messwerte,
    die vom MQTT-Subscriber im Speicher gehalten werden.
    Keine Datenbankabfrage notwendig → minimale Latenz.

    Returns:
        JSON mit allen Messwerten oder 503 wenn keine Daten verfügbar.
    """
    if mqtt_service.last_data is None:
        raise HTTPException(
            status_code=503,
            detail="Noch keine Daten empfangen. Warte auf ESP32-Verbindung."
        )

    return {
        "data": mqtt_service.last_data,
        "received_at": mqtt_service.last_received.isoformat()
            if mqtt_service.last_received else None,
        "age_seconds": (
            (datetime.now(timezone.utc) - mqtt_service.last_received).total_seconds()
            if mqtt_service.last_received else None
        )
    }


@app.get("/api/history")
async def get_history(
    range: str = Query(
        default="-1h",
        description="Zeitbereich: -1h, -6h, -24h, -7d",
    ),
    _token: str = Depends(verify_token),
):
    """
    Gibt historische Messdaten aus der Supabase-Datenbank zurück.

    Query-Parameter:
        range: Zeitbereich relativ zur aktuellen Zeit
               Beispiele: -1h (letzte Stunde), -24h (letzter Tag),
                          -7d (letzte Woche)

    Returns:
        JSON-Array mit Zeitreihen-Datenpunkten.
    """
    data = mqtt_service.query_history(range)
    return {
        "range": range,
        "count": len(data),
        "data": data
    }


@app.post("/api/toggle-load")
async def toggle_load(
    request: ToggleLoadRequest,
    _token: str = Depends(verify_token),
):
    """
    Schaltet den Lastausgang (Load) des EPEVER Ladereglers.

    Der Befehl wird per MQTT an den ESP32 gesendet, der ihn über
    Modbus RTU (Coil 0x0002) an den Laderegler weiterleitet.

    Request Body:
        { "state": true }   → Load EIN
        { "state": false }  → Load AUS

    Returns:
        Bestätigung oder Fehlermeldung.
    """
    command = json.dumps({
        "command": "toggle_load",
        "state": request.state
    })

    success = mqtt_service.publish_command(command)

    if success:
        return {
            "status": "ok",
            "message": f"Load-Befehl gesendet: {'EIN' if request.state else 'AUS'}",
            "requested_state": request.state
        }
    else:
        raise HTTPException(
            status_code=502,
            detail="MQTT-Befehl konnte nicht gesendet werden."
        )


@app.get("/api/health", response_model=HealthResponse)
async def health_check():
    """
    Health-Check Endpunkt für Monitoring-Systeme.

    Gibt den Status des Backends zurück:
    - MQTT-Verbindungsstatus
    - Zeitpunkt des letzten Datenempfangs
    - Uptime des Backends
    """
    return HealthResponse(
        status="healthy",
        mqtt_connected=mqtt_service.client.is_connected(),
        last_data_received=(
            mqtt_service.last_received.isoformat()
            if mqtt_service.last_received else None
        ),
        uptime_seconds=int(time.time() - SERVER_START_TIME)
    )


# =============================================================================
# Direkter Start (Entwicklungsmodus)
# =============================================================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True,
        log_level="info"
    )
