# =============================================================================
# Fensterkraftwerk – MQTT-Service (HiveMQ Cloud → Supabase)
# =============================================================================
#
# Verbindet sich per TLS mit dem HiveMQ Cloud Broker, empfängt die
# JSON-Daten vom ESP32 und schreibt sie in die Supabase-Tabelle
# "power_logs". Hält außerdem den letzten Datensatz im Speicher
# für den Live-Endpunkt.
#
# Pipeline:  ESP32 → HiveMQ Cloud (MQTT/TLS) → dieses Modul → Supabase
#
# =============================================================================

import json
import logging
import ssl
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
import paho.mqtt.client as mqtt

from app.config import settings

logger = logging.getLogger("fensterkraftwerk.mqtt")


class MQTTService:
    """
    MQTT-Client der sich mit HiveMQ Cloud verbindet und Daten
    in Supabase persistiert.
    """

    def __init__(self):
        # Letzter empfangener Datensatz (für /api/live)
        self.last_data: Optional[Dict[str, Any]] = None
        self.last_received: Optional[datetime] = None

        # MQTT-Client (Paho)
        self.client = mqtt.Client(
            client_id="fensterkraftwerk-backend",
            protocol=mqtt.MQTTv311,
            clean_session=True,
        )

        # TLS für HiveMQ Cloud (Port 8883)
        # Ohne tls_version → nutzt automatisch die beste verfügbare Version
        self.client.tls_set(cert_reqs=ssl.CERT_REQUIRED, tls_version=ssl.PROTOCOL_TLSv1_2)

        # Reconnect-Verhalten: exponentielles Backoff (1s → max 30s)
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)

        # Benutzername / Passwort
        if settings.mqtt_username and settings.mqtt_password:
            self.client.username_pw_set(
                settings.mqtt_username, settings.mqtt_password
            )

        # Callbacks registrieren
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

        # Supabase HTTP-Client (persistent)
        self._http = httpx.Client(timeout=10.0)

        # Hintergrund-Thread
        self._thread: Optional[threading.Thread] = None

    # -----------------------------------------------------------------
    # Start / Stop
    # -----------------------------------------------------------------
    def start(self):
        """Verbindet sich mit HiveMQ und startet den Empfangs-Loop."""
        if not settings.mqtt_host:
            logger.warning("MQTT_HOST nicht konfiguriert – MQTT deaktiviert.")
            return

        try:
            logger.info(
                f"Verbinde mit MQTT Broker: {settings.mqtt_host}:{settings.mqtt_port}"
            )
            self.client.connect(
                settings.mqtt_host,
                port=settings.mqtt_port,
                keepalive=60,
            )
            self.client.loop_start()
            logger.info("MQTT-Loop gestartet.")
        except Exception as e:
            logger.error(f"MQTT-Verbindung fehlgeschlagen: {e}")

    def stop(self):
        """Trennt die MQTT-Verbindung und stoppt den Loop."""
        try:
            self.client.loop_stop()
            self.client.disconnect()
            self._http.close()
            logger.info("MQTT-Verbindung getrennt.")
        except Exception:
            pass

    # -----------------------------------------------------------------
    # MQTT Callbacks
    # -----------------------------------------------------------------
    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info(f"MQTT verbunden (rc={rc}). Abonniere Topics...")
            client.subscribe(settings.mqtt_topic_data, qos=1)
            logger.info(f"  → {settings.mqtt_topic_data}")
        else:
            logger.error(f"MQTT-Verbindung fehlgeschlagen: rc={rc}")

    def _on_disconnect(self, client, userdata, rc):
        if rc != 0:
            logger.warning(f"MQTT unerwartet getrennt (rc={rc}). Reconnect...")

    def _on_message(self, client, userdata, msg):
        """
        Wird bei jeder eingehenden MQTT-Nachricht aufgerufen.
        Parst das JSON, speichert es als Live-Daten und schreibt
        es in die Supabase-Datenbank.
        """
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
            logger.info(
                f"Daten empfangen von Device '{payload.get('device_id', '?')}'"
            )

            # Live-Daten im Speicher halten
            self.last_data = payload
            self.last_received = datetime.utcnow()

            # In Supabase schreiben
            self._write_to_supabase(payload)

        except json.JSONDecodeError as e:
            logger.error(f"Ungültiges JSON empfangen: {e}")
        except Exception as e:
            logger.error(f"Fehler bei Nachrichtenverarbeitung: {e}")

    # -----------------------------------------------------------------
    # Supabase – Daten schreiben
    # -----------------------------------------------------------------
    def _write_to_supabase(self, data: Dict[str, Any]):
        """
        Schreibt einen Datensatz in die Supabase-Tabelle 'power_logs'.
        Das verschachtelte JSON wird flach in Spalten gemappt.
        """
        if not settings.supabase_url or not settings.supabase_key:
            logger.debug("Supabase nicht konfiguriert – Daten nur im Speicher.")
            return

        pv = data.get("pv", {})
        batt = data.get("battery", {})
        load = data.get("load", {})
        energy = data.get("energy", {})

        row = {
            "device_id": data.get("device_id", "unknown"),
            "pv_voltage": pv.get("voltage"),
            "pv_current": pv.get("current"),
            "pv_power": pv.get("power"),
            "batt_voltage": batt.get("voltage"),
            "batt_charge_current": batt.get("charge_current"),
            "batt_charge_power": batt.get("charge_power"),
            "batt_temperature": batt.get("temperature"),
            "batt_soc": batt.get("soc"),
            "load_voltage": load.get("voltage"),
            "load_current": load.get("current"),
            "load_power": load.get("power"),
            "load_enabled": load.get("enabled"),
            "energy_today": energy.get("today_kwh"),
            "energy_total": energy.get("total_kwh"),
            "rssi": data.get("rssi"),
            "uptime": data.get("uptime"),
        }

        try:
            response = self._http.post(
                f"{settings.supabase_url}/rest/v1/power_logs",
                headers={
                    "apikey": settings.supabase_key,
                    "Authorization": f"Bearer {settings.supabase_key}",
                    "Content-Type": "application/json",
                    "Prefer": "return=minimal",
                },
                json=row,
            )
            if response.status_code in (200, 201):
                logger.debug("Datensatz in Supabase gespeichert.")
            else:
                logger.error(
                    f"Supabase-Fehler {response.status_code}: {response.text}"
                )
        except Exception as e:
            logger.error(f"Supabase-Schreibfehler: {e}")

    # -----------------------------------------------------------------
    # Supabase – History abfragen
    # -----------------------------------------------------------------
    def query_history(
        self, range_str: str = "-1h", limit: int = 500
    ) -> List[Dict[str, Any]]:
        """
        Fragt historische Daten aus der Supabase-Tabelle 'power_logs' ab.

        Args:
            range_str: Zeitbereich relativ zu jetzt, z.B. "-1h", "-24h", "-7d"
            limit: Maximale Anzahl Datenpunkte

        Returns:
            Liste von Datensätzen, chronologisch sortiert.
        """
        if not settings.supabase_url or not settings.supabase_key:
            return []

        # Range-String parsen: "-1h" → 1 Stunde, "-7d" → 7 Tage
        from datetime import timedelta

        amount = int(range_str[1:-1])
        unit = range_str[-1]
        delta_map = {"h": "hours", "d": "days", "w": "weeks", "m": "days"}
        multiplier = 30 if unit == "m" else 1  # m = Monat ≈ 30 Tage
        delta = timedelta(**{delta_map.get(unit, "hours"): amount * multiplier})

        since = (datetime.utcnow() - delta).isoformat() + "Z"

        try:
            response = self._http.get(
                f"{settings.supabase_url}/rest/v1/power_logs",
                headers={
                    "apikey": settings.supabase_key,
                    "Authorization": f"Bearer {settings.supabase_key}",
                },
                params={
                    "select": "*",
                    "created_at": f"gte.{since}",
                    "order": "created_at.asc",
                    "limit": str(limit),
                },
            )
            if response.status_code == 200:
                rows = response.json()
                # In das Format mappen, das das Dashboard erwartet
                return [
                    {
                        "time": r.get("created_at"),
                        "pv_voltage": r.get("pv_voltage"),
                        "pv_current": r.get("pv_current"),
                        "pv_power": r.get("pv_power"),
                        "batt_voltage": r.get("batt_voltage"),
                        "batt_charge_current": r.get("batt_charge_current"),
                        "batt_soc": r.get("batt_soc"),
                        "load_power": r.get("load_power"),
                        "energy_today": r.get("energy_today"),
                    }
                    for r in rows
                ]
            else:
                logger.error(
                    f"Supabase-Abfrage fehlgeschlagen: {response.status_code}"
                )
                return []
        except Exception as e:
            logger.error(f"Supabase-Abfragefehler: {e}")
            return []

    # -----------------------------------------------------------------
    # MQTT – Befehl senden (z.B. Load-Toggle)
    # -----------------------------------------------------------------
    def publish_command(self, command_json: str) -> bool:
        """Sendet einen Befehl per MQTT an den ESP32."""
        if not self.client.is_connected():
            logger.warning("MQTT nicht verbunden – Befehl nicht gesendet.")
            return False

        try:
            result = self.client.publish(
                settings.mqtt_topic_command, command_json, qos=1
            )
            logger.info(
                f"MQTT publish result: rc={result.rc}, mid={result.mid}, "
                f"is_published={result.is_published() if result.rc == mqtt.MQTT_ERR_SUCCESS else 'N/A'}"
            )
            # Bei QoS 1 warten bis die Nachricht tatsächlich gesendet wurde
            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                result.wait_for_publish(timeout=5)
                logger.info(f"MQTT Befehl erfolgreich gesendet: {command_json}")
                return True
            else:
                logger.error(f"MQTT publish fehlgeschlagen: rc={result.rc}")
                return False
        except Exception as e:
            logger.error(f"MQTT publish Exception: {e}")
            return False


# Singleton-Instanz
mqtt_service = MQTTService()
