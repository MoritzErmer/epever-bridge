# =============================================================================
# Fensterkraftwerk – Konfiguration (Umgebungsvariablen)
# =============================================================================
#
# Alle sensiblen Daten werden aus Umgebungsvariablen geladen.
# Auf Render.com werden diese im Dashboard unter "Environment" gesetzt.
# Lokal können sie in einer .env-Datei definiert werden.
#
# =============================================================================

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    Zentrale Konfiguration – wird aus Umgebungsvariablen oder .env geladen.
    """

    # --- Supabase ---
    supabase_url: str = ""
    supabase_key: str = ""  # anon/public Key reicht für Inserts

    # --- HiveMQ Cloud MQTT ---
    mqtt_host: str = ""               # z.B. "abc123.s1.eu.hivemq.cloud"
    mqtt_port: int = 8883             # TLS-Port
    mqtt_username: str = ""           # HiveMQ Credentials
    mqtt_password: str = ""
    mqtt_topic_data: str = "fensterkraftwerk/data"
    mqtt_topic_command: str = "fensterkraftwerk/command"
    mqtt_topic_status: str = "fensterkraftwerk/status"

    # --- Authentifizierung ---
    # Dieses Token ist gleichzeitig das „Passwort" für die Web-Oberfläche.
    # Kann auch als QR-Code geteilt werden: https://domain/?token=xxx
    api_token: str = "changeme"

    # --- CORS (kommaseparierte Liste erlaubter Origins) ---
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # --- Trusted Hosts (kommasepariert, leer = alle erlaubt) ---
    trusted_hosts: str = ""

    # --- FastAPI ---
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# Singleton-Instanz – wird überall importiert
settings = Settings()
